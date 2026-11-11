#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Instala (ou atualiza) o robô do WhatsApp do i.cannal como um serviço do
# servidor, que liga sozinho junto com o computador. Rodar de novo é seguro:
# só atualiza o que precisa.
#
# Uso:  sudo bash /home/cannal/cannal/whatsapp_robo/instalar.sh
# ---------------------------------------------------------------------------
set -euo pipefail

PROJ="/home/cannal/cannal"
ROBO="$PROJ/whatsapp_robo"
USUARIO="cannal"
ENV_FILE="/etc/cannal.env"
SERVICO="/etc/systemd/system/icannal-whatsapp.service"

[ "$(id -u)" -eq 0 ] || { echo "Rode com sudo: sudo bash $0"; exit 1; }
[ -f "$ROBO/robo.js" ] || { echo "Não achei $ROBO/robo.js — suba a versão nova do i.cannal primeiro."; exit 1; }
[ -f "$ENV_FILE" ] || { echo "Não achei $ENV_FILE"; exit 1; }

# 1) Node.js 18 ou mais novo (o Comunica já deve ter instalado)
if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 18 ]; then
  echo "→ Instalando o Node.js 20…"
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
echo "Node: $(node -v)"

# 2) Bibliotecas que o navegador invisível precisa (se já tiver, não faz nada)
echo "→ Conferindo as bibliotecas do navegador…"
apt-get install -y --no-install-recommends \
  ca-certificates fonts-liberation libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libcups2t64 \
  libdrm2 libgbm1 libgtk-3-0t64 libnss3 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 \
  libxrandr2 libpango-1.0-0 libcairo2 >/dev/null 2>&1 \
  || apt-get install -y --no-install-recommends \
  ca-certificates fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 \
  libdrm2 libgbm1 libgtk-3-0 libnss3 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 \
  libxrandr2 libpango-1.0-0 libcairo2 >/dev/null

# 3) Chave secreta entre o i.cannal e o robô (criada uma vez só; não aparece na tela)
if ! grep -q '^WHATSAPP_ROBO_CHAVE=' "$ENV_FILE"; then
  echo "WHATSAPP_ROBO_CHAVE=$(openssl rand -hex 24)" >> "$ENV_FILE"
  echo "→ Chave do robô criada em $ENV_FILE"
fi
grep -q '^WHATSAPP_ROBO_URL=' "$ENV_FILE" || echo "WHATSAPP_ROBO_URL=http://127.0.0.1:3011" >> "$ENV_FILE"

# 4) Dependências do robô (baixa também o navegador dele, ~170 MB, só na 1ª vez)
echo "→ Instalando o robô (pode levar alguns minutos na primeira vez)…"
chown -R "$USUARIO":"$USUARIO" "$ROBO"
sudo -u "$USUARIO" -H bash -c "cd '$ROBO' && npm install --omit=dev --no-audit --no-fund"

# 5) Serviço do sistema
cat > "$SERVICO" <<SERV
[Unit]
Description=i.cannal — robô do WhatsApp (relatório diário)
After=network-online.target
Wants=network-online.target

[Service]
User=$USUARIO
WorkingDirectory=$ROBO
EnvironmentFile=$ENV_FILE
ExecStart=$(command -v node) $ROBO/robo.js
Restart=always
RestartSec=10
MemoryMax=800M

[Install]
WantedBy=multi-user.target
SERV

systemctl daemon-reload
systemctl enable icannal-whatsapp >/dev/null 2>&1
systemctl restart icannal-whatsapp
systemctl restart cannal   # o i.cannal lê a chave nova

sleep 3
if systemctl is-active --quiet icannal-whatsapp; then
  echo ""
  echo "✅ Robô do WhatsApp instalado e rodando."
  echo "   Agora entre no i.cannal → Admin → Relatório diário → Conectar WhatsApp e leia o QR code."
else
  echo "⚠️  O robô não subiu. Veja o motivo com: sudo journalctl -u icannal-whatsapp -n 50"
  exit 1
fi
