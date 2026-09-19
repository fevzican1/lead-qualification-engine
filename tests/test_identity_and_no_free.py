"""Kimlik imzası (LinkedIn) + ücretsiz-teklif yasağı kapıları.

Kapsanan maddeler:
  26/52/54 — gönderilen form/uygulama ve Telegram açılışında gerçek insan
             kimliği (LinkedIn) görünür.
  57      — müşteriye giden hiçbir metinde ücretsiz/indirim teklifi olmaz.
"""
from __future__ import annotations

import re

import config
import telegram_handoff as handoff
import telegram_sales_bot as bot

LINKEDIN = "https://www.linkedin.com/in/fevzican-aytekin-0b5501105"
FREE_RE = re.compile(
    r"ücretsiz|ucretsiz|free\s*(trial|pilot|preview|audit)|no\s*cost|at\s*no\s*cost|"
    r"indirim|discount|no\s*charge",
    re.I,
)
LINK = "https://t.me/B2B_SalesAssistant_Bot?start=ds12345678"


def _smb_row(*, turkish: bool) -> dict:
    return {
        "session_token": "ds12345678",
        "host": "shop.example",
        "company": "Shop Example",
        "report_id": "DS-2026-12345",
        "turkish": turkish,
        "variant": "A",
        "platform": "",
        "platform_confirmed": False,
        "diagnostics": {"detected_issues": ["REST webhook retry delay"]},
        "lead_info": {"form_page_url": "https://shop.example/contact", "lead_score": "warm"},
    }


def test_smb_form_copy_carries_linkedin_and_no_free_offer(monkeypatch):
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", LINKEDIN)
    for turkish in (True, False):
        _subject, body = handoff.form_copy(
            host="shop.example", hints=["woocommerce"], link=LINK, turkish=turkish,
        )
        assert "linkedin.com/in/fevzican-aytekin" in body
        assert not FREE_RE.search(body), body


def test_enterprise_application_copy_carries_linkedin_and_no_free_offer(monkeypatch):
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", LINKEDIN)
    opportunity = {
        "company": "Acme",
        "evidence": {"demand_quote": "Need integration capacity", "source": "Job board",
                     "source_url": "https://jobs.example/1"},
    }
    for turkish in (True, False):
        _subject, body = handoff.form_copy(
            host="acme.example", hints=[], link=LINK, turkish=turkish,
            audience="enterprise", opportunity=opportunity,
        )
        assert "linkedin.com/in/fevzican-aytekin" in body
        assert not FREE_RE.search(body), body


def test_opener_never_offers_free_work(monkeypatch):
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", LINKEDIN)
    rows = [
        _smb_row(turkish=True),
        _smb_row(turkish=False),
        {**_smb_row(turkish=True), "audience": "enterprise", "variant": "X"},
        {**_smb_row(turkish=False), "audience": "enterprise", "variant": "X"},
    ]
    for hidden in (True, False):
        monkeypatch.setattr(config, "PRICE_HIDDEN", hidden)
        for row in rows:
            text = handoff.opener(row)
            assert not FREE_RE.search(text), (hidden, text)
            assert "linkedin.com/in/fevzican-aytekin" in text


def test_audited_gate_removes_free_offer_from_greeting(monkeypatch):
    cleaned = bot._audited(
        "Ücretsiz deneme başlatabiliriz. Ölçülen kopuk checkout adımında.",
        turkish=True,
    )
    assert "ücretsiz" not in cleaned.lower()
    assert "kopuk" in cleaned.lower()


def test_identity_line_empty_without_linkedin(monkeypatch):
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", "")
    assert handoff.identity_line(True) == ""
    assert handoff.identity_line(False) == ""
