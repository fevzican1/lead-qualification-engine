#!/usr/bin/env bash
# Pull latest ready_queue.json (API beats stale raw CDN) and ingest into Oracle queue.
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/devsolve}"
mkdir -p "$APP_DIR/logs"
cd "$APP_DIR"
exec flock -w 30 /tmp/devsolve-feed.lock .venv/bin/python feed_ingest.py
