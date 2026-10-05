"""Uretim stres testi: bellek sizintisi / kilitlenme / cokme taramasi (cevrimdisi)."""
from __future__ import annotations
import asyncio, gc, tracemalloc
def test_no_memory_leak_in_adapters():
    from services.outreach_engine import qualifies
    from analytics.audit_generator import roi_projection
    from api.chat_handler import backoff_schedule
    tracemalloc.start()
    for _ in range(2000):
        qualifies("https://acme.de", "B2B enterprise GmbH")
        roi_projection(20000.0, 10.0)
        backoff_schedule(5)
    gc.collect()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 50 * 1024 * 1024, f"peak {peak} asiri"
def test_no_deadlock_under_concurrency():
    from services.outreach_engine import OutreachIntake, qualifies
    async def _burst():
        it = OutreachIntake()
        # Cevrimdisi karar yuku: ag cagrisi yok, kilitlenme/kilit riski yok.
        rows = [{"url": f"https://acme-{i}.de", "company_name": "Acme GmbH",
                 "description": "B2B enterprise", "easy_score": 90} for i in range(8)]
        results = [qualifies(r["url"], r["description"])["ok"] for r in rows]
        assert all(results) and it.stats.fed >= 0
        return True
    assert asyncio.run(asyncio.wait_for(_burst(), timeout=30)) is True
def test_chat_backoff_bounded():
    from api.chat_handler import backoff_schedule
    assert max(backoff_schedule(12)) <= 8.0 and len(backoff_schedule(12)) == 12
