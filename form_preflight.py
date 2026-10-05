"""Lightweight HTML form verification for harvest, merge, and Oracle prefilter."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_BODY = 180_000
DEFAULT_TIMEOUT = 8.0

FORM_TAG_RE = re.compile(r"<form\b", re.I)
SUBMIT_RE = re.compile(
    r'type\s*=\s*["\']?(?:submit|button)["\']?|<button\b|'
    r'input[^>]+type\s*=\s*["\']?(?:submit|button)',
    re.I,
)
FORM_FIELD_RE = re.compile(
    r'type\s*=\s*["\']?(?:email|text|tel)["\']?|<textarea\b|'
    r'name\s*=\s*["\']?(?:email|e-?mail|message|phone|name)',
    re.I,
)
CAPTCHA_RE = re.compile(
    r"recaptcha|hcaptcha|h-captcha|cf-turnstile|turnstile|"
    r"g-recaptcha|data-sitekey|challenge-platform|"
    r"cf-browser-verification|just a moment|attention required",
    re.I,
)
WAF_BODY_RE = re.compile(
    r"cf-browser-verification|just a moment|attention required|"
    r"enable javascript and cookies|checking your browser",
    re.I,
)
CONTACT_PATHS = (
    "/iletisim",
    "/iletişim",
    "/contact",
    "/contact-us",
    "/bize-ulasin",
    "/iletişim-formu",
    "/iletisim-formu",
)


def _headers() -> dict[str, str]:
    """Katman 4: her istekte güncel tarayıcı imza listesinden rastgele UA."""
    ua = (
        "Mozilla/5.0 (compatible; devsolve-form-preflight/1.0; +contact-discovery)"
    )
    try:
        from nirvana.fingerprint_rotator import rotate_ua

        ua = rotate_ua()
    except Exception:  # noqa: BLE001 — rotasyon yoksa tanımlayıcı UA kalır
        pass
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }


def analyze_html(body: str, *, header_blob: str = "") -> dict[str, Any]:
    """Return whether HTML contains a submittable contact form (+honeypot/jeton izleri)."""
    text = (body or "")[:MAX_BODY]
    blob = f"{header_blob}\n{text}"
    captcha = bool(CAPTCHA_RE.search(blob))
    waf = bool(WAF_BODY_RE.search(blob))
    has_form = bool(FORM_TAG_RE.search(text))
    has_submit = bool(SUBMIT_RE.search(text))
    has_fields = bool(FORM_FIELD_RE.search(text))
    form_verified = has_form and has_submit and has_fields and not captcha and not waf
    # Rapor: gizli honeypot alanları ve dinamik jetonlar forma karar verirken izlenir.
    honeypots: list[str] = []
    tokens: list[str] = []
    js_hints: list[str] = []
    try:
        from nirvana.honeypot_human_sim import detect_honeypot_fields

        honeypots = [str(row.get("name") or "") for row in detect_honeypot_fields(text)][:10]
    except Exception:  # noqa: BLE001
        honeypots = []
    try:
        from nirvana.form_tokens import token_payload

        tokens = sorted(token_payload(text))[:10]
    except Exception:  # noqa: BLE001
        tokens = []
    try:
        from nirvana.preflight_lite import js_framework_hints

        js_hints = js_framework_hints(text)
    except Exception:  # noqa: BLE001
        js_hints = []
    return {
        "form_verified": form_verified,
        "captcha": captcha,
        "waf_strict": waf,
        "has_form": has_form,
        "has_submit": has_submit,
        "has_fields": has_fields,
        "honeypots": honeypots,
        "tokens": tokens,
        "js_hints": js_hints,
    }


def _origin(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def probe(client: Any | None, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """GET one URL and verify form markup.

    `client` verilmezse TLS impersonation taşıyıcısı (curl_cffi -> httpx fallback)
    kullanılır; verilirse geriye dönük uyum için o istemciye dokunulmaz.
    Does not touch Oracle HTTP budget by itself.
    """
    result: dict[str, Any] = {
        "url": url,
        "ok": False,
        "form_verified": False,
        "captcha": False,
        "waf_strict": False,
        "status_code": None,
        "error": None,
        "backend": "",
        "elapsed_ms": 0,
    }
    try:
        if client is None:
            from nirvana import net_stealth

            page = net_stealth.get_html(url, timeout=timeout)
            result["status_code"] = page.status
            result["backend"] = page.backend
            result["elapsed_ms"] = page.elapsed_ms
            if page.error:
                result["error"] = page.error
                return result
            if page.status >= 400:
                result["error"] = f"http_{page.status}"
                return result
            header_blob = page.header_blob()
            body = page.text or ""
            analysis = analyze_html(body, header_blob=header_blob)
            result.update(analysis)
            result["ok"] = True
            if analysis["form_verified"]:
                result["url"] = str(page.url or url)
            return result
        response = client.get(url, headers=_headers(), timeout=timeout)
        result["status_code"] = response.status_code
        if response.status_code >= 400:
            result["error"] = f"http_{response.status_code}"
            return result
        header_blob = " ".join(f"{k}:{v}" for k, v in response.headers.items()).lower()
        body = response.text or ""
        analysis = analyze_html(body, header_blob=header_blob)
        result.update(analysis)
        result["ok"] = True
        if analysis["form_verified"]:
            result["url"] = str(response.url)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


def probe_contact(client: Any | None, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Try the contact URL, then common contact paths on the same origin."""
    primary = probe(client, url, timeout=timeout)
    if primary.get("form_verified"):
        return primary
    if primary.get("captcha") or primary.get("waf_strict"):
        return primary
    origin = _origin(url)
    if not origin:
        return primary
    parsed = urlparse(url)
    seen = {parsed.path.rstrip("/") or "/"}
    first = True
    for path in CONTACT_PATHS:
        if path in seen:
            continue
        seen.add(path)
        if not first:
            # Katman 1: istek döngüsü arasında uniform insan jitter (3.0–9.0 sn).
            try:
                from nirvana import protection

                protection.human_delay()
            except Exception:  # noqa: BLE001 — jitter hatası taramayı durdurmaz
                pass
        first = False
        alt = probe(client, origin + path, timeout=timeout)
        if alt.get("form_verified"):
            logger.info("Form verified on %s%s", origin, path)
            return alt
        if alt.get("captcha"):
            return alt
    return primary


def verify_row(client: Any | None, row: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
    url = str(row.get("url") or "").strip()
    if not url:
        return None
    checked = probe_contact(client, url, timeout=timeout)
    if not checked.get("form_verified"):
        return None
    out = dict(row)
    out["url"] = str(checked.get("url") or url)
    out["form_verified"] = True
    out["preflight_at"] = checked.get("preflight_at")
    return out


def filter_verified_rows(
    rows: list[dict[str, Any]],
    *,
    client: Any | None = None,
    deadline_s: float,
    workers: int = 8,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Concurrent preflight; keep only rows with verified submittable forms.

    `client` verilmezse TLS impersonation taşıyıcısı kullanılır (curl_cffi).
    """
    import time

    started = time.monotonic()
    if not rows:
        return []
    verified: list[dict[str, Any]] = []
    workers = max(1, min(int(workers), 12))

    def _left() -> float:
        return deadline_s - (time.monotonic() - started)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(verify_row, client, row, timeout=timeout): row for row in rows
        }
        for future in as_completed(futures):
            if _left() <= 2:
                for pending in futures:
                    pending.cancel()
                break
            try:
                kept = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.info("Preflight task failed: %s", exc)
                continue
            if kept:
                verified.append(kept)
    verified.sort(key=lambda row: -int(row.get("easy_score") or 0))
    logger.info(
        "Preflight kept %s/%s verified form host(s)",
        len(verified),
        len(rows),
    )
    return verified
