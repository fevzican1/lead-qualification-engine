#!/usr/bin/env bash
# Nirvana Whitelabel MANIFEST PREFLIGHT — Oracle canliya alma kapisi (tmux karsiligi).
# Kullanim (Oracle VM, /opt/devsolve icinde):
#   sudo bash oracle/manifest_preflight.sh
#   PAYONEER_LINK=https://link.payoneer.com/... sudo -E bash oracle/manifest_preflight.sh
#
# Mantik (kullanici sozlesmesi):
#   manifest --strict TRUE  -> git push + systemd/tmux tazeleme + kesintisiz yayin
#   manifest --strict FALSE -> deploy ENGELLENIR, eski canli yapi korunur, hata loglanir
set -u
APP_DIR="${APP_DIR:-/opt/devsolve}"
PY="${APP_DIR}/.venv/bin/python"
LOG_DIR="${APP_DIR}/logs"
STAMP="$(date -u +%FT%TZ)"
mkdir -p "$LOG_DIR"
cd "$APP_DIR" || exit 1

echo "=== [MANIFEST] tmux karsiligi preflight $STAMP ==="
echo "--- env kilitleri ---"
for k in WEBCHAT_PUBLIC_URL TELEGRAM_BOT_TOKEN TELEGRAM_BOT_USERNAME PAYONEER_PAYMENT_URL TELEGRAM_OWNER_CHAT_ID SENDER_NAME SENDER_EMAIL SENDER_COMPANY; do
  v="$(grep -E "^${k}=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  if [ -z "${v:-}" ]; then echo "FALSE  $k bos"; else echo "TRUE   $k dolu"; fi
done | tee -a "$LOG_DIR/manifest_preflight.log"

echo "--- disk/kaynak ---"
df -h / | tail -n1 | tee -a "$LOG_DIR/manifest_preflight.log"
free -h | head -n2 | tee -a "$LOG_DIR/manifest_preflight.log"

echo "--- manifest --strict (Oracle canli kapisi) ---"
if [ -x "$PY" ]; then
  "$PY" manifest_check.py --strict --json "$LOG_DIR/manifest_report.json" 2>&1 | tee -a "$LOG_DIR/manifest_preflight.log"
  code=$?
else
  echo "FALSE  venv python yok ($PY)" | tee -a "$LOG_DIR/manifest_preflight.log"
  code=1
fi
if [ "$code" -ne 0 ]; then
  echo "SONUC: FALSE — deploy ENGELLENDI, eski canli yapi korunuyor." | tee -a "$LOG_DIR/manifest_preflight.log"
  exit 1
fi
echo "SONUC: TRUE — Oracle canliya alma serbest (systemd+tmux tazeleme)." | tee -a "$LOG_DIR/manifest_preflight.log"