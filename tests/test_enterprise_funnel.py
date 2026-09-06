from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import enterprise_apply as apply
import enterprise_quality as quality
import enterprise_targets as targets
import feed_ingest
import payment_safety
import proof_card
import telegram_handoff as handoff
import telegram_sales_bot as bot
import telegram_sessions as sessions
from scripts import enterprise_demand_feed as discovery


def target():
    now = datetime.now(timezone.utc).isoformat()
    return {"company": "Example Labs", "url": "https://jobs.example.org/application/1", "platform": "",
            "lane": "contractor-application", "location_eligible": True, "form_verified": True,
            "channel_purpose": "contractor_application", "priority_score": 80,
            "evidence": {"source_url": "https://remotive.com/remote-jobs/1", "published_at": now,
                         "scanned_at": now, "form_url": "https://jobs.example.org/application/1",
                         "form_action": "https://jobs.example.org/application/1",
                         "demand_quote": "Contract Python automation engineer needed immediately",
                         "channel_quote": "Apply for contract automation engineer"}}


def payload(rows):
    return {"schema_version": 2, "scanned": True, "updated_at": datetime.now(timezone.utc).isoformat(), "targets": rows}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "LEADS_PATH", tmp_path / "leads.json")
    monkeypatch.setattr(config, "OPTOUTS_PATH", tmp_path / "optouts.json")
    monkeypatch.setattr(handoff, "PATH", tmp_path / "handoffs.json")
    monkeypatch.setattr(sessions, "PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(apply, "STATE_PATH", tmp_path / "applications.json")
    monkeypatch.setattr(payment_safety, "PATH", tmp_path / "payment_readiness.json")
    monkeypatch.setattr(proof_card, "CACHE", tmp_path / "cards")
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL", "https://link.payoneer.com/example")
    monkeypatch.setattr(config, "PRICE_USD", 2500)
    monkeypatch.setattr(config, "PRICE_HIDDEN", True)
    monkeypatch.setattr(config, "SMB_LANE_ENABLED", False)
    monkeypatch.setattr(config, "ENTERPRISE_MODE", True)
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "ExampleTestBot")
    return tmp_path


@pytest.mark.parametrize("field,value", [("form_verified", False), ("channel_purpose", "sales"),
                                         ("location_eligible", False), ("url", "http://localhost")])
def test_feed_rejects_unqualified_rows(field, value):
    row = target()
    row[field] = value
    assert not quality.valid_payload(payload([row]))


@pytest.mark.parametrize("key,value", [("scanned_at", "2020-01-01T00:00:00Z"),
                                      ("demand_quote", "We sell automation tools"),
                                      ("channel_quote", "Contact sales to apply for a demo"),
                                      ("published_at", "bad timestamp")])
def test_evidence_gate(key, value):
    row = target()
    row["evidence"][key] = value
    assert not quality.eligible(row)


def test_only_scanned_feed_no_curated_fallback(isolated):
    folder = isolated / "feeds"
    folder.mkdir()
    path = folder / "enterprise_targets.json"
    path.write_text(json.dumps({"scanned": True, "targets": targets.TARGETS}))
    assert targets.load_all() == []
    first = target()
    other = copy.deepcopy(first)
    other["url"] = other["evidence"]["form_url"] = "https://different-ats.example.net/apply"
    path.write_text(json.dumps(payload([first, other])))
    assert len(targets.load_all()) == 1
    assert targets.load_all(0) == []


def test_empty_verified_feed_replaces_stale_targets(isolated, monkeypatch):
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload([]))
    monkeypatch.setattr(feed_ingest.httpx, "get", lambda *a, **kw: response)
    assert feed_ingest.sync_enterprise_feed()["count"] == 0
    assert targets.load_all() == []


def test_scan_failure_never_publishes_fallback(isolated, monkeypatch):
    folder = isolated / "feeds"
    folder.mkdir()
    path = folder / "enterprise_targets.json"
    path.write_text("old")
    monkeypatch.setattr(discovery, "ROOT", isolated)
    monkeypatch.setattr(discovery.sys, "argv", ["build", "--scan"])
    monkeypatch.setattr(discovery.httpx, "get", lambda *a, **kw: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"jobs": []}))
    def fail(_rows):
        raise RuntimeError("browser failed")
    monkeypatch.setattr(discovery, "scan_targets", fail)
    assert discovery.main() == 1
    assert path.read_text() == "old"


def test_demand_filter_contract_location_and_age():
    job = {"company_name": "Example Labs", "url": "https://remotive.com/remote-jobs/1",
           "title": "Urgent Python automation", "job_type": "contract", "candidate_required_location": "Worldwide",
           "publication_date": datetime.now(timezone.utc).isoformat(), "description": "API workflow"}
    assert discovery.demand_candidates([job])[0]["priority_score"] == 80
    for change in [{"job_type": "full_time"}, {"candidate_required_location": "USA"},
                   {"publication_date": "2020-01-01"}, {"description": "Position closed"}]:
        assert discovery.demand_candidates([{**job, **change}]) == []


def test_retry_zero_and_alternate_url_dedup(isolated, monkeypatch):
    row = target()
    monkeypatch.setattr(config, "ENTERPRISE_RETRY_SKIP_DAYS", 0)
    monkeypatch.setattr(targets, "load_all", lambda: [row])
    assert apply.retry_days_for({}) == 0
    for status in ["submitted_confirmed", "failed", "skipped_submit_failed", "submitting"]:
        apply._save_state({"old-url": {"company": row["company"], "url": "https://another.example/apply",
                                      "last_status": status, "last_at": "2020-01-01T00:00:00Z"}})
        assert apply.eligible_targets(2) == []


def test_attempt_caps_cross_midnight_and_history(monkeypatch):
    fixed = datetime(2026, 9, 6, 0, 10, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(apply, "datetime", Clock)
    history = [(fixed - timedelta(minutes=n)).isoformat() for n in (5, 20, 25)]
    assert apply.enterprise_counts({"x": {"last_status": "skipped_no_open_form", "attempts_at": history}}) == (1, 3)
    assert apply.enterprise_counts({}) == (0, 0)


def test_zero_quota_and_corrupt_ledger_fail_closed(isolated, monkeypatch):
    monkeypatch.setattr(config, "ENTERPRISE_DAILY_CAP", 0)
    assert apply.run_batch()["ran"] is False
    apply.STATE_PATH.write_text("broken")
    with pytest.raises(RuntimeError):
        apply.run_batch()


def test_form_token_brief_card_identity(isolated):
    row = target()
    lead = targets.target_lead(row)
    token = handoff.remember(lead, company=row["company"], pain="Proposed scope", quote="", turkish=False)
    record = handoff.lookup(token)
    subject, body = handoff.form_copy(host=lead["identity_url"], hints=[], link=config.telegram_deeplink(token),
                                      turkish=False, audience="enterprise", opportunity=row)
    assert record["report_id"] in subject and record["report_id"] in body
    assert token in body
    assert record["diagnostics"]["detected_issues"] == []
    assert record["report_id"] in handoff.brief_block(record)
    assert record["report_id"] in proof_card.caption(record, turkish=False)
    assert "$" not in body and "zero-risk" not in body
    assert proof_card.render(record, turkish=False).exists()
    other = {**row, "company": "Other Labs"}
    assert quality.identity_url(other) != lead["identity_url"]
    assert quality.identity_url({**row, "url": "https://other.example/apply"}) == lead["identity_url"]


@pytest.mark.parametrize("text", ["approved", "onboard us", "we are not ready to pay", "Do you use Payoneer?",
                                  "we accept applications", "if we accept your terms", "not interested, send payment link"])
def test_ambiguous_interest_is_not_purchase(text):
    assert not bot._wants_to_buy(text)


@pytest.mark.parametrize("text", ["we accept your terms", "go ahead with the pilot", "onaylıyoruz",
                                  "pilot'a başlayalım", "I want to buy and pay."])
def test_explicit_interest(text):
    assert bot._wants_to_buy(text)


def test_payment_readiness_bound_to_actual_url_and_amount(isolated):
    assert payment_safety.ready_request(42) is None
    payment_safety.approve_link(chat_id=42, amount=2500, currency="USD", recipient="ExampleRecipient", reference="REQ-1", owner_id=12)
    assert payment_safety.ready_request(42)["amount"] == 2500
    assert payment_safety.ready_request(43) is None
    config.PRICE_USD = 5000
    assert payment_safety.ready_request(42) is None


def test_report_is_not_payment_and_fulfillment_requires_contract(isolated):
    sessions.touch_start(42, company="Example", turkish=False)
    sessions.mark_payment_confirmed(42)
    assert sessions._row(42)["payment_reported"]
    assert not sessions.fulfillment_ready(42)
    sessions._put(42, payment_request={"amount": 2500, "currency": "USD"})
    sessions.mark_payment(42)
    with pytest.raises(ValueError):
        sessions.verify_payment(42, amount=5000, currency="USD", reference="TX-1", owner_id=12)
    sessions.verify_payment(42, amount=2500, currency="USD", reference="TX-1", owner_id=12)
    assert not sessions.fulfillment_ready(42)
    sessions.approve_contract(42, contract_ref="C1", scope_ref="S1", access_ref="A1", owner_id=12)
    assert sessions.fulfillment_ready(42)
    sessions.mark_declined(42)
    assert not sessions.fulfillment_ready(42)


def test_enterprise_followup_stops(isolated):
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    sessions._put(42, started_at=old, last_at=old, audience="enterprise")
    assert sessions.due_followups() == []


def test_hidden_price_is_shown_when_explicit(isolated):
    assert config.price_label() == ""
    assert config.price_label(explicit=True) == "$2500 USD"


def test_non_owner_cannot_verify_payment(isolated, monkeypatch):
    monkeypatch.setattr(bot, "_is_owner", lambda cid: False)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=42, type="private"),
                             effective_user=SimpleNamespace(id=42), message=SimpleNamespace(reply_text=AsyncMock()))
    asyncio.run(bot.cmd_verifypayment(update, SimpleNamespace(args=["42", "2500", "USD", "TX1"])))
    assert sessions._row(42) == {}


def test_explicit_price_and_interest_paths_skip_model(isolated, monkeypatch):
    monkeypatch.setattr(bot, "_is_owner", lambda cid: False)
    monkeypatch.setattr(bot, "_hot_ping", AsyncMock())
    monkeypatch.setattr(bot.owner_notify, "send", lambda *a, **kw: True)
    monkeypatch.setattr(bot.optout, "is_chat_opted_out", lambda cid: False)
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=999, type="private"),
                             effective_user=SimpleNamespace(id=999, language_code="en", username="test"),
                             message=SimpleNamespace(text="How much?", reply_text=reply))
    asyncio.run(bot.on_text(update, SimpleNamespace(bot=None)))
    assert "$2500 USD" in reply.call_args.args[0]
    update.message.text = "I want to buy and pay."
    asyncio.run(bot.on_text(update, SimpleNamespace(bot=None)))
    assert "link.payoneer.com" not in reply.call_args.args[0]
    assert not sessions.is_payment_sent(999)
    assert not sessions.fulfillment_ready(999)


def test_binding_cannot_mix_two_companies(isolated):
    first = targets.target_lead(target())
    second = targets.target_lead({**target(), "company": "Other Labs"})
    t1 = handoff.remember(first, company="Example Labs", pain="", quote="", turkish=False)
    t2 = handoff.remember(second, company="Other Labs", pain="", quote="", turkish=False)
    assert bot._bind_token(321, t1)
    assert bot._bind_token(321, t2) is None
    assert sessions._row(321)["session_token"] == t1


def test_batch_reserves_attempts_and_respects_two_per_hour(isolated, monkeypatch):
    import form_submitter
    monkeypatch.setattr(config, "ENTERPRISE_DAILY_CAP", 100)
    monkeypatch.setattr(config, "ENTERPRISE_HOURLY_CAP", 100)
    monkeypatch.setattr(config, "ENTERPRISE_RETRY_SKIP_DAYS", 0)
    monkeypatch.setattr(apply.knowledge, "submit_counts", lambda: (0, 0))
    monkeypatch.setattr(apply.knowledge, "daily_cap", lambda: 400)
    monkeypatch.setattr(apply.knowledge, "hourly_cap", lambda: 32)
    monkeypatch.setattr(apply.optout, "is_url_opted_out", lambda url: False)
    monkeypatch.setattr(apply.time, "sleep", lambda _: None)
    monkeypatch.setattr(bot.owner_notify, "send", lambda *a: True)
    monkeypatch.setattr(targets, "load_all", lambda: [target(), {**target(), "company": "Other Labs"}])
    seen = []
    def submit(lead, **kwargs):
        assert any(r["last_status"] == "submitting" for r in apply._load_state().values())
        seen.append(lead["url"])
        return {"status": "skipped_no_open_form"}
    monkeypatch.setattr(form_submitter, "submit_lead", submit)
    assert apply.run_batch()["skipped"] == 2
    assert apply.run_batch()["ran"] is False
    assert len(seen) == 2
    assert apply.enterprise_counts() == (2, 2)


def test_global_caps_cannot_expand_and_zero_stops(monkeypatch):
    import knowledge
    monkeypatch.setattr(knowledge, "oracle_lock", lambda: {"daily_submit_limit": 900, "hourly_submit_limit": 900})
    monkeypatch.setattr(config, "DAILY_SUBMIT_LIMIT", 900)
    monkeypatch.setattr(config, "HOURLY_SUBMIT_LIMIT", 900)
    assert (knowledge.daily_cap(), knowledge.hourly_cap()) == (400, 32)
    monkeypatch.setattr(config, "HOURLY_SUBMIT_LIMIT", 0)
    assert knowledge.hourly_cap() == 0


def test_price_request_does_not_send_link_and_payment_report_stays_unverified(isolated, monkeypatch):
    monkeypatch.setattr(bot, "_is_owner", lambda cid: False)
    monkeypatch.setattr(bot, "_hot_ping", AsyncMock())
    monkeypatch.setattr(bot.owner_notify, "send", lambda *a, **kw: True)
    monkeypatch.setattr(bot.optout, "is_chat_opted_out", lambda cid: False)
    sessions.touch_start(997, company="Example", turkish=False)
    sessions.mark_payment(997)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=997, type="private"),
                             effective_user=SimpleNamespace(id=997, language_code="en", username="test"),
                             message=SimpleNamespace(text="payment sent", reply_text=AsyncMock()))
    asyncio.run(bot.on_text(update, SimpleNamespace(bot=None)))
    assert sessions._row(997)["payment_reported"]
    assert not sessions._row(997).get("payment_verified")
    assert not sessions.fulfillment_ready(997)


def test_cold_enterprise_intake_does_not_claim_a_review(isolated):
    assert "have not inspected" in bot._cold_intro(turkish=False)
    assert "ENTERPRISE APPLICATION BRIEF" in bot._closer_brief(None)
    reply, pay = bot._offline_reply("Hello", None)
    assert "have not inspected" in reply and not pay