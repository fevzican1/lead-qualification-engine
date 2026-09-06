"""Lane H — retention_agent [GitHub Actions].

Monthly value report per client (blocked outages / resolved errors from the
delivery history) plus next month's 2.500 EUR renewal Payoneer link. Runs on
GitHub's free cron; only the Telegram notify touches the network.
"""
from __future__ import annotations

import json
import time
from typing import Any

from nirvana.payment import payment_link, retainer_label, renewal_message
from nirvana.registry import state_path

HISTORY_NAME = "delivery_history.json"
DEFAULT_OUT = "retention_{month}.json"


def monthly_stats(history: list[dict[str, Any]], *, year: int, month: int) -> dict[str, Any]:
    """Aggregate one calendar month of delivery sweeps."""
    blocked = resolved = sweeps = 0
    per_domain: dict[str, dict[str, int]] = {}
    for row in history:
        stamp = str(row.get("at") or "")
        if not stamp.startswith(f"{year:04d}-{month:02d}"):
            continue
        sweeps += 1
        for res in row.get("results") or []:
            domain = str(res.get("domain"))
            slot = per_domain.setdefault(domain, {"down": 0, "degraded": 0, "ok": 0})
            slot[res.get("status", "ok")] = slot.get(res.get("status", "ok"), 0) + 1
            if res.get("status") == "down":
                blocked += 1
            elif res.get("status") == "degraded":
                resolved += 1
    return {"year": year, "month": month, "sweeps": sweeps,
            "blocked_outages": blocked, "resolved_or_degraded_events": resolved,
            "per_domain": per_domain}


def build_report(stats: dict[str, Any], client: str = "") -> str:
    head = f"Aylık değer raporu — {client or 'müşteri'} ({stats['year']:04d}-{stats['month']:02d})"
    lines = [
        head,
        f"Engellenen kesinti sinyali: {stats['blocked_outages']}",
        f"İzlenen dalgalanma olayı: {stats['resolved_or_degraded_events']}",
        f"Tamamlanan tur: {stats['sweeps']}",
    ]
    if not stats["per_domain"]:
        lines.append("Bu ay tur verisi yok — rapor yine de düzenli üretilir.")
    return "\n".join(lines)


def run_batch(*, history_path: Any = None, notify: bool = True, client: str = "") -> dict[str, Any]:
    source = history_path or state_path(HISTORY_NAME)
    try:
        history = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        history = []
    now = time.gmtime()
    stats = monthly_stats(history, year=now.tm_year, month=now.tm_mon)
    report = build_report(stats, client=client)
    report += "\n" + renewal_message(client)
    try:
        link = payment_link()
        link_note = f"Yenileme linki canlı: {link}"
    except Exception as exc:  # placeholder not replaced yet — report still ships
        link_note = f"Yenileme linki yer tutucuda: {exc}"
    report += "\n" + link_note

    out_path = state_path(DEFAULT_OUT.format(month=f"{now.tm_year:04d}-{now.tm_mon:02d}"))
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"stats": stats, "report": report}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(out_path)

    sent = False
    if notify:
        try:
            import owner_notify
            sent = owner_notify.send(report)
        except Exception:
            sent = False
    return {"report": report, "stats": stats, "out": str(out_path), "notified": sent,
            "retainer": retainer_label()}
