#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Backup diário do banco do i.cannal (Supabase/Postgres).
#
# - Lê a DATABASE_URL de /etc/cannal.env (o mesmo arquivo que o sistema usa).
# - Salva uma cópia completa e compactada em /var/backups/icannal.
# - Confere se a cópia ficou inteira antes de considerar que deu certo.
# - Mantém os últimos 14 dias e apaga os mais antigos sozinho.
# - Se falhar, avisa no Telegram (TELEGRAM_USUARIO) e/ou WhatsApp (se
#   WHATSAPP_TELEFONE/APIKEY) — o erro também aparece em: journalctl -u icannal-backup
#
# Só LÊ o banco. Nunca altera nem apaga nada no Supabase.
# ---------------------------------------------------------------------------
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/cannal.env}"
PASTA="${PASTA_BACKUP:-/var/backups/icannal}"
DIAS="${DIAS_BACKUP:-14}"

umask 077  # arquivos de backup só legíveis pelo root

# Lê uma variável do arquivo de configuração sem "executar" o arquivo
# (senhas com caracteres especiais como & ou $ não quebram nada).
ler_var() {
  local valor
  valor="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2-)" || true
  valor="${valor%\"}"; valor="${valor#\"}"
  valor="${valor%\'}"; valor="${valor#\'}"
  printf '%s' "$valor"
}

avisar_falha() {
  local motivo="$1"
  local tel apikey tg texto
  tel="$(ler_var WHATSAPP_TELEFONE)"
  apikey="$(ler_var WHATSAPP_APIKEY)"
  tg="$(ler_var TELEGRAM_USUARIO)"
  texto="⚠️ i.cannal — o backup diário do banco FALHOU ($(date '+%d/%m %H:%M')). Motivo: $motivo"
  echo "BACKUP FALHOU: $motivo" >&2
  if [ -n "$tg" ]; then
    case "$tg" in @*) ;; *) tg="@$tg" ;; esac
    curl -s --max-time 15 -G "https://api.callmebot.com/text.php" \
      --data-urlencode "user=$tg" \
      --data-urlencode "text=$texto" >/dev/null || true
  fi
  if [ -n "$tel" ] && [ -n "$apikey" ]; then
    curl -s --max-time 15 -G "https://api.callmebot.com/whatsapp.php" \
      --data-urlencode "phone=$tel" \
      --data-urlencode "text=$texto" \
      --data-urlencode "apikey=$apikey" >/dev/null || true
  fi
}

TEMP=""
falhou() {
  [ -n "$TEMP" ] && rm -f "$TEMP"
  avisar_falha "$1"
  exit 1
}

[ -r "$ENV_FILE" ] || falhou "não consegui ler $ENV_FILE"
command -v pg_dump >/dev/null || falhou "pg_dump não está instalado (veja o passo 1 do backup/LEIA-ME.md)"

URL="$(ler_var BACKUP_DATABASE_URL)"
[ -n "$URL" ] || URL="$(ler_var DATABASE_URL)"
[ -n "$URL" ] || falhou "DATABASE_URL não encontrada em $ENV_FILE"

# No Supabase, a porta 6543 (modo "transaction") não serve pra backup; a 5432
# do mesmo endereço (modo "session") serve. Troca sozinho.
URL="$(printf '%s' "$URL" | sed -E 's#(pooler\.supabase\.com):6543#\1:5432#')"

mkdir -p "$PASTA" || falhou "não consegui criar a pasta $PASTA"

NOME="icannal-$(date -u '+%Y-%m-%d_%H%M')UTC.sql.gz"
TEMP="$PASTA/.$NOME.parcial"
FINAL="$PASTA/$NOME"

echo "Iniciando backup em $(date '+%Y-%m-%d %H:%M:%S %Z')..."

# --schema=public: só as tabelas do i.cannal (não as internas do Supabase).
# --no-owner/--no-privileges: dá pra restaurar em qualquer banco/projeto.
# O sed ajusta 2 linhas do começo pra restauração não parar num banco novo
# (onde o "schema public" já existe e não pertence a você).
if ! pg_dump "$URL" --schema=public --no-owner --no-privileges --format=plain 2>"$TEMP.erro" \
     | sed -e 's/^CREATE SCHEMA public;$/CREATE SCHEMA IF NOT EXISTS public;/' \
           -e '/^COMMENT ON SCHEMA public IS /d' \
     | gzip -6 > "$TEMP"; then
  ERRO="$(tail -n 3 "$TEMP.erro" 2>/dev/null | tr '\n' ' ' | sed -E 's#postgres(ql)?://[^ ]*#[endereço do banco]#g')"
  rm -f "$TEMP.erro"
  falhou "pg_dump deu erro: ${ERRO:-sem detalhes}"
fi
rm -f "$TEMP.erro"

# Confere se a cópia está inteira: o pg_dump escreve uma marca no final
# quando termina direito. Sem ela, a cópia está cortada e não serve.
if ! gzip -t "$TEMP" 2>/dev/null; then
  falhou "arquivo compactado ficou corrompido"
fi
if ! gzip -dc "$TEMP" | tail -n 20 | grep -q "PostgreSQL database dump complete"; then
  falhou "a cópia ficou incompleta (o pg_dump não terminou)"
fi
TABELAS="$(gzip -dc "$TEMP" | grep -c '^CREATE TABLE ' || true)"
if [ "${TABELAS:-0}" -lt 3 ]; then
  falhou "a cópia veio sem as tabelas do sistema ($TABELAS tabelas)"
fi

mv "$TEMP" "$FINAL"
TEMP=""

# Apaga cópias com mais de $DIAS dias — mas nunca deixa ficar menos de 3.
TOTAL="$(find "$PASTA" -maxdepth 1 -name 'icannal-*.sql.gz' | wc -l)"
if [ "$TOTAL" -gt 3 ]; then
  find "$PASTA" -maxdepth 1 -name 'icannal-*.sql.gz' -mtime +"$DIAS" -delete
fi

echo "Backup OK: $FINAL ($(du -h "$FINAL" | cut -f1), $TABELAS tabelas). Cópias guardadas: $(find "$PASTA" -maxdepth 1 -name 'icannal-*.sql.gz' | wc -l)"
