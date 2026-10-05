#!/usr/bin/env bash
# Nirvana Web Live Chat Engine - Oracle Always Free tek tik deploy (idempotent).
#
# Musteri trafigi Telegram'dan bu servise tasindi: form dolduran lead
# WEBCHAT_PUBLIC_URL'ye yonlendirilir; Telegram yalnizca pasif admin/operator
# bildirim hattidir (VIP lead / odeme istegi / status).
#
# Kullanim (Oracle VM):
#   sudo bash oracle/deploy_webchat.sh
#   WEBCHAT_PUBLIC_URL=https://chat.ornek.com sudo -E bash oracle/deploy_webchat.sh
#   GITHUB_TOKEN=ghp_xxx GITHUB_REPO=owner/repo sudo -E bash oracle/deploy_webchat.sh
#
# Adimlar: [1] venv bagimliliklari -> [2] .env webchat adresi -> [3] systemd
# birimi (Type=notify + WatchdogSec + RestartSec=1) -> [4] nginx reverse proxy
# (80 -> 127.0.0.1:8765, WebSocket upgrade) -> [5] VM firewall (80/tcp) ->
# [6] servis restart -> [7] /health + /chat dogrulama -> [8] (varsa) Git push.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/devsolve}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_NAME="nirvana-webchat.service"
SITE_NAME="nirvana-webchat"
PORT="${WEBCHAT_PORT:-8765}"
PUBLIC_URL="${WEBCHAT_PUBLIC_URL:-}"

as_root() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo -n "$@" || sudo "$@"; fi
}

echo "=== [1/8] Python ortami + bagimliliklar (Always Free: hafif, $0) ==="
mkdir -p "$APP_DIR"
cd "$APP_DIR"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
  echo "venv olusturuldu: $APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r requirements.txt
"$APP_DIR/.venv/bin/python" - <<'PY'
import importlib
for mod in ("fastapi", "uvicorn", "edge_tts"):
    try:
        importlib.import_module(mod)
        print(f"OK  {mod}")
    except Exception as exc:  # noqa: BLE001
        print(f"UYARI {mod} yok ({exc}) - chat calisir, ilgili ozellik pasif")
PY

echo "=== [2/8] .env: WEBCHAT_PUBLIC_URL (musteri adresi) ==="
touch "$APP_DIR/.env"
if [ -z "$PUBLIC_URL" ]; then
  # OCI metadata (169.254.169.254) -> yoksa genel IP servisi -> yoksa mevcut deger.
  META_IP="$(curl -s --max-time 4 -H 'Authorization: Bearer Oracle' -L \
    http://169.254.169.254/opc/v2/vnics/ 2>/dev/null | head -c 400 \
    | sed -n 's/.*"publicIp"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')" || true
  [ -z "${META_IP:-}" ] && META_IP="$(curl -s --max-time 6 https://api.ipify.org 2>/dev/null || true)"
  if [ -n "${META_IP:-}" ]; then
    PUBLIC_URL="http://${META_IP}"
    echo "genel adres otomatik bulundu: $PUBLIC_URL"
  else
    PUBLIC_URL="$(grep -E '^WEBCHAT_PUBLIC_URL=' "$APP_DIR/.env" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
    [ -n "$PUBLIC_URL" ] && echo "mevcut WEBCHAT_PUBLIC_URL korundu: $PUBLIC_URL" \
      || echo "::uyari:: WEBCHAT_PUBLIC_URL bulunamadi - .env'e elle yazin (or. http://<vm-ip>)"
  fi
fi
if [ -n "$PUBLIC_URL" ]; then
  if grep -q '^WEBCHAT_PUBLIC_URL=' "$APP_DIR/.env"; then
    sed -i "s|^WEBCHAT_PUBLIC_URL=.*|WEBCHAT_PUBLIC_URL=${PUBLIC_URL}|" "$APP_DIR/.env"
  else
    printf 'WEBCHAT_PUBLIC_URL=%s\n' "$PUBLIC_URL" >> "$APP_DIR/.env"
  fi
fi
for kv in "WEBCHAT_PORT|$PORT" "WEBCHAT_BIND_HOST|127.0.0.1" "TELEGRAM_OWNER_CHAT_ID|8465614326"; do
  key="${kv%%|*}"; val="${kv#*|}"
  if grep -q "^${key}=" "$APP_DIR/.env"; then :; else printf '%s=%s\n' "$key" "$val" >> "$APP_DIR/.env"; fi
done
echo "musteri hatti adresi: ${PUBLIC_URL:-<tanimsiz>}"

echo "=== [3/8] systemd birimi: self-healing (WatchdogSec + RestartSec=1) ==="
UNIT_SRC=""
for cand in "$SCRIPT_DIR/$UNIT_NAME" "$APP_DIR/oracle/$UNIT_NAME" \
            "$APP_DIR/$UNIT_NAME" "/tmp/$UNIT_NAME"; do
  if [ -f "$cand" ]; then UNIT_SRC="$cand"; break; fi
done
if [ -n "$UNIT_SRC" ]; then
  as_root install -m 0644 "$UNIT_SRC" "/etc/systemd/system/$UNIT_NAME"
  echo "birim kuruldu: /etc/systemd/system/$UNIT_NAME  (kaynak: $UNIT_SRC)"
else
  echo "::uyari::$UNIT_NAME kaynagi bulunamadi - mevcut birim korunuyor"
fi
if [ ! -f "/etc/systemd/system/$UNIT_NAME" ]; then
  echo "::hata::$UNIT_NAME yok - once repoyu Oracle'a getirin (oracle/$UNIT_NAME)"; exit 1
fi
as_root systemctl daemon-reload
as_root systemctl enable "$UNIT_NAME" >/dev/null 2>&1 || true

echo "=== [4/8] nginx reverse proxy (80 -> 127.0.0.1:$PORT, WebSocket upgrade) ==="
if ! command -v nginx >/dev/null 2>&1; then
  echo "nginx kuruluyor (ucretsiz apt paketi)"
  as_root apt-get update -qq || true
  as_root apt-get install -y -qq nginx || echo "::uyari::nginx kurulamadi - servis 127.0.0.1:$PORT uzerinden erisilebilir"
fi
NGINX_SRC=""
for cand in "$SCRIPT_DIR/webchat-nginx.conf" "$APP_DIR/oracle/webchat-nginx.conf" "/tmp/webchat-nginx.conf"; do
  if [ -f "$cand" ]; then NGINX_SRC="$cand"; break; fi
done
if command -v nginx >/dev/null 2>&1 && [ -n "$NGINX_SRC" ]; then
  as_root install -m 0644 "$NGINX_SRC" "/etc/nginx/sites-available/nirvana-webchat"
  as_root ln -sf "/etc/nginx/sites-available/nirvana-webchat" "/etc/nginx/sites-enabled/nirvana-webchat"
  as_root rm -f /etc/nginx/sites-enabled/default
  # 80'i baska bir site kapattiysa: varsayilan siteyi devre disi birak (idempotent).
  if ! as_root nginx -t >/dev/null 2>&1; then
    echo "::uyari::nginx -t basarisiz; varsayilan site kaldirilip yeniden denenecek"
    as_root rm -f /etc/nginx/sites-enabled/default
    as_root nginx -t
  fi
  as_root systemctl enable nginx >/dev/null 2>&1 || true
  as_root systemctl reload nginx 2>/dev/null || as_root systemctl restart nginx
  echo "nginx yapilandirildi: /etc/nginx/sites-available/nirvana-webchat"
else
  echo "::uyari::nginx atlandi (nginx veya webchat-nginx.conf yok)"
fi

echo "=== [5/8] VM firewall: 80/tcp (OCI Security List ayri kapi - asagida not) ==="
if command -v iptables >/dev/null 2>&1; then
  if ! as_root iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null; then
    as_root iptables -I INPUT 5 -p tcp --dport 80 -m state --state NEW,ESTABLISHED -j ACCEPT 2>/dev/null \
      && echo "iptables: 80/tcp acildi" || echo "::uyari::iptables kurali eklenemedi"
    if as_root netfilter-persistent save >/dev/null 2>&1; then echo "iptables kalici hale getirildi"; fi
  else
    echo "iptables: 80/tcp zaten acik"
  fi
fi
echo "NOT: OCI konsolunda VCN -> Security List/NSG icin 'Ingress 0.0.0.0/0 TCP 80' kurali sart"
echo "     (Oracle bulut katmani VM icinden acilamaz; tek seferlik konsol islemi)."

echo "=== [6/8] Servis restart (self-healing: cokse 1 sn icinde kendini onarir) ==="
as_root systemctl restart "$UNIT_NAME"
sleep 2
STATE="$(systemctl is-active "$UNIT_NAME" 2>/dev/null || true)"
echo "durum: $STATE"

echo "=== [7/8] Canli dogrulama (/health + /chat) ==="
ok=0
for i in $(seq 1 15); do
  if curl -fsS --max-time 5 "http://127.0.0.1:$PORT/health" -o /tmp/webchat-health.json 2>/dev/null; then
    ok=1; break
  fi
  sleep 2
done
if [ "$ok" != "1" ]; then
  echo "::hata::webchat servisi /health vermiyor"
  as_root journalctl -u "$UNIT_NAME" -n 40 --no-pager -o cat || true
  exit 1
fi
cat /tmp/webchat-health.json | head -c 600; echo
"$APP_DIR/.venv/bin/python" - <<PY
import json, sys
data = json.load(open("/tmp/webchat-health.json", encoding="utf-8"))
assert data.get("ok") is True, data
assert data.get("engine") == "webchat", data
print("health OK | workers:", data.get("workers"), "| oturum:", data.get("sessions"),
      "| cevrimici:", data.get("online"), "| flood:", bool(data.get("flood")))
PY
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/chat" -o /tmp/webchat-page.html
grep -q "/ws/" /tmp/webchat-page.html && echo "chat UI OK (/chat + WebSocket istemcisi)" \
  || echo "::uyari::/chat sayfasi WebSocket istemcisi icermiyor"
if command -v nginx >/dev/null 2>&1; then
  curl -fsS --max-time 8 "http://127.0.0.1/health" -o /dev/null 2>/dev/null \
    && echo "nginx proxy OK (80 -> $PORT)" || echo "::uyari::nginx 80 uzerinden /health vermedi"
fi
echo "MUSTERI ADRESI: ${PUBLIC_URL:-<.env icindeki WEBCHAT_PUBLIC_URL>}/chat"
as_root journalctl -u "$UNIT_NAME" -n 12 --no-pager -o cat || true

echo "=== [8/8] Git'e push (durum + kod; token verilirse) ==="
if [ -d "$APP_DIR/.git" ] && command -v git >/dev/null 2>&1; then
  cd "$APP_DIR"
  git config user.email >/dev/null 2>&1 || git config user.email "deploy@nirvana.local"
  git config user.name  >/dev/null 2>&1 || git config user.name  "nirvana-deploy"
  if [ -n "${GITHUB_TOKEN:-}" ] && [ -n "${GITHUB_REPO:-}" ]; then
    git remote remove origin >/dev/null 2>&1 || true
    git remote add origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
  fi
  git add -A >/dev/null 2>&1 || true
  if ! git diff --cached --quiet; then
    git commit -q -m "webchat: Oracle live ($(date -u +%Y-%m-%dT%H:%MZ))" || true
  fi
  if git remote get-url origin >/dev/null 2>&1; then
    BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    git push -u origin "$BRANCH" && echo "git push OK ($BRANCH)" \
      || echo "::uyari::git push basarisiz (token/remote kontrol edin) - servis CANLI kalir"
  else
    echo "git remote tanimli degil - push atlandi (GITHUB_TOKEN + GITHUB_REPO verin)"
  fi
else
  echo "$APP_DIR bir git deposu degil - push atlandi (tar/scp deployment; kod GitHub Actions ile gelir)"
fi

echo "NIRVANA WEBCHAT LIVE"


