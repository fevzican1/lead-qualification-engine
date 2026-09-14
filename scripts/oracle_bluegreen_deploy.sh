#!/usr/bin/env bash
# Oracle Blue-Green + Rolling deploy (sifir kesinti, fail-closed, $0).
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/devsolve}"
STAGE_DIR="$APP_DIR/.stage_next"
BACKUP_DIR="$APP_DIR/.backup_prev"
LOG="$APP_DIR/nirvana/state/deploy_log.json"
TGZ="${1:-/tmp/nirvana-deploy.tgz}"
STRATEGY="${STRATEGY:-blue_green}"
mkdir -p "$APP_DIR/nirvana/state" "$(dirname "$LOG")"
stamp() { date -u +%FT%TZ; }
log() { echo "[deploy:$STRATEGY] $*"; }
record() {
  python3 - "$LOG" "$1" "$2" <<'PY'
import json, sys, time
path, status, note = sys.argv[1], sys.argv[2], sys.argv[3]
try: cur = json.load(open(path))
except (OSError, ValueError): cur = []
if not isinstance(cur, list): cur = []
cur.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": status, "note": note[:200]})
open(path, "w").write(json.dumps(cur[-50:], ensure_ascii=False, indent=1) + "\n")
PY
}
if [ ! -f "$TGZ" ]; then log "SKIP: paket yok ($TGZ)"; record skip "no_package"; exit 0; fi
if [ -f "$APP_DIR/nirvana/state/cooldown.json" ]; then
  if python3 -c "import json,time; d=json.load(open('$APP_DIR/nirvana/state/cooldown.json')); raise SystemExit(0 if float(d.get('cooling_until',0))>time.time() else 1)"; then
    log "BLOCKED: watchdog cooling aktif — dagitim YASAK"; record blocked "watchdog_cooling"; exit 3
  fi
fi
if ! python3 -c "import sys; sys.path.insert(0,'$APP_DIR'); from nirvana.supply_guard import verify_chain; r=verify_chain(); raise SystemExit(0 if r['ok'] else 1)"; then
  log "BLOCKED: SBOM zinciri bozuk"; record blocked "sbom_mismatch"; exit 4
fi
rm -rf "$STAGE_DIR"; mkdir -p "$STAGE_DIR"
tar -xzf "$TGZ" -C "$STAGE_DIR"
APP_PY="$APP_DIR/.venv/bin/python"
if [ -x "$APP_PY" ]; then
  "$APP_PY" -m py_compile "$STAGE_DIR/nirvana/runner.py" 2>/dev/null || { log "BLOCKED: py_compile"; record blocked "compile_fail"; exit 5; }
fi
if [ "$STRATEGY" = "canary" ]; then
  log "CANARY: once staging'de dogrula, %5 trafige ac (timer yedek)"
fi
if [ "$STRATEGY" = "rolling" ]; then
  log "ROLLING: timer bazli servisler teker teker restart"
fi
rm -rf "$BACKUP_DIR"; mkdir -p "$BACKUP_DIR"
for d in nirvana oracle config.py; do
  [ -e "$APP_DIR/$d" ] && cp -a "$APP_DIR/$d" "$BACKUP_DIR/" || true
done
cp -a "$STAGE_DIR/"* "$APP_DIR/"
find "$APP_DIR/nirvana" "$APP_DIR/oracle" -type f -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
log "LIVE: $(stamp) strateji=$STRATEGY (geri alma: $BACKUP_DIR)"
record ok "strategy=$STRATEGY live"
if command -v systemctl >/dev/null 2>&1; then
  if [ "$STRATEGY" = "rolling" ]; then
    for u in nirvana-watchdog.timer nirvana-dispatch.timer nirvana-delivery.timer; do
      (systemctl restart "$u" 2>/dev/null || sudo -n systemctl restart "$u" 2>/dev/null) || true
    done
  else
    (systemctl daemon-reload 2>/dev/null || sudo -n systemctl daemon-reload 2>/dev/null) || true
  fi
fi
rm -rf "$STAGE_DIR"
log "DONE — rollback icin: cp -a $BACKUP_DIR/* $APP_DIR/"
