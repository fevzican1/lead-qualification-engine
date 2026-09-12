"""Lane AF — delivery_worker (teslimat işçisi) unit tests. All offline."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import telegram_sessions
from nirvana import delivery_worker as dw


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect every nirvana state file into a temp dir."""
    monkeypatch.setattr("nirvana.registry.STATE_DIR", tmp_path / "state")
    return tmp_path


@pytest.fixture(autouse=True)
def fake_ok_http(monkeypatch):
    real_get = httpx.get
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200))
    yield
    monkeypatch.setattr(httpx, "get", real_get)


# --- iş açma / karışmama ------------------------------------------------------

def test_unknown_service_is_refused(isolated_state):
    res = dw.start_job(42, "acme.com", "world-domination")
    assert res["ok"] is False and res["reason"] == "unknown_service"


def test_domain_never_bound_to_two_chats(isolated_state):
    """EN ÖNEMLİ KURAL: bir domain yalnızca bir chate bağlıdır; hizmet karışmaz."""
    first = dw.start_job(111, "acme.com", "infra-sweep")
    assert first["ok"] is True
    second = dw.start_job(222, "acme.com", "contact-audit")
    assert second["ok"] is False
    assert second["reason"] == "domain_bound_to_other_chat"
    assert second["owner_chat_id"] == 111


def test_same_job_not_duplicated(isolated_state):
    dw.start_job(111, "acme.com", "infra-sweep")
    res = dw.start_job(111, "acme.com", "infra-sweep")
    assert res["ok"] is False and res["reason"] == "already_active"


def test_quota_full_at_max_concurrent(isolated_state):
    for i, dom in enumerate(("a.com", "b.com", "c.com", "d.com", "e.com")):
        dw.start_job(1 + i, dom, "infra-sweep")
    res = dw.start_job(6, "f.com", "infra-sweep")
    assert res["ok"] is False and res["reason"] == "quota_full"


# --- ödeme kapısı -------------------------------------------------------------

def test_unverified_payment_is_never_delivered(isolated_state, monkeypatch):
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: False)
    job = dw.start_job(42, "acme.com", "infra-sweep")["job"]
    assert job["status"] == "awaiting_payment"
    result = dw.run_batch(notify=False)
    assert result["delivered"] == 0
    assert dw.get_job(job["job_id"])["status"] == "awaiting_payment"
    assert dw.reports_for_chat(42) == []


def test_awaiting_payment_promotes_after_verification(isolated_state, monkeypatch):
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: False)
    job = dw.start_job(42, "acme.com", "infra-sweep")["job"]
    # Müşteri geri döndü, ödeme teyit edildi:
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    result = dw.run_batch(notify=False)
    assert result["delivered"] == 1
    updated = dw.get_job(job["job_id"])
    assert updated["status"] == "delivered"
    assert updated["last_report_id"] == result["reports"][0]
    reports = dw.reports_for_chat(42)
    assert len(reports) == 1 and reports[0]["chat_id"] == 42


def test_run_service_never_runs_another_job_service(isolated_state, monkeypatch):
    """İşin kayıtlı servisi dışındaki handler asla çağrılmaz."""
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)

    def _must_not_run(job):
        raise AssertionError("contact-audit handler ran for an infra-sweep job")

    monkeypatch.setattr(dw, "_svc_contact_audit", _must_not_run)
    job = dw.start_job(42, "acme.com", "infra-sweep")["job"]
    result = dw.run_service(job)
    assert result["status"] == "ok"


def test_unknown_service_in_job_returns_error(isolated_state):
    res = dw.run_service({"domain": "acme.com", "service": "ghost"})
    assert res["status"] == "error"


# --- rapor numaraları / çoklu hizmet ------------------------------------------

def test_report_ids_unique_and_chat_bound(isolated_state, monkeypatch):
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    dw.start_job(111, "acme.com", "infra-sweep")
    dw.start_job(222, "globex.com", "contact-audit")
    result = dw.run_batch(notify=False, limit=5)
    assert result["delivered"] == 2
    reports = dw.load_reports()
    ids = [r["report_id"] for r in reports]
    assert len(ids) == len(set(ids))
    for r in reports:
        assert r["report_id"].startswith("RPT-") and r["chat_id"] in (111, 222)
    assert [r["service"] for r in reports] == ["infra-sweep", "contact-audit"]
    # chat 111 yalnızca kendi raporunu görür
    own = dw.reports_for_chat(111)
    assert len(own) == 1 and own[0]["domain"] == "acme.com"


def test_next_report_id_increases_per_day(isolated_state):
    assert dw.next_report_id([]).endswith("-0001")
    first = dw.next_report_id([])
    rows = [{"report_id": first}]
    second = dw.next_report_id(rows)
    assert second.endswith("-0002") and second != first


def test_renewal_monitor_references_last_report(isolated_state, monkeypatch):
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    dw.start_job(42, "acme.com", "infra-sweep")
    dw.run_batch(notify=False)
    job = dw.start_job(42, "acme.com", "renewal-monitor")
    # aynı chat+domain farklı service → yeni iş açılabilir (hizmet karışmaz)
    assert job["ok"] is True and job["job"]["service"] == "renewal-monitor"
    dw.run_batch(notify=False, limit=5)
    reports = dw.reports_for_chat(42)
    assert len(reports) == 2
    assert "önceki rapor" in " ".join(reports[-1]["findings"])


# --- müşteri hafızası ---------------------------------------------------------

def test_customer_memory_remembers_returning_customer(isolated_state, monkeypatch):
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    dw.start_job(42, "acme.com", "infra-sweep")
    dw.run_batch(notify=False)
    mem = dw.customer_memory(42)
    assert mem["returning"] is True and mem["payment_verified"] is True
    assert mem["last_report"] and mem["report_count"] == 1
    # yabancı chat kendi hafızasını görmez
    other = dw.customer_memory(999)
    assert other["returning"] is False and other["report_count"] == 0


def test_customer_memory_records_past_issues(isolated_state, monkeypatch):
    real_get = httpx.get
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(status_code=503))
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    dw.start_job(42, "acme.com", "infra-sweep")
    dw.run_batch(notify=False)
    monkeypatch.setattr(httpx, "get", real_get)
    mem = dw.customer_memory(42)
    assert mem["past_issues"] and mem["past_issues"][0]["status"] == "down"


# --- tur / bildirim -----------------------------------------------------------

def test_run_batch_respects_notify_off(isolated_state, monkeypatch):
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    dw.start_job(42, "acme.com", "infra-sweep")
    sent = []

    def _record_send(chat_id, text):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(dw, "send_customer_message", _record_send)
    result = dw.run_batch(notify=False)
    assert result["delivered"] == 1 and sent == []


def test_run_limit_keeps_oracle_quota(isolated_state, monkeypatch):
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    for i, dom in enumerate(("a.com", "b.com", "c.com")):
        dw.start_job(100 + i, dom, "infra-sweep")
    result = dw.run_batch(notify=False)   # limit varsayılan 2
    assert result["delivered"] == 2 and result["skipped_over_limit"] == 1
    result2 = dw.run_batch(notify=False)
    assert result2["delivered"] == 1


def test_report_text_contains_ids(isolated_state, monkeypatch):
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    dw.start_job(42, "acme.com", "infra-sweep")
    dw.run_batch(notify=False)
    report = dw.reports_for_chat(42)[0]
    text = dw.report_text(report)
    assert report["report_id"] in text and report["job_id"] in text
    assert "chat 42" in text and "acme.com" in text

