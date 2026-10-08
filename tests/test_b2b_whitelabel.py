"""B2B whitelabel regresyon kapilari (cevrimdisi, hizli)."""
from __future__ import annotations
import asyncio
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
def test_prompt_isolated_from_router():
    txt = (ROOT / "config" / "prompts" / "agency_partner.txt").read_text(encoding="utf-8")
    assert "Whitelabel Retainer" in txt and "PROOF" in txt
    from core import llm_router as r
    assert r.load_agency_prompt() in txt or len(r.load_agency_prompt()) > 200
    assert r.AGENCY_MODEL == "qwen2.5:3b"
def test_state_machine_flow():
    from core import agency_state as a
    s: dict = {}
    st, data, ch = a.next_state(s, "merhaba ajans olarak ortak olmak istiyoruz")
    assert st == "QUALIFIED" and ch is True
    s = a.set_state({}, st, data)
    st2, data2, ch2 = a.next_state(dict(s, history=[{"role": "user", "content": "Nova Medya"}]), "ajans Nova Medya info@novamedya.com")
    assert st2 == "PARTNER_ONBOARDED"
    assert data2["agency_name"] and "@" in data2["contact_info"]
    st3, _, ch3 = a.next_state({"partner_state": "PARTNER_ONBOARDED", "partner_data": data2}, "baska mesaj")
    assert st3 == "PARTNER_ONBOARDED" and ch3 is False
def test_premium_markets_tr_blocked_and_premium_open():
    from core import premium_markets as pm
    assert pm.qualifies_premium("https://magaza.com.tr", "istanbul", 5000)["ok"] is False
    assert pm.qualifies_premium("https://acme.de", "B2B enterprise GmbH", 5000)["ok"] is True
    assert pm.qualifies_premium("https://partner.se", "B2B solutions AB", 5000)["ok"] is True
    assert pm.qualifies_premium("https://acme.de", "B2B enterprise", 1000)["ok"] is False
    assert pm.qualifies_premium("https://myblog.blogspot.com", "personal blog", 5000)["ok"] is False
    assert pm.is_turkey("https://x.com.tr") is True
    assert pm.is_turkey("https://acme.de", "B2B enterprise GmbH") is False
def test_concurrency_is_five_and_crash_safe():
    from core import llm_router as r
    assert r.MAX_CONCURRENCY == 5
    async def _probe():
        s1 = r._sem()
        s2 = r._sem()
        return (s1 is s2)
    assert asyncio.run(_probe()) is True
def test_proof_demo_triggers():
    from core import proof_demo as p
    assert p.is_skeptic("buna inanmiyorum kanit goster") is True
    assert "domaininizi" in p.demo_reply("tr")
    assert "Crawl4AI + Playwright disposable" in p.demo_reply("tr")
    assert p.normalize_site("ornek.com/x").startswith("https://")
def test_margin_uses_measured_only():
    from core import margin_proof as m
    low = m.monthly_loss_estimate(dom_ms=0, bad_requests=0, slow_resources=0)
    high = m.monthly_loss_estimate(dom_ms=3000, bad_requests=2, slow_resources=2)
    assert high["monthly_high_eur"] > low["monthly_high_eur"]
    assert "5.000 EUR" in m.margin_line({"dom_ms": 2500, "bad_reqs": [1], "slow_res": [1]}, lang="tr")
def test_notifier_message_shape():
    from services import notifier as n
    msg = n.partner_message("Nova", "info@x.com", "https://pay.link/1")
    assert "YENI B2B IS ORTAGI" in msg and "Nova" in msg and "Whitelabel Retainer" in msg
def test_event_dispatcher_isolated():
    import pipeline_loader as pl
    async def _run():
        bus = pl.load().bus
        async def _boom(payload):
            raise RuntimeError("boom")
        async def _ok(payload):
            return "ok"
        bus.on("t-agency", _boom)
        bus.on("t-agency", _ok)
        return await bus.emit("t-agency", {"a": 1})
    out = asyncio.run(_run())
    assert out["handled"] == 2 and out["ok"] == 1 and out["failed"] == 1
def test_b2b_targets_and_ws_guards():
    from core import b2b_targets as b
    assert b.B2B_DAILY_TARGET == 100 and b.PARTNER_TARGET == 100
    src = (ROOT / "webchat_server.py").read_text(encoding="utf-8")
    assert "receive_json(), timeout=300.0" in src
    assert "partner_onboarded" in src and "notify_partner_onboarded" in src
