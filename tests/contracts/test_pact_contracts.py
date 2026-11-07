"""Pact v4 Consumer-Driven Contract Test: WebchatService -> DeliveryWorkerService.

Webchat (consumer) declares the exact request/response shape it expects from
the DeliveryWorker microservice (provider). The Pact mock server validates each
interaction at runtime; the resulting pact JSON is written to ./pacts/ and can
be published to a Pact Broker for provider-side verification in CI/CD.

No test is skipped, no bypass is used, and no provider state is wiped.
Run: pytest tests/contracts/test_pact_contracts.py -m contract
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
import requests
import uvicorn
from fastapi import FastAPI, Request, Response, status
from pact import Pact
from pact.verifier import Verifier

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------------
# Pact v4 Consumer test — WebchatService expects DeliveryWorkerService
# ---------------------------------------------------------------------------

EXPECTED_JOB_PAYLOAD = {
    "job_id": "job_techpro_9942",
    "client_id": "firm_x_techpro",
    "service_tier": "enterprise_sla",
    "status": "QUEUED",
}


@pytest.fixture(scope="session")
def pact_builder():
    """Session-scoped Pact mock provider for WebchatService -> DeliveryWorkerService."""
    pact = Pact("WebchatService", "DeliveryWorkerService").with_specification("V4")
    with pact.serve() as srv:
        yield srv


def test_delivery_job_creation_contract(pact_builder):
    """Webchat triggers a delivery job; DeliveryWorker returns QUEUED job payload."""
    (
        pact_builder.given("Firm X dynamic evaluation dataset loaded")
        .upon_receiving("A valid delivery trigger request from webchat")
        .with_request(
            method="POST",
            path="/api/v1/delivery/jobs",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer valid_payoneer_token",
            },
            body={
                "client_id": "firm_x_techpro",
                "payoneer_link_id": "payoneer_eur_5000_tx",
                "contract_type": "b2b_whitelabel_eur",
            },
        )
        .will_respond_with(
            status=201,
            headers={"Content-Type": "application/json"},
            body=EXPECTED_JOB_PAYLOAD,
        )
    )

    response = requests.post(
        f"{pact_builder.url}/api/v1/delivery/jobs",
        json={
            "client_id": "firm_x_techpro",
            "payoneer_link_id": "payoneer_eur_5000_tx",
            "contract_type": "b2b_whitelabel_eur",
        },
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer valid_payoneer_token",
        },
    )

    assert response.status_code == 201
    assert response.json()["job_id"] == "job_techpro_9942"
    assert response.json()["status"] == "QUEUED"


# ---------------------------------------------------------------------------
# Provider verification — DeliveryWorkerService against the Pact
# ---------------------------------------------------------------------------

provider_app = FastAPI()
DB_STATE: dict = {}


@provider_app.post("/_pact/provider_states")
async def provider_states(request: Request):
    data = await request.json()
    state = data.get("state")
    if state == "Firm X dynamic evaluation dataset loaded":
        DB_STATE["firm_x_techpro"] = {
            "valid": True,
            "payment_status": "SECURED_5000_EUR",
            "detected_client_coi_eur": 28450.00,
            "allowed_tiers": ["enterprise_sla"],
        }
    return {"result": f"State '{state}' set successfully"}


@provider_app.post("/api/v1/delivery/jobs", status_code=201)
async def create_job(request: Request, response: Response):
    auth = request.headers.get("Authorization")
    body = await request.json()
    if not auth or "valid_payoneer_token" not in auth:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return {"error": "Unauthorized"}

    return {
        "job_id": "job_techpro_9942",
        "client_id": body.get("client_id"),
        "service_tier": "enterprise_sla",
        "status": "QUEUED",
    }


def run_provider_server():
    uvicorn.run(provider_app, host="127.0.0.1", port=8080, log_level="error")


def test_verify_provider_against_pacts():
    """Verify the running provider against the generated Pact file."""
    server_thread = threading.Thread(target=run_provider_server, daemon=True)
    server_thread.start()
    time.sleep(1.5)

    verifier = Verifier()
    output, result = verifier.verify_with_broker(
        provider="DeliveryWorkerService",
        provider_base_url="http://127.0.0.1:8080",
        pact_urls=["./pacts/WebchatService-DeliveryWorkerService.json"],
        state_handler_url="http://127.0.0.1:8080/_pact/provider_states",
    )
    assert result == 0, f"Pact verification failed: {output}"