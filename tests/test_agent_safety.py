from __future__ import annotations

from types import SimpleNamespace

import bounded_agents
import stack_fingerprint
import telegram_handoff


def test_single_platform_marker_stays_neutral() -> None:
    result = stack_fingerprint.fingerprint(html="https://cdn.shopify.com/assets/theme.js")

    assert result["platform"] == ""
    assert result["confidence"] < 95


def test_two_exclusive_platform_markers_can_confirm() -> None:
    result = stack_fingerprint.fingerprint(
        html="https://cdn.shopify.com/assets/theme.js /cdn/shop/t/1/assets/app.js"
    )

    assert result["platform"] == "Shopify"
    assert result["confidence"] >= 95
    assert len(result["evidence"]) >= 2


def test_hook_requires_the_confidence_threshold() -> None:
    assert telegram_handoff.classify_hook(
        platform="Shopify", confidence=94
    )["confirmed"] == "no"
    assert telegram_handoff.classify_hook(
        platform="Shopify", confidence=95
    )["confirmed"] == "yes"


def test_closer_context_cannot_restore_an_unconfirmed_platform() -> None:
    context = bounded_agents.closer_context(
        {
            "url": "https://merchant.example",
            "platform": "Shopify",
            "platform_confidence": 94,
            "platform_evidence": ["cdn.shopify.com", "/cdn/shop/ assets"],
        }
    )

    assert context["platform"] == ""
    assert context["platform_confirmed"] is False


def test_outreach_gate_allows_high_score_discovery() -> None:
    lead = {
        "url": "https://merchant.example/contact",
        "contact_form": {"found": True},
        "easy_score": 80,
    }
    assert bounded_agents.outreach_gate(lead)["allowed"] is True


def test_outreach_gate_blocks_low_score_without_authorization() -> None:
    lead = {
        "url": "https://merchant.example/contact",
        "contact_form": {"found": True},
        "easy_score": 65,
        "authorized_contact": False,
    }
    assert bounded_agents.outreach_gate(lead) == {
        "allowed": False,
        "reason": "below_auto_approve_score",
    }


def test_network_watcher_ignores_redirects_and_analytics() -> None:
    try:
        from form_submitter import FormNetWatcher
    except ModuleNotFoundError:
        return

    page = SimpleNamespace(
        on=lambda *_args: None,
        remove_listener=lambda *_args: None,
        wait_for_timeout=lambda *_args: None,
    )
    watcher = FormNetWatcher(page)

    def response(status: int, url: str, resource_type: str = "xhr"):
        request = SimpleNamespace(
            method="POST",
            url=url,
            resource_type=resource_type,
        )
        watcher._on_response(SimpleNamespace(request=request, status=status))

    response(302, "https://merchant.example/contact")
    assert watcher.hit is False
    response(204, "https://www.google-analytics.com/collect")
    assert watcher.hit is False
    response(201, "https://merchant.example/api/contact")
    assert watcher.hit is True
    assert watcher.status == 201


def test_sales_link_requires_explicit_purchase_intent() -> None:
    try:
        from telegram_sales_bot import _parse_model_output
    except ModuleNotFoundError:
        return

    _reply, send_link = _parse_model_output("PAY: yes\nREPLY: Here is the detail.", "How much?")
    assert send_link is False
    _reply, send_link = _parse_model_output(
        "PAY: yes\nREPLY: I can send the payment link.", "I want to buy and pay."
    )
    assert send_link is True


def test_site_budget_is_hard_capped() -> None:
    try:
        import form_submitter
    except ModuleNotFoundError:
        return

    original = form_submitter.config.SITE_TIMEOUT_SECONDS
    try:
        form_submitter.config.SITE_TIMEOUT_SECONDS = 90
        assert form_submitter._site_budget({}) == 30
    finally:
        form_submitter.config.SITE_TIMEOUT_SECONDS = original


def test_decline_signal_is_detected() -> None:
    try:
        from telegram_sales_bot import _DECLINE_RE
    except ModuleNotFoundError:
        return

    assert _DECLINE_RE.search("İlgilenmiyorum, lütfen tekrar yazmayın")
