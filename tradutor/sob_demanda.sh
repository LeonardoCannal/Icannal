#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Deixa o tradutor (LibreTranslate) ligado SÓ quando alguém usa a página de
# Artigos. Quando fica 15 minutos parado, desliga sozinho e devolve memória e
# processador para o resto do servidor. Rodar de novo é seguro.
#
# Uso:  sudo bash /home/cannal/cannal/tradutor/sob_demanda.sh
# Para voltar ao jeito antigo (sempre ligado):  sudo bash .../sob_demanda.sh --desfazer
# ---------------------------------------------------------------------------
set -euo pipefail
PASTA="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "Rode com sudo: sudo bash $0"; exit 1; }

if [ "${1:-}" = "--desfazer" ]; then
  systemctl disable --now icannal-tradutor.socket icannal-tradutor.service 2>/dev/null || true
  rm -f /etc/systemd/system/icannal-tradutor.socket /etc/systemd/system/icannal-tradutor.service
  sed 's/--port 5001/--port 5000/; /StopWhenUnneeded/d; /ExecStartPost/d' "$PASTA/libretranslate.service" \
    > /etc/systemd/system/libretranslate.service
  printf '\n[Install]\nWantedBy=multi-user.target\n' >> /etc/systemd/system/libretranslate.service
  systemctl daemon-reload
  systemctl enable --now libretranslate
  echo "✅ Tradutor voltou a ficar sempre ligado."
  exit 0
fi

[ -x /home/cannal/libretranslate-venv/bin/libretranslate ] || {
  echo "O tradutor ainda não foi instalado. Siga antes o tradutor/LEIA-ME.md (passos 1 e 2)."; exit 1; }
command -v curl >/dev/null || apt-get install -y curl >/dev/null

PROXYD=""
for p in /usr/lib/systemd/systemd-socket-proxyd /lib/systemd/systemd-socket-proxyd; do
  [ -x "$p" ] && PROXYD="$p" && break
done
[ -n "$PROXYD" ] || { echo "Não achei o systemd-socket-proxyd neste servidor."; exit 1; }

echo "→ Trocando o tradutor para o modo sob demanda…"
systemctl disable --now libretranslate >/dev/null 2>&1 || true
cp "$PASTA/libretranslate.service" /etc/systemd/system/libretranslate.service
cp "$PASTA/icannal-tradutor.socket" /etc/systemd/system/icannal-tradutor.socket
sed "s#PROXYD#$PROXYD#" "$PASTA/icannal-tradutor.service" > /etc/systemd/system/icannal-tradutor.service
systemctl daemon-reload
systemctl enable --now icannal-tradutor.socket

# O i.cannal continua falando com a porta 5000
ENV_FILE=/etc/cannal.env
if grep -q '^TRADUTOR_URL=' "$ENV_FILE"; then
  sed -i 's#^TRADUTOR_URL=.*#TRADUTOR_URL=http://127.0.0.1:5000#' "$ENV_FILE"
else
  echo "TRADUTOR_URL=http://127.0.0.1:5000" >> "$ENV_FILE"
fi
systemctl restart cannal

echo "→ Testando (a primeira tradução liga o tradutor; pode levar até 1 minuto)…"
RESPOSTA=$(timeout 240 curl -s -X POST http://127.0.0.1:5000/translate -H 'Content-Type: application/json' \
  -d '{"q":"Medical cannabis reduces chronic pain","source":"en","target":"pt"}' || true)
if echo "$RESPOSTA" | grep -q translatedText; then
  echo "   $RESPOSTA"
  echo ""
  echo "✅ Pronto. O tradutor liga quando alguém abre os Artigos e desliga sozinho"
  echo "   depois de 15 minutos sem uso. Agora ele está ligado por causa do teste."
else
  echo "⚠️  O teste não respondeu. Veja: sudo journalctl -u libretranslate -n 40"
  exit 1
fi
