"""Lane I — watchdog_quota_agent [Oracle VM].

Always-Free guard: watches CPU load, memory and the repo's own HTTP-quota
counters. Near the limits it flips the system into a cooling mode (cooldown
file consumed by the Oracle-side runners) and reports to Telegram. Pure
stdlib on Linux (/proc), graceful no-op elsewhere.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import config
from nirvana.registry import state_path

COOLDOWN_NAME = "cooldown.json"
STATUS_NAME = "watchdog_state.json"
COOLDOWN_MINUTES = 30
# Always-Free VM shapes are tiny; stay far below the ceiling.
CPU_LOAD_THRESHOLD = 0.85   # load / cores
MEM_THRESHOLD_PCT = 0.85
HTTP_QUOTA_THRESHOLD = 0.90  # used / cap


def system_snapshot() -> dict[str, Any]:
    """CPU + memory read without external deps. {'available': False} off-Linux."""
    try:
        cores = os.cpu_count() or 1
        load1, _, _ = os.getloadavg()
    except (AttributeError, OSError):
        return {"available": False}
    mem_used_pct: float | None = None
    try:
        info = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                info[key.strip()] = int(rest.strip().split()[0])  # kB
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        if total > 0:
            mem_used_pct = round((total - available) / total, 4)
    except (OSError, ValueError):
        mem_used_pct = None
    return {"available": True, "load1": round(load1, 3), "cores": cores,
            "load_pct": round(load1 / cores, 4), "mem_used_pct": mem_used_pct}


def quota_snapshot(state_path_override: Any = None) -> dict[str, Any]:
    """Repo HTTP/submit counters vs. caps (knowledge.py lock-aware)."""
    import knowledge
    source = state_path_override or config.ROOT / "knowledge_state.json"
    try:
        state = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    oracle = state.get("oracle") or {}
    daily_cap = knowledge.daily_cap()
    hourly_cap = knowledge.hourly_cap()
    day_used = int(oracle.get("daily_submit_count") or state.get("daily_submit_count") or 0)
    hour_used = int(oracle.get("hourly_submit_count") or state.get("hourly_submit_count") or 0)
    return {
        "daily": {"used": day_used, "cap": daily_cap,
                  "pct": round(day_used / daily_cap, 4) if daily_cap else 0.0},
        "hourly": {"used": hour_used, "cap": hourly_cap,
                   "pct": round(hour_used / hourly_cap, 4) if hourly_cap else 0.0},
    }


def evaluate(snapshot: dict[str, Any], quotas: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if snapshot.get("available"):
        if snapshot.get("load_pct", 0) >= CPU_LOAD_THRESHOLD:
            reasons.append(f"cpu_load={snapshot['load_pct']}")
        mem = snapshot.get("mem_used_pct")
        if mem is not None and mem >= MEM_THRESHOLD_PCT:
            reasons.append(f"mem={mem}")
    for lane in ("daily", "hourly"):
        if quotas[lane]["pct"] >= HTTP_QUOTA_THRESHOLD:
            reasons.append(f"http_{lane}={quotas[lane]['pct']}")
    mode = "cooling" if reasons else "normal"
    return {"mode": mode, "reasons": reasons}


def set_cooling(minutes: int = COOLDOWN_MINUTES) -> dict[str, Any]:
    until = time.time() + minutes * 60
    payload = {"cooling_until": until, "minutes": minutes,
               "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = state_path(COOLDOWN_NAME)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def clear_cooling() -> None:
    path = state_path(COOLDOWN_NAME)
    try:
        path.unlink()
    except OSError:
        pass


def in_cooldown() -> bool:
    try:
        row = json.loads(state_path(COOLDOWN_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return float(row.get("cooling_until") or 0) > time.time()


def run_batch(*, notify: bool = True, dry_run: bool = False) -> dict[str, Any]:
    snap = system_snapshot()
    quotas = quota_snapshot()
    verdict = evaluate(snap, quotas)
    cooling_now = in_cooldown()
    if verdict["mode"] == "cooling" and not cooling_now and not dry_run:
        set_cooling()
        cooling_now = True
    elif verdict["mode"] == "normal" and cooling_now:
        clear_cooling()
        cooling_now = False

    status = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "mode": verdict["mode"], "cooldown_active": cooling_now,
              "reasons": verdict["reasons"], "system": snap, "quotas": quotas}
    path = state_path(STATUS_NAME)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

    if notify:
        msg = (f"[NIRVANA WATCHDOG] mod: {verdict['mode']}\n"
               f"load: {snap.get('load_pct', 'n/a')} mem: {snap.get('mem_used_pct', 'n/a')}\n"
               f"HTTP kota gün/gün-cap: {quotas['daily']['used']}/{quotas['daily']['cap']} "
               f"saat: {quotas['hourly']['used']}/{quotas['hourly']['cap']}\n"
               f"sebep: {', '.join(verdict['reasons']) or '-'}")
        try:
            import owner_notify
            owner_notify.send(msg)
        except Exception:
            pass
    return status
