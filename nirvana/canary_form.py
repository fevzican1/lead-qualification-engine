"""Lane CF — canary_form [GitHub Actions + Oracle, light].

IP & Shadowban detection: own test form with Akismet + Cloudflare active.
Every 50 external submissions, system auto-sends to canary. Owner verifies
delivery to confirm IP/browser fingerprint is not silently blocked.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

import config
from nirvana.registry import state_path

CANARY_LOG = "canary_log.json"
CANARY_INTERVAL = 50

CANARY_TARGETS = [
    {
        "url": "https://devsolvev2.com/canary/contact",
        "name": "canary-primary",
        "expected_response": "received",
        "email_check": "canary@devsolvev2.com",
    },
]


def canary_targets() -> list[dict[str, Any]]:
    return list(CANARY_TARGETS)


def should_trigger_canary(counter: int) -> bool:
    return counter > 0 and counter % CANARY_INTERVAL == 0


def _fingerprint_ip() -> str:
    try:
        r = httpx.get("https://api.ipify.org", timeout=8)
        return r.text.strip()
    except Exception:
        return "unknown"


def send_canary(timeout: float = 15.0) -> dict[str, Any]:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ip = _fingerprint_ip()
    results: list[dict[str, Any]] = []
    for tgt in CANARY_TARGETS:
        try:
            r = httpx.post(
                tgt["url"],
                data={"name": "Canary", "email": tgt["email_check"],
                      "message": f"canary-ping-{int(time.time())}"},
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 (CanaryBot/1.0)"},
            )
            body = r.text[:200]
            delivered = r.status_code == 200 and tgt["expected_response"] in body.lower()
            results.append({
                "name": tgt["name"], "url": tgt["url"],
                "status_code": r.status_code, "delivered": delivered,
                "ip": ip, "ts": ts,
            })
        except Exception as e:
            results.append({
                "name": tgt["name"], "url": tgt["url"],
                "status_code": 0, "delivered": False,
                "error": str(e)[:100], "ip": ip, "ts": ts,
            })
    return {"ip": ip, "ts": ts, "results": results}


def record_canary(result: dict[str, Any]) -> None:
    path = state_path(CANARY_LOG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = []
    data.append(result)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_batch(*, counter: int = 0, **kwargs: Any) -> dict[str, Any]:
    if not should_trigger_canary(counter):
        return {"fired": False, "counter": counter, "interval": CANARY_INTERVAL}
    result = send_canary()
    record_canary(result)
    all_ok = all(r.get("delivered") for r in result.get("results", []))
    return {"fired": True, "all_delivered": all_ok, **result}
