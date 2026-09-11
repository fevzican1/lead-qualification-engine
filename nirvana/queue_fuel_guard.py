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
# Acil dolum eşiği: kuyruk bu sayının altına düşerse EMERGENCY_REFILL_REQUIRED.
LOW_WATERMARK = 50
REFILL_TARGET = 200


def _count_rows(name: str) -> tuple[int, list[dict[str, Any]]]:
    path = state_path(name)
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, []
    if isinstance(rows, dict):
        rows = rows.get("routed") or rows.get("targets") or rows.get("rows") or []
    if not isinstance(rows, list):
        return 0, []
    rows = [r for r in rows if isinstance(r, dict)]
    return len(rows), rows


def queue_depth() -> dict[str, int]:
    """Üretim kuyruklarının anlık derinliği."""
    verified_n, _ = _count_rows("verified_queue.json")
    pending_n, _ = _count_rows("tactic_matrix_pending.json")
    routed_n, _ = _count_rows("tactic_matrix.json")
    try:
        bench_raw = json.loads(state_path("enterprise_targets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        bench_raw = []
    bench = bench_raw.get("targets", bench_raw) if isinstance(bench_raw, dict) else bench_raw
    bench_n = len(bench) if isinstance(bench, list) else 0
    return {"verified": verified_n, "matrix_pending": pending_n,
            "matrix_routed": routed_n, "enterprise_targets": bench_n,
            "total": verified_n + pending_n + routed_n}


def check_refill_needed(*, low: int = LOW_WATERMARK) -> dict[str, Any]:
    """Eşik kontrolü: kuyruk < low ise EMERGENCY_REFILL_REQUIRED bayrağı."""
    depth = queue_depth()
    needed = depth["total"] < low
    flag_path = state_path("refill_required.json")
    if needed:
        payload = {"flag": "EMERGENCY_REFILL_REQUIRED", "depth": depth,
                   "low_watermark": low, "target": REFILL_TARGET,
                   "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        tmp = flag_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(flag_path)
    else:
        try:
            flag_path.unlink()
        except OSError:
            pass
    return {"needed": needed, "depth": depth, "low_watermark": low,
            "flag": "EMERGENCY_REFILL_REQUIRED" if needed else None,
            "flag_file": str(flag_path) if needed else None}


def request_refill_via_github(*, reason: str = "low_watermark") -> dict[str, Any]:
    """Oracle bekçisi: GitHub API ile enterprise-feed.yml'i tetikler (ağır iş Actions'ta).

    Token yoksa dry-run döner (yerel test/CI bozulmaz). Token varsa gerçek
    workflow_dispatch POST'u atar — Oracle kotasına dokunmaz.
    """
    import os as _os
    check = check_refill_needed()
    if not check["needed"]:
        return {"dispatched": False, "reason": "queue_healthy", **check}
    token = _os.getenv("GITHUB_TOKEN") or _os.getenv("GH_TOKEN") or ""
    owner = _os.getenv("GITHUB_OWNER", "") or _os.getenv("GITHUB_REPOSITORY_OWNER", "")
    repo_full = _os.getenv("GITHUB_REPOSITORY", "")
    repo = ""
    if "/" in repo_full:
        owner = owner or repo_full.split("/")[0]
        repo = repo_full.split("/")[1]
    else:
        repo = _os.getenv("GITHUB_REPO", "")
    if not token or not owner or not repo:
        return {"dispatched": False, "reason": "no_token_or_repo_env",
                "hint": "Oracle'da GITHUB_TOKEN + GITHUB_OWNER/GITHUB_REPO tanımla", **check}
    try:
        from nirvana.github_orchestrator import dispatch_workflow
        res = dispatch_workflow("enterprise-feed.yml", {}, owner=owner, repo=repo, ref="master")
        res = {"dispatched": bool(res.get("ok")), "dispatch": res,
               "workflow": "enterprise-feed.yml", "trigger_reason": reason, **check}
        log_path = state_path("refill_dispatch_log.json")
        try:
            hist = json.loads(log_path.read_text(encoding="utf-8"))
            if not isinstance(hist, list):
                hist = []
        except (OSError, ValueError):
            hist = []
        hist.append({**res, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        tmp = log_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(hist[-100:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(log_path)
        return res
    except Exception as e:  # noqa: BLE001 — tetikleme hatası guard'ı bozmaz
        return {"dispatched": False, "reason": f"dispatch_error: {e}"[:120], **check}


def fallback_widen(*, target: int = REFILL_TARGET) -> dict[str, Any]:
    """Yedekli boru hattı: ana kaynak boşsa tactic_router kriterlerini genişletir.

    SLOW_MS eşiğini düşürüp (daha çok Taktik A), platform imza listesini
    genişleterek kuyruğu min `target` hedefe tamamlamaya çalışır.
    Saf durum dosyası yazar — maliyet sıfır, Oracle kotasına dokunmaz.
    """
    try:
        from nirvana import tactic_router as _tr
        widened_b = dict(_tr.STACK_SIGNATURES)
        extra = {"Wix": ("wix.com", "wixstatic", "parastorage"),
                 "Squarespace": ("squarespace", "sqsp", "squarespace-cdn"),
                 "Weebly": ("weebly", "weeblycloud"),
                 "BigCommerce": ("bigcommerce", "mybigcommerce", "bigcommerce.com"),
                 "TicimaxX": ("tsoftpanel", "idea-panel")}
        for k, v in extra.items():
            widened_b.setdefault(k, v)
        widened = {"slow_ms": 800, "platforms": sorted(widened_b.keys()),
                   "target": target,
                   "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    except Exception:
        widened = {"slow_ms": 800, "platforms": [], "target": target,
                   "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = state_path("tactic_widen.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(widened, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {**widened, "out": str(path)}


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
    out = {"day": time.strftime("%Y-%m-%d"), "used": _load().get("used", 0),
           "remaining": remaining(), "cap": DAILY_CAP,
           "out": str(state_path(STATE))}
    # Otomatik Acil Yakıt Dolumu: eşik ihlalinde bayrak + (token varsa) GitHub tetikleme.
    try:
        out["refill"] = check_refill_needed()
        out["refill_dispatch"] = request_refill_via_github()
    except Exception as e:  # noqa: BLE001 — guard raporu ana görevi bozmaz
        out["refill_error"] = str(e)[:120]
    return out
