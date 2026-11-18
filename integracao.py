"""Integração com o sistema interno.

Dois caminhos, sempre ligados pelo CPF do promotor:

1. i.cannal → sistema interno: todo médico que está no painel de um promotor
   é cadastrado lá, no promotor com o mesmo CPF.
2. sistema interno → i.cannal: todo médico cadastrado lá entra no painel do
   promotor com o mesmo CPF aqui, como "prospectado".

O mesmo médico nos dois lados é reconhecido pelo CRM + UF (sem CRM, pelo nome
+ cidade), então ninguém é cadastrado duas vezes. Quando o médico já está no
painel de OUTRO promotor no i.cannal, ou o promotor não existe aqui, nada é
mexido: vira um conflito para o admin decidir (Admin → Integração).

Configuração no servidor (/etc/cannal.env):
  INTEGRACAO_API_URL    endereço do sistema interno (https://...)
  INTEGRACAO_API_CHAVE  a chave de API gerada lá

O formato da troca (o que o sistema interno precisa ter) está em
INTEGRACAO-CONTRATO.md.
"""
import json
import os
import re
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

CONFIG_PADRAO = {
    "integracao_ligado": "0",
    "integracao_intervalo_min": "30",
    "integracao_cursor": "",        # maior "atualizado_em" já recebido do sistema interno
    "integracao_ultima": "",        # resumo (JSON) da última sincronização
    "integracao_ultima_em": "",
}
INTERVALOS_MIN = (15, 30, 60, 180, 360)
LOTE_ENVIO = 100                  # médicos por pedido ao sistema interno
MAX_ENVIO_POR_RODADA = 3000       # o resto vai na próxima rodada
POR_PAGINA = 500
MAX_PAGINAS = 200
TENTAR_ERRO_DE_NOVO_HORAS = 6
PAUSA_ENTRE_PEDIDOS = 0.5         # segundos (não sobrecarregar o sistema interno)

UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB",
       "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}

# quem segura a trava neste processo (cada processo do servidor tem o seu)
_DONO = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
def _url_base():
    return (os.environ.get("INTEGRACAO_API_URL") or "").strip().rstrip("/")


def _chave():
    return (os.environ.get("INTEGRACAO_API_CHAVE") or "").strip()


def url_permitida(url):
    """Só https (ou http dentro do próprio servidor): a chave não pode
    viajar aberta pela internet."""
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme == "https" and u.hostname:
        return True
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")


def situacao_config():
    """{configurada, problema, host} — para a tela do admin (nunca mostra a chave)."""
    url, chave = _url_base(), _chave()
    if not url and not chave:
        return {"configurada": False, "problema": "", "host": ""}
    if not url:
        return {"configurada": False, "problema": "Falta INTEGRACAO_API_URL em /etc/cannal.env.", "host": ""}
    if not chave:
        return {"configurada": False, "problema": "Falta INTEGRACAO_API_CHAVE em /etc/cannal.env.", "host": urlparse(url).hostname or ""}
    if not url_permitida(url):
        return {"configurada": False, "problema": "O endereço precisa começar com https://.", "host": urlparse(url).hostname or ""}
    return {"configurada": True, "problema": "", "host": urlparse(url).hostname or url}


# ---------------------------------------------------------------------------
# Pedidos ao sistema interno
# ---------------------------------------------------------------------------
class ErroIntegracao(Exception):
    pass


def _pedido(metodo, caminho, params=None, corpo=None):
    if not situacao_config()["configurada"]:
        raise ErroIntegracao(situacao_config()["problema"] or "Integração não configurada.")
    try:
        r = requests.request(
            metodo, _url_base() + caminho, params=params, json=corpo, timeout=(10, 90),
            headers={"Authorization": "Bearer " + _chave(), "Accept": "application/json", "User-Agent": "i.cannal"},
        )
    except requests.Timeout:
        raise ErroIntegracao("O sistema interno demorou demais para responder.")
    except requests.RequestException:
        raise ErroIntegracao("Não foi possível falar com o sistema interno (fora do ar ou endereço errado).")
    if r.status_code == 401:
        raise ErroIntegracao("O sistema interno recusou a chave (errada ou revogada).")
    if r.status_code == 403:
        raise ErroIntegracao("A chave não tem permissão para isso no sistema interno.")
    if r.status_code == 404:
        raise ErroIntegracao(f"O sistema interno não tem o endereço {caminho} (veja INTEGRACAO-CONTRATO.md).")
    if r.status_code == 429:
        raise ErroIntegracao("O sistema interno pediu para esperar (limite de pedidos). Continua na próxima rodada.")
    if r.status_code >= 400:
        raise ErroIntegracao(f"O sistema interno respondeu com erro {r.status_code}.")
    try:
        return r.json()
    except ValueError:
        raise ErroIntegracao("O sistema interno respondeu algo que não é JSON.")


def testar_conexao():
    """Devolve (ok, mensagem)."""
    try:
        dados = _pedido("GET", "/api/v1/medicos", params={"por_pagina": 1, "pagina": 1})
    except ErroIntegracao as e:
        return False, str(e)
    if not isinstance(dados, dict) or "dados" not in dados:
        return False, "O sistema interno respondeu, mas não no formato combinado (falta \"dados\")."
    total = dados.get("total")
    return True, f"Conectado. O sistema interno tem {total} médico(s) cadastrado(s)." if total is not None else "Conectado."


# ---------------------------------------------------------------------------
# Como saber que é o mesmo médico
# ---------------------------------------------------------------------------
def _sem_acento(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", t).strip().lower()


def cpf_digitos(cpf):
    return re.sub(r"\D", "", str(cpf or ""))


def crm_numero(crm):
    return re.sub(r"\D", "", str(crm or "")).lstrip("0")


def uf_do_medico(crm, uf):
    """UF do CRM ("CRM/SP 1234" → SP); se não tiver, a UF do cadastro."""
    for pedaco in re.findall(r"[A-Za-z]{2}", str(crm or "").replace("CRM", " ").replace("crm", " ")):
        if pedaco.upper() in UFS:
            return pedaco.upper()
    uf = str(uf or "").strip().upper()
    return uf if uf in UFS else ""


def chave_crm_uf(crm, uf):
    numero = crm_numero(crm)
    return f"{numero}-{uf_do_medico(crm, uf)}" if numero else ""


def chave_nome_cidade(nome, cidade):
    nome = re.sub(r"^(dra?\.?|doutora?)\s+", "", _sem_acento(nome))
    return f"{nome}|{_sem_acento(cidade)}" if nome else ""


class Indice:
    """Médicos de todos os painéis, achados por CRM+UF ou nome+cidade."""

    def __init__(self, linhas):
        self.por_crm, self.por_nome = {}, {}
        for l in linhas:
            self.adicionar(l)

    def adicionar(self, l):
        c = chave_crm_uf(l.get("crm"), l.get("uf"))
        if c:
            self.por_crm.setdefault(c, l)
        else:
            n = chave_nome_cidade(l.get("nome"), l.get("cidade"))
            if n:
                self.por_nome.setdefault(n, l)

    def achar(self, crm, uf, nome, cidade):
        c = chave_crm_uf(crm, uf)
        if c:
            return self.por_crm.get(c)
        n = chave_nome_cidade(nome, cidade)
        return self.por_nome.get(n) if n else None


def _texto(v):
    v = "" if v is None else str(v).strip()
    return "" if v.lower() == "não encontrado" else v


def _cpf_formatado(cpf):
    d = cpf_digitos(cpf)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else (d or "sem CPF")


# ---------------------------------------------------------------------------
# 1. i.cannal → sistema interno
# ---------------------------------------------------------------------------
def _para_envio(m):
    uf = uf_do_medico(m.get("crm"), m.get("uf"))
    return {
        "icannal_id": m["id"],
        "nome": _texto(m.get("nome")),
        "crm": crm_numero(_texto(m.get("crm"))),
        "uf": uf or _texto(m.get("uf")).upper()[:2],
        "especialidade": _texto(m.get("especialidade")),
        "cidade": _texto(m.get("cidade")),
        "endereco": _texto(m.get("endereco")),
        "telefone": _texto(m.get("telefone")),
        "telefone_pessoal": _texto(m.get("telefone_pessoal")),
        "situacao": m.get("status") or "prospectado",
        "cliente": bool(m.get("ja_comprou")),
        "promotor_cpf": cpf_digitos(m.get("promotor_cpf")),
        "promotor_nome": m.get("promotor_nome") or "",
    }


def enviar_medicos(auth, dormir=time.sleep):
    """Cadastra no sistema interno os médicos dos painéis que ainda não estão lá."""
    r = {"enviados": 0, "ja_existiam": 0, "sem_promotor": 0, "erros": 0, "conflitos_novos": 0}
    antes = (datetime.utcnow() - timedelta(hours=TENTAR_ERRO_DE_NOVO_HORAS)).isoformat()
    pendentes = auth.integ_pendentes_envio(MAX_ENVIO_POR_RODADA, antes)
    for i in range(0, len(pendentes), LOTE_ENVIO):
        if i:
            dormir(PAUSA_ENTRE_PEDIDOS)
        lote = pendentes[i:i + LOTE_ENVIO]
        por_id = {m["id"]: m for m in lote}
        resposta = _pedido("POST", "/api/v1/medicos/sincronizar", corpo={"medicos": [_para_envio(m) for m in lote]})
        resultados = resposta.get("resultados") if isinstance(resposta, dict) else None
        if not isinstance(resultados, list):
            raise ErroIntegracao("O sistema interno não devolveu \"resultados\" ao receber os médicos.")
        vistos = set()
        for res in resultados:
            try:
                pid = int(res.get("icannal_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            m = por_id.get(pid)
            if not m or pid in vistos:
                continue
            vistos.add(pid)
            status = res.get("status")
            externo = str(res.get("id")) if res.get("id") not in (None, "") else None
            if status == "criado":
                auth.integ_salvar_link(pid, externo, "icannal", "enviado")
                r["enviados"] += 1
            elif status == "ja_existe":
                auth.integ_salvar_link(pid, externo, "icannal", "ja_existia")
                r["ja_existiam"] += 1
                cpf_la = cpf_digitos(res.get("promotor_cpf"))
                if externo and cpf_la and cpf_la != cpf_digitos(m.get("promotor_cpf")):
                    quem = res.get("promotor_nome") or _cpf_formatado(cpf_la)
                    motivo = (f"No i.cannal está com {m.get('promotor_nome') or 'outro promotor'}; "
                              f"no sistema interno está com {quem}.")
                    if auth.integ_salvar_conflito(externo, "promotor_diferente", motivo,
                                                  json.dumps(_para_envio(m), ensure_ascii=False), pid):
                        r["conflitos_novos"] += 1
            elif status == "promotor_nao_encontrado":
                auth.integ_salvar_link(pid, externo, "icannal", "sem_promotor",
                                       f"Não há promotor com o CPF {_cpf_formatado(m.get('promotor_cpf'))} no sistema interno.")
                r["sem_promotor"] += 1
            else:
                auth.integ_salvar_link(pid, externo, "icannal", "erro", str(res.get("erro") or "Recusado pelo sistema interno.")[:300])
                r["erros"] += 1
        for pid in por_id.keys() - vistos:
            auth.integ_salvar_link(pid, None, "icannal", "erro", "O sistema interno não respondeu sobre este médico.")
            r["erros"] += 1
    return r


# ---------------------------------------------------------------------------
# 2. sistema interno → i.cannal
# ---------------------------------------------------------------------------
def _dados_externos(d):
    return {
        "id": str(d.get("id") or ""),
        "nome": _texto(d.get("nome")),
        "crm": _texto(d.get("crm")),
        "uf": _texto(d.get("uf")).upper()[:2],
        "especialidade": _texto(d.get("especialidade")),
        "cidade": _texto(d.get("cidade")),
        "endereco": _texto(d.get("endereco")),
        "telefone": _texto(d.get("telefone")),
        "promotor_cpf": cpf_digitos(d.get("promotor_cpf")),
        "promotor_nome": _texto(d.get("promotor_nome")),
    }


def processar_externo(auth, d, indice, links):
    """Coloca 1 médico do sistema interno no painel certo. Devolve
    (resultado, conflito_novo) — resultado: importado, vinculado, ja_ligado,
    conflito ou ignorado."""
    d = _dados_externos(d)
    eid = d["id"]
    if not eid or not d["nome"]:
        return "ignorado", False
    if eid in links:
        # já ligado a um médico do painel; o conflito de "promotor diferente"
        # (achado no envio) continua até o admin resolver
        auth.integ_apagar_conflito(eid, manter_tipo="promotor_diferente")
        return "ja_ligado", False
    dados_json = json.dumps(d, ensure_ascii=False)

    def conflito(tipo, motivo, painel_id=None):
        return "conflito", auth.integ_salvar_conflito(eid, tipo, motivo, dados_json, painel_id)

    cpf = d["promotor_cpf"]
    usuario = auth.buscar_usuario_por_cpf(cpf) if len(cpf) == 11 else None
    if not usuario:
        return conflito("sem_promotor", f"Não há promotor com o CPF {_cpf_formatado(cpf)} no i.cannal"
                        + (f" ({d['promotor_nome']} no sistema interno)." if d["promotor_nome"] else "."))
    usuario = dict(usuario)
    if usuario.get("desativado"):
        return conflito("sem_promotor", f"A conta de {usuario['nome']} está desativada no i.cannal.")
    if not usuario.get("aprovado"):
        return conflito("sem_promotor", f"O cadastro de {usuario['nome']} ainda não foi aprovado no i.cannal.")

    achado = indice.achar(d["crm"], d["uf"], d["nome"], d["cidade"])
    if achado:
        if achado["usuario_id"] == usuario["id"]:
            auth.integ_salvar_link(achado["id"], eid, "externo", "vinculado")
            links[eid] = achado["id"]
            auth.integ_apagar_conflito(eid)
            return "vinculado", False
        return conflito("outro_painel", f"Já está no painel de {achado.get('promotor_nome') or 'outro promotor'} no i.cannal; "
                        f"no sistema interno está com {usuario['nome']}.", achado["id"])

    uf = uf_do_medico(d["crm"], d["uf"])
    numero = crm_numero(d["crm"])
    medico = {
        "nome": d["nome"], "crm": f"CRM {uf} {numero}".replace("  ", " ") if numero else "",
        "uf": uf or d["uf"], "especialidade": d["especialidade"], "cidade": d["cidade"],
        "endereco": d["endereco"], "telefone": d["telefone"],
    }
    ok, mensagem = auth.adicionar_ao_painel(usuario["id"], medico)
    if not ok:
        return conflito("outro_painel", mensagem)
    pid = auth.integ_id_no_painel(usuario["id"], auth.computar_chave_medico(medico))
    if pid:
        auth.integ_salvar_link(pid, eid, "externo", "importado")
        links[eid] = pid
        indice.adicionar({"id": pid, "usuario_id": usuario["id"], "nome": medico["nome"], "crm": medico["crm"],
                          "uf": medico["uf"], "cidade": medico["cidade"], "promotor_nome": usuario["nome"]})
    auth.integ_apagar_conflito(eid)
    return "importado", False


def puxar_medicos(auth, cursor_desde="", dormir=time.sleep):
    """Traz os médicos do sistema interno alterados desde o último cursor."""
    r = {"recebidos": 0, "importados": 0, "vinculados": 0, "conflitos": 0, "conflitos_novos": 0, "cursor": cursor_desde}
    indice = Indice(auth.integ_mapa_painel())
    links = auth.integ_links_por_externo()
    maior = cursor_desde or ""
    pagina = 1
    while pagina and pagina <= MAX_PAGINAS:
        if pagina > 1:
            dormir(PAUSA_ENTRE_PEDIDOS)
        params = {"pagina": pagina, "por_pagina": POR_PAGINA}
        if cursor_desde:
            params["alterado_desde"] = cursor_desde
        resposta = _pedido("GET", "/api/v1/medicos", params=params)
        dados = resposta.get("dados") if isinstance(resposta, dict) else None
        if not isinstance(dados, list):
            raise ErroIntegracao("O sistema interno não devolveu a lista \"dados\" de médicos.")
        for d in dados:
            if not isinstance(d, dict):
                continue
            r["recebidos"] += 1
            resultado, novo = processar_externo(auth, d, indice, links)
            if resultado == "importado":
                r["importados"] += 1
            elif resultado == "vinculado":
                r["vinculados"] += 1
            elif resultado == "conflito":
                r["conflitos"] += 1
                r["conflitos_novos"] += int(bool(novo))
            quando = str(d.get("atualizado_em") or "")
            if quando > maior:
                maior = quando
        prox = resposta.get("proxima_pagina")
        pagina = int(prox) if isinstance(prox, int) or (isinstance(prox, str) and prox.isdigit()) else None
    r["cursor"] = maior
    # Conflitos em aberto: tenta de novo (o admin pode ter criado o promotor
    # ou mudado o médico de painel desde a última vez).
    for c in auth.integ_conflitos(abertos=True, limite=1000):
        if c["tipo"] == "promotor_diferente":
            continue
        try:
            d = json.loads(c["dados"] or "{}")
        except ValueError:
            continue
        resultado, _ = processar_externo(auth, d, indice, links)
        if resultado == "importado":
            r["importados"] += 1
        elif resultado == "vinculado":
            r["vinculados"] += 1
    return r


# ---------------------------------------------------------------------------
# Rodada completa
# ---------------------------------------------------------------------------
def ultima_rodada(auth):
    cfg = auth.ler_config(CONFIG_PADRAO)
    try:
        return json.loads(cfg["integracao_ultima"]) if cfg["integracao_ultima"] else None
    except ValueError:
        return None


def sincronizar(auth, origem="automatico", dormir=time.sleep):
    """Envia e depois puxa. Só um processo por vez (trava no banco).
    Devolve o resumo, ou None se outra sincronização já está rodando."""
    if not auth.integ_pegar_trava(_DONO):
        return None
    inicio = datetime.utcnow()
    resumo = {"origem": origem, "inicio": inicio.isoformat(), "ok": True, "mensagem": ""}
    try:
        resumo.update(enviar_medicos(auth, dormir=dormir))
        cfg = auth.ler_config(CONFIG_PADRAO)
        puxado = puxar_medicos(auth, cfg["integracao_cursor"], dormir=dormir)
        novo_cursor = puxado.pop("cursor")
        resumo["conflitos_novos"] = resumo.get("conflitos_novos", 0) + puxado.pop("conflitos_novos")
        resumo.update(puxado)
        if novo_cursor and novo_cursor != cfg["integracao_cursor"]:
            auth.salvar_config({"integracao_cursor": novo_cursor})
    except ErroIntegracao as e:
        resumo.update(ok=False, mensagem=str(e))
    except Exception as e:  # nunca derruba o i.cannal
        resumo.update(ok=False, mensagem=f"Erro inesperado: {type(e).__name__}.")
    finally:
        resumo["fim"] = datetime.utcnow().isoformat()
        auth.salvar_config({"integracao_ultima": json.dumps(resumo, ensure_ascii=False),
                            "integracao_ultima_em": resumo["fim"]})
        auth.integ_soltar_trava(_DONO)
    return resumo


def esta_na_hora(cfg, agora=None):
    if cfg.get("integracao_ligado") != "1":
        return False
    try:
        intervalo = int(cfg.get("integracao_intervalo_min") or 30)
    except ValueError:
        intervalo = 30
    ultima = cfg.get("integracao_ultima_em") or ""
    if not ultima:
        return True
    try:
        return (agora or datetime.utcnow()) - datetime.fromisoformat(ultima) >= timedelta(minutes=intervalo)
    except ValueError:
        return True


def rodar_se_for_hora(auth):
    if not situacao_config()["configurada"]:
        return None
    if not esta_na_hora(auth.ler_config(CONFIG_PADRAO)):
        return None
    return sincronizar(auth, "automatico")
