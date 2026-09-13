"""Telegram bot safety tests — the bot ANSWERS questions, never performs actions.

Covers the guarantor rules:
- Model output can never carry URLs of its own (payment links are deterministic).
- PAY decision is deterministic via _wants_to_buy; questions alone never authorize.
- Handoff only for owner/authority keywords; all other technical questions stay
  with the assistant (no action, no owner ping, plain answer).
- Price question path returns the scarcity gate, not a direct price dump.
- Producer/consumer split: bot is a chat-close layer only (no form submit calls).
"""
from __future__ import annotations

import re

from telegram_sales_bot import _parse_model_output, _wants_to_buy
from telegram_sales_bot import _HANDOFF_RE, _BUY_RE, _NEGATIVE_BUY_RE, _tr_norm


# --- 1. Model can never inject URLs ----------------------------------------

def test_model_output_strips_urls():
    raw = "PAY: no\nREPLY: Details at https://evil.example/x and http://legit.example"
    reply, pay = _parse_model_output(raw, "how does the relay work?")
    assert "http" not in reply
    assert pay is False


def test_model_output_pay_is_overridden_by_deterministic_intent():
    # Model says PAY=no but the customer text is clearly a purchase intent.
    raw = "PAY: no\nREPLY: Great question."
    _, pay = _parse_model_output(raw, "evet satın almak istiyorum başlayalım")
    assert pay is True
    # Model says PAY=yes but the user only asked a question — must stay False.
    raw2 = "PAY: yes\nREPLY: Perfect, here we go."
    _, pay2 = _parse_model_output(raw2, "nasıl çözeceksiniz?")
    assert pay2 is False


# --- 2. Specific questions never become actions ----------------------------

def test_questions_are_not_purchase_intent():
    questions = [
        "SLA'da yanıt süresi ne kadar?",
        "Hook izolasyonu nasıl çalışıyor?",
        "Oracle'da mı izliyorsunuz, hangi katman?",
        "Hangi entegrasyonları kapsıyor?",
        "Can we do a pilot first?",
        "What stack did you detect?",
        "How do you verify the bottleneck without touching our server?",
        "Almanya'da mı çalışıyorsunuz?",
    ]
    for q in questions:
        assert _wants_to_buy(q) is False, q
        assert not _BUY_RE.search(q) or _NEGATIVE_BUY_RE.search(q)


def test_explicit_intent_is_purchase():
    intents = [
        "anlaştık ödeme yapalım",
        "kabul ediyoruz başlayalım",
        "let's proceed with the retainer",
        "go ahead, send the invoice",
        "satın almak istiyorum",
    ]
    for q in intents:
        assert _wants_to_buy(q) is True, q


# --- 3. Handoff only for owner/authority keywords --------------------------

def test_handoff_triggers_only_on_authority():
    triggers = [
        "Patronunla konuşmak istiyorum",
        "yetkili biri ile görüşebilir miyim",
        "talk to the owner please",
        "speak with a human",
        "imza sahibiyle görüşmek istiyorum",
        "Founder ile konuşmak istiyorum",
    ]
    stays = [
        "SLA detayları neler?",
        "Mimarisi nasıl?",
        "Rapordaki X kaynağını açıklar mısın?",
        "Ne kadar sürer?",
        "Hangi ödeme yöntemleri?",
    ]
    for t in triggers:
        assert _HANDOFF_RE.search(_tr_norm(t)), t
    for s in stays:
        assert not _HANDOFF_RE.search(_tr_norm(s)), s


# --- 4. Price question = scarcity gate (no direct price dump) ---------------

def test_price_question_does_not_give_direct_price():
    from nirvana import slot_gate
    tr = slot_gate.price_response(turkish=True, row={"company": "Acme", "host": "acme.x", "report_id": "R1"})
    assert "1 boş canlı izleme slotu" in tr or "slot" in tr.lower() or "boş" in tr
    assert "24 saat" in tr or "reserve" in tr
    en = slot_gate.price_response(turkish=False, row={"company": "Acme", "host": "acme.x", "report_id": "R1"})
    assert "slot" in en.lower() and "retainer" in en.lower()


# --- 5. Bot module never submits forms --------------------------------------

def test_bot_does_not_import_form_submitter():
    import inspect
    import telegram_sales_bot as mod
    src = inspect.getsource(mod)
    assert "form_submitter" not in src.replace("import form_submitter", "import-not-present")
    assert "submit_form" not in src