"""leads.json kompaktlastirma — 400/gun hattinin disk/CPU darbogazini keser.

Canli teshis (2026-09-29): /opt/devsolve/leads.json 76 MB'a ulasmisti; her
load_leads/save_leads cagrisi (pipeline + watchdog + dispatch + webchat) bu
dosyayi bastan parse edip yeniden yaziyor ve tur basina dakikalar kaybediliyor,
systemd WatchdogSec kesmeleri olusuyordu. Bu arac:
  - terminal durumdaki (skipped_*, submitted*, failed, waf_*) satirlarin agir
    alanlarini (page_excerpt ve benzeri) 200 karaktere kirpar,
  - keep-days'ten eski terminal satirlari tamamen duser (ozet sayilar korunur),
  - URL bazinda mukerrer kayitlari tekillestirir (en guncel satir kalir),
  - atomik yazar ve once yedek alir.

Kullanim:
    python scripts/compact_leads.py                       # kuru calisma raporu
    python scripts/compact_leads.py --apply               # yedekle + yaz
    python scripts/compact_leads.py --path /opt/devsolve/leads.json --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEAVY_FIELDS = ("page_excerpt", "raw_text", "html", "page_html", "body_text")
TERMINAL_PREFIXES = ("skipped_", "submitted", "failed", "waf_", "opted_out")


def _parse_ts(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        stamp = datetime.fromisoformat(text)
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def compact(path: Path, *, keep_days: int, apply: bool) -> dict[str, object]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("leads.json bir liste degil — dokunulmadi")
    before = path.stat().st_size
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    floor = datetime.min.replace(tzinfo=timezone.utc)
    seen: dict[str, dict] = {}
    dropped_old = trimmed = merged = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        status = str(row.get("status") or "")
        stamp = _parse_ts(row.get("updated_at"))
        terminal = status.startswith(TERMINAL_PREFIXES)
        if terminal and stamp is not None and stamp < cutoff:
            dropped_old += 1
            continue
        if terminal:
            for field in HEAVY_FIELDS:
                value = str(row.get(field) or "")
                if len(value) > 200:
                    row[field] = value[:200]
                    trimmed += 1
        key = url or f"__nourl_{len(seen)}"
        prev = seen.get(key)
        if prev is not None:
            merged += 1
            if (stamp or floor) >= (_parse_ts(prev.get("updated_at")) or floor):
                seen[key] = row
            continue
        seen[key] = row
    out = list(seen.values())
    payload = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
    after = len(payload.encode("utf-8"))
    stats: dict[str, object] = {
        "before_rows": len(rows),
        "kept_rows": len(out),
        "dropped_old_terminal": dropped_old,
        "trimmed_heavy_fields": trimmed,
        "merged_duplicates": merged,
        "before_mb": round(before / 1_048_576, 2),
        "after_mb": round(after / 1_048_576, 2),
        "saved_pct": round((1 - after / max(1, before)) * 100, 1),
    }
    if apply:
        backup = path.with_suffix(path.suffix + f".{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(path, backup)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
        stats["backup"] = str(backup)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(Path(__file__).resolve().parent.parent / "leads.json"))
    ap.add_argument("--keep-days", type=int, default=30)
    ap.add_argument("--apply", action="store_true", help="yedekle ve dosyayi yaz")
    args = ap.parse_args()
    target = Path(args.path)
    if not target.exists():
        raise SystemExit(f"dosya yok: {target}")
    stats = compact(target, keep_days=args.keep_days, apply=args.apply)
    mode = "UYGULANDI" if args.apply else "KURU CALISMA"
    print(f"[{mode}] {target}")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
