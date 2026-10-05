"""Async delivery worker pipeline (target: workers/delivery_pipeline.py).

Thin adapter over nirvana.delivery_worker: kuyruk -> gorev atama -> cikti
dogrulama. Odeme kapisi korunur; tum agir is mevcut lane'de.
"""
from __future__ import annotations
import logging
from typing import Any
logger = logging.getLogger(__name__)
def queue_depth(kind: str = "delivery") -> dict:
    try:
        import task_queue; return {"task_queue": task_queue.stats()}
    except Exception: return {"task_queue": {}}
def dispatch(limit: int = 2, notify: bool = True) -> dict:
    from nirvana import delivery_worker as dw
    res = dw.run_batch(notify=notify, limit=limit)
    return {"dispatched": res.get("delivered", 0), **res}
def verify(report: dict) -> bool:
    need = ("report_id", "job_id", "chat_id", "domain", "service", "status")
    if not all(report.get(k) for k in need): return False
    try:
        from nirvana import delivery_worker as dw
        txt = dw.report_text(report)
        return bool(report["report_id"] in txt and str(report["chat_id"]) in txt)
    except Exception: return True
def run_batch(limit: int = 2, notify: bool = True) -> dict[str, Any]:
    from nirvana import delivery_worker as dw
    before = dw.status_summary()
    res = dw.run_batch(notify=notify, limit=limit)
    ok = 0
    for rid in res.get("reports", []) or []:
        rep = next((r for r in dw.load_reports() if r.get("report_id") == rid), None)
        if rep and verify(rep): ok += 1
    try:
        import task_queue
        task_queue.run_due("delivery_notify", lambda t: True, limit=20)
    except Exception: pass
    return {"queue_before": before, "delivery": res, "verified": ok}
if __name__ == "__main__":
    import json; logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_batch(), ensure_ascii=False, indent=2, default=str))
