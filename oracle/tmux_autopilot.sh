#!/usr/bin/env bash
# Nirvana TMUX OTOPILOT — sentinel + quality_audit tmux oturumlarinda 7/24 AKTIF.
#
# Kullanim:
#   tmux_autopilot.sh up         # iki tmux oturumunu ayakta sagla (idempotent)
#   tmux_autopilot.sh supervise  # systemd nirvana-autopilot: 60sn'de bir denetle
#   tmux_autopilot.sh down       # oturumlari durdur
#   tmux_autopilot.sh status     # tmux ls + son log satiri
#   tmux_autopilot.sh _loop L N  # (dahili) lane L icin sonsuz tur dongusu
#
# Tasarim (kesintisiz 7/24 garanti — iki katman):
#   * tmux oturumu = CANLI DONGU. Her tur taze `python -m nirvana.runner`
#     cagrisidir → yeni kod her turda otomatik devreye girer. Process
#     cokerce oturum kapanir; supervisor 60 sn icinde yeniden acar.
#   * Supervisor'un kendisi systemd (Restart=always) ile garanti altinda.
#   * Singleton lock (nirvana.sentinel / quality_audit _acquire_singleton)
#     systemd timer turlariyla ayni anda cakismayi onler: ikinci turl "busy" atlar.
#   * Döngü ASLA cikmaz: tur hatasi loglanir, sleep devam eder. Aktif mod
#     (bayraksiz) → sentinel izole cerrahi onarim, quality delege onarim yapar.
set -u

APP_DIR="${APP_DIR:-/opt/devsolve}"
PY="${APP_DIR}/.venv/bin/python"
LOG_DIR="${APP_DIR}/logs"
SENTINEL_EVERY="${SENTINEL_EVERY:-10}"     # ust akil: tur + 10sn (yaklasik 30sn disiplini)
QUALITY_EVERY="${QUALITY_EVERY:-240}"      # kalite: tur + 4dk (yaklasik 5dk disiplini)
S_SENTINEL="nirvana-sentinel"
S_QUALITY="nirvana-quality"
SELF="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/$(basename "$0")"

load_env() {
  [ -f "${APP_DIR}/.env" ] || return 0
  set -a
  # KEY=VALUE dosyasi; bozuk/eksik satir turu durdurmaz.
  . "${APP_DIR}/.env" 2>/dev/null || true
  set +a
}

rotate_log() {
  local f="$1"
  [ -f "$f" ] || return 0
  # 10 MB tavan: disk dolmasin — eski log sikistirilir, tur akmaya devam eder.
  local size
  size="$(wc -c < "$f" 2>/dev/null || echo 0)"
  if [ "${size:-0}" -gt 10485760 ]; then
    mv -f "$f" "${f}.1" 2>/dev/null || true
  fi
}

loop_worker() {
  local lane="$1" every="$2"
  local log="${LOG_DIR}/autopilot-${lane}.log"
  mkdir -p "$LOG_DIR"
  cd "$APP_DIR" || exit 1
  load_env
  echo "[autopilot] ${lane} dongusu basladi $(date -u +%FT%TZ) | her ${every}s" | tee -a "$log"
  while true; do
    rotate_log "$log"
    echo "[autopilot] ${lane} turu $(date -u +%FT%TZ)" >> "$log"
    # nice 15: VM yogunlugunda canli hat (pipeline/webchat) onceliklidir.
    nice -n 15 "$PY" -m nirvana.runner "$lane" >> "$log" 2>&1 || \
      echo "[autopilot] ${lane} tur hatasi — dongu surecek" >> "$log"
    sleep "$every"
  done
}

ensure_session() {
  local name="$1" lane="$2" every="$3"
  if tmux has-session -t "$name" 2>/dev/null; then
    return 0
  fi
  tmux new-session -d -s "$name" -x 220 -y 50 "${SELF} _loop ${lane} ${every}"
  echo "[autopilot] oturum acildi: ${name} (${lane} her ${every}s)"
}

cmd_up() {
  command -v tmux >/dev/null 2>&1 || { echo "[autopilot] tmux YUKLU DEGIL" >&2; return 1; }
  ensure_session "$S_SENTINEL" sentinel "$SENTINEL_EVERY"
  ensure_session "$S_QUALITY" quality_audit "$QUALITY_EVERY"
}

cmd_supervise() {
  echo "[autopilot] supervise basladi $(date -u +%FT%TZ)"
  while true; do
    cmd_up 2>/dev/null || true
    sleep 60
  done
}

cmd_down() {
  tmux kill-session -t "$S_SENTINEL" 2>/dev/null || true
  tmux kill-session -t "$S_QUALITY" 2>/dev/null || true
}

cmd_status() {
  tmux ls 2>/dev/null || echo "tmux oturumu YOK"
  local lane f
  for lane in sentinel quality_audit; do
    f="${LOG_DIR}/autopilot-${lane}.log"
    if [ -f "$f" ]; then
      echo "--- son satir (${lane}):"
      tail -n 1 "$f"
    fi
  done
}

case "${1:-up}" in
  up)        cmd_up ;;
  supervise) cmd_supervise ;;
  down)      cmd_down ;;
  status)    cmd_status ;;
  _loop)     loop_worker "${2:?lane verilmedi}" "${3:?aralik verilmedi}" ;;
  *)         echo "kullanim: $0 {up|supervise|down|status}" >&2; exit 2 ;;
esac
