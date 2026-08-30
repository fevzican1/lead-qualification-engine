"""Run one bounded 50-slot discovery fleet on a single GitHub runner.

The slots are logical shards, not 50 independent Actions jobs. Keeping the
fleet in one runner makes the 250-slot design affordable and prevents a burst
of concurrent CDX requests and Git commits.
"""

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
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--shard-count", type=int, default=50)
    parser.add_argument("--per-page", type=int, default=800)
    parser.add_argument("--shard-deadline", type=int, default=30)
    parser.add_argument("--fleet-deadline", type=int, default=1320)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    shard_count = max(1, int(args.shard_count))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    completed = 0
    preserved = 0

    for shard_index in range(shard_count):
        remaining = float(args.fleet_deadline) - (time.monotonic() - started)
        if remaining < 5:
            logger.warning("Fleet deadline reached after %s/%s shards", completed, shard_count)
            break
        out = out_dir / f"{args.source}-{shard_index}.json"
        try:
            rows = harvest(
                per_page=max(100, args.per_page),
                deadline_s=max(5.0, min(float(args.shard_deadline), remaining)),
                workers=max(1, min(args.workers, 2)),
                profile=args.profile,
                shard_index=shard_index,
                shard_count=shard_count,
            )
        except Exception:
            logger.exception("Shard %s failed", shard_index)
            rows = []
        if not rows and out.exists():
            preserved += 1
            completed += 1
            continue
        payload = {
            "version": 1,
            "source": args.source,
            "profile": args.profile,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "rotation": int(time.time() // 1800),
            "updated_at": _utc(),
            "count": len(rows),
            "urls": [
                {
                    **row,
                    "source": row.get("source") or args.source,
                    "profile": row.get("profile") or args.profile,
                    "shard_index": shard_index,
                    "shard_count": shard_count,
                }
                for row in rows
            ],
        }
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(out)
        completed += 1
        logger.info("Fleet shard %s/%s wrote %s row(s)", shard_index + 1, shard_count, len(rows))
        # Gentle pacing between shards: hammering CDX back-to-back invites 429s.
        if shard_index + 1 < shard_count and (time.monotonic() - started) < args.fleet_deadline:
            time.sleep(2.0)

    print(
        f"Fleet {args.source}: completed={completed}/{shard_count}, "
        f"preserved={preserved}, elapsed={time.monotonic() - started:.0f}s"
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
