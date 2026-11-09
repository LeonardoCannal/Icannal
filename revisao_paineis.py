"""
Revisão dos painéis médicos (ferramenta do admin): procura médicos
duplicados e dados estranhos em TODOS os painéis.

Grupos que aparecem:
  - mesmo_registro: o mesmo CRM (conselho + UF + número), mesmo escrito de
    jeitos diferentes ("CRM SP 123", "CRM-SP 123", "123/SP"). Quase certeza
    de duplicado.
  - numero_parecido: CRMs da mesma UF que diferem em UM dígito (trocado,
    sobrando, faltando ou invertido) e com nomes parecidos — típico erro de
    digitação.
  - mesmo_nome: mesmo nome (sem acento, sem "Dr./Dra."), CRM diferente ou
    ausente. Pode ser duplicado ou homônimo — por isso só "possível".
  - nome_parecido: nomes quase iguais (erro de digitação, nome a mais/menos).
  - telefone / endereco: o mesmo telefone ou endereço em médicos diferentes.
  - incompletos: sem CRM/registro, ou com número curto/longo demais.

Em cada grupo vai uma sugestão de qual manter (cliente > visitado/agendado >
tem telefone > mais antigo); apagar é sempre escolha do admin.
"""

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

from clientes_import import registro_do_painel

TITULOS = re.compile(r"^(dr|dra|doutor|doutora)\.?\s+")
_NAO_LETRA = re.compile(r"[^a-z ]")
_ESPACOS = re.compile(r"\s+")


def nome_normalizado(nome) -> str:
    return _nome_normalizado(str(nome or ""))


@lru_cache(maxsize=20000)
def _nome_normalizado(nome: str) -> str:  # o mesmo nome é comparado várias vezes
    texto = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    texto = _NAO_LETRA.sub(" ", texto.lower())
    texto = _ESPACOS.sub(" ", texto).strip()
    return TITULOS.sub("", texto)


def nomes_parecidos(a: str, b: str) -> bool:
    na, nb = nome_normalizado(a), nome_normalizado(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    pa, pb = na.split(), nb.split()
    if pa[0] == pb[0] and pa[-1] == pb[-1]:
        return True
    curto, longo = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    ignorar = {"de", "da", "do", "das", "dos", "e"}
    curto = [t for t in curto if t not in ignorar]
    if len(curto) >= 2 and curto[0] == longo[0] and set(curto) <= set(longo):
        return True  # "Fernanda Cortes" x "Fernanda Cortes Ribeiro"
    return SequenceMatcher(None, na, nb).ratio() >= 0.82


def _variacoes(numero: str):
    """Chaves que dois números com UMA diferença compartilham: o próprio
    número, ele sem cada dígito (pega troca, sobra e falta) e com vizinhos
    invertidos (pega 12 <-> 21). Os candidatos são conferidos depois."""
    saida = {numero}
    saida |= {numero[:i] + numero[i + 1:] for i in range(len(numero))}
    saida |= {numero[:i] + numero[i + 1] + numero[i] + numero[i + 2:] for i in range(len(numero) - 1)}
    return saida


def _um_digito_de_diferenca(a: str, b: str) -> bool:
    if a == b:
        return False
    if len(a) == len(b):
        dif = [i for i in range(len(a)) if a[i] != b[i]]
        if len(dif) == 1:
            return True
        return len(dif) == 2 and dif[1] == dif[0] + 1 and a[dif[0]] == b[dif[1]] and a[dif[1]] == b[dif[0]]
    if abs(len(a) - len(b)) == 1:
        maior, menor = (a, b) if len(a) > len(b) else (b, a)
        return any(maior[:i] + maior[i + 1:] == menor for i in range(len(maior)))
    return False


def _sugestao_de_qual_manter(entradas: list) -> int:
    """Cliente > visitado > agendado > tem telefone > o mais antigo."""
    def ordem(e):
        tem_tel = len(re.sub(r"\D", "", f"{e.get('telefone') or ''}{e.get('telefone_pessoal') or ''}")) >= 8
        status = {"visitado": 2, "agendado": 1}.get(e.get("status"), 0)
        return (-(1 if e.get("ja_comprou") else 0), -status, -(1 if tem_tel else 0), e.get("adicionado_em") or "9999")
    return sorted(entradas, key=ordem)[0]["id"]


def _juntar_pares(pares):
    """Junta pares (a, b) encadeados em grupos (a~b e b~c viram {a, b, c})."""
    pai = {}
    def raiz(x):
        while pai.get(x, x) != x:
            pai[x] = pai.get(pai[x], pai[x])
            x = pai[x]
        return x
    for a, b in pares:
        ra, rb = raiz(a), raiz(b)
        if ra != rb:
            pai[ra] = rb
    grupos = {}
    for a, b in pares:
        grupos.setdefault(raiz(a), set()).update((a, b))
    return list(grupos.values())


def _sem_acento(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", texto.lower()).strip()


def nomes_muito_parecidos(a: str, b: str) -> bool:
    """Mais exigente que nomes_parecidos — pra comparar nomes sem outro indício
    (erro de digitação, nome do meio a mais/a menos)."""
    na, nb = nome_normalizado(a), nome_normalizado(b)
    if not na or not nb or na == nb:
        return False
    pa, pb = na.split(), nb.split()
    if len(pa) < 2 or len(pb) < 2:
        return False
    ignorar = {"de", "da", "do", "das", "dos", "e"}
    curto, longo = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    curto = [t for t in curto if t not in ignorar]
    if len(curto) >= 2 and curto[0] == longo[0] and curto[-1] == longo[-1] and set(curto) <= set(longo):
        return True
    m = SequenceMatcher(None, na, nb)
    return m.real_quick_ratio() >= 0.88 and m.quick_ratio() >= 0.88 and m.ratio() >= 0.88


def telefone_chave(telefone: str):
    """(DDD, número) pra comparar telefones: '+55 (15) 99777-3480' -> ('15', '997773480')."""
    d = re.sub(r"\D", "", str(telefone or ""))
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    d = d.lstrip("0")
    if len(d) not in (10, 11):
        return None
    numero = d[2:]
    if len(set(numero)) <= 2:  # 0000-0000, 9999-9999...
        return None
    return d[:2], numero


_ABREV = {"r": "rua", "av": "avenida", "avn": "avenida", "al": "alameda", "tv": "travessa", "trav": "travessa",
          "pca": "praca", "pc": "praca", "rod": "rodovia", "est": "estrada", "dr": "doutor", "prof": "professor",
          "cel": "coronel", "gal": "general", "gen": "general", "sto": "santo", "sta": "santa", "n": "", "no": "", "nro": ""}
_ENDERECO_CORTE = re.compile(r"\b(sala|sl|conj|conjunto|cj|andar|apto|ap|bloco|bl|loja|lj|cep|terreo)\b.*$")


def endereco_chave(endereco: str):
    """(rua normalizada, número) — sem sala/andar/bairro. None se não der pra ler."""
    texto = _sem_acento(endereco)
    if not texto or "nao encontrado" in texto or "nao informado" in texto:
        return None
    texto = re.sub(r"[^a-z0-9 ]", " ", texto)
    numero = re.search(r"\b(\d{1,6})\b", texto)
    if not numero:
        return None
    rua = texto[:numero.start()]
    rua = _ENDERECO_CORTE.sub("", rua)
    palavras = [_ABREV.get(t, t) for t in rua.split()]
    palavras = [t for t in palavras if t]
    if len(palavras) < 2:
        return None
    return " ".join(palavras), numero.group(1)


def analisar(entradas: list) -> dict:
    """entradas: todos os médicos de todos os painéis (dicts com id, usuario_id,
    usuario_nome, nome, crm, cidade, uf, telefone, status, ja_comprou, adicionado_em)."""
    por_id = {e["id"]: e for e in entradas}
    registro = {e["id"]: registro_do_painel(e.get("crm")) for e in entradas}

    grupos = []
    # Não repete o mesmo conjunto de médicos dentro do mesmo tipo, nem o que já
    # é duplicado certo (mesmo CRM) ou CRM quase igual. Telefone e endereço
    # aparecem mesmo que o nome também tenha aparecido (são outra pista).
    ja_agrupados = {}

    def novo_grupo(tipo, titulo, motivo, ids):
        ids = sorted(set(ids), key=lambda i: (por_id[i].get("usuario_nome") or "", por_id[i]["nome"]))
        if len(ids) < 2:
            return
        chave = frozenset(ids)
        fortes = {"mesmo_nome": ("mesmo_registro", "numero_parecido"),
                  "nome_parecido": ("mesmo_registro", "numero_parecido", "mesmo_nome")}.get(tipo, ("mesmo_registro",))
        comparar = [g for t in set(fortes + (tipo,)) for g in ja_agrupados.get(t, [])]
        if any(chave <= g for g in comparar):
            return
        ja_agrupados.setdefault(tipo, []).append(chave)
        grupos.append({
            "tipo": tipo, "titulo": titulo, "motivo": motivo,
            "manter": _sugestao_de_qual_manter([por_id[i] for i in ids]),
            "paineis": len({por_id[i]["usuario_id"] for i in ids}),
            "itens": [por_id[i] for i in ids],
        })

    # 1) mesmo registro
    por_reg = {}
    for i, reg in registro.items():
        if reg and reg[1]:
            por_reg.setdefault(reg, []).append(i)
    sem_uf = {}
    for i, reg in registro.items():
        if reg and not reg[1]:
            sem_uf.setdefault((reg[0], reg[2]), []).append(i)
    for (conselho, uf, numero), ids in sorted(por_reg.items()):
        ids = ids + [j for j in sem_uf.get((conselho, numero), []) if nomes_parecidos(por_id[j]["nome"], por_id[ids[0]]["nome"])]
        if len(ids) > 1:
            novo_grupo("mesmo_registro", f"{conselho} {uf} {numero}", "Mesmo registro em mais de um lugar", ids)
    for (conselho, numero), ids in sem_uf.items():
        if len(ids) > 1 and all(nomes_parecidos(por_id[ids[0]]["nome"], por_id[j]["nome"]) for j in ids[1:]):
            novo_grupo("mesmo_registro", f"{conselho} {numero}", "Mesmo registro em mais de um lugar", ids)

    # 2) números muito parecidos (mesmo conselho + UF, 1 dígito de diferença, nomes parecidos)
    indice = {}
    for i, reg in registro.items():
        if not reg or not reg[1] or len(reg[2]) < 3:
            continue
        for var in _variacoes(reg[2]):
            indice.setdefault((reg[0], reg[1], var), set()).add(i)
    pares = set()
    for ids in indice.values():
        if len(ids) < 2:
            continue
        lista = sorted(ids)
        for x in range(len(lista)):
            for y in range(x + 1, len(lista)):
                a, b = lista[x], lista[y]
                if _um_digito_de_diferenca(registro[a][2], registro[b][2]) and nomes_parecidos(por_id[a]["nome"], por_id[b]["nome"]):
                    pares.add((a, b))
    # junta pares encadeados num grupo só
    pai = {}
    def raiz(x):
        while pai.get(x, x) != x:
            x = pai[x]
        return x
    for a, b in pares:
        pai[raiz(a)] = raiz(b)
    juntos = {}
    for a, b in pares:
        juntos.setdefault(raiz(a), set()).update((a, b))
    for ids in juntos.values():
        numeros = " / ".join(sorted({registro[i][2] for i in ids}))
        uf = registro[next(iter(ids))][1]
        novo_grupo("numero_parecido", f"CRM {uf}: {numeros}", "Números de registro quase iguais (1 dígito de diferença) e nomes parecidos", ids)

    # 3) mesmo nome
    por_nome = {}
    for e in entradas:
        n = nome_normalizado(e["nome"])
        if len(n.split()) >= 2:
            por_nome.setdefault(n, []).append(e["id"])
    for n, ids in sorted(por_nome.items()):
        if len(ids) > 1:
            regs = {registro[i] for i in ids}
            mesma_cidade = len({nome_normalizado(por_id[i].get("cidade")) for i in ids}) == 1
            motivo = "Mesmo nome" + (" e mesma cidade" if mesma_cidade else "") + (", registros diferentes" if len(regs) > 1 else "")
            novo_grupo("mesmo_nome", por_id[ids[0]]["nome"], motivo, ids)

    def uf_de(i):
        reg = registro[i]
        return (reg[1] if reg and reg[1] else (por_id[i].get("uf") or "").strip().upper())

    # 3b) nomes parecidos (erro de digitação, nome do meio a mais/a menos), mesma UF
    blocos = {}
    for e in entradas:
        partes = nome_normalizado(e["nome"]).split()
        if len(partes) < 2:
            continue
        for chave in (("p", partes[0]), ("u", partes[-1])):
            blocos.setdefault(chave, []).append(e["id"])
    pares = set()
    for ids in blocos.values():
        if len(ids) < 2 or len(ids) > 800:
            continue
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                a, b = ids[x], ids[y]
                ua, ub = uf_de(a), uf_de(b)
                if ua and ub and ua != ub:
                    continue
                if nomes_muito_parecidos(por_id[a]["nome"], por_id[b]["nome"]):
                    pares.add((min(a, b), max(a, b)))
    for ids in _juntar_pares(pares):
        nomes = sorted({por_id[i]["nome"] for i in ids}, key=len)
        novo_grupo("nome_parecido", " / ".join(nomes[:3]), "Nomes muito parecidos (pode ser erro de digitação)", ids)

    # 5) telefones iguais ou com 1 dígito de diferença (mesmo DDD)
    tel = {}
    for e in entradas:
        chaves = {telefone_chave(e.get("telefone")), telefone_chave(e.get("telefone_pessoal"))} - {None}
        if chaves:
            tel[e["id"]] = chaves
    indice = {}
    for i, chaves in tel.items():
        for ddd, numero in chaves:
            for var in _variacoes(numero):
                indice.setdefault((ddd, var), set()).add(i)
    iguais, quase = set(), set()
    for ids in indice.values():
        if len(ids) < 2 or len(ids) > 60:
            continue
        lista = sorted(ids)
        for x in range(len(lista)):
            for y in range(x + 1, len(lista)):
                a, b = lista[x], lista[y]
                if tel[a] & tel[b]:
                    iguais.add((a, b))
                elif any(da == db and _um_digito_de_diferenca(na, nb) for da, na in tel[a] for db, nb in tel[b]):
                    quase.add((a, b))
    def _fmt_tel(chaves):
        ddd, n = sorted(chaves)[0]
        return f"({ddd}) {n[:-4]}-{n[-4:]}"
    for ids in _juntar_pares(iguais):
        comuns = set.intersection(*(tel[i] for i in ids)) or tel[next(iter(ids))]
        novo_grupo("telefone", _fmt_tel(comuns), "Mesmo telefone (pode ser a mesma clínica com vários médicos)", ids)
    for ids in _juntar_pares(quase - iguais):
        numeros = " / ".join(sorted({_fmt_tel({c}) for i in ids for c in tel[i]}))[:80]
        novo_grupo("telefone", numeros, "Telefones quase iguais (1 dígito de diferença) — pode ser erro de digitação", ids)

    # 6) endereços iguais ou parecidos (mesma cidade e mesmo número)
    por_lugar = {}
    for e in entradas:
        chave = endereco_chave(e.get("endereco"))
        if chave:
            cidade = _sem_acento(e.get("cidade")) or (e.get("uf") or "").lower()
            por_lugar.setdefault((cidade, chave[1]), []).append((e["id"], chave[0]))
    pares_iguais, pares_quase = set(), set()
    for itens in por_lugar.values():
        if len(itens) < 2 or len(itens) > 80:
            continue
        for x in range(len(itens)):
            for y in range(x + 1, len(itens)):
                (a, ra), (b, rb) = itens[x], itens[y]
                if ra == rb:
                    pares_iguais.add((a, b))
                elif SequenceMatcher(None, ra, rb).ratio() >= 0.85:
                    pares_quase.add((a, b))
    for ids in _juntar_pares(pares_iguais | pares_quase):
        e0 = por_id[next(iter(ids))]
        exato = all(p in pares_iguais for p in pares_iguais | pares_quase if p[0] in ids)
        novo_grupo("endereco", (e0.get("endereco") or "")[:90] + (f" — {e0.get('cidade')}" if e0.get("cidade") else ""),
                   ("Mesmo endereço" if exato else "Endereços muito parecidos") + " (pode ser a mesma clínica com vários médicos)", ids)

    # 4) dados incompletos / estranhos
    incompletos = []
    for e in entradas:
        reg = registro[e["id"]]
        if not reg:
            incompletos.append({**e, "problema": "Sem CRM/registro"})
        elif len(reg[2]) < 3 or len(reg[2]) > 7:
            incompletos.append({**e, "problema": f"Número de registro estranho ({reg[2]})"})
        elif not reg[1]:
            incompletos.append({**e, "problema": "Registro sem UF"})

    ordem = {"mesmo_registro": 0, "numero_parecido": 1, "mesmo_nome": 2, "nome_parecido": 3, "telefone": 4, "endereco": 5}
    grupos.sort(key=lambda g: (ordem[g["tipo"]], g["titulo"]))
    totais = {t: sum(1 for g in grupos if g["tipo"] == t) for t in ordem}
    totais["incompletos"] = len(incompletos)
    totais["medicos"] = len(entradas)
    return {"grupos": grupos, "incompletos": incompletos, "totais": totais}
