"""TLS/JA4 parmak izi taklitli HTTP istemcisi + devre kesici ve jittersiz retry.

Rapor gereksinimi:
  * ``curl_cffi`` ile güncel tarayıcı (Chrome/Firefox) TLS sıkışma parametreleri ve
    HTTP/2 başlık sıralamaları birebir taklit edilir → Cloudflare/Akamai anti-bot
    duvarlarının tespit mekanizmaları aşılır (IP ban / JA3-JA4 parmak izi
    çıkarması zorlaşır).
  * ``tenacity`` ile exponentiel backoff + jitter:
    ``T_gecikme = 2^deneme + jitter``. Jitter, aynı anda çöken yüzlerce
    istemcinin sunucuya aynı milisaniyede tekrar saldırması (thundering herd)
    riskini ortadan kaldırır.
  * ``pybreaker`` devre kesici: hedef servis sürekli HTTP 429 / 5xx döndürürse
    devre açılır ve ilgili servise istekler geçici olarak durdurulur → IP kalıcı
    engellenmez.
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Optional

import pybreaker
from curl_cffi import requests as curl_requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_jitter,
)

from nirvana.tenant_isolation import require_tenant_id

# --- Devre kesici (pybreaker) -------------------------------------------------
# 3 ardışık hata (429 / 5xx / bağlantı kopması) → 60 saniye devre açık.
_circuit_breaker = pybreaker.CircuitBreaker(
    fail_max=3,
    reset_timeout=60,
    exclude=[],
)


def _is_transient(exc: BaseException) -> bool:
    """Geçici hata mı? (429, 5xx, bağlantı/zaman aşımı) → yeniden denenir."""
    status = getattr(exc, "response", None)
    if status is not None:
        try:
            code = int(status.status_code)
        except Exception:
            code = 0
        return code == 429 or 500 <= code < 600
    # Bağlantı / zaman aşımı hataları tenacity'nin yeniden denemesine tabi.
    return True


# --- curl_cffi browser-impersonation oturumu -----------------------------------
# Varsayılan impersonation: Chrome (en yaygın tarayıcı imzası).
# İstenirse "firefox" / "chrome120" vb. değiştirilebilir.
_impersonate: str = "chrome"


def set_impersonation(browser: str = "chrome") -> None:
    """TLS/JA4 parmak izi taklit edilecek tarayıcıyı seçer."""
    global _impersonate
    _impersonate = browser


def _backoff_with_jitter() -> Any:
    """tenacity bekleme stratejisi: 2^deneme + jitter (milisaniye cinsinden)."""
    return wait_exponential(multiplier=1, min=1, max=30) + wait_jitter(max=5)


_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(Exception),
    wait=_backoff_with_jitter(),
)


def _sleep_with_jitter(attempt: int) -> float:
    """T_gecikme = 2^deneme + jitter — döndürülen bekleme saniyesi."""
    delay = (2 ** attempt) + random.uniform(0, 2)
    time.sleep(delay)
    return delay


class HttpClient:
    """curl_cffi tabanlı, parmak izi taklitli ve dayanıklı HTTP istemcisi.

    Her istek:
      1. Aktif tenant_id gerektirir (multi-tenant izolasyonu).
      2. curl_cffi ile tarayıcı imzası ile gönderilir.
      3. pybreaker devre kapalıysa devam eder; açıksa hata fırlatır.
      4. Geçici hatada tenacity ile 3 kez (2^deneme + jitter) denenir.
    """

    def __init__(
        self,
        *,
        impersonate: str = "chrome",
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        set_impersonation(impersonate)
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = curl_requests.Session(impersonate=impersonate)

    @_retry
    def _request_with_retry(
        self, method: str, url: str, **kwargs: Any
    ) -> curl_requests.Response:
        # Devre kesici kontrolü — açıksa istek atılmaz.
        try:
            _circuit_breaker.call(self._check_circuit)
        except pybreaker.CircuitBreakerError as exc:
            raise RuntimeError(
                f"Devre kesici açık: {url} için istek durduruldu (IP koruması)"
            ) from exc

        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("impersonate", _impersonate)
        resp = self._session.request(method, url, **kwargs)

        # HTTP 429 / 5xx → devre kesiciye hata kaydı + yeniden deneme tetikle.
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            _circuit_breaker.record_failure()
            raise RuntimeError(f"Geçici sunucu hatası: HTTP {resp.status_code}")

        _circuit_breaker.record_success()
        return resp

    @staticmethod
    def _check_circuit() -> None:
        """pybreaker için kullanılabilir; hata fırlatırsa devre açıktır."""
        pass

    def get(self, url: str, **kwargs: Any) -> curl_requests.Response:
        require_tenant_id()  # multi-tenant izolasyonu zorunlu kılar
        return self._request_with_retry("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> curl_requests.Response:
        require_tenant_id()  # multi-tenant izolasyonu zorunlu kılar
        return self._request_with_retry("POST", url, **kwargs)

    def close(self) -> None:
        self._session.close()


__all__ = [
    "HttpClient",
    "set_impersonation",
    "_circuit_breaker",
]
