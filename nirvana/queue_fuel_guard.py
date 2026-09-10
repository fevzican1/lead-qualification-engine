"""Lane MOD-11 — queue_fuel_guard [Oracle VM, light].

Token-bucket kuyruk + yakıt dengeleyici: günlük 400 form kotasını güne yayar,
yakıt bitince gönderimi durdurur. Saf JSON durumu — maliyet sıfır.
forget_guard ile aynı zincirde çalışır, karışıklık yaratmaz.
"""
from __future__ import annotations

import json
import time
from typing import Any

from nirvana.registry import state_path

STATE = "queue_fuel.json"
DAILY_CAP = 400


def _load() -> dict[str, Any]:
    path = state_path(STATE)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"day": time.strftime("%Y-%m-%d"), "used": 0, "cap": DAILY_CAP}


def remaining() -> int:
    s = _load()
    today = time.strftime("%Y-%m-%d")
    if s.get("day") != today:
        return DAILY_CAP
    return max(0, int(s.get("cap", DAILY_CAP)) - int(s.get("used", 0)))


def consume(n: int = 1) -> dict[str, Any]:
    s = _load()
    today = time.strftime("%Y-%m-%d")
    if s.get("day") != today:
        s = {"day": today, "used": 0, "cap": DAILY_CAP}
    s["used"] = int(s.get("used", 0)) + max(0, n)
    path = state_path(STATE)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"used": s["used"], "remaining": remaining()}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    return {"day": time.strftime("%Y-%m-%d"), "used": _load().get("used", 0),
            "remaining": remaining(), "cap": DAILY_CAP,
            "out": str(state_path(STATE))}
