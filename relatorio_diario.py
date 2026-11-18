"""Relatório diário pelo WhatsApp.

Todo dia, no horário escolhido no admin, o i.cannal monta para cada promotor
um resumo do painel médico dele (médicos no painel, já visitados, ainda não
visitados e as próximas visitas agendadas) e pede ao robô do WhatsApp
(whatsapp_robo/, o mesmo tipo de robô do Comunica) para enviar.

Configuração no servidor (/etc/cannal.env, o instalar.sh do robô já coloca):
  WHATSAPP_ROBO_URL     http://127.0.0.1:3011 (o instalar.sh já coloca)
  WHATSAPP_ROBO_CHAVE   criada pelo instalar.sh
"""
import os
import random
import re
import time
from datetime import date, datetime, timedelta

import requests

FUSO = timedelta(hours=3)  # Brasília (sem horário de verão)
DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
CONFIG_PADRAO = {
    "relatorio_ligado": "0",
    "relatorio_hora": "07:00",
    "relatorio_dias": "0123456",      # segunda (0) a domingo (6)
    "relatorio_aviso_robo_dia": "",   # último dia em que avisou o admin que o WhatsApp caiu
}
JANELA_HORAS = 4          # se o servidor estava fora às 7h, ainda envia até as 11h
PAUSA_ENTRE_ENVIOS = (8, 20)  # segundos, sorteado — parece mais com uma pessoa enviando
MAX_VISITAS_NA_MENSAGEM = 10


def agora_br(agora_utc=None):
    return (agora_utc or datetime.utcnow()) - FUSO


# ---------------------------------------------------------------------------
# Mensagem
# ---------------------------------------------------------------------------
def inteiro(valor):
    try:
        return f"{int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def primeiro_nome(nome):
    partes = (nome or "").strip().split()
    return partes[0].capitalize() if partes else ""


def _quando_visita(data_txt, horario, hoje):
    try:
        d = date.fromisoformat(str(data_txt)[:10])
    except ValueError:
        return str(data_txt)
    if d == hoje:
        dia = "Hoje"
    elif d == hoje + timedelta(days=1):
        dia = "Amanhã"
    else:
        dia = f"{DIAS_SEMANA[d.weekday()]} {d.strftime('%d/%m')}"
    return f"{dia} às {horario}" if horario else dia


def montar_mensagem(nome, painel, hoje=None):
    """`painel` vem de auth.resumo_painel_para_relatorio()."""
    hoje = hoje or agora_br().date()
    saudacao = "Bom dia" if agora_br().hour < 12 else "Olá"
    revisitar = painel.get("revisitar", 0)
    linhas = [
        f"*{saudacao}, {primeiro_nome(nome)}!* ☀️",
        f"Seu painel médico no i.cannal hoje ({hoje.strftime('%d/%m')}):",
        "",
        f"🩺 *Médicos no painel:* {inteiro(painel.get('total'))}",
        f"✅ *Já visitados:* {inteiro(painel.get('visitados'))}"
        + (f" ({inteiro(revisitar)} para revisitar)" if revisitar else ""),
        f"⏳ *Ainda não visitados:* {inteiro(painel.get('nao_visitados'))}",
        "",
        "📅 *Próximas visitas*",
    ]
    proximas = painel.get("proximas") or []
    for v in proximas[:MAX_VISITAS_NA_MENSAGEM]:
        extra = " · ".join(x for x in (v.get("especialidade"), v.get("cidade")) if x)
        linhas.append(f"• {_quando_visita(v.get('data'), v.get('horario'), hoje)} — {v.get('nome')}"
                      + (f" ({extra})" if extra else ""))
    if len(proximas) > MAX_VISITAS_NA_MENSAGEM:
        linhas.append(f"_e mais {len(proximas) - MAX_VISITAS_NA_MENSAGEM} visita(s) agendada(s)_")
    if not proximas:
        linhas.append("• Nenhuma visita agendada.")
    linhas += ["", "👉 Acesse seu painel: https://icannal.com.br", "", "Boas visitas! — _i.cannal_"]
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Robô do WhatsApp
# ---------------------------------------------------------------------------
def robo_configurado():
    return bool(os.environ.get("WHATSAPP_ROBO_CHAVE"))


def _robo(metodo, caminho, corpo=None, timeout=15):
    if not robo_configurado():
        return None, "O robô do WhatsApp ainda não foi instalado no servidor (rode whatsapp_robo/instalar.sh)."
    url = (os.environ.get("WHATSAPP_ROBO_URL") or "http://127.0.0.1:3011").rstrip("/") + caminho
    try:
        r = requests.request(metodo, url, json=corpo, timeout=timeout,
                             headers={"X-Robo-Chave": os.environ["WHATSAPP_ROBO_CHAVE"]})
        return r.json(), None
    except requests.RequestException:
        return None, "O robô do WhatsApp não está rodando no servidor (sudo systemctl status icannal-whatsapp)."
    except ValueError:
        return None, "O robô do WhatsApp respondeu algo inesperado."


def robo_status():
    dados, erro = _robo("GET", "/status", timeout=5)
    if erro:
        return {"estado": "indisponivel", "erro": erro}
    return dados


def robo_conectar():
    return _robo("POST", "/conectar", timeout=10)


def robo_desconectar():
    return _robo("POST", "/desconectar", timeout=30)


def robo_dormir():
    """Fecha o navegador do robô (fica em espera, sem perder a conexão)."""
    return _robo("POST", "/dormir", timeout=30)


def acordar_robo(espera_max=150):
    """Garante o WhatsApp pronto para enviar: se está em espera, abre o
    navegador e espera conectar. Devolve (ok, motivo)."""
    st = robo_status()
    if st.get("estado") == "conectado":
        return True, ""
    if st.get("estado") == "indisponivel":
        return False, st.get("erro") or "O robô do WhatsApp não está rodando."
    if st.get("estado") == "desligado" or st.get("estado") == "qr":
        return False, "Nenhum WhatsApp conectado. Entre em Admin → Relatório diário e leia o QR code."
    if st.get("estado") == "dormindo":
        _, erro = robo_conectar()
        if erro:
            return False, erro
    limite = time.monotonic() + espera_max
    while time.monotonic() < limite:
        time.sleep(1.5)
        st = robo_status()
        estado = st.get("estado")
        if estado == "conectado":
            return True, ""
        if estado == "qr":
            robo_dormir()
            return False, ("O WhatsApp pediu para ler o QR code de novo (a conexão expirou). "
                           "Entre em Admin → Relatório diário e conecte outra vez.")
        if estado in ("erro", "desligado", "indisponivel"):
            return False, st.get("erro") or "O WhatsApp não conseguiu abrir."
    return False, "O WhatsApp demorou demais para abrir. Tente de novo em alguns minutos."


def robo_enviar(numero, texto):
    dados, erro = _robo("POST", "/enviar", {"numero": numero, "texto": texto}, timeout=90)
    if erro:
        return False, erro
    return bool(dados.get("ok")), dados.get("erro") or ""


def numero_valido(numero):
    d = re.sub(r"\D", "", numero or "")
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    return len(d) in (10, 11)


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------
def _robo_caiu(erro):
    erro = (erro or "").lower()
    return "não está conectado" in erro or "não está rodando" in erro or "não foi instalado" in erro


def enviar_para_promotor(auth, promotor, tipo="automatico", dia=None, chave=None):
    """Monta o resumo do painel e envia para 1 promotor. Devolve (status, detalhe)."""
    dia = dia or agora_br().date().isoformat()
    numero = (promotor.get("whatsapp") or "").strip()
    chave = chave or f"{promotor['id']}:{dia}:{tipo}:{datetime.utcnow().timestamp()}"
    envio_id = auth.reservar_envio(chave, promotor, numero, dia, tipo)
    if envio_id is None:
        return "repetido", "Esse relatório já foi enviado hoje."
    if not numero_valido(numero):
        auth.finalizar_envio(envio_id, "erro", "Promotor sem WhatsApp válido.")
        return "erro", "Promotor sem WhatsApp válido."
    hoje = agora_br().date()
    texto = montar_mensagem(promotor.get("nome"), auth.resumo_painel_para_relatorio(promotor["id"], hoje.isoformat()), hoje)
    ok, erro = robo_enviar(numero, texto)
    if not ok and tipo == "automatico" and _robo_caiu(erro):
        # o WhatsApp caiu no meio: desfaz a reserva pra tentar de novo depois
        auth.apagar_envio(envio_id)
        return "adiado", erro
    if not ok:
        auth.finalizar_envio(envio_id, "erro", erro, texto)
        return "erro", erro
    auth.finalizar_envio(envio_id, "enviado", "", texto)
    return "enviado", ""


def hora_config(texto):
    try:
        h, m = (int(x) for x in str(texto).split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    return 7, 0


def esta_na_hora(config, agora_utc=None):
    """True se hoje é dia de envio e já passou do horário (até JANELA_HORAS depois)."""
    if config.get("relatorio_ligado") != "1":
        return False
    br = agora_br(agora_utc)
    if str(br.weekday()) not in (config.get("relatorio_dias") or ""):
        return False
    h, m = hora_config(config.get("relatorio_hora"))
    inicio = br.replace(hour=h, minute=m, second=0, microsecond=0)
    return inicio <= br < inicio + timedelta(hours=JANELA_HORAS)


def rodar_envio_automatico(auth, avisar_admin=None, agora_utc=None, dormir=time.sleep):
    """Uma rodada do envio automático. Pode ser chamada várias vezes no dia
    (por vários processos): cada promotor só recebe 1 vez por dia."""
    config = auth.ler_config(CONFIG_PADRAO)
    if not esta_na_hora(config, agora_utc):
        return {"rodou": False, "motivo": "fora do horário"}
    auth.destravar_envios_presos()
    dia = agora_br(agora_utc).date().isoformat()
    pendentes = [p for p in auth.listar_promotores_relatorio() if p["ativo"]]
    ja_feitos = {e["usuario_id"] for e in auth.listar_envios_relatorio(limite=2000, dia=dia) if e["tipo"] == "automatico"}
    pendentes = [p for p in pendentes if p["id"] not in ja_feitos]
    if not pendentes:
        return {"rodou": False, "motivo": "nada pendente"}

    pronto, motivo = acordar_robo()  # tira o robô da espera (abre o navegador)
    if not pronto:
        # avisa o admin 1 vez por dia e tenta de novo na próxima volta
        if avisar_admin and config.get("relatorio_aviso_robo_dia") != dia:
            auth.salvar_config({"relatorio_aviso_robo_dia": dia})
            avisar_admin(f"⚠️ i.cannal — o relatório diário ainda não saiu: {motivo}")
        return {"rodou": False, "motivo": "WhatsApp desconectado", "detalhe": motivo}

    resultado = {"rodou": True, "enviados": 0, "erros": 0, "repetidos": 0}
    for i, promotor in enumerate(pendentes):
        if i:
            dormir(random.uniform(*PAUSA_ENTRE_ENVIOS))
        status, _ = enviar_para_promotor(auth, promotor, "automatico", dia, chave=f"{promotor['id']}:{dia}")
        if status == "adiado":
            resultado["adiado"] = True
            break  # o WhatsApp caiu: o resto fica pra próxima volta
        chave = {"enviado": "enviados", "erro": "erros"}.get(status, "repetidos")
        resultado[chave] += 1
    # O robô volta sozinho para a espera alguns minutos depois do último envio
    # (não fechamos aqui: o outro processo do servidor pode estar enviando).
    return resultado
