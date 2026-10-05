"""Katman 1 — TLS/JA3-JA4 impersonation taşıyıcısı (curl_cffi -> httpx fallback).

Genişletilmiş Nihai Mimari, Aşama 1: istek katmanı curl_cffi ile Chrome/Firefox
TLS parmak izini (JA3/JA4 + HTTP/2 ayarları) taklit eder; böylece Cloudflare
benzeri anti-bot katmanları varsayılan Python OpenSSL parmak izini görmez.

$0 kuralı: ek servis, paralı çözümleyici veya proxy YOK.
- curl_cffi kurulu değilse (veya ARM wheel yoksa) httpx'e DÜŞER; çağıran taraf
  hiçbir zaman ImportError görmez.
- Pre-flight, form doğrulama ve doğrudan POST bu taşıyıcıyı kullanır.

Ortam değişkenleri:
- TLS_IMPERSONATE (varsayılan "chrome"): chrome, firefox133, safari...
- TLS_IMPERSONATE_ENABLED (varsayılan "1"): "0" -> her zaman httpx (tanı modu)
- STEALTH_TEXT_WINDOW (varsayılan 180000): yanıttan tutulan maksimum karakter
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from nirvana import protection

logger = logging.getLogger(__name__)

DEFAULT_IMPERSONATE = (os.getenv("TLS_IMPERSONATE") or "chrome").strip() or "chrome"
TEXT_WINDOW = int(os.getenv("STEALTH_TEXT_WINDOW", "180000") or 180000)
# httpx fallback'i aynı tarayıcı sınıfına sokmak için gerçek Chrome UA'sı.
UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

_backend: str | None = None
_sessions: dict[str, Any] = {}


def _cffi() -> Any:
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore

        return cffi_requests
    except Exception:  # noqa: BLE001 — wheel yok/ARM uyumsuz: sessiz fallback
        return None


def enabled() -> bool:
    raw = (os.getenv("TLS_IMPERSONATE_ENABLED", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def backend() -> str:
    """Aktif taşıyıcı: 'curl_cffi' (TLS impersonation) veya 'httpx' (fallback)."""
    global _backend
    if _backend is None:
        _backend = "curl_cffi" if (enabled() and _cffi() is not None) else "httpx"
        logger.info("Stealth taşıyıcı: %s (impersonate=%s)", _backend, DEFAULT_IMPERSONATE)
    return _backend


def _rotated_ua() -> str:
    """Katman 4: güncel tarayıcı imza listesinden rastgele UA (httpx fallback)."""
    try:
        from nirvana.fingerprint_rotator import rotate_ua

        return rotate_ua()
    except Exception:  # noqa: BLE001 — rotasyon yoksa sabit Chrome UA
        return UA_CHROME


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {"Accept": ACCEPT_HTML, "Accept-Language": "tr,en;q=0.9"}
    if backend() == "httpx":
        base["User-Agent"] = _rotated_ua()
    if extra:
        base.update({str(k): str(v) for k, v in extra.items() if v is not None})
    return base


def session(impersonate: str | None = None, proxy: str | None = None) -> Any:
    """Bağlantı havuzu paylaşılan oturum (100ms pre-flight için TCP/TLS reuse).

    Katman 3: ``proxy`` verilirse o proxy üzerinden ayrı oturum açılır (havuz
    rotasyonu); boş havuz eskisi gibi doğrudan bağlantıdır ($0 korunur).
    """
    who = (impersonate or DEFAULT_IMPERSONATE).strip() or DEFAULT_IMPERSONATE
    key = f"{who}|{proxy or ''}"
    existing = _sessions.get(key)
    if existing is not None:
        return existing
    if len(_sessions) > 16:  # havuz büyümesini sınırla (hafif rotasyon)
        _sessions.clear()
    if backend() == "curl_cffi":
        kwargs: dict[str, Any] = {"impersonate": who, "headers": _headers()}
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        try:
            client = _cffi().Session(**kwargs)
        except TypeError:  # curl_cffi proxies desteklemiyorsa doğrudan devam
            client = _cffi().Session(impersonate=who, headers=_headers())
    else:
        import httpx

        http_kwargs: dict[str, Any] = {"headers": _headers(), "follow_redirects": True}
        if proxy:
            http_kwargs["proxy"] = proxy
        try:
            client = httpx.Client(**http_kwargs)
        except TypeError:  # eski httpx: proxy parametresi yoksa doğrudan devam
            client = httpx.Client(headers=_headers(), follow_redirects=True)
    _sessions[key] = client
    return client


def close() -> None:
    """Tüm oturumları kapat (auto-recycle / servis kapanışı)."""
    for client in list(_sessions.values()):
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    _sessions.clear()


@dataclass(slots=True)
class Page:
    """Taşıyıcıdan bağımsız tek tip yanıt zarfı (httpx/curl_cffi aynı sözleşme)."""

    url: str
    status: int = 0
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: int = 0
    backend: str = "httpx"
    impersonate: str = ""
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.status and self.status < 400 and not self.error)

    def header_blob(self) -> str:
        return " ".join(f"{k}:{v}" for k, v in self.headers.items()).lower()


def fetch(
    url: str,
    *,
    method: str = "GET",
    timeout: float = 6.0,
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    impersonate: str | None = None,
    max_bytes: int = TEXT_WINDOW,
    allow_redirects: bool = True,
) -> Page:
    """Tek istek; ASLA istisna fırlatmaz (Page.error doldurulur)."""
    who = (impersonate or DEFAULT_IMPERSONATE).strip() or DEFAULT_IMPERSONATE
    page = Page(url=url, backend=backend(), impersonate=who)
    started = time.monotonic()
    # Katman 2: circuit open ise istek ATILMAZ (boşa zaman kaybı yok; fail-open).
    if not protection.allow(url):
        page.error = "circuit_open"
        page.elapsed_ms = int((time.monotonic() - started) * 1000)
        return page
    try:
        # Katman 3: havuzdan rastgele proxy ile oturum (havuz boşsa doğrudan).
        proxy = None
        try:
            proxy = protection.pick_proxy()
        except Exception:  # noqa: BLE001 — proxy katmanı hatası isteği düşürmez
            proxy = None
        client = session(who, proxy=proxy)
        kwargs: dict[str, Any] = {"timeout": float(timeout), "headers": _headers(headers)}
        if data is not None:
            kwargs["data"] = data
        if backend() == "curl_cffi":
            kwargs["allow_redirects"] = bool(allow_redirects)
        else:
            kwargs["follow_redirects"] = bool(allow_redirects)
        verb = method.upper()
        if verb == "HEAD":
            response = client.head(url, **kwargs)
        elif verb == "POST":
            response = client.post(url, **kwargs)
        else:
            response = client.get(url, **kwargs)
        page.status = int(getattr(response, "status_code", 0) or 0)
        # Sonucu şaltere yaz: kalıcı 4xx/5xx/429 domain'i soğutur.
        if page.status:
            protection.record(
                url, page.status < 400,
                error=f"http_{page.status}" if page.status >= 400 else "",
            )
        page.headers = {
            str(k).lower(): str(v)
            for k, v in dict(getattr(response, "headers", {}) or {}).items()
        }
        raw = getattr(response, "text", "") or ""
        if max_bytes and len(raw) > max_bytes:
            raw = raw[:max_bytes]
            page.truncated = True
        page.text = raw
        final = getattr(response, "url", None)
        if final:
            page.url = str(final)
    except Exception as exc:  # noqa: BLE001 — ağ hatası çağıranı durdurmaz
        page.error = f"{type(exc).__name__}: {exc}"[:200]
    page.elapsed_ms = int((time.monotonic() - started) * 1000)
    return page


def head(url: str, *, timeout: float = 3.0, headers: dict[str, str] | None = None) -> Page:
    """Hafif ön sorgu: gövde indirmeden durum kodu/başlık (pre-flight 1. adım)."""
    return fetch(url, method="HEAD", timeout=timeout, headers=headers, max_bytes=0)


def get_html(url: str, *, timeout: float = 6.0, headers: dict[str, str] | None = None,
             max_bytes: int = TEXT_WINDOW) -> Page:
    """Gövde (DOM) indir — pre-flight 2. adım; pencere ile bant genişliği sınırlı."""
    return fetch(url, method="GET", timeout=timeout, headers=headers, max_bytes=max_bytes)


def post_form(url: str, data: dict[str, Any], *, timeout: float = 8.0,
              headers: dict[str, str] | None = None) -> Page:
    """Doğrudan (tarayıcısız) form POST'u — TLS parmak izi taklitli."""
    extra = {"Content-Type": "application/x-www-form-urlencoded"}
    if headers:
        extra.update(headers)
    return fetch(url, method="POST", timeout=timeout, headers=extra, data=data)


def describe() -> dict[str, Any]:
    """Deploy/sağlık çıktısı: aktif taşıyıcı ve parmak izi."""
    return {
        "backend": backend(),
        "impersonate": DEFAULT_IMPERSONATE,
        "enabled": enabled(),
        "curl_cffi_installed": _cffi() is not None,
        "text_window": TEXT_WINDOW,
    }
