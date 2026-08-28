"""Merge isolated discovery shards into the single Oracle feed."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import cc_discover  # noqa: E402
import domain_store  # noqa: E402
import easy_score  # noqa: E402

logger = logging.getLogger(__name__)


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rows(payload: Any) -> list[dict[str, Any]]:
    values = payload.get("urls") if isinstance(payload, dict) else payload
    return [row for row in (values or []) if isinstance(row, dict)]


def _candidate(
    row: dict[str, Any],
    fallback_source: str,
    fallback_profile: str = "",
) -> dict[str, Any] | None:
    raw_url = str(row.get("url") or "").strip()
    if not raw_url or not cc_discover._keep(raw_url):
        return None
    host = domain_store.host_of(raw_url)
    if not host:
        return None
    score, stack = easy_score.from_contact_url(raw_url)
    return {
        "url": cc_discover._origin_contact(raw_url),
        "easy_score": int(score),
        "stack": str(row.get("stack") or stack),
        "host": host,
        "source": str(row.get("source") or fallback_source)[:80],
        "profile": str(row.get("profile") or fallback_profile)[:40],
        "shard_index": row.get("shard_index"),
        "shard_count": row.get("shard_count"),
    }


def _better(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return whether left wins, with stable tie-breaking."""
    left_key = (
        int(left.get("easy_score") or 0),
        str(left.get("source") or ""),
        str(left.get("url") or ""),
    )
    right_key = (
        int(right.get("easy_score") or 0),
        str(right.get("source") or ""),
        str(right.get("url") or ""),
    )
    return left_key > right_key


def _valid_shard(payload: Any, path: Path) -> bool:
    """Reject malformed/empty replacements without discarding old good data."""
    if not isinstance(payload, dict):
        logger.warning("Ignoring non-object shard %s", path.name)
        return False
    rows = payload.get("urls")
    if not isinstance(rows, list):
        logger.warning("Ignoring shard without urls list %s", path.name)
        return False
    declared = payload.get("count")
    if declared is not None:
        try:
            count = int(declared)
        except (TypeError, ValueError):
            logger.warning("Ignoring shard with bad count %s", path.name)
            return False
        if count != len(rows):
            logger.warning("Ignoring shard with bad count %s", path.name)
            return False
    shard_count = payload.get("shard_count")
    shard_index = payload.get("shard_index")
    if shard_count is not None:
        try:
            count = int(shard_count)
            index = int(shard_index)
        except (TypeError, ValueError):
            logger.warning("Ignoring shard with invalid identity %s", path.name)
            return False
        if count < 1 or index < 0 or index >= count:
            logger.warning("Ignoring shard with invalid identity %s", path.name)
            return False
    if len(rows) > 10000:
        logger.warning("Ignoring unexpectedly large shard %s", path.name)
        return False
    updated_at = str(payload.get("updated_at") or "").strip()
    if updated_at:
        try:
            datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Ignoring shard with bad timestamp %s", path.name)
            return False
    return bool(rows)


def merge(*, feed_path: Path, shard_dir: Path, cap: int) -> dict[str, Any]:
    previous: dict[str, Any] = {}
    if feed_path.exists():
        try:
            previous = json.loads(feed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid existing feed %s", feed_path)

    by_host: dict[str, dict[str, Any]] = {}
    for row in _rows(previous):
        item = _candidate(
            row,
            str(previous.get("source") or "legacy-feed"),
            str(previous.get("profile") or ""),
        )
        if item is not None and (item["host"] not in by_host or _better(item, by_host[item["host"]])):
            by_host[item["host"]] = item

    for path in sorted(shard_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid shard %s", path.name)
            continue
        if not _valid_shard(payload, path):
            continue
        fallback = str(payload.get("source") or path.stem) if isinstance(payload, dict) else path.stem
        profile = str(payload.get("profile") or "") if isinstance(payload, dict) else ""
        for row in _rows(payload):
            item = _candidate(row, fallback, profile)
            if item is None:
                continue
            host = item["host"]
            if host not in by_host or _better(item, by_host[host]):
                by_host[host] = item

    ranked = sorted(
        by_host.values(),
        key=lambda row: (-int(row.get("easy_score") or 0), str(row.get("host") or "")),
    )[: max(500, cap)]
    old_hosts = {str(row.get("host") or domain_store.host_of(str(row.get("url") or ""))) for row in _rows(previous)}
    old_urls = _rows(previous)
    changed = old_urls != ranked
    return {
        "version": 2,
        "source": "multi-public-discovery",
        "updated_at": _utc() if changed else str(previous.get("updated_at") or _utc()),
        "count": len(ranked),
        "new_hosts": len({str(row["host"]) for row in ranked} - old_hosts),
        "urls": ranked,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed", default=str(ROOT / "feeds" / "ready_queue.json"))
    parser.add_argument("--shards", default=str(ROOT / "feeds" / "shards"))
    parser.add_argument("--cap", type=int, default=60000)
    args = parser.parse_args()
    feed_path = Path(args.feed)
    payload = merge(feed_path=feed_path, shard_dir=Path(args.shards), cap=args.cap)
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = feed_path.with_suffix(feed_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(feed_path)
    print(f"Merged {payload['count']} URL(s), +{payload['new_hosts']} new host(s) -> {feed_path}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
