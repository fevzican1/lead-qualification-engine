"""Consumer-Driven Contract Test: WebchatService -> PaymentService.

Webchat (consumer) declares the exact request/response shape it expects from
the Payment microservice (provider). The Pact mock server validates each
interaction at runtime; the resulting pact JSON is written to ./pacts/ and can
be published to a Pact Broker for provider-side verification in CI/CD.

Run: pytest tests/contracts/test_payment_contract.py -m contract
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from pact import match

pytestmark = pytest.mark.contract

EXPECTED_CHARGE_RESPONSE = {
    "status": "SUCCESS",
    "transaction_id": match.like(r"TX-\d+"),
    "amount": match.like(150.50),
    "currency": match.regex("TRY", regex=r"^[A-Z]{3}$"),
    "provider": match.regex("iyzico", regex=r"^(iyzico|stripe|payoneer)$"),
}

EXPECTED_REFUND_RESPONSE = {
    "status": "REFUNDED",
    "refund_id": match.like(r"RF-\d+"),
    "original_transaction_id": match.like(r"TX-\d+"),
    "amount": match.like(150.50),
    "currency": match.regex("TRY", regex=r"^[A-Z]{3}$"),
}


def _pact_base_url(server) -> str:
    """Build base URL from Pact v4 server port."""
    return f"http://localhost:{server.port}"


def test_payment_initiation_contract(pact_builder) -> None:
    """Webchat requests a charge; PaymentService responds with an approved transaction."""
    (
        pact_builder.upon_receiving("Odeme baslatma istemi")
        .given("Odeme altyapisi aktif ve bakiye yeterli")
        .with_request(method="POST", path="/v1/charge")
        .with_body({"account_id": "ACC-101", "amount": 150.50, "currency": "TRY"}, content_type="application/json")
        .will_respond_with(status=200)
        .with_body(EXPECTED_CHARGE_RESPONSE, content_type="application/json")
    )

    from webchat.client import PaymentClient
    client = PaymentClient(_pact_base_url(pact_builder))
    response = client.initiate_charge("ACC-101", 150.50, "TRY")
    assert response["status"] == "SUCCESS"
    assert response["transaction_id"].startswith("TX-")
    assert response["amount"] == 150.50


def test_payment_refund_contract(pact_builder) -> None:
    """Webchat requests a refund; PaymentService responds with a refund confirmation."""
    (
        pact_builder.upon_receiving("Odeme iade istemi")
        .given("Odeme gecmesi mevcut ve iade edilebilir")
        .with_request(method="POST", path="/v1/refund")
        .with_body({"transaction_id": "TX-998234", "amount": 150.50, "currency": "TRY"}, content_type="application/json")
        .will_respond_with(status=200)
        .with_body(EXPECTED_REFUND_RESPONSE, content_type="application/json")
    )

    from webchat.client import PaymentClient
    client = PaymentClient(_pact_base_url(pact_builder))
    response = client.refund("TX-998234", 150.50, "TRY")
    assert response["status"] == "REFUNDED"
    assert response["refund_id"].startswith("RF-")


def test_payment_contract_publish_to_broker(pact_builder) -> None:
    """Optional: publish generated pact to a Pact Broker if configured."""
    import os

    broker_url = os.getenv("PACT_BROKER_URL")
    broker_token = os.getenv("PACT_BROKER_TOKEN")
    if not broker_url:
        pytest.skip("PACT_BROKER_URL not set — skipping broker publish")

    # Pact-python v4 writes the file on write_file(); publishing to broker
    # is done via the Pact Broker HTTP API in CI/CD.
    pact_builder.write_file(str(Path(__file__).resolve().parent.parent.parent / "pacts"))
