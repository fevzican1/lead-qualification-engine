"""
Cheap HTTP probe before Chromium.

Skips CAPTCHA/WAF challenge pages and sites with no contact/form hint.
Ranks e-commerce / integration stacks to the front of the queue.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from site_signals import extract_stack_hints, high_value_score, looks_turkish
import easy_score
import config
import stack_fingerprint

logger = logging.getLogger(__name__)

FORM_HINT_RE = re.compile(
    r"<form|/contact|contact-us|contactus|get-in-touch|"
    r"iletisim|iletişim|bize[-_/ ]?ula|"
    r"<textarea|type=[\"']email|name=[\"']e-?mail",
    re.I,
)
CAPTCHA_RE = re.compile(
    r"recaptcha|hcaptcha|h-captcha|cf-turnstile|turnstile|"
    r"g-recaptcha|data-sitekey|challenge-platform|"
    r"cf-browser-verification|just a moment|attention required",
    re.I,
)
EASY_FORM_RE = re.compile(
    r"hsforms|hubspot|wpcf7|wpforms|contact-form-7|formspree|ninja-forms|"
    r"fluentform|gravityform|woocommerce|wp-content/plugins|type=\"email\"",
    re.I,
)
MAX_BODY = 180_000
TIMEOUT = 12.0
CONTACT_PATHS_TR = ("/iletisim",)
CONTACT_PATHS_EN = ("/contact",)
WAF_BODY_RE = re.compile(
    r"cf-browser-verification|just a moment|attention required|"
    r"enable javascript and cookies|checking your browser",
    re.I,
)


def _normalize(url: str) -> str:
    url = (url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _origin(url: str) -> str:
    parsed = urlparse(_normalize(url))
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _contact_paths(url: str) -> tuple[str, ...]:
    host = (urlparse(_normalize(url)).hostname or "").lower()
    if host.endswith(".tr"):
        return CONTACT_PATHS_TR
    return CONTACT_PATHS_EN


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }


def probe(url: str) -> dict[str, Any]:
    url = _normalize(url)
    result: dict[str, Any] = {
        "url": url,
        "ok": False,
        "form_likely": False,
        "captcha": False,
        "waf_strict": False,
        "easy_form": False,
        "stack_hints": [],
        "priority": 0,
        "turkish": False,
        "error": None,
        "status_code": None,
    }
    headers = _headers()
    try:
        import domain_store

        if not domain_store.consume_http(1):
            result["error"] = "http_budget"
            result["defer"] = True
            return result

        def _fetch():
            with httpx.Client(follow_redirects=True, timeout=TIMEOUT) as client:
                return client.get(url, headers=headers)

        try:
            import risk_guard

            response = risk_guard.call_once_retry(_fetch)
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
            logger.info("HTTP probe failed %s: %s", url, exc)
            return result
        result["status_code"] = response.status_code
        header_blob = " ".join(f"{k}:{v}" for k, v in response.headers.items()).lower()
        body = (response.text or "")[:MAX_BODY]
        blob = f"{header_blob}\n{body}"
        fp = stack_fingerprint.fingerprint(
            html=body, headers=response.headers, url=str(response.url)
        )
        hints = stack_fingerprint.merge_hints(fp, extract_stack_hints(url, body))
        result["stack_hints"] = hints
        result["platform"] = str(fp.get("platform") or "")
        result["platform_confidence"] = int(fp.get("confidence") or 0)
        result["platform_evidence"] = list(fp.get("evidence") or [])
        result["turkish"] = looks_turkish(body)
        result["captcha"] = bool(CAPTCHA_RE.search(blob))
        result["waf_strict"] = bool(
            WAF_BODY_RE.search(blob)
            or "cf-ray" in header_blob
            or "cloudflare" in header_blob
        )
        result["form_likely"] = bool(FORM_HINT_RE.search(body))
        result["easy_form"] = bool(EASY_FORM_RE.search(blob))
        result["ok"] = 200 <= response.status_code < 400
        result["easy_score"] = easy_score.from_probe(result)
        result["priority"] = _priority(result)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        logger.info("HTTP probe failed %s: %s", url, exc)
    return result


def probe_for_form(url: str) -> dict[str, Any]:
    """Homepage probe, then one cheap /contact or /iletisim GET if the home HTML has no form."""
    item = probe(url)
    if item.get("defer") or item.get("captcha"):
        return item
    if item.get("ok") and item.get("form_likely"):
        return item
    if not item.get("ok"):
        return item
    origin = _origin(url)
    if not origin:
        return item
    for path in _contact_paths(url):
        import domain_store

        if domain_store.http_budget_remaining() < 1:
            return item
        alt = probe(origin + path)
        if alt.get("defer"):
            return item
        if alt.get("ok") and alt.get("form_likely") and not alt.get("captcha"):
            logger.info("Form hint on %s%s — using that URL", origin, path)
            return alt
    return item


def probe_many(urls: list[str]) -> list[dict[str, Any]]:
    return [probe(url) for url in urls]


def split_and_rank(urls: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (browser_jobs, skipped_leads). Browser jobs are high-priority first."""
    jobs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for url in urls:
        item = probe_for_form(url)
        if item.get("defer"):
            logger.info("HTTP budget empty — stop probing this slice, rest stay queued")
            break
        if item.get("captcha"):
            item["easy_score"] = 10
            skipped.append(_skip_lead(item, "skipped_captcha", "CAPTCHA/WAF challenge in HTTP probe"))
            logger.info("Skip %s (captcha in HTML, score=10)", url)
            continue
        code = int(item.get("status_code") or 0)
        if code in {401, 403, 408, 425, 429, 500, 502, 503, 504}:
            import domain_store

            domain_store.defer(url, reason=f"http_{code}", count_fail=False)
            logger.info("Retry later %s (HTTP %s, not burned)", url, code)
            continue
        if not item.get("ok"):
            import domain_store

            domain_store.defer(url, reason="http_fail")
            logger.info("Retry later %s (dead HTTP, not burned)", url)
            continue
        if not item.get("form_likely"):
            skipped.append(_skip_lead(item, "skipped_no_form", "No contact/form hint in HTML"))
            logger.info("Skip %s (no form hint)", url)
            continue
        score = int(item.get("easy_score") or easy_score.from_probe(item))
        item["easy_score"] = score
        min_easy = int(getattr(config, "EASY_SCORE_MIN", 55) or 55)
        if score < min_easy:
            import domain_store

            domain_store.defer(
                url, hours=6, reason="low_easy_score", count_fail=False, easy_score=score
            )
            logger.info("Back-line %s (easy_score=%s < %s)", url, score, min_easy)
            continue
        jobs.append(item)
    jobs.sort(
        key=lambda row: (
            -int(row.get("easy_score") or 0),
            -int(row.get("priority") or 0),
        )
    )
    return jobs, skipped


def _priority(item: dict[str, Any]) -> int:
    score = 10
    if item.get("form_likely"):
        score += 40
    if item.get("easy_form"):
        score += 28
    hints = [str(h).lower() for h in (item.get("stack_hints") or [])]
    if any(h in {"wordpress", "hubspot", "woocommerce"} for h in hints):
        score += 18
    score += high_value_score(list(item.get("stack_hints") or []))
    if item.get("turkish"):
        score += 8
    if item.get("captcha"):
        score -= 80
    if item.get("waf_strict"):
        score -= 28
    host = (urlparse(str(item.get("url") or "")).hostname or "").lower()
    if host.endswith(".com.tr") or host.endswith(".tr"):
        score += 6
    return score


def _skip_lead(item: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        "url": item.get("url"),
        "company_name": None,
        "description": "",
        "page_excerpt": "",
        "stack_hints": list(item.get("stack_hints") or []),
        "contact_form": {"found": False, "page_url": None, "fields": []},
        "captcha_detected": status == "skipped_captcha",
        "waf_strict": bool(item.get("waf_strict")),
        "priority": int(item.get("priority") or 0),
        "easy_score": int(item.get("easy_score") or 0),
        "fit_score": 0,
        "value_proposition": "",
        "should_contact": False,
        "fit_rationale": reason,
        "status": status,
        "error": item.get("error"),
    }
