#!/usr/bin/env bash
# Nirvana Oracle VM canlıya alma (Always-Free kotasını koruyan kurulum).
# Tek seferlik, root ile:  sudo bash oracle/nirvana_oracle_install.sh
set -euo pipefail

APP_DIR=/opt/devsolve
UNIT_SRC="$(cd "$(dirname "$0")" && pwd)"

echo "[1/6] Depo güncelleme"
cd "$APP_DIR"
git pull --ff-only origin master

echo "[2/6] Python bağımlılıkları (yalnız ücretsiz paketler)"
"$APP_DIR/.venv/bin/pip" install -q -r requirements.txt

echo "[3/6] Payoneer linki doğrulaması (2.500 EUR)"
"$APP_DIR/.venv/bin/python" - <<'PY'
from nirvana.payment import retainer_amount, retainer_currency, retainer_label
import config
assert retainer_amount() == 2500 and retainer_currency() == "EUR", "PAYMENT_AMOUNT/PAYMENT_CURRENCY .env'de 2500/EUR olmalı"
assert "[BURAYA_YENI_PAYONEER_LINKINI_EKLEYIN]" not in config.PAYONEER_PAYMENT_URL, "PAYONEER_PAYMENT_URL hâlâ yer tutucu"
print("OK — retainer:", retainer_label())
PY

echo "[4/6] Watchdog dry-run (kota koruması canlı test)"
"$APP_DIR/.venv/bin/python" -m nirvana.runner watchdog_quota_agent --no-notify

echo "[5/6] systemd unit + timer kurulumu"
install -m 644 "$UNIT_SRC/nirvana-watchdog.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-watchdog.timer" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-delivery.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-delivery.timer" /etc/systemd/system/
systemctl daemon-reload

echo "[6/6] Timer'ları canlıya alma"
systemctl enable --now nirvana-watchdog.timer
systemctl enable --now nirvana-delivery.timer

systemctl list-timers 'nirvana-*' --no-pager
echo "NIRVANA ORACLE LIVE — watchdog her 5 dk, delivery her Pazartesi 06:00 UTC."
