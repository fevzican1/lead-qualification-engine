"""Tests for Payoneer retainer config: 5.000 EUR retainer + live URL presence."""

import config
import nirvana.payment as p


def test_retainer_amount_is_5000_eur():
    assert config.PAYMENT_AMOUNT == 5000
    assert config.PAYMENT_CURRENCY == "EUR"
    assert p.retainer_amount() == 5000
    assert p.retainer_currency() == "EUR"


def test_payoneer_link_is_live():
    url = config.PAYONEER_PAYMENT_URL
    assert url and url.startswith("https://link.payoneer.com/")
    assert p.PLACEHOLDER not in url


def test_payment_label_contains_eur_5000():
    label = p.retainer_label()
    assert "5000" in label or "5.000" in label
    assert "EUR" in label


def test_payment_link_raises_when_missing():
    p.PLACEHOLDER  # still defined
    old = config.PAYONEER_PAYMENT_URL
    try:
        config.PAYONEER_PAYMENT_URL = "[BURAYA_YENI_PAYONEER_LINKINI_EKLEYIN]"
        try:
            p.payment_link()
            assert False, "should have raised"
        except p.PaymentLinkMissing:
            pass
    finally:
        config.PAYONEER_PAYMENT_URL = old
