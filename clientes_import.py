"""
Importação de "médicos clientes" a partir da planilha do sistema de pedidos
(ex.: ESPECIALISTAS_dd_mm_aaaa.xlsx).

Regra: todo médico com "ULTIMO PEDIDO" preenchido já comprou/prescreveu pelo
promotor da coluna "PROMOTOR" -> vira "cliente" (vermelho) no painel desse
promotor dentro do i.cannal.

Funciona em 2 passos (a tela do admin chama os dois):
  1. prévia: lê a planilha, liga cada promotor a um usuário do i.cannal e diz
     o que vai acontecer com cada médico — nada é gravado;
  2. aplicar: grava (marca os que já estão no painel e, se o admin quiser,
     adiciona os que faltam já marcados como cliente).

Médico que está no painel de OUTRO usuário não é mexido (só aparece no
relatório), porque cada médico só pode estar em um painel.
"""

import csv
import io
import re
import unicodedata
from datetime import datetime

UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
       "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}
CONSELHOS = ("CRMV", "CRM", "CRO", "CRN", "CRF", "CRP", "COREN", "CREFITO")
LIMITE_LINHAS = 20000


def _sem_acento(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", texto).strip().lower()


def _cabecalho(texto) -> str:
    return re.sub(r"[^a-z0-9]", "", _sem_acento(texto))


# nomes de coluna aceitos (sem acento/espaço) -> campo interno
_COLUNAS = {
    "nome": "nome", "nomemedico": "nome", "medico": "nome",
    "registro": "registro", "crm": "registro", "numeroregistro": "registro",
    "conselho": "conselho",
    "estado": "estado", "ufregistro": "estado", "ufconselho": "estado",
    "telefone": "telefone", "celular": "telefone",
    "endereco": "endereco", "numero": "numero", "complemento": "complemento", "bairro": "bairro",
    "cidade": "cidade", "uf": "uf",
    "especialidades": "especialidade", "especialidade": "especialidade",
    "promotor": "promotor", "promotorresponsavel": "promotor", "representante": "promotor",
    "ultimopedido": "ultimo_pedido", "ultimaprescricao": "ultimo_pedido", "ultimapreescricao": "ultimo_pedido",
}


def ler_planilha(conteudo: bytes, nome_arquivo: str):
    """Devolve (linhas, erro). Cada linha é um dict com os campos internos."""
    nome_arquivo = (nome_arquivo or "").lower()
    try:
        if nome_arquivo.endswith((".xlsx", ".xlsm")):
            from openpyxl import load_workbook
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                planilha = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True).worksheets[0]
            brutas = list(planilha.iter_rows(values_only=True))
        elif nome_arquivo.endswith(".csv"):
            texto = conteudo.decode("utf-8-sig", errors="replace")
            dialeto = csv.Sniffer().sniff(texto[:4000], delimiters=";,\t") if texto.strip() else csv.excel
            brutas = list(csv.reader(io.StringIO(texto), dialeto))
        else:
            return [], "Envie a planilha em .xlsx ou .csv."
    except Exception:
        return [], "Não consegui abrir esse arquivo. Confira se é a planilha .xlsx/.csv do sistema de pedidos."

    if not brutas:
        return [], "A planilha está vazia."
    mapa = {}
    for i, titulo in enumerate(brutas[0]):
        campo = _COLUNAS.get(_cabecalho(titulo))
        if campo and campo not in mapa:
            mapa[campo] = i
    faltando = [c for c in ("nome", "registro", "promotor", "ultimo_pedido") if c not in mapa]
    if faltando:
        nomes = {"nome": "NOME", "registro": "REGISTRO", "promotor": "PROMOTOR", "ultimo_pedido": "ULTIMO PEDIDO"}
        return [], "A planilha precisa das colunas: " + ", ".join(nomes[c] for c in faltando) + "."
    if len(brutas) - 1 > LIMITE_LINHAS:
        return [], f"A planilha tem mais de {LIMITE_LINHAS} linhas."

    linhas = []
    for bruta in brutas[1:]:
        linha = {}
        for campo, i in mapa.items():
            valor = bruta[i] if i < len(bruta) else None
            linha[campo] = "" if valor is None else str(valor).strip()
        if linha.get("nome"):
            linhas.append(linha)
    return linhas, None


def data_do_pedido(texto: str):
    """'29719/2026 - 05/11/2026' -> '2026-11-05' (ou None)."""
    achados = re.findall(r"(\d{2})/(\d{2})/(\d{4})", texto or "")
    if not achados:
        return None
    dia, mes, ano = achados[-1]
    try:
        return datetime(int(ano), int(mes), int(dia)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _numero(texto) -> str:
    """Maior sequência de dígitos, sem zeros à esquerda ('043.501' -> '43501')."""
    juntos = re.sub(r"[.\s]", "", str(texto or ""))
    sequencias = re.findall(r"\d+", juntos)
    if not sequencias:
        return ""
    return max(sequencias, key=len).lstrip("0") or "0"


def registro_da_planilha(linha: dict):
    """(conselho, uf, numero) do médico da planilha, ou None se não der pra ler."""
    numero = _numero(linha.get("registro"))
    if not numero or numero == "0":
        return None
    conselho = (linha.get("conselho") or "CRM").strip().upper()
    conselho = next((c for c in CONSELHOS if conselho.startswith(c)), "CRM")
    uf = (linha.get("estado") or "").strip().upper()
    if uf not in UFS:
        # às vezes a UF vem dentro do número: "MG33409", "CRM/PR 3577"
        dentro = [t for t in re.findall(r"[A-Z]{2}", str(linha.get("registro") or "").upper()) if t in UFS]
        uf = dentro[0] if dentro else (linha.get("uf") or "").strip().upper()
    return conselho, (uf if uf in UFS else ""), numero


def registro_do_painel(texto_crm: str):
    """Mesmo formato, a partir do CRM salvo no painel ('CRM SP 12345', 'CRM-SP 99100', '12345/SP')."""
    texto = str(texto_crm or "").upper()
    if not texto or "ENCONTRADO" in texto:
        return None
    numero = _numero(texto)
    if not numero or numero == "0":
        return None
    conselho = next((c for c in CONSELHOS if re.search(rf"\b{c}\b", texto)), "CRM")
    ufs = [t for t in re.findall(r"\b[A-Z]{2}\b", texto) if t in UFS]
    if not ufs:
        ufs = [t for t in re.findall(r"[A-Z]{2}", texto.replace(conselho, " ")) if t in UFS]
    return conselho, (ufs[0] if ufs else ""), numero


def sugerir_usuario(nome_promotor: str, usuarios: list):
    """Liga o nome do promotor da planilha a um usuário do i.cannal: nome igual
    (sem acento/maiúscula) ou, se só houver um, mesmo 1º e último nome."""
    alvo = _sem_acento(nome_promotor)
    if not alvo:
        return None
    for u in usuarios:
        if _sem_acento(u["nome"]) == alvo:
            return u["id"]
    partes = alvo.split()
    if len(partes) < 2:
        return None
    candidatos = [u for u in usuarios
                  if len(_sem_acento(u["nome"]).split()) >= 2
                  and _sem_acento(u["nome"]).split()[0] == partes[0]
                  and _sem_acento(u["nome"]).split()[-1] == partes[-1]]
    return candidatos[0]["id"] if len(candidatos) == 1 else None


def _nome_chave(nome: str) -> str:
    return re.sub(r"^(dr|dra|doutor|doutora)\.?\s+", "", _sem_acento(nome))


def planejar(linhas: list, mapa_promotores: dict, painel_todos: list, adicionar_ausentes: bool):
    """Decide o que fazer com cada médico com pedido. Não grava nada.

    mapa_promotores: {nome do promotor na planilha: id do usuário ou None}
    painel_todos: todos os médicos de todos os painéis (id, usuario_id, nome, crm, ja_comprou)
    Devolve (acoes, resumo_por_promotor, totais)."""
    por_registro = {}   # (conselho, uf, numero) -> [entradas]
    por_numero = {}     # (conselho, numero) -> [entradas sem UF no CRM]
    por_nome = {}       # (usuario_id, nome) -> entrada
    for e in painel_todos:
        reg = registro_do_painel(e["crm"])
        if reg:
            if reg[1]:
                por_registro.setdefault(reg, []).append(e)
            else:
                por_numero.setdefault((reg[0], reg[2]), []).append(e)
        por_nome.setdefault((e["usuario_id"], _nome_chave(e["nome"])), e)

    acoes, vistos = [], set()
    resumo = {}
    totais = {"com_pedido": 0, "marcar": 0, "ja_cliente": 0, "adicionar": 0, "ausente": 0,
              "outro_painel": 0, "sem_promotor": 0, "sem_registro": 0}

    for linha in linhas:
        pedido = data_do_pedido(linha.get("ultimo_pedido"))
        if not (linha.get("ultimo_pedido") or "").strip():
            continue
        totais["com_pedido"] += 1
        promotor = (linha.get("promotor") or "").strip() or "(sem promotor)"
        r = resumo.setdefault(promotor, {"promotor": promotor, "usuario_id": mapa_promotores.get(promotor),
                                         "medicos": 0, "marcar": 0, "ja_cliente": 0, "adicionar": 0,
                                         "ausente": 0, "outro_painel": 0, "sem_registro": 0})
        r["medicos"] += 1
        usuario_id = mapa_promotores.get(promotor)
        reg = registro_da_planilha(linha)
        acao = {"linha": linha, "promotor": promotor, "usuario_id": usuario_id, "registro": reg,
                "ultimo_pedido": pedido, "tipo": None, "entrada": None}

        if not usuario_id:
            acao["tipo"] = "sem_promotor"
            totais["sem_promotor"] += 1
            acoes.append(acao)
            continue

        chave_dup = (usuario_id, reg or _nome_chave(linha["nome"]))
        if chave_dup in vistos:
            continue  # mesmo médico repetido na planilha pro mesmo promotor
        vistos.add(chave_dup)

        candidatos = []
        if reg:
            candidatos = list(por_registro.get(reg, []))
            if reg[1]:
                candidatos += por_numero.get((reg[0], reg[2]), [])
            else:
                candidatos += [e for (c, uf, n), lst in por_registro.items() if c == reg[0] and n == reg[2] for e in lst]
                candidatos += por_numero.get((reg[0], reg[2]), [])
        no_meu = [e for e in candidatos if e["usuario_id"] == usuario_id]
        if not no_meu:
            por_nome_meu = por_nome.get((usuario_id, _nome_chave(linha["nome"])))
            if por_nome_meu:
                no_meu = [por_nome_meu]

        if no_meu:
            entrada = no_meu[0]
            acao["entrada"] = entrada
            acao["tipo"] = "ja_cliente" if entrada["ja_comprou"] else "marcar"
        elif candidatos:
            acao["entrada"] = candidatos[0]
            acao["tipo"] = "outro_painel"
        else:
            # sem registro na planilha também entra (o painel identifica por nome + cidade)
            acao["tipo"] = "adicionar" if adicionar_ausentes else "ausente"
        totais[acao["tipo"]] += 1
        r[acao["tipo"]] += 1
        acoes.append(acao)

    return acoes, sorted(resumo.values(), key=lambda x: (-x["medicos"], x["promotor"])), totais


def texto_registro(reg) -> str:
    if not reg:
        return "Não encontrado"
    conselho, uf, numero = reg
    return f"{conselho} {uf} {numero}".replace("  ", " ")


def medico_para_painel(linha: dict, reg) -> dict:
    """Monta o médico no formato do painel (pra adicionar quem ainda não está)."""
    endereco = ", ".join(x for x in (linha.get("endereco"), linha.get("numero")) if x)
    if linha.get("complemento"):
        endereco += f" - {linha['complemento']}"
    if linha.get("bairro"):
        endereco += f" - {linha['bairro']}"
    especialidade = (linha.get("especialidade") or "").split(",")[0].strip().title()
    cidade = (linha.get("cidade") or "").strip().title()
    return {
        "nome": (linha.get("nome") or "").strip().title(),
        "crm": texto_registro(reg),
        "especialidade": especialidade,
        "cidade": cidade,
        "uf": (linha.get("uf") or "").strip().upper()[:2],
        "endereco": endereco.strip(" -,") or "Não encontrado",
        "telefone": (linha.get("telefone") or "").strip() or "Não disponível",
    }
