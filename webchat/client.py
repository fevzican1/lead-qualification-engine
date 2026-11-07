"""WebChat payment client — used by Pact consumer-driven contract tests.

Provides `PaymentClient` that talks to a Pact mock provider service during
contract verification. In production this would call the real PaymentService
over HTTP; under Pact it points at the mock server URI.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class PaymentClient:
    """Client for the PaymentService HTTP API.

    Parameters
    ----------
    base_url:
        Base URL of the provider (real service or Pact mock server).
    timeout:
        Request timeout in seconds.
    """

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            raise RuntimeError(f"HTTP {exc.code} for {url}: {raw}") from exc

    def initiate_charge(
        self, account_id: str, amount: float, currency: str = "TRY"
    ) -> dict[str, Any]:
        """Initiate a payment charge and return the provider response."""
        return self._post(
            "/v1/charge",
            {"account_id": account_id, "amount": amount, "currency": currency},
        )

    def refund(self, transaction_id: str, amount: float, currency: str = "TRY") -> dict[str, Any]:
        """Refund a previous transaction."""
        return self._post(
            "/v1/refund",
            {"transaction_id": transaction_id, "amount": amount, "currency": currency},
        )

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            raise RuntimeError(f"HTTP {exc.code} for {url}: {raw}") from exc

    def get_session(self, sid: str) -> dict[str, Any]:
        """Fetch webchat session state by session id."""
        return self._get(f"/api/session/{sid}")


__all__ = ["PaymentClient"]
