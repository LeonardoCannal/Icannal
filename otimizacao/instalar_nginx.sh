#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Faz o nginx entregar direto os arquivos fixos do i.cannal (CSS, fontes,
# ícones), sem passar pelo Python. Seguro:
#   - guarda uma cópia da configuração atual antes de mudar;
#   - testa a configuração nova (nginx -t) e, se der qualquer erro, volta
#     sozinho a cópia antiga — o site nunca fica fora do ar;
#   - rodar de novo não duplica nada.
# Uso:  sudo bash /home/cannal/cannal/otimizacao/instalar_nginx.sh
# ---------------------------------------------------------------------------
set -euo pipefail

PROJ="/home/cannal/cannal"
ESTATICOS="$PROJ/static"
MAPA_DESTINO="/etc/nginx/conf.d/icannal-cache.conf"

[ "$(id -u)" -eq 0 ] || { echo "Rode com sudo: sudo bash $0"; exit 1; }
[ -d "$ESTATICOS" ] || { echo "Não achei $ESTATICOS"; exit 1; }

CONF=$(grep -l "proxy_pass http://127.0.0.1:8000" /etc/nginx/sites-enabled/* 2>/dev/null | head -1 || true)
[ -n "$CONF" ] || { echo "Não achei a configuração do site no nginx (proxy_pass 127.0.0.1:8000)."; exit 1; }
CONF=$(readlink -f "$CONF")
echo "Configuração do site: $CONF"

if grep -q "icannal-estaticos" "$CONF"; then
  echo "Já estava instalado. Nada a fazer."
  exit 0
fi

# 1) o nginx (usuário www-data) precisa conseguir LER a pasta static
usermod -aG cannal www-data
chmod g+x /home/cannal "$PROJ"
chmod -R g+rX "$ESTATICOS"
if ! sudo -u www-data test -r "$ESTATICOS/ui-novo.css"; then
  echo "Aviso: o nginx ainda não consegue ler $ESTATICOS (vai valer depois do restart)."
fi

# 2) cópia de segurança + mudanças
BACKUP="$CONF.antes-estaticos-$(date +%Y%m%d-%H%M%S)"
cp "$CONF" "$BACKUP"
cp "$PROJ/otimizacao/icannal-cache.conf" "$MAPA_DESTINO"

# Compactação de CSS/JS/SVG (só acrescenta se o nginx ainda não tiver isso)
if ! grep -Rqs "^\s*gzip_types" /etc/nginx/nginx.conf /etc/nginx/conf.d/; then
  printf '\ngzip_types text/css application/javascript image/svg+xml application/json application/manifest+json;\ngzip_vary on;\n' >> "$MAPA_DESTINO"
fi

python3 - "$CONF" "$ESTATICOS" <<'PY'
import re, sys
caminho, estaticos = sys.argv[1], sys.argv[2].rstrip("/") + "/"
texto = open(caminho).read()
def bloco(indent):
    linhas = [
        "# icannal-estaticos: CSS, fontes e ícones entregues direto pelo nginx",
        "location /static/ {",
        f"    alias {estaticos};",
        "    access_log off;",
        "    add_header Cache-Control $icannal_cache_estaticos always;",
        "    add_header X-Content-Type-Options nosniff always;",
        "}",
        "",
    ]
    return "".join(indent + l + "\n" if l else "\n" for l in linhas)
novo, n = re.subn(r"(?m)^([ \t]*)location / \{", lambda m: bloco(m.group(1)) + m.group(0), texto)
if n == 0:
    sys.exit("Não achei 'location / {' na configuração.")
open(caminho, "w").write(novo)
print(f"Bloco acrescentado em {n} lugar(es).")
PY

# 3) testa; se falhar, desfaz tudo
if nginx -t; then
  systemctl restart nginx
  echo
  echo "Pronto! O nginx agora entrega os arquivos fixos direto."
  echo "Cópia da configuração antiga: $BACKUP"
else
  echo "A configuração nova deu erro — desfazendo, o site continua como antes."
  cp "$BACKUP" "$CONF"
  rm -f "$MAPA_DESTINO"
  nginx -t && systemctl reload nginx
  exit 1
fi
