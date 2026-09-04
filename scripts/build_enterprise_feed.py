"""Build feeds/enterprise_targets.json for the Oracle enterprise lane (Faz B).

Runs on GitHub Actions (free, unlimited on public repos). Oracle downloads
the committed file via raw.githubusercontent.com — zero Oracle HTTP budget.
Expansion hook: more contractor/partner application channels can be appended
by scraping public partner directories here; the curated list is the floor.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import enterprise_targets  # noqa: E402


def main() -> int:
    rows = []
    for t in enterprise_targets.TARGETS:
        rows.append(
            {
                "company": t["company"],
                "url": t["url"],
                "platform": t.get("platform", ""),
                "lane": t.get("lane", ""),
            }
        )
    payload = {
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "targets": rows,
    }
    dest = ROOT / "feeds" / "enterprise_targets.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"enterprise feed written: {len(rows)} targets -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
