"""
Busca de artigos científicos por especialidade — PubMed e SciELO.

- PubMed: API oficial e gratuita do NCBI (E-utilities). Três chamadas por
  busca: esearch (acha os IDs), esummary (título, revista, autores, data) e
  efetch (resumo/abstract).
- SciELO: página de resultados do search.scielo.org (não há API pública de
  busca); cada artigo vem num bloco ".item".

Por padrão a busca é focada em cannabis medicinal + especialidade (é o que
interessa pro time da Cannal); dá pra desligar e ver a especialidade em geral.
"""
import re
import warnings
from datetime import date
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# O resumo do PubMed vem em XML; o leitor de HTML dá conta dele sem instalar nada a mais.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from scraper import _get, CacheTTL

PUBMED_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PUBMED_ARTIGO = "https://pubmed.ncbi.nlm.nih.gov/{}/"
SCIELO_BUSCA = "https://search.scielo.org/"

MAX_POR_FONTE = 15

# Termos de cannabis em inglês (PubMed) e português+inglês (SciELO).
CANNABIS_EN = ["cannabis", "cannabinoid*", "cannabidiol", "\"medical marijuana\"", "tetrahydrocannabinol", "THC"]
CANNABIS_PT = ["cannabis", "canabidiol", "cannabidiol", "canabinoides", "cannabinoids", "\"maconha medicinal\""]

# Temas típicos de cada especialidade. "en" vai pro PubMed; o SciELO recebe
# os dois (lá tem artigos em português, inglês e espanhol).
TERMOS_ESPECIALIDADE = {
    "neurologista": {
        "en": ["neurology", "epilepsy", "seizure*", "\"multiple sclerosis\"", "parkinson*", "migraine", "alzheimer*", "\"neuropathic pain\""],
        "pt": ["neurologia", "epilepsia", "\"esclerose múltipla\"", "parkinson", "enxaqueca", "alzheimer"],
    },
    "psiquiatra": {
        "en": ["psychiatry", "anxiety", "depression", "schizophrenia", "PTSD", "\"posttraumatic stress\"", "insomnia", "\"substance use disorder\""],
        "pt": ["psiquiatria", "ansiedade", "depressão", "esquizofrenia", "\"estresse pós-traumático\"", "\"dependência química\""],
    },
    "neurologista-pediatrico": {
        "en": ["\"pediatric epilepsy\"", "\"childhood epilepsy\"", "Dravet", "\"Lennox-Gastaut\"", "autism", "\"autism spectrum disorder\"", "\"cerebral palsy\""],
        "pt": ["\"epilepsia infantil\"", "\"epilepsia refratária\"", "Dravet", "\"Lennox-Gastaut\"", "autismo", "\"transtorno do espectro autista\"", "\"paralisia cerebral\""],
    },
    "medico-clinico-geral": {
        "en": ["\"internal medicine\"", "\"primary care\"", "\"chronic pain\"", "\"general practice\""],
        "pt": ["\"clínica médica\"", "\"atenção primária\"", "\"dor crônica\""],
    },
    "pediatra": {
        "en": ["pediatrics", "\"pediatric patients\"", "children", "infant*"],
        "pt": ["pediatria", "crianças", "\"pacientes pediátricos\""],
    },
    "medico-de-familia": {
        "en": ["\"family medicine\"", "\"primary care\"", "\"general practitioner*\""],
        "pt": ["\"medicina de família\"", "\"atenção primária\"", "\"saúde da família\""],
    },
    "especialista-em-dor": {
        "en": ["\"chronic pain\"", "\"pain management\"", "\"neuropathic pain\"", "analgesia", "fibromyalgia", "anesthesiology"],
        "pt": ["\"dor crônica\"", "\"manejo da dor\"", "\"dor neuropática\"", "fibromialgia", "anestesiologia"],
    },
    "ortopedista-traumatologista": {
        "en": ["orthopedic*", "osteoarthritis", "\"musculoskeletal pain\"", "\"low back pain\"", "fracture*"],
        "pt": ["ortopedia", "osteoartrite", "artrose", "lombalgia", "\"dor musculoesquelética\""],
    },
    "oncologista": {
        "en": ["oncology", "cancer", "chemotherapy", "\"chemotherapy-induced nausea\"", "\"palliative care\"", "tumor*"],
        "pt": ["oncologia", "câncer", "quimioterapia", "\"cuidados paliativos\""],
    },
    "geriatra": {
        "en": ["geriatric*", "elderly", "\"older adults\"", "dementia", "aging"],
        "pt": ["geriatria", "idosos", "demência", "envelhecimento"],
    },
    "alergista": {
        "en": ["allergy", "immunology", "asthma", "\"atopic dermatitis\"", "allergic"],
        "pt": ["alergia", "imunologia", "asma", "\"dermatite atópica\""],
    },
    "cardiologista": {
        "en": ["cardiology", "cardiovascular", "hypertension", "\"heart failure\"", "arrhythmia*"],
        "pt": ["cardiologia", "cardiovascular", "hipertensão", "\"insuficiência cardíaca\""],
    },
    "dermatologista": {
        "en": ["dermatology", "psoriasis", "\"atopic dermatitis\"", "acne", "skin"],
        "pt": ["dermatologia", "psoríase", "\"dermatite atópica\"", "acne", "pele"],
    },
    "endocrinologista": {
        "en": ["endocrinology", "diabetes", "obesity", "\"metabolic syndrome\"", "thyroid"],
        "pt": ["endocrinologia", "diabetes", "obesidade", "\"síndrome metabólica\"", "tireoide"],
    },
    "endocrinologista-pediatrico": {
        "en": ["\"pediatric endocrinology\"", "\"childhood obesity\"", "\"type 1 diabetes\"", "\"growth disorders\""],
        "pt": ["\"endocrinologia pediátrica\"", "\"obesidade infantil\"", "\"diabetes tipo 1\""],
    },
    "especialista-em-medicina-fisica-e-reabilitacao": {
        "en": ["\"physical medicine\"", "rehabilitation", "spasticity", "\"spinal cord injury\""],
        "pt": ["reabilitação", "fisiatria", "espasticidade", "\"lesão medular\""],
    },
    "especialista-em-medicina-preventiva": {
        "en": ["\"preventive medicine\"", "\"public health\"", "epidemiology"],
        "pt": ["\"medicina preventiva\"", "\"saúde pública\"", "epidemiologia"],
    },
    "gastroenterologista": {
        "en": ["gastroenterology", "\"inflammatory bowel disease\"", "Crohn*", "\"ulcerative colitis\"", "\"irritable bowel\"", "nausea"],
        "pt": ["gastroenterologia", "\"doença inflamatória intestinal\"", "Crohn", "\"colite ulcerativa\"", "\"intestino irritável\""],
    },
    "generalista": {
        "en": ["\"general practice\"", "\"primary care\"", "\"internal medicine\""],
        "pt": ["\"clínica geral\"", "\"atenção primária\""],
    },
    "ginecologista": {
        "en": ["gynecology", "endometriosis", "dysmenorrhea", "menopause", "\"pelvic pain\""],
        "pt": ["ginecologia", "endometriose", "dismenorreia", "menopausa", "\"dor pélvica\""],
    },
    "medico-acupunturista": {
        "en": ["acupuncture", "\"integrative medicine\""],
        "pt": ["acupuntura", "\"medicina integrativa\""],
    },
    "medico-do-esporte": {
        "en": ["\"sports medicine\"", "athlete*", "\"sports injur*\"", "exercise"],
        "pt": ["\"medicina esportiva\"", "atletas", "exercício"],
    },
    "medico-do-sono": {
        "en": ["sleep", "insomnia", "\"sleep apnea\"", "\"sleep disorder*\""],
        "pt": ["sono", "insônia", "\"apneia do sono\"", "\"distúrbios do sono\""],
    },
    "medico-do-trabalho": {
        "en": ["\"occupational health\"", "\"occupational medicine\"", "workplace"],
        "pt": ["\"saúde do trabalhador\"", "\"medicina do trabalho\""],
    },
    "nutricionista": {
        "en": ["nutrition", "diet", "dietary", "\"eating behavior\"", "appetite"],
        "pt": ["nutrição", "dieta", "alimentação", "apetite"],
    },
    "nutrologo": {
        "en": ["\"clinical nutrition\"", "obesity", "appetite", "\"nutritional status\"", "metabolism"],
        "pt": ["nutrologia", "\"nutrição clínica\"", "obesidade", "apetite", "metabolismo"],
    },
    "traumatologista": {
        "en": ["traumatology", "orthopedic*", "fracture*", "\"musculoskeletal pain\""],
        "pt": ["traumatologia", "ortopedia", "fraturas", "\"dor musculoesquelética\""],
    },
    "reumatologista": {
        "en": ["rheumatology", "\"rheumatoid arthritis\"", "fibromyalgia", "lupus", "arthritis"],
        "pt": ["reumatologia", "\"artrite reumatoide\"", "fibromialgia", "lúpus", "artrite"],
    },
}

PERIODOS = {"5": 5, "2": 2, "10": 10, "todos": None}

_CACHE = CacheTTL(6 * 3600, 400)  # artigos mudam pouco: guarda 6 horas


def _ou(termos, sufixo=""):
    return "(" + " OR ".join(f"{t}{sufixo}" for t in termos) + ")"


def montar_consulta_pubmed(slug: str, so_cannabis: bool, anos) -> str:
    partes = [_ou(TERMOS_ESPECIALIDADE[slug]["en"], "[tiab]")]
    if so_cannabis:
        partes.insert(0, _ou(CANNABIS_EN, "[tiab]"))
    if anos:
        partes.append(f'("{date.today().year - anos + 1}"[dp] : "3000"[dp])')
    return " AND ".join(partes)


def montar_consulta_scielo(slug: str, so_cannabis: bool) -> str:
    termos = TERMOS_ESPECIALIDADE[slug]
    tema = _ou(termos["pt"] + termos["en"][:4])
    if so_cannabis:
        return f"{_ou(CANNABIS_PT)} AND {tema}"
    return tema


def link_pubmed_site(consulta: str) -> str:
    return f"https://pubmed.ncbi.nlm.nih.gov/?term={quote_plus(consulta)}&sort=date"


def link_scielo_site(consulta: str, anos) -> str:
    url = f"{SCIELO_BUSCA}?q={quote_plus(consulta)}&lang=pt&sort=YEAR_DESC"
    for ano in _anos_filtro(anos):
        url += f"&filter%5Byear_cluster%5D%5B%5D={ano}"
    return url


def _anos_filtro(anos):
    if not anos:
        return []
    atual = date.today().year
    return list(range(atual - anos + 1, atual + 1))


def _limpar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------

def buscar_pubmed(slug: str, so_cannabis: bool, anos) -> dict:
    consulta = montar_consulta_pubmed(slug, so_cannabis, anos)
    comuns = {"db": "pubmed", "tool": "icannal", "retmode": "json"}

    r = _get(f"{PUBMED_EUTILS}/esearch.fcgi", params={**comuns, "term": consulta, "retmax": MAX_POR_FONTE, "sort": "pub_date"}, timeout=15)
    r.raise_for_status()
    resultado = r.json().get("esearchresult", {})
    ids = resultado.get("idlist", [])
    total = int(resultado.get("count", 0) or 0)
    if not ids:
        return {"artigos": [], "total": total, "link": link_pubmed_site(consulta)}

    r = _get(f"{PUBMED_EUTILS}/esummary.fcgi", params={**comuns, "id": ",".join(ids)}, timeout=15)
    r.raise_for_status()
    resumos = r.json().get("result", {})

    abstracts = {}
    try:
        r = _get(f"{PUBMED_EUTILS}/efetch.fcgi", params={"db": "pubmed", "tool": "icannal", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml"}, timeout=20)
        r.raise_for_status()
        abstracts = _abstracts_pubmed(r.text)
    except Exception:
        pass  # sem resumo, o resto continua útil

    artigos = []
    for pmid in ids:
        item = resumos.get(pmid)
        if not item:
            continue
        ids_artigo = {a.get("idtype"): a.get("value") for a in item.get("articleids", [])}
        autores = [a.get("name") for a in item.get("authors", []) if a.get("name")]
        ano = (re.search(r"\b(19|20)\d{2}\b", item.get("pubdate", "") or "") or [None])[0]
        artigos.append({
            "fonte": "PubMed",
            "titulo": _limpar(re.sub(r"<[^>]+>", "", item.get("title", ""))).rstrip("."),
            "autores": autores[:3] + (["et al."] if len(autores) > 3 else []),
            "revista": item.get("fulljournalname") or item.get("source") or "",
            "ano": ano or "",
            "resumo": abstracts.get(pmid, ""),
            "link": PUBMED_ARTIGO.format(pmid),
            "doi": ids_artigo.get("doi", ""),
            "gratis": bool(ids_artigo.get("pmc")),
            "idioma": "Inglês",
        })
    return {"artigos": artigos, "total": total, "link": link_pubmed_site(consulta)}


def _abstracts_pubmed(xml: str) -> dict:
    soup = BeautifulSoup(xml, "html.parser")
    saida = {}
    for artigo in soup.find_all("pubmedarticle"):
        pmid = artigo.find("pmid")
        partes = artigo.find_all("abstracttext")
        if pmid and partes:
            textos = []
            for p in partes:
                rotulo = p.get("label")
                textos.append((f"{rotulo.capitalize()}: " if rotulo else "") + _limpar(p.get_text(" ")))
            saida[pmid.get_text(strip=True)] = " ".join(textos)
    return saida


# ---------------------------------------------------------------------------
# SciELO
# ---------------------------------------------------------------------------

def buscar_scielo(slug: str, so_cannabis: bool, anos) -> dict:
    consulta = montar_consulta_scielo(slug, so_cannabis)
    params = [("q", consulta), ("lang", "pt"), ("count", MAX_POR_FONTE), ("from", 1),
              ("output", "site"), ("sort", "YEAR_DESC"), ("format", "summary"), ("page", 1)]
    params += [("filter[year_cluster][]", ano) for ano in _anos_filtro(anos)]
    r = _get(SCIELO_BUSCA, params=params, timeout=20)
    r.raise_for_status()
    artigos, total = ler_pagina_scielo(r.text)
    return {"artigos": artigos, "total": total, "link": link_scielo_site(consulta, anos)}


def ler_pagina_scielo(html: str):
    soup = BeautifulSoup(html, "html.parser")
    total = 0
    no_total = soup.select_one("#TotalHits")
    if no_total:
        digitos = re.sub(r"\D", "", no_total.get_text())
        total = int(digitos) if digitos else 0

    artigos = []
    for item in soup.select(".item"):
        no_titulo = item.select_one(".title")
        if not no_titulo:
            continue
        titulo = _limpar(no_titulo.get_text(" "))
        if not titulo:
            continue

        autores = [_limpar(a.get_text(" ")) for a in item.select(".authors a.author")]
        if not autores and item.select_one(".authors"):
            autores = [_limpar(x) for x in item.select_one(".authors").get_text(";").split(";") if _limpar(x)]

        fonte_txt = _limpar(item.select_one(".source").get_text(" ")) if item.select_one(".source") else ""
        ano = (re.search(r"\b(19|20)\d{2}\b", fonte_txt) or [None])[0] or ""
        revista = _limpar(re.split(r",|\d{4}", fonte_txt)[0]) if fonte_txt else ""

        link = ""
        a_titulo = no_titulo.find_parent("a") or no_titulo.find("a")
        if a_titulo and a_titulo.get("href"):
            link = a_titulo["href"]
        if not link:
            for a in item.find_all("a", href=True):
                if "scielo" in a["href"] and ("pid=" in a["href"] or "/a/" in a["href"] or "/article/" in a["href"]):
                    link = a["href"]
                    break
        doi_a = item.select_one(".DOIResults a")
        doi = doi_a["href"] if doi_a and doi_a.get("href") else ""
        if not link:
            link = doi
        if link.startswith("//"):
            link = "https:" + link

        resumo, idioma = "", ""
        id_item = item.get("id")
        if id_item:
            for sufixo, nome_idioma in (("pt", "Português"), ("en", "Inglês"), ("es", "Espanhol")):
                bloco = soup.find("div", id=f"{id_item}_{sufixo}")
                if bloco and _limpar(bloco.get_text(" ")):
                    resumo, idioma = _limpar(bloco.get_text(" ")), nome_idioma
                    break

        artigos.append({
            "fonte": "SciELO",
            "titulo": titulo.rstrip("."),
            "autores": autores[:3] + (["et al."] if len(autores) > 3 else []),
            "revista": revista,
            "ano": ano,
            "resumo": resumo,
            "link": link,
            "doi": doi,
            "gratis": True,  # tudo no SciELO é de acesso aberto
            "idioma": idioma,
        })
    return artigos, total


# ---------------------------------------------------------------------------

def chave_cache(slug, so_cannabis, anos):
    return (slug, bool(so_cannabis), anos)


def cache():
    return _CACHE
