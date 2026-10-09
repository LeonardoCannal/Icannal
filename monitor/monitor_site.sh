#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Monitor do i.cannal — roda a cada 5 minutos (icannal-monitor.timer).
#
# Avisa no Telegram/WhatsApp do admin quando:
#   - o site ou o banco param de responder (e avisa de novo quando voltam);
#   - o disco do servidor passa de 90% de uso;
#   - o certificado HTTPS (cadeado) está a menos de 10 dias de vencer.
#
# Cada problema gera 1 aviso quando começa e, se continuar, um lembrete a
# cada 6 horas — nada de enxurrada. Não altera nada no site nem no banco.
# ---------------------------------------------------------------------------
set -uo pipefail

ENV_FILE="${ENV_FILE:-/etc/cannal.env}"
ESTADO="${ESTADO_MONITOR:-/var/lib/icannal-monitor}"
URL_SITE="${URL_MONITOR:-https://icannal.com.br/saude}"
DOMINIO="${DOMINIO_MONITOR:-icannal.com.br}"
LEMBRETE_SEG=$((6 * 3600))

mkdir -p "$ESTADO"

ler_var() {
  local valor
  valor="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2-)" || true
  valor="${valor%\"}"; valor="${valor#\"}"
  valor="${valor%\'}"; valor="${valor#\'}"
  printf '%s' "$valor"
}

enviar() {
  local texto="$1" tg tel apikey
  tg="$(ler_var TELEGRAM_USUARIO)"
  tel="$(ler_var WHATSAPP_TELEFONE)"
  apikey="$(ler_var WHATSAPP_APIKEY)"
  echo "AVISO: $texto"
  if [ -n "$tg" ]; then
    case "$tg" in @*) ;; *) tg="@$tg" ;; esac
    curl -s --max-time 15 -G "https://api.callmebot.com/text.php" \
      --data-urlencode "user=$tg" --data-urlencode "text=$texto" >/dev/null || true
  fi
  if [ -n "$tel" ] && [ -n "$apikey" ]; then
    curl -s --max-time 15 -G "https://api.callmebot.com/whatsapp.php" \
      --data-urlencode "phone=$tel" --data-urlencode "text=$texto" \
      --data-urlencode "apikey=$apikey" >/dev/null || true
  fi
}

# problema NOME "mensagem": avisa se é novo ou se o último aviso foi há 6h+.
problema() {
  local nome="$1" texto="$2" arq="$ESTADO/$1" agora ultimo
  agora="$(date +%s)"
  ultimo="$(cat "$arq" 2>/dev/null || echo 0)"
  if [ $((agora - ultimo)) -ge "$LEMBRETE_SEG" ]; then
    [ "$ultimo" != 0 ] && texto="$texto (continua)"
    enviar "$texto"
    echo "$agora" > "$arq"
  fi
}

# resolvido NOME "mensagem": se havia problema aberto, avisa que normalizou.
resolvido() {
  local arq="$ESTADO/$1"
  if [ -f "$arq" ]; then
    rm -f "$arq"
    [ -n "${2:-}" ] && enviar "$2"
  fi
}

# 1. Site + banco. Tenta 2 vezes (com 20s de intervalo) antes de avisar,
#    pra não alarmar por uma piscada.
codigo=""
for tentativa in 1 2; do
  codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL_SITE" || true)"
  [ "$codigo" = "200" ] && break
  [ "$tentativa" = 1 ] && sleep 20
done
if [ "$codigo" = "200" ]; then
  resolvido site "✅ i.cannal — o site voltou a funcionar normalmente."
  echo "Site OK"
else
  case "$codigo" in
    503) motivo="o site está no ar, mas o BANCO DE DADOS (Supabase) não responde" ;;
    000|"") motivo="o site não responde (servidor, nginx ou o sistema parado)" ;;
    502|504) motivo="o nginx está no ar, mas o sistema (gunicorn) não responde — tente: sudo systemctl restart cannal" ;;
    *) motivo="o site respondeu com erro $codigo" ;;
  esac
  problema site "🚨 i.cannal FORA DO AR — $motivo."
fi

# 2. Disco
uso="$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')"
if [ -n "$uso" ] && [ "$uso" -ge 90 ]; then
  problema disco "💾 i.cannal — o disco do servidor está com ${uso}% de uso. Se encher, o site e o backup param."
else
  resolvido disco ""
fi

# 3. Certificado HTTPS (o certbot renova sozinho; isso avisa se a renovação falhar)
fim="$(echo | timeout 20 openssl s_client -servername "$DOMINIO" -connect "$DOMINIO:443" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
if [ -n "$fim" ]; then
  dias=$(( ($(date -d "$fim" +%s) - $(date +%s)) / 86400 ))
  if [ "$dias" -lt 10 ]; then
    problema certificado "🔐 i.cannal — o certificado HTTPS (cadeado) vence em $dias dias e não foi renovado. Rode: sudo certbot renew"
  else
    resolvido certificado ""
  fi
fi
exit 0
