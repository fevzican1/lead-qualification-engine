"""curl_cffi TLS/JA4 impersonation + devre kesici + tenacity retry testi."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nirvana.http_client import HttpClient, set_impersonation, _circuit_breaker
from nirvana.tenant_isolation import set_tenant_id, clear_tenant_id


def test_http_client_requires_tenant_id():
    """İstekten önce aktif tenant_id zorunludur (multi-tenant izolasyonu)."""
    clear_tenant_id()
    client = HttpClient()
    with pytest.raises(RuntimeError, match="tenant_id"):
        client.get("https://example.com")


def test_http_client_impersonation_sets_browser():
    """curl_cffi impersonation tarayıcı seçilebilir."""
    set_impersonation("chrome")
    client = HttpClient()
    assert client._session.impersonate == "chrome"
    client.close()


def test_circuit_breaker_initial_state_closed():
    """Devre kesici başlangıçta kapalıdır (istek yapılebilir)."""
    assert _circuit_breaker.current_state == "closed"
