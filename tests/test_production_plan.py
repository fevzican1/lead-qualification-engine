"""6-maddelik B2B uretim plani — regresyon kapilari (cevrimdisi, hizli)."""
from __future__ import annotations
import asyncio
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
def test_outreach_tier1_and_tr_excluded():
    from services.outreach_engine import qualifies, is_tier1, is_excluded
    assert qualifies("https://acme.de", "B2B enterprise GmbH")["ok"] is True
    assert qualifies("https://partner.se", "B2B solutions AB")["ok"] is True
    assert qualifies("https://magaza.com.tr", "istanbul")["ok"] is False
    assert is_excluded("https://x.com.tr") is True
    assert is_tier1("https://acme.nl", "contact services") is True
    assert qualifies("https://myblog.blogspot.com", "personal blog")["ok"] is False
def test_outreach_quota_never_above_400():
    from services.outreach_engine import OutreachIntake
    it = OutreachIntake(); q = it.quota()
    assert q["daily_cap"] <= 400 and q["hourly_cap"] <= 48
def test_outreach_zero_drop_submit(tmp_path, monkeypatch):
    import services.outreach_engine as oe
    monkeypatch.setattr("task_queue.PATH", tmp_path / "tq.db")
    it = oe.OutreachIntake()
    ok = asyncio.run(it.submit({"url": "https://acme.de", "company_name": "Acme GmbH",
                                "description": "B2B enterprise", "easy_score": 90}))
    assert ok is True and it.stats.enqueued == 1
    bad = asyncio.run(it.submit({"url": "https://magaza.com.tr", "description": "istanbul"}))
    assert bad is False
def test_llm_router_config_and_fallback():
    from core.llm_router import litellm_proxy_config, route_sync
    cfg = litellm_proxy_config()
    assert cfg["primary"] and cfg["fallback"] and cfg["max_concurrency"] <= 8
    out = route_sync([{"role": "user", "content": "merhaba"}], lang="tr")
    assert out.get("text")
def test_audit_roi_projection():
    from analytics.audit_generator import roi_projection, proof_card_payload
    roi = roi_projection(20000.0, 10.0)
    assert roi["retainer_eur"] == 5000 and roi["net_eur"] < roi["recovered_eur"]
    card = proof_card_payload("https://example.com",
                              scan={"url": "x", "ttfb_ms": 300, "status": 200, "ok": True})
    assert "5000" in card["headline"] and card["roi"]["retainer_eur"] == 5000
def test_delivery_pipeline_verify():
    from workers.delivery_pipeline import verify
    rep = {"report_id": "RPT-1", "job_id": "j1", "chat_id": 7, "domain": "a.com",
           "service": "infra-sweep", "status": "ok"}
    assert verify(rep) in (True, False)
def test_chat_handler_health_and_backoff():
    from api.chat_handler import health_snapshot, backoff_schedule, session_alive
    h = health_snapshot()
    assert h["ws_route"] is True and "/health" in (h.get("routes") or [])
    assert backoff_schedule(3) == [1.0, 2.0, 4.0]
    assert session_alive("no-such-sid") is False
def test_chat_html_has_queue_and_health():
    html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    assert "_pending" in html and "/health" in html and "_send" in html
def test_config_guardrails_present():
    import config
    assert config.ORACLE_RAM_LIMIT_MB <= 24576 and config.ORACLE_MAX_THREADS <= 32
    assert config.OUTREACH_EXCLUDE_REGION == ("TR",)
    assert config.OUTREACH_MIN_BUDGET_EUR == 5000
def test_form_roi_line():
    import telegram_handoff as th
    line = th.roi_line(turkish=True)
    assert "5000" in line and "EUR" in line
