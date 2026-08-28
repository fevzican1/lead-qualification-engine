"""Run one bounded discovery profile and publish an isolated feed shard."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cc_discover import harvest  # noqa: E402

logger = logging.getLogger(__name__)


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=6000)
    parser.add_argument("--per-page", type=int, default=800)
    parser.add_argument("--deadline", type=int, default=480)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--rotation", type=int, default=None)
    args = parser.parse_args()

    rows: list[dict] = []
    shard_count = max(1, int(args.shard_count))
    shard_index = int(args.shard_index)
    rotation = args.rotation
    if rotation is None and shard_count > 1:
        rotation = int(time.time() // 1800)
    try:
        rows = harvest(
            per_page=max(100, args.per_page),
            deadline_s=max(60.0, float(args.deadline)),
            workers=max(1, min(args.workers, 2)),
            profile=args.profile,
            shard_index=shard_index,
            shard_count=shard_count,
            rotation=rotation,
        )
    except Exception:
        logger.exception("Shard harvest failed")
        out = Path(args.out)
        if out.exists():
            print(f"Keeping previous shard after harvest failure: {out}")
            return 0

    if not rows and Path(args.out).exists():
        print(f"Keeping previous non-empty shard after empty harvest: {args.out}")
        return 0

    rows = rows[: max(100, args.limit)]
    for row in rows:
        row.setdefault("source", args.source)
        row.setdefault("profile", args.profile)
        row["shard_index"] = shard_index
        row["shard_count"] = shard_count
    payload = {
        "version": 1,
        "source": args.source,
        "profile": args.profile,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "rotation": rotation,
        "updated_at": _utc(),
        "count": len(rows),
        "urls": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"Shard {args.source}: {len(rows)} URL(s) -> {out}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
