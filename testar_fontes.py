"""Teste rápido das fontes novas, rodado NO SERVIDOR (só lê os sites; não mexe
no banco nem no i.cannal que está no ar).

Uso:
  cd ~/cannal && source venv/bin/activate
  python3 testar_fontes.py catalogomed cardiologista Sorocaba SP
  python3 testar_fontes.py drsac cardiologista Sorocaba SP
"""
import ast
import pathlib
import sys
import time

import scraper


def _especialidades():
    fonte = pathlib.Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    ini = fonte.index("ESPECIALIDADES = {")
    fim = fonte.index("\n}\n", ini) + 2
    return ast.literal_eval(fonte[ini + len("ESPECIALIDADES = "):fim])


def main(fonte, slug, cidade, uf):
    especialidades = _especialidades()
    if slug not in especialidades:
        print("Especialidade desconhecida. Use um destes códigos:", ", ".join(especialidades))
        return
    info = especialidades[slug]
    alvo = info.get(fonte)
    if not alvo:
        print(f"{info['nome']}: sem correspondente em {fonte} (não é buscado lá).")
        return
    inicio = time.time()
    try:
        if fonte == "catalogomed":
            medicos = scraper.buscar_medicos_catalogomed(alvo, info["nome"], cidade, uf, slug in scraper.ESPECIALIDADES_COM_CRN)
        else:
            medicos = scraper.buscar_medicos_drsac(alvo, info["nome"], cidade, uf)
    except Exception as e:
        print(f"ERRO ao consultar {fonte}: {e}")
        return
    print(f"{fonte} · {info['nome']} em {cidade}/{uf}: {len(medicos)} médico(s) em {time.time() - inicio:.1f}s\n")
    for m in medicos[:10]:
        print(f"  - {m['nome']} | {m['crm']} | {m['telefone']} | {m['endereco']}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 4 and a[0] in ("catalogomed", "drsac"):
        main(*a)
    else:
        print(__doc__)
