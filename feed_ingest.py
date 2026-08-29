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
import target_pool

logger = logging.getLogger(__name__)

FEED_PATH = config.ROOT / "feeds" / "ready_queue.json"
FEED_STATE_PATH = config.ROOT / "feeds" / "feed_state.json"


def _min_score() -> int:
    return int(getattr(config, "FEED_MIN_SCORE", 80) or 80)


def _load_state() -> dict[str, Any]:
    if not FEED_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(FEED_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(data: dict[str, Any]) -> None:
    FEED_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = domain_store.utc_now()
    tmp = FEED_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(FEED_STATE_PATH)


def _decode_github_payload(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict) and payload.get("encoding") == "base64" and payload.get("content"):
        try:
            payload = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.info("GitHub contents decode failed: %s", exc)
            return None
    return payload if isinstance(payload, dict) else None


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("urls") if isinstance(payload, dict) else payload
    return [row for row in (rows or []) if isinstance(row, dict)]


def _load_file() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not FEED_PATH.exists():
        return [], {}
    try:
        payload = json.loads(FEED_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt %s", FEED_PATH.name)
        return [], {}
    if not isinstance(payload, dict):
        return [], {}
    return _rows_from_payload(payload), payload


def _pull_github_raw() -> dict[str, Any] | None:
    url = str(getattr(config, "FEED_URL", "") or "").strip()
    if not url:
        return None
    headers = {"Accept": "application/json", "User-Agent": "devsolve-feed-ingest"}
    token = str(getattr(config, "FEED_GITHUB_TOKEN", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(url, headers=headers, timeout=45.0, follow_redirects=True)
        response.raise_for_status()
        return _decode_github_payload(response.json())
    except Exception as exc:  # noqa: BLE001
        logger.info("GitHub feed pull skipped: %s", exc)
        return None


def sync_github_feed() -> dict[str, Any] | None:
    """Pull ready_queue.json from GitHub and persist locally. Zero probe budget."""
    payload = _pull_github_raw()
    if not payload:
        return None
    rows = _rows_from_payload(payload)
    if not rows:
        logger.info("GitHub feed empty")
        return None
    payload.setdefault("version", 2)
    payload.setdefault("source", "multi-public-discovery")
    payload["count"] = len(rows)
    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = FEED_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(FEED_PATH)
    state = _load_state()
    state["last_sync_at"] = domain_store.utc_now()
    state["feed_updated_at"] = str(payload.get("updated_at") or "")
    state["feed_count"] = len(rows)
    _save_state(state)
    logger.info("Synced GitHub feed rows=%s updated=%s", len(rows), state["feed_updated_at"])
    return {
        "count": len(rows),
        "updated_at": state["feed_updated_at"],
        "new_hosts": int(payload.get("new_hosts") or 0),
    }


def ingest(*, limit: int | None = None) -> int:
    """Enqueue score>=FEED_MIN_SCORE contact URLs. Zero Oracle probe budget."""
    min_score = _min_score()
    cap = int(getattr(config, "QUEUE_MAX", 1500) or 1500)
    room = max(0, cap - domain_store.queue_depth())
    if limit is not None:
        room = min(room, max(0, int(limit)))

    file_rows, file_meta = _load_file()
    state = _load_state()
    feed_stamp = str(file_meta.get("updated_at") or state.get("feed_updated_at") or "")
    feed_refresh = feed_stamp and feed_stamp != str(state.get("last_ingest_feed_at") or "")

    merged: dict[str, dict[str, Any]] = {}
    retry_budget = max(room, 24) if feed_refresh else max(room, 8)
    for row in file_rows:
        url = str(row.get("url") or "").strip()
        canonical = domain_store.origin_url(url)
        host = domain_store.host_of(canonical)
        if not host or domain_store.is_enterprise(url):
            continue
        if domain_store.is_processed(url):
            if retry_budget <= 0 or not domain_store.requeue_if_retryable(url):
                continue
            retry_budget -= 1
        if not domain_store.feed_eligible(url) and domain_store.is_processed(url):
            continue
        if domain_store.is_processed(url):
            continue
        if domain_store.is_noise(url):
            continue
        if not row.get("form_verified"):
            continue
        score, _stack = easy_score.from_contact_url(url)
        if score < min_score:
            continue
        prev = merged.get(host)
        if prev and int(prev.get("easy_score") or 0) >= score:
            continue
        merged[host] = {
            "url": canonical,
            "easy_score": score,
            "stack": row.get("stack") or "",
            "source": str(row.get("source") or "public-discovery")[:80],
            "profile": str(row.get("profile") or "")[:40],
            "form_verified": True,
        }

    ranked = sorted(merged.values(), key=lambda r: -int(r["easy_score"]))
    if not ranked:
        if feed_refresh:
            state["last_ingest_feed_at"] = feed_stamp
            _save_state(state)
        if room <= 0:
            logger.info("Queue full %s/%s — feed ingest skip", domain_store.queue_depth(), cap)
        else:
            logger.info("Feed has no new eligible hosts (queue=%s/%s)", domain_store.queue_depth(), cap)
        return 0

    staged = 0
    for row in ranked:
        target_pool.stage_candidate(
            str(row["url"]),
            easy_score=int(row["easy_score"]),
            source=str(row.get("source") or "public-discovery"),
            profile=str(row.get("profile") or ""),
            form_verified=True,
        )
        staged += 1
    approved = target_pool.auto_approve()

    added = 0
    for row in ranked:
        if room > 0 and added >= room:
            break
        if domain_store.enqueue(
            str(row["url"]),
            source=str(row.get("source") or "authorized-discovery"),
            easy_score=int(row["easy_score"]),
            authorized_contact=True,
            form_verified=True,
        ):
            added += 1

    state["last_ingest_feed_at"] = feed_stamp
    state["last_ingest_added"] = added
    _save_state(state)
    logger.info(
        "Feed ingest staged=%s approved=%s enqueued=%s (min_score=%s, queue=%s/%s, refresh=%s)",
        staged,
        approved,
        added,
        min_score,
        domain_store.queue_depth(),
        cap,
        feed_refresh,
    )
    return added


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sync_github_feed()
    n = ingest()
    print(f"Ingested {n} feed URL(s). Queue={domain_store.queue_depth()} fuel={domain_store.chromium_fuel_count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
