"""Pact fixtures for consumer-driven contract testing.

Provides a session-scoped Pact builder that starts a mock provider service
and tears it down after the test session. Each consumer test defines expected
request/response interactions against a mock server, producing a Pact JSON
file that can be published to a Pact Broker for provider verification.

Pact-python v4 API:
  pact = Pact(consumer, provider)
  server = pact.serve()        # starts mock HTTP server
  server.port                  # bound port
  pact.write_file(dir)         # writes pact JSON on teardown
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from pact import Pact
from pact import match

PACT_DIR = Path(__file__).resolve().parent.parent.parent / "pacts"
PACT_DIR.mkdir(parents=True, exist_ok=True)

BROKER_URL = os.getenv("PACT_BROKER_URL", "")
BROKER_TOKEN = os.getenv("PACT_BROKER_TOKEN", "")


@pytest.fixture(scope="session")
def pact_builder() -> SimpleNamespace:
    """Start Pact mock service for WebchatService <-> PaymentService contract.

    Yields a SimpleNamespace with:
      - upon_receiving, given, with_request, with_body, will_respond_with (from Pact)
      - port (from PactServer, for building base URL)
      - url (from PactServer, full base URL)
    """
    pact = Pact("WebchatService", "PaymentService")
    with pact.serve() as server:
        try:
            yield SimpleNamespace(
                pact=pact,
                server=server,
                upon_receiving=pact.upon_receiving,
                port=server.port,
                url=server.url,
            )
        finally:
            pact.write_file(str(PACT_DIR))


@pytest.fixture
def pact_dir() -> Path:
    return PACT_DIR


__all__ = ["pact_builder", "pact_dir", "Pact", "match"]
