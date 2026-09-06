"""Owner-attested Payoneer request readiness; no payment API or charge creation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

import config
import enterprise_quality

PATH = config.ROOT / "payment_readiness.json"


def _fingerprint() -> str:
    return hashlib.sha256(config.PAYONEER_PAYMENT_URL.strip().encode()).hexdigest()


def _provider_url() -> bool:
    try:
        url = config.PAYONEER_PAYMENT_URL.strip()
        host = urlsplit(url).hostname or ""
        return enterprise_quality.public_https(url) and (host == "payoneer.com" or host.endswith(".payoneer.com"))
    except ValueError:
        return False


# Nirvana retainer currency (config.PAYMENT_CURRENCY, default EUR) plus the
# legacy USD lane. The numeric amount must still match the configured offer
# (PRICE_USD == PAYMENT_AMOUNT == 2500); only the currency lane is widened.
def accepted_currencies() -> set[str]:
    return {"USD", "EUR", config.PAYMENT_CURRENCY.upper()} - {""}


def amount_matches(amount: int) -> bool:
    return int(amount) == config.PRICE_USD


def approve_link(*, chat_id: int, amount: int, currency: str, recipient: str, reference: str, owner_id: int) -> None:
    """Owner must inspect the actual provider request, recipient and account eligibility first."""
    if (not _provider_url() or not amount_matches(amount) or amount not in {2500, 5000}
            or currency.upper() not in accepted_currencies() or not recipient.strip()
            or not reference.strip() or not owner_id):
        raise ValueError("Request must match the configured offer amount and Payoneer recipient")
    row = {"chat_id": int(chat_id), "url_sha256": _fingerprint(), "amount": amount, "currency": currency,
           "recipient": recipient[:160], "provider_request_reference": reference[:160],
           "verified_by_owner": int(owner_id), "verified_at": datetime.now(timezone.utc).isoformat()}
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(PATH)


def ready_request(chat_id: int) -> dict | None:
    try:
        row = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(row, dict) or row.get("chat_id") != chat_id or not _provider_url()
            or row.get("url_sha256") != _fingerprint()
            or not amount_matches(row.get("amount", 0))
            or str(row.get("currency", "")).upper() not in accepted_currencies()
            or not row.get("recipient") or not row.get("provider_request_reference")
            or not row.get("verified_by_owner") or not enterprise_quality.fresh(row.get("verified_at", ""), 30 * 24)):
        return None
    return row