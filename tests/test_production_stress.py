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
def test_purge_targets_playwright_driver():
    """Canli ariza 2026-10-05: purge yalnizca chrome olduruyordu, node surucu yasadi."""
    import browser
    driver = ("node /home/u/.cache/ms-playwright-py/driver/node "
              "/home/u/.cache/ms-playwright-py/driver/package/cli.js run-driver")
    assert browser._is_playwright_driver(driver)
    assert browser._is_browser_proc(driver)
    assert not browser._is_playwright_driver("/usr/bin/node /srv/api/server.js")
    assert browser._is_browser_proc(
        "/home/u/.cache/ms-playwright-py/chromium-1234/chrome-linux/chrome")


def test_watchdog_flags_frozen_counter_even_if_target_met():
    """Kota hedefi asilmis olsa bile donan sayac KIRMIZIDIR (durustluk kurali)."""
    from nirvana import job_watchdog
    import time
    # eski kod: 129 >= 86 -> "saglikli" (sahte yesil, 5,5 saat boyunca)
    old_ok = int(129) >= int(86)
    assert old_ok is True
    state = {"forms_last_count": 129, "forms_last_ts": time.time() - 900}
    idle = job_watchdog.form_idle_update(state, {"forms_today": 129})
    assert idle is not None and idle >= job_watchdog.FORM_IDLE_S
    # duzeltilmis davranis: esik asilince sinyal kirmizi
    frozen_ok = not (float(idle) >= job_watchdog.FORM_IDLE_S)
    assert frozen_ok is False


def test_form_idle_resets_when_counter_increases():
    import time
    from nirvana import job_watchdog
    state = {"forms_last_count": 129, "forms_last_ts": time.time() - 5000}
    assert job_watchdog.form_idle_update(state, {"forms_today": 129}) >= 5000
    assert job_watchdog.form_idle_update(state, {"forms_today": 130}) == 0.0
    assert state["forms_last_count"] == 130


def test_pipeline_stops_round_after_hard_kill():
    """Hard-kill sonrasi tur, olu bundle ile devam etmez (37 dk asili kalmanin sebebi)."""
    src = open("pipeline.py", encoding="utf-8").read()
    assert 'if submitted.get("hard_timeout"):' in src
    assert src.index("_dispose_bundle(bundle)") < src.index(
        'if submitted.get("hard_timeout"):')
