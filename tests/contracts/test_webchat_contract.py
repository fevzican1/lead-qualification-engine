"""Consumer-Driven Contract Test: DeliveryWorker -> WebchatService (reverse).

Webchat acts as provider here for the delivery worker consumer that queries
webchat session state. Demonstrates bi-directional contract coverage between
microservices in the white-label ecosystem.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from pact import match

pytestmark = pytest.mark.contract

EXPECTED_SESSION_RESPONSE = {
    "sid": match.like(r"sess-[a-z0-9]+"),
    "status": match.regex("active", regex=r"^(active|closed|queued)$"),
    "profile_key": match.like(r"profile-[a-z0-9-]+"),
    "last_message_at": match.regex("2026-10-22T10:00:00Z", regex=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),
}


def _pact_base_url(server) -> str:
    """Build base URL from Pact v4 server port."""
    return f"http://localhost:{server.port}"


def test_webchat_session_contract(pact_builder) -> None:
    """DeliveryWorker queries Webchat for session state before assigning a job."""
    (
        pact_builder.upon_receiving("Oturum durumu sorgusu")
        .given("Webchat oturumu aktif ve musteri beklemede")
        .with_request(method="GET", path="/api/session/sess-7f3a9c1e")
        .will_respond_with(status=200)
        .with_body(EXPECTED_SESSION_RESPONSE, content_type="application/json")
    )

    from webchat.client import PaymentClient
    client = PaymentClient(_pact_base_url(pact_builder))
    session = client.get_session("sess-7f3a9c1e")
    assert session["status"] == "active"
    assert session["sid"].startswith("sess-")


__all__ = ["test_webchat_session_contract"]
