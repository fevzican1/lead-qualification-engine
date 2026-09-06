"""Nirvana payment gate — 2.500 EUR Payoneer retainer link, owner-curated.

No payment API is used: the owner creates the Payoneer request in the provider
panel and stores the link in PAYONEER_PAYMENT_URL. Nirvana flows only surface
that link and validate the configured amount/currency.
"""
from __future__ import annotations

import config

PLACEHOLDER = "[BURAYA_YENI_PAYONEER_LINKINI_EKLEYIN]"


class PaymentLinkMissing(RuntimeError):
    """Raised when PAYONEER_PAYMENT_URL is unset or still holds the placeholder."""


def retainer_amount() -> int:
    return int(config.PAYMENT_AMOUNT)


def retainer_currency() -> str:
    return str(config.PAYMENT_CURRENCY).upper()


def retainer_label() -> str:
    return config.payment_label()


def payment_link() -> str:
    """Return the live Payoneer link; refuse to emit a placeholder."""
    url = (config.PAYONEER_PAYMENT_URL or "").strip()
    if not url or PLACEHOLDER in url:
        raise PaymentLinkMissing(
            "PAYONEER_PAYMENT_URL hâlâ yer tutucu. Gerçek 2.500 EUR Payoneer "
            "talep linkini Oracle /opt/devsolve/.env içine ve repo secret'larına girin."
        )
    return url


def renewal_message(name: str = "") -> str:
    """Monthly retention renewal message (retention_agent uses this)."""
    who = f"{name.strip()} — " if name.strip() else ""
    return (
        f"{who}bu ayki özet: engellenen kesintiler ve çözülen hatalar raporun altında. "
        f"Yenileme: aylık {retainer_label()} retainer. Ödeme talebi: {payment_link()}"
    )
