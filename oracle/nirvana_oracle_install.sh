#!/usr/bin/env bash
# Nirvana Oracle VM canlıya alma (Always-Free kotasını koruyan kurulum).
# Tek seferlik, root ile:  sudo bash oracle/nirvana_oracle_install.sh
set -euo pipefail

APP_DIR=/opt/devsolve
UNIT_SRC="$(cd "$(dirname "$0")" && pwd)"

echo "[1/6] Uygulama dizini: $APP_DIR (tar/scp tabanlı deployment — git deposu değildir)"
cd "$APP_DIR"

echo "[2/6] Python bağımlılıkları (yalnız ücretsiz paketler)"
"$APP_DIR/.venv/bin/pip" install -q -r requirements.txt

echo "[2.6/6] Dedicated CAPTCHA OCR yığını (ücretsiz apt paketleri; yoksa DOM çözücü)"
if command -v apt-get >/dev/null 2>&1; then
  (sudo -n apt-get install -y -q tesseract-ocr python3-opencv 2>/dev/null \
    || sudo apt-get install -y -q tesseract-ocr python3-opencv 2>/dev/null \
    || apt-get install -y -q tesseract-ocr python3-opencv 2>/dev/null) || echo "apt OCR kurulamadı — DOM çözücü + Pillow ile devam (maliyet $0)"
  "$APP_DIR/.venv/bin/pip" install -q pytesseract 2>/dev/null || echo "pytesseract kurulamadı — OCR'siz DOM katmanı ile devam"
else
  echo "apt yok — OCR'siz DOM katmanı ile devam"
fi
"$APP_DIR/.venv/bin/python" - <<'PY'
try:
    from nirvana import free_captcha_solver as f
    print("OCR durumu: tesseract=", f._TESSERACT_AVAILABLE, "opencv=", f._OPENCV_AVAILABLE)
except Exception as e:
    print("OCR durum okunamadı:", str(e)[:100])
PY

echo "[2.5/6] Payoneer linkini /opt/devsolve/.env içine yazma (PAYONEER_LINK verilmişse)"
if [ -n "${PAYONEER_LINK:-}" ]; then
  # sed replacement'taki & işaretini escape et (link ?t=..&src=pl içerir)
  safe="${PAYONEER_LINK//&/\\&}"
  touch "$APP_DIR/.env"
  if grep -q '^PAYONEER_PAYMENT_URL=' "$APP_DIR/.env"; then
    sed -i "s|^PAYONEER_PAYMENT_URL=.*|PAYONEER_PAYMENT_URL=${safe}|" "$APP_DIR/.env"
  else
    printf 'PAYONEER_PAYMENT_URL=%s\n' "${PAYONEER_LINK}" >> "$APP_DIR/.env"
  fi
  grep -q '^PAYMENT_CURRENCY=' "$APP_DIR/.env" || printf 'PAYMENT_CURRENCY=EUR\n' >> "$APP_DIR/.env"
  grep -q '^PAYMENT_AMOUNT=' "$APP_DIR/.env" || printf 'PAYMENT_AMOUNT=2500\n' >> "$APP_DIR/.env"
  echo "PAYONEER_PAYMENT_URL güncellendi."
else
  echo "PAYONEER_LINK verilmedi — .env'deki mevcut link korunuyor."
fi

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

echo "[4.5/6] Dedicated CAPTCHA worker derleme kontrolü (max 2 worker sabiti)"
"$APP_DIR/.venv/bin/python" -m py_compile "$APP_DIR/nirvana/free_captcha_solver.py" "$APP_DIR/nirvana/free_captcha_worker.py" "$APP_DIR/nirvana/stealth_former.py"
"$APP_DIR/.venv/bin/python" - <<'PY'
from nirvana import stealth_former as sf, free_captcha_solver as fcs
assert sf.CAPTCHA_MAX_WORKERS == 2 and fcs.CAPTCHA_MAX_WORKERS == 2, "captcha worker max 2 olmali"
print("OK — captcha_queue:", sf.CAPTCHA_QUEUE_NAME, "| max_workers=2 | $0 (parali API yok)")
PY

echo "[5/6] systemd unit + timer kurulumu"
install -m 644 "$UNIT_SRC/nirvana-watchdog.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-watchdog.timer" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-idleguard.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-idleguard.timer" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-delivery.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-delivery.timer" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-deliveryworker.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-deliveryworker.timer" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-linkedin.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-linkedin.timer" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-captcha.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-captcha.timer" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-dispatch.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-dispatch.timer" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-salesbot.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/nirvana-pipeline.service" /etc/systemd/system/
systemctl daemon-reload

echo "[5.5/6] Dispatch hub derleme kontrolü"
"$APP_DIR/.venv/bin/python" -m py_compile "$APP_DIR/oracle/dispatch_hub.py"

echo "[5.6/6] idle_guard dry-run (OCI Always-Free idle geri alım koruması canlı test)"
"$APP_DIR/.venv/bin/python" -m nirvana.runner idle_guard --no-notify

echo "[5.7/6] Yerel Bot API durumu (tanımlıysa yerel, değilse bulut — fail-safe)"
"$APP_DIR/.venv/bin/python" - <<'PY'
from telegram_bot_api import status_line
print(status_line())
PY

echo "[6/6] Timer'ları canlıya alma"
# Legacy birimler AYNI işi yapar (devsolve-runner = auto_runner, devsolve-bot =
# telegram_sales_bot). İkisi birlikte açık kalırsa Telegram getUpdates çakışır
# ve ÇİFT form gönderimi olur; bu yüzden kapatılır. Yetkili birimler nirvana-*.
for legacy in devsolve-bot.service devsolve-runner.service; do
  if systemctl list-unit-files "$legacy" 2>/dev/null | grep -q "^${legacy}"; then
    systemctl disable --now "$legacy" 2>/dev/null || true
    echo "$legacy devre dışı (çift çalışma önlendi)"
  fi
done
systemctl enable --now nirvana-watchdog.timer
systemctl enable --now nirvana-idleguard.timer
systemctl enable --now nirvana-delivery.timer
systemctl enable --now nirvana-deliveryworker.timer
systemctl enable --now nirvana-linkedin.timer
systemctl enable --now nirvana-captcha.timer
systemctl enable --now nirvana-dispatch.timer
# Form hattı (günlük 400 form kotasının taşıyıcısı): keşif + nitelendirme +
# form gönderimi döngüsü. Kurulumla BİRLİKTE gelir; aksi halde taze bir VM'de
# hiçbir birim form göndermiyor ve günlük kota 0'da kalıyordu (canlı arıza
# 2026-09: gün boyu 0 form, kuyruk 2500).
if ! systemctl enable --now nirvana-pipeline.service; then
  echo "HATA: nirvana-pipeline.service baslamadi — FORM HATTI CANLI DEGIL" >&2
  systemctl --no-pager -l status nirvana-pipeline.service | head -30 || true
  journalctl -u nirvana-pipeline.service -n 40 --no-pager -o cat || true
  exit 1
fi
# Satis botu: Type=notify + WatchdogSec. Import zinciri eksikse (ornek:
# heartbeat.py pakette yok) servis sessizce dusuyordu; artik acikca patlar.
if ! systemctl enable --now nirvana-salesbot.service; then
  echo "HATA: nirvana-salesbot.service baslamadi — satis botu CANLI DEGIL" >&2
  systemctl --no-pager -l status nirvana-salesbot.service | head -30 || true
  journalctl -u nirvana-salesbot.service -n 40 --no-pager -o cat || true
  exit 1
fi

systemctl list-timers 'nirvana-*' --no-pager
echo "NIRVANA ORACLE LIVE — form hattı (nirvana-pipeline: keşif+nitelendirme+400 form/gün), watchdog 5dk, idle_guard 10dk (Always-Free koruması), delivery haftalık, teslimat işçisi 2 saatte bir, captcha worker 10dk (max 2), dispatch hub 1dk tick (CDX feed dilimleri 1/2/4/5dk; diğer modüller 30dk-24sa)."
