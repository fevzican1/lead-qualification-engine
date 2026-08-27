"""Load the Common Crawl / GitHub Actions feed into the Oracle queue.

GitHub HTTP is not a site probe and does not consume DAILY_HTTP_PROBE_LIMIT.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx

import config
import domain_store
import easy_score

logger = logging.getLogger(__name__)

FEED_PATH = config.ROOT / "feeds" / "ready_queue.json"


def _min_score() -> int:
    return int(getattr(config, "FEED_MIN_SCORE", 80) or 80)


def _load_file() -> list[dict[str, Any]]:
    if not FEED_PATH.exists():
        return []
    try:
        payload = json.loads(FEED_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt %s", FEED_PATH.name)
        return []
    rows = payload.get("urls") if isinstance(payload, dict) else payload
    return [row for row in (rows or []) if isinstance(row, dict)]


def _pull_github() -> list[dict[str, Any]]:
    url = str(getattr(config, "FEED_URL", "") or "").strip()
    if not url:
        return []
    headers = {"Accept": "application/json", "User-Agent": "devsolve-feed-ingest"}
    token = str(getattr(config, "FEED_GITHUB_TOKEN", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(url, headers=headers, timeout=45.0, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("GitHub feed pull skipped: %s", exc)
        return []
    if isinstance(payload, dict) and payload.get("encoding") == "base64" and payload.get("content"):
        try:
            payload = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.info("GitHub contents decode failed: %s", exc)
            return []
    rows = payload.get("urls") if isinstance(payload, dict) else payload
    logger.info("Pulled GitHub feed rows=%s", len(rows or []))
    return [row for row in (rows or []) if isinstance(row, dict)]


def ingest(*, limit: int | None = None) -> int:
    """Enqueue score>=FEED_MIN_SCORE contact URLs. Zero Oracle probe budget."""
    min_score = _min_score()
    cap = int(getattr(config, "QUEUE_MAX", 1500) or 1500)
    room = max(0, cap - domain_store.queue_depth())
    if limit is not None:
        room = min(room, max(0, int(limit)))
    if room <= 0:
        logger.info("Queue full %s/%s — feed ingest skip", domain_store.queue_depth(), cap)
        return 0

    merged: dict[str, dict[str, Any]] = {}
    for row in [*_load_file(), *_pull_github()]:
        url = domain_store.origin_url(str(row.get("url") or ""))
        host = domain_store.host_of(url)
        if not host or domain_store.is_processed(url) or domain_store.is_enterprise(url):
            continue
        if domain_store.is_noise(url):
            continue
        score, _stack = easy_score.from_contact_url(url)
        if score < min_score:
            continue
        prev = merged.get(host)
        if prev and int(prev.get("easy_score") or 0) >= score:
            continue
        merged[host] = {"url": url, "easy_score": score, "stack": row.get("stack") or ""}

    ranked = sorted(merged.values(), key=lambda r: -int(r["easy_score"]))
    added = 0
    for row in ranked:
        if added >= room:
            break
        if domain_store.enqueue(
            str(row["url"]),
            source="cc-feed",
            easy_score=int(row["easy_score"]),
        ):
            added += 1
    logger.info("Feed ingest +%s (min_score=%s, queue=%s/%s)", added, min_score, domain_store.queue_depth(), cap)
    return added


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    n = ingest()
    print(f"Ingested {n} feed URL(s). Queue={domain_store.queue_depth()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
