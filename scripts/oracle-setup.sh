#!/usr/bin/env bash
# Ubuntu ARM (Oracle A1.Flex) — native Ollama + systemd, no Docker.
set -euo pipefail

APP="${APP_DIR:-/opt/devsolve}"
SERVICE_USER="${SUDO_USER:-${USER}}"
if [[ -z "${OLLAMA_MODEL:-}" && -f "${APP}/.env" ]]; then
  OLLAMA_MODEL="$(grep -E '^OLLAMA_MODEL=' "${APP}/.env" | tail -1 | cut -d= -f2- | tr -d '[:space:]\"')"
fi
MODEL="${OLLAMA_MODEL:-deepseek-r1:14b}"

if [[ "$(id -u)" -ne 0 ]]; then
  exec sudo --preserve-env=APP_DIR,OLLAMA_MODEL bash "$0" "$@"
fi

avail_kb="$(df --output=avail / | tail -1 | tr -d ' ')"
if [[ "${avail_kb}" -lt 5000000 ]]; then
  echo "Disk too small (${avail_kb} KB free). In OCI: Storage -> Boot volume -> 50 GB+."
  echo "Always Free quota allows up to 200 GB block storage. 'Block storage only' is normal; size is on the Boot volume page."
  exit 1
fi

# 14B + Chromium on 24 GB RAM: keep a swap cushion.
mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
if [[ "${mem_kb}" -lt 33000000 ]] && [[ ! -f /swapfile ]]; then
  echo "RAM is ${mem_kb} KB — creating 8G swap"
  fallocate -l 8G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=8192
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  git curl ca-certificates \
  tini
apt-get clean
rm -rf /var/lib/apt/lists/*

if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
install -d /etc/systemd/system/ollama.service.d
cat >/etc/systemd/system/ollama.service.d/override.conf <<EOF
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
EOF
systemctl daemon-reload
systemctl enable ollama
systemctl restart ollama

for _ in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

ollama pull "${MODEL}"
# Keep disk inside Always Free boot volume: one production model only.
for extra in llama3.2:1b qwen2.5:7b; do
  if [[ "${extra}" != "${MODEL}" ]]; then
    ollama rm "${extra}" >/dev/null 2>&1 || true
  fi
done

install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${APP}"
if [[ ! -f "${APP}/requirements.txt" ]]; then
  echo "Project files missing in ${APP}. Upload first."
  exit 1
fi

sudo -u "${SERVICE_USER}" python3 -m venv "${APP}/.venv"
sudo -u "${SERVICE_USER}" "${APP}/.venv/bin/pip" install --upgrade pip
sudo -u "${SERVICE_USER}" "${APP}/.venv/bin/pip" install -r "${APP}/requirements.txt"

# Playwright system libs need root; browsers stay in the app dir.
PLAYWRIGHT_BROWSERS_PATH="${APP}/.playwright"
export PLAYWRIGHT_BROWSERS_PATH
"${APP}/.venv/bin/python" -m playwright install-deps chromium
sudo -u "${SERVICE_USER}" env PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH}" \
  "${APP}/.venv/bin/python" -m playwright install chromium

if [[ ! -f "${APP}/.env" ]]; then
  echo "Missing ${APP}/.env"
  exit 1
fi

# Force local Ollama on the VM; do not inherit a Windows Playwright path.
sed -i 's|^OLLAMA_HOST=.*|OLLAMA_HOST=http://127.0.0.1:11434|' "${APP}/.env" || true
if grep -q '^PLAYWRIGHT_BROWSERS_PATH=' "${APP}/.env"; then
  sed -i "s|^PLAYWRIGHT_BROWSERS_PATH=.*|PLAYWRIGHT_BROWSERS_PATH=${APP}/.playwright|" "${APP}/.env"
else
  printf '\nPLAYWRIGHT_BROWSERS_PATH=%s\n' "${APP}/.playwright" >> "${APP}/.env"
fi
chown "${SERVICE_USER}:${SERVICE_USER}" "${APP}/.env"
chmod 600 "${APP}/.env"

feed_raw="https://raw.githubusercontent.com/fevzican1/lead-qualification-engine/master/feeds/ready_queue.json"
grep -q '^FEED_RAW_URL=' "${APP}/.env" && sed -i "s|^FEED_RAW_URL=.*|FEED_RAW_URL=${feed_raw}|" "${APP}/.env" || printf '\nFEED_RAW_URL=%s\n' "${feed_raw}" >> "${APP}/.env"
grep -q '^QUEUE_REFILL_BELOW=' "${APP}/.env" || printf '\nQUEUE_REFILL_BELOW=80\n' >> "${APP}/.env"

cat >/etc/systemd/system/devsolve-bot.service <<EOF
[Unit]
Description=DevSolve Telegram sales bot
After=network-online.target ollama.service
Wants=network-online.target ollama.service

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${APP}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=${APP}/.env
ExecStart=${APP}/.venv/bin/python ${APP}/telegram_sales_bot.py
Restart=always
RestartSec=15
TimeoutStopSec=20
KillMode=mixed

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/devsolve-runner.service <<EOF
[Unit]
Description=DevSolve lead finder + form pipeline
After=network-online.target ollama.service
Wants=network-online.target ollama.service

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${APP}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=${APP}/.env
ExecStart=${APP}/.venv/bin/python ${APP}/auto_runner.py
Restart=always
RestartSec=30
KillMode=control-group
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now devsolve-bot.service devsolve-runner.service
# enable --now does not reload already-running Python; force new code.
systemctl restart devsolve-bot.service devsolve-runner.service

mkdir -p "${APP}/logs"
chmod +x "${APP}/scripts/oracle-feed-sync.sh" 2>/dev/null || true
if [[ -f "${APP}/scripts/devsolve-feed-sync.service" ]]; then
  cp "${APP}/scripts/devsolve-feed-sync.service" "${APP}/scripts/devsolve-feed-sync.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now devsolve-feed-sync.timer
fi
if [[ -f "${APP}/scripts/devsolve-ingest.service" ]]; then
  cp "${APP}/scripts/devsolve-ingest.service" /etc/systemd/system/devsolve-ingest.service
  systemctl daemon-reload
  systemctl enable --now devsolve-ingest.service
fi

systemctl --no-pager --full status devsolve-bot.service || true
systemctl --no-pager --full status devsolve-runner.service || true
echo "Oracle setup finished. Bot and runner are enabled on boot."
