#!/usr/bin/env bash
# Pull public ready_queue.json and ingest into Oracle queue (no GitHub API, no probe budget).
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/devsolve}"
FEED_RAW="${FEED_RAW_URL:-https://raw.githubusercontent.com/fevzican1/lead-qualification-engine/master/feeds/ready_queue.json}"
mkdir -p "$APP_DIR/feeds"
curl -sfL --connect-timeout 20 --max-time 60 "$FEED_RAW" -o "$APP_DIR/feeds/ready_queue.json.tmp"
mv -f "$APP_DIR/feeds/ready_queue.json.tmp" "$APP_DIR/feeds/ready_queue.json"
cd "$APP_DIR"
exec flock -w 30 /tmp/devsolve-feed.lock .venv/bin/python -c "import feed_ingest, domain_store; feed_ingest.ingest(force_low=domain_store.queue_depth() < int(__import__('config').QUEUE_REFILL_BELOW))"
