"""Load the Common Crawl / GitHub Actions feed into the Oracle queue.

GitHub HTTP is not a site probe and does not consume DAILY_HTTP_PROBE_LIMIT.
Public repo: pull via raw.githubusercontent.com (no token).
"""

from __future__ import annotations

import base64
import json
import logging
import time
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


def _refill_below() -> int:
    return int(getattr(config, "QUEUE_REFILL_BELOW", 80) or 80)


def _fuel_thin() -> bool:
    """Fuel below target = the tank needs a burst fill, not a trickle."""
    return domain_store.chromium_fuel_count() < domain_store.chromium_fuel_target()


def _burst_scan_cap(room: int, *, force: bool, fuel_thin: bool) -> int:
    """Feed rows scanned per ingest pass.

    24/48 kept the tank on a trickle (fuel filled over 4+ cycles). When fuel is
    thin, scan enough rows to close the whole deficit in ONE pass so a single
    5-minute feed-sync cycle tops the tank up.
    """
    if fuel_thin:
        fuel = domain_store.chromium_fuel_count()
        target = domain_store.chromium_fuel_target()
        deficit = max(0, target - fuel)
        return max(120, min(480, deficit * 3 + 48))
    return max(room, 48 if force else 24)


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


def _pull_raw_public() -> dict[str, Any] | None:
    url = str(getattr(config, "FEED_RAW_URL", "") or "").strip()
    if not url:
        return None
    # raw.githubusercontent.com CDN can lag master by 30+ minutes; bust cache.
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}ts={int(time.time())}"
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": "devsolve-feed-ingest"},
            timeout=45.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.info("Raw public feed pull failed: %s", exc)
        return None


def _pull_github_api() -> dict[str, Any] | None:
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
        logger.info("GitHub API feed pull skipped: %s", exc)
        return None


def _persist_feed(payload: dict[str, Any]) -> dict[str, Any]:
    rows = _rows_from_payload(payload)
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
    return state


def sync_github_feed() -> dict[str, Any] | None:
    """Pull ready_queue.json — GitHub API first (fresh), raw CDN with cache-bust fallback."""
    candidates: list[dict[str, Any]] = []
    for pull in (_pull_github_api, _pull_raw_public):
        payload = pull()
        if payload and _rows_from_payload(payload):
            candidates.append(payload)
    if not candidates:
        return None
    payload = max(candidates, key=lambda p: str(p.get("updated_at") or ""))
    rows = _rows_from_payload(payload)
    if not rows:
        logger.info("Remote feed empty")
        return None
    state = _persist_feed(payload)
    logger.info("Synced feed rows=%s updated=%s", len(rows), state.get("feed_updated_at"))
    return {
        "count": len(rows),
        "updated_at": state.get("feed_updated_at"),
        "new_hosts": int(payload.get("new_hosts") or 0),
    }


def _row_to_candidate(row: dict[str, Any], *, min_score: int) -> dict[str, Any] | None:
    url = str(row.get("url") or "").strip()
    canonical = domain_store.origin_url(url)
    host = domain_store.host_of(canonical)
    if not host or domain_store.is_enterprise(url) or domain_store.is_noise(url):
        return None
    if not row.get("form_verified"):
        return None
    score, _stack = easy_score.from_contact_url(url)
    if score < min_score:
        return None
    return {
        "url": canonical,
        "easy_score": score,
        "stack": row.get("stack") or "",
        "source": str(row.get("source") or "public-discovery")[:80],
        "profile": str(row.get("profile") or "")[:40],
        "form_verified": True,
    }


def _accept_host(url: str, *, retry_budget: list[int], force_low: bool) -> bool:
    if not domain_store.is_processed(url):
        return True
    if retry_budget[0] <= 0:
        return False
    if domain_store.requeue_if_retryable(url):
        retry_budget[0] -= 1
        return True
    if force_low and domain_store.feed_eligible(url):
        # Cooldown retry for unreachable-only hosts when queue is starving.
        host = domain_store.host_of(url)
        row = (domain_store._processed().get("domains") or {}).get(host or "")
        if isinstance(row, dict) and str(row.get("status") or "") in domain_store.RETRYABLE_NO_SEND:
            if domain_store.requeue_if_retryable(url):
                retry_budget[0] -= 1
                return True
    return False


def ingest(*, limit: int | None = None, force_low: bool = False) -> int:
    """Enqueue score>=FEED_MIN_SCORE contact URLs. Zero Oracle probe budget."""
    min_score = _min_score()
    cap = int(getattr(config, "QUEUE_MAX", 1500) or 1500)
    room = max(0, cap - domain_store.queue_depth())
    if limit is not None:
        room = min(room, max(0, int(limit)))

    low_queue = domain_store.queue_depth() < _refill_below()
    fuel_thin = _fuel_thin()
    force = bool(force_low or low_queue or fuel_thin)

    file_rows, file_meta = _load_file()
    if not file_rows:
        sync_github_feed()
        file_rows, file_meta = _load_file()
    state = _load_state()
    feed_stamp = str(file_meta.get("updated_at") or state.get("feed_updated_at") or "")
    feed_refresh = bool(feed_stamp and feed_stamp != str(state.get("last_ingest_feed_at") or ""))

    merged: dict[str, dict[str, Any]] = {}
    retry_budget = [max(room, 96 if force else 24 if feed_refresh else 8)]

    scan_rows = list(file_rows)
    if force and scan_rows:
        cursor = int(state.get("ingest_cursor") or 0) % len(scan_rows)
        scan_rows = scan_rows[cursor:] + scan_rows[:cursor]

    for row in scan_rows:
        url = str(row.get("url") or "").strip()
        candidate = _row_to_candidate(row, min_score=min_score)
        if not candidate:
            continue
        canonical = str(candidate["url"])
        host = domain_store.host_of(canonical)
        if not host:
            continue
        if domain_store.is_processed(canonical):
            if not _accept_host(canonical, retry_budget=retry_budget, force_low=force):
                continue
            if domain_store.is_processed(canonical):
                continue
        prev = merged.get(host)
        if prev and int(prev.get("easy_score") or 0) >= int(candidate["easy_score"]):
            continue
        merged[host] = candidate
        if len(merged) >= _burst_scan_cap(room, force=force, fuel_thin=fuel_thin):
            break

    ranked = sorted(merged.values(), key=lambda r: -int(r["easy_score"]))
    if not ranked:
        if feed_refresh or force:
            state["last_ingest_feed_at"] = feed_stamp
            if scan_rows:
                state["ingest_cursor"] = (int(state.get("ingest_cursor") or 0) + 32) % len(file_rows)
            _save_state(state)
        if room <= 0:
            logger.info("Queue full %s/%s — feed ingest skip", domain_store.queue_depth(), cap)
        else:
            logger.info(
                "Feed has no new eligible hosts (queue=%s/%s force=%s)",
                domain_store.queue_depth(),
                cap,
                force,
            )
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

    state["last_ingest_feed_at"] = feed_stamp if feed_refresh else state.get("last_ingest_feed_at", feed_stamp)
    state["last_ingest_added"] = added
    if file_rows:
        state["ingest_cursor"] = (int(state.get("ingest_cursor") or 0) + max(added, 1)) % len(file_rows)
    _save_state(state)
    logger.info(
        "Feed ingest staged=%s approved=%s enqueued=%s (min_score=%s, queue=%s/%s, force=%s)",
        staged,
        approved,
        added,
        min_score,
        domain_store.queue_depth(),
        cap,
        force,
    )
    return added


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sync_github_feed()
    n = ingest(
        force_low=domain_store.queue_depth() < _refill_below() or _fuel_thin()
    )
    print(f"Ingested {n} feed URL(s). Queue={domain_store.queue_depth()} fuel={domain_store.chromium_fuel_count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
