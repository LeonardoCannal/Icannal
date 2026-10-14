"""
Tradução dos artigos científicos (inglês → português).

Quem traduz, em ordem de preferência:
  1. LibreTranslate rodando no PRÓPRIO servidor (TRADUTOR_URL no
     /etc/cannal.env, ex.: http://127.0.0.1:5000). Grátis, sem conta, sem
     cota, e os textos não saem do servidor. Instalação: tradutor/LEIA-ME.md
  2. Google Cloud Translation (GOOGLE_TRANSLATE_KEY) — opcional, só se um dia
     alguém configurar a chave.
Sem nenhum dos dois, nada é traduzido e a página de artigos funciona igual.

Cada texto é traduzido UMA vez: o resultado fica guardado no banco (tabela
"traducoes") e é reaproveitado por toda a equipe.
"""
import hashlib
import html
import os

import requests

from auth import buscar_traducoes, guardar_traducoes

URL_GOOGLE = "https://translation.googleapis.com/language/translate/v2"
MAX_POR_PEDIDO = 20            # poucos por vez: o tradutor local responde mais rápido assim
MAX_CARACTERES_POR_PEDIDO = 12000


class ErroTraducao(Exception):
    pass


def _url_libre() -> str:
    return os.environ.get("TRADUTOR_URL", "").strip().rstrip("/")


def ativa() -> bool:
    return bool(_url_libre() or os.environ.get("GOOGLE_TRANSLATE_KEY", "").strip())


def _chamar_libretranslate(textos: list) -> list:
    resposta = requests.post(
        f"{_url_libre()}/translate",
        json={"q": textos, "source": "en", "target": "pt", "format": "text"},
        timeout=90,  # roda no processador do servidor: resumos longos levam alguns segundos
    )
    if resposta.status_code != 200:
        try:
            motivo = resposta.json().get("error", "")
        except ValueError:
            motivo = ""
        raise ErroTraducao(f"LibreTranslate respondeu {resposta.status_code}: {str(motivo)[:160]}")
    traduzidos = resposta.json().get("translatedText")
    if isinstance(traduzidos, str):
        traduzidos = [traduzidos]
    if not isinstance(traduzidos, list) or len(traduzidos) != len(textos):
        raise ErroTraducao("LibreTranslate devolveu uma resposta inesperada")
    return traduzidos


def _chamar(textos: list) -> list:
    return _chamar_libretranslate(textos) if _url_libre() else _chamar_google(textos)


def _chave(texto: str) -> str:
    return "en>pt:" + hashlib.sha256(texto.strip().encode("utf-8")).hexdigest()


def _chamar_google(textos: list) -> list:
    resposta = requests.post(
        URL_GOOGLE,
        params={"key": os.environ["GOOGLE_TRANSLATE_KEY"].strip()},
        json={"q": textos, "target": "pt", "format": "text"},
        timeout=20,
    )
    if resposta.status_code != 200:
        try:
            motivo = resposta.json().get("error", {}).get("message", "")
        except ValueError:
            motivo = ""
        raise ErroTraducao(f"Google respondeu {resposta.status_code}: {motivo[:160]}")
    traducoes = resposta.json()["data"]["translations"]
    return [html.unescape(t["translatedText"]) for t in traducoes]


def traduzir(textos: list) -> list:
    """Traduz uma lista de textos (inglês → português), na mesma
    ordem. Usa o que já está guardado e só manda pro tradutor o que é novo."""
    textos = [(t or "").strip() for t in textos]
    if not ativa():
        return textos

    chaves = [_chave(t) for t in textos]
    guardadas = buscar_traducoes([c for c, t in zip(chaves, textos) if t])

    faltando = []  # (chave, texto), sem repetir
    vistos = set()
    for c, t in zip(chaves, textos):
        if t and c not in guardadas and c not in vistos:
            faltando.append((c, t))
            vistos.add(c)

    novas = {}
    lote, tamanho = [], 0
    for item in faltando + [None]:
        if item is not None and len(lote) < MAX_POR_PEDIDO and tamanho + len(item[1]) <= MAX_CARACTERES_POR_PEDIDO:
            lote.append(item)
            tamanho += len(item[1])
            continue
        if lote:
            for (c, _), traduzido in zip(lote, _chamar([t for _, t in lote])):
                novas[c] = traduzido
        lote, tamanho = ([item], len(item[1])) if item is not None else ([], 0)

    if novas:
        guardar_traducoes(novas)
        guardadas.update(novas)
    return [guardadas.get(c, t) if t else t for c, t in zip(chaves, textos)]
