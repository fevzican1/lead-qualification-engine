"""Lane G — delivery_runner [Oracle VM].

Post-payment service lane: weekly scheduled sweeps of each client's public
endpoints. Light by design (1 request per client per sweep), report goes to
the ops/customer Telegram channel. No SMTP from this host.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

import owner_notify
from nirvana.registry import state_path

CLIENTS_PATH_NAME = "clients.json"
HISTORY_NAME = "delivery_history.json"
TIMEOUT = 15.0
DEGRADED_MS = 1500


def load_clients(path: Any = None) -> list[dict[str, Any]]:
    source = path or state_path(CLIENTS_PATH_NAME)
    try:
        rows = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("domain")]


def check_domain(domain: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """One lightweight probe. Status: ok | degraded | down."""
    started = time.monotonic()
    try:
        if client is not None:
            r = client.get(f"https://{domain}/", timeout=TIMEOUT)
            elapsed = int((time.monotonic() - started) * 1000)
            status_code = r.status_code
        else:
            r = httpx.get(f"https://{domain}/", timeout=TIMEOUT,
                          headers={"User-Agent": "nirvana-delivery/1.0"})
            elapsed = int((time.monotonic() - started) * 1000)
            status_code = r.status_code
        if status_code >= 500:
            state = "down"
        elif status_code >= 400 or elapsed > DEGRADED_MS:
            state = "degraded"
        else:
            state = "ok"
        return {"domain": domain, "status": state, "http": status_code, "ms": elapsed}
    except httpx.HTTPError:
        return {"domain": domain, "status": "down", "http": 0,
                "ms": int((time.monotonic() - started) * 1000)}


def build_report(results: list[dict[str, Any]], *, turkish: bool = True) -> str:
    lines = []
    counts = {"ok": 0, "degraded": 0, "down": 0}
    for res in results:
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        badge = {"ok": "✅", "degraded": "⚠️", "down": "🔴"}.get(res["status"], "•")
        lines.append(f"{badge} {res['domain']} — {res['status']} ({res['ms']} ms, HTTP {res['http']})")
    head = ("Haftalık altyapı turu — "
            f"{counts['ok']} sağlıklı / {counts['degraded']} dalgalı / {counts['down']} kesinti"
            if turkish else
            "Weekly infrastructure sweep — "
            f"{counts['ok']} ok / {counts['degraded']} degraded / {counts['down']} down")
    body = "\n".join(lines) if lines else ("hedef tanımlı değil" if turkish else "no targets")
    return f"{head}\n{body}"


def run_batch(*, clients_path: Any = None, notify: bool = True, limit: int = 25) -> dict[str, Any]:
    clients = load_clients(clients_path)[:limit]
    results = [check_domain(str(c["domain"])) for c in clients]
    report = build_report(results)
    history_path = state_path(HISTORY_NAME)
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        history = []
    history.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "results": results, "report": report})
    tmp = history_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(history[-100:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(history_path)
    sent = False
    if notify and results:
        sent = owner_notify.send(report)
    return {"checked": len(results), "report": report, "notified": sent, "history": str(history_path)}
