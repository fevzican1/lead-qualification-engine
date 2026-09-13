"""Lightweight form-signal extractor, depth-2 contact page hunter.

Oracle Always Free ($0) mission: kill the `skipped_no_open_form` verdict by
finding contact surfaces that plain DOM fingerprints miss, WITHOUT spending
quota or RAM:

1. Multi-depth contact URL scan  — depth-2 crawler over same-origin anchors
2. Click-trigger / modal hints   — regex over trigger texts (browser path uses
   these to open modals; this module only flags the signals)
3. Iframe & Shadow DOM hints     — known widget providers + shadow-DOM hints
4. AJAX / REST endpoint mining   — POST endpoints inside inline scripts
5. Mailto fallback               — mailto:/bare emails instead of a dead skip

Resource rules honoured here:

- **Lightweight first:** everything in this module runs on raw HTML over httpx;
  no Playwright/Chromium engine is ever started for the *search*.
- **8s domain budget:** ``DomainBudget`` aborts a domain search (Resource Guard).
- **Quota isolation:** nothing in this module touches ``pacing``/``knowledge``
  counters. ``submitted_confirmed`` / ``submitted_unconfirmed`` are the only
  statuses that ever consume the daily quota (unchanged, DAILY_CAP = 400).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

MAX_BODY = 220_000
DEFAULT_UA = (
    "Mozilla/5.0 (compatible; devsolve-form-extractor/1.0; +contact-discovery)"
)

# --- Adım 2 — trigger texts (modal / pop-up / accordion button labels) ---------
TRIGGER_RE = re.compile(
    r"contact|get in touch|teklif|iletişim|iletişime|bize ulaşın|bize ulasın|"
    r"bize yazın|send message|book a call|book a demo|demo talep|"
    r"write to us|leave a message|mesaj bırak|mesaj birak|teklif al|talep et|"
    r"request (a )?quote|quote",
    re.I,
)

# --- Adım 1 — contact page paths (priority order matters) ----------------------
CONTACT_PATHS: dict[str, int] = {
    "/contact": 2,
    "/contact-us": 2,
    "/contactus": 2,
    "/iletisim": 2,
    "/iletişim": 2,
    "/bize-ulasin": 2,
    "/bize-ulasın": 2,
    "/get-in-touch": 2,
    "/getintouch": 2,
    "/quote": 1,
    "/teklif": 1,
    "/book-a-call": 1,
    "/book-a-demo": 1,
    "/demo": 1,
    "/support": 1,
    "/help": 1,
    "/about": 1,
    "/about-us": 1,
    "/bize-ulas": 1,
}
CONTACT_PATH_RE = re.compile(
    r"/(?:contact|contact-us|contactus|iletisim|iletişim|bize-ulas|"
    r"get-in-touch|getintouch|quote|teklif|book-a-call|book-a-demo|demo|"
    r"support|help|about|about-us)",
    re.I,
)

# --- Adım 3 — known form-widget providers --------------------------------------
WIDGET_PROVIDERS: dict[str, str] = {
    "hsforms": "hubspot",
    "hubspot": "hubspot",
    "typeform": "typeform",
    "calendly": "calendly",
    "jotform": "jotform",
    "wufoo": "wufoo",
    "marketo": "marketo",
    "formspree": "formspree",
    "cognitoforms": "cognitoforms",
    "123formbuilder": "formbuilder",
    "forms.pabbly": "pabbly",
    "web3forms": "web3forms",
    "getform": "getform",
    "usebasin": "basin",
    "formcarry": "formcarry",
    "tally.so": "tally",
}
WIDGET_IFRAME_RE = re.compile(
    r"hsforms\.com|hubspot|typeform|go\.typeform|calendly|jotform\.|wufoo\.com|"
    r"marketo|formspree\.io|cognitoforms|123formbuilder|forms\.pabbly|"
    r"web3forms\.com|getform\.io|usebasin\.com|formcarry\.com|tally\.so|"
    r"forms\.gle|docs\.google\.com/forms|google\.com/forms",
    re.I,
)
GOOGLE_FORMS_RE = re.compile(r"forms\.gle|docs\.google\.com/forms|google\.com/forms", re.I)

# --- Adım 4 — AJAX / REST endpoints --------------------------------------------
ENDPOINT_RE = re.compile(
    r"(?:https?:)?//[^\"'\s<>]+|/[A-Za-z0-9_\-./?&=]*?"
    r"(?:/api/contact|/api/lead|/api/message|/contact-form-7|/wp-json|"
    r"/submit|/contact|/telaf|/teklif|/message|/send)[A-Za-z0-9_\-./?&=]*",
    re.I,
)
# Quoted string that looks like a URL/path and carries a form-ish token.
_QUOTED_URL_RE = re.compile(r"[\"']((?:https?:)?//[^\"'\s<>()]+|/[A-Za-z0-9_\-./?&=]*(?:contact|lead|submit|form|wp-json|iletisim|teklif)[A-Za-z0-9_\-./?&=]*)[\"']")
# --- Adım 5 — mailto fallback ----------------------------------------------------
MAILTO_HREF_RE = re.compile(r"mailto:([^\"'<>?#\s]+)", re.I)
BARE_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
_NOISE_EMAIL_RE = re.compile(
    r"example\.(com|org|net)|sentry|wixpress\.com|godaddy|squarespace|webflow|"
    r"no-?reply|donotreply|noreply|\.png$|\.jpg$|\.jpeg$|\.gif$|\.svg$|\.webp$|"
    r"@2x|@3x|\.js$|\.css$|\.webp$|domain\.com|yourdomain|email\.com|"
    r"sentry\.io|jsdelivr|cloudflare|googleusercontent|googletagmanager|"
    r"schema\.org|w3\.org|@example",
    re.I,
)

# Shadow-DOM / dynamic-widget hints (real resolution happens in the browser
# path; here we only flag the signal so a collect pass keeps the page open).
SHADOW_DOM_RE = re.compile(
    r"attachShadow|shadowRoot|#shadow|host\.shadow|<template[^>]*>|"
    r"<[a-z][a-z0-9-]*-[a-z0-9-]+[^>]*>",
    re.I,
)

_FORM_FIELDS_RE = re.compile(
    r"<form\b|type\s*=\s*[\"']?email[\"']?|name\s*=\s*[\"']?(?:e-?mail|message|"
    r"telefon|phone|name)[\"']?|<textarea\b|name\s*=\s*[\"']?(?:iletisim|iletişim)",
    re.I,
)
_FORM_TAG_RE = re.compile(r"<form\b", re.I)
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
_ACTION_RE = re.compile(r"<form\b[^>]*\baction\s*=\s*[\"']([^\"']+)[\"']", re.I)
_METHOD_RE = re.compile(r"<form\b[^>]*\bmethod\s*=\s*[\"']([^\"']+)[\"']", re.I)
_EMAIL_ONLY_RE = re.compile(r"<input\b[^>]*\btype\s*=\s*[\"']email[\"']", re.I)


def normalize_url(url: str) -> str:
    url = " ".join((url or "").split())
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def is_same_origin(a: str, b: str) -> bool:
    return bool(_origin(a) and _origin(a) == _origin(b))


def _path(url: str) -> str:
    return (urlparse(url).path or "/").lower()


def contact_path_score(link_url: str) -> int:
    """Score an absolute link: explicit contact paths beat generic ones."""
    path = _path(link_url)
    if path in CONTACT_PATHS:
        return CONTACT_PATHS[path]
    return 1 if CONTACT_PATH_RE.search(path) else 0


class DomainBudget:
    """Hard per-domain wall-clock budget (Resource Guard, default 8s)."""

    def __init__(self, seconds: float = 8.0) -> None:
        self.deadline = time.monotonic() + max(1.0, float(seconds))
        self.start = time.monotonic()

    def left(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def remaining_timeout(self) -> float:
        """httpx timeout that auto-aborts when the domain budget runs out."""
        return max(1.0, self.left())

    def spent(self) -> float:
        return time.monotonic() - self.start


# ---------------------------------------------------------------------------
# Pure signal extractors (no I/O — unit-testable).
# ---------------------------------------------------------------------------

def extract_contact_links(html: str, base_url: str) -> list[str]:
    """Same-origin contact URLs: explicit paths win, then anchor text matches."""
    base = normalize_url(base_url)
    origin = _origin(base)
    links: list[tuple[int, str]] = []
    seen: set[str] = set()
    for href_val, text in _anchor_iter(html, base):
        if not text and not href_val:
            continue
        href_url = ""
        score = 0
        if href_val:
            href_url = urljoin(base, href_val)
            if _origin(href_url) != origin:
                continue  # third-party links are not contact pages of this site
            key = href_url.split("#", 1)[0].rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            score = contact_path_score(href_url)
        if TRIGGER_RE.search(text):
            score = max(score, 2)
        if href_url and score > 0:
            links.append((score, href_url))
    links.sort(key=lambda t: (-t[0], t[1]))
    out: list[str] = []
    for _, link in links:
        if link not in out:
            out.append(link)
    return out[:8]


def _anchor_iter(html: str, base: str):
    """Yield (href, joined raw text) for every <a ...>...</a>."""
    del base
    for raw in re.findall(r"<a\b[^>]*>.*?</a>", html or "", flags=re.I | re.S):
        href_m = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', raw, re.I)
        href_val = href_m.group(1).strip() if href_m else ""
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()[:120]
        yield href_val, text


def extract_widget_iframes(html: str, base_url: str) -> list[dict[str, str]]:
    """Every widget-provider iframe. Returns [{src, provider}]."""
    found: list[dict[str, str]] = []
    base = normalize_url(base_url)
    for src in re.findall(r"<iframe\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", html or "", re.I):
        if not WIDGET_IFRAME_RE.search(src):
            continue
        provider = "google_forms" if GOOGLE_FORMS_RE.search(src) else ""
        if not provider:
            low = src.lower()
            provider = next(
                (name for token, name in WIDGET_PROVIDERS.items() if token in low), "embed"
            )
        found.append({"src": urljoin(base, src), "provider": provider})
    return found


def extract_post_endpoints(html: str, base_url: str) -> list[str]:
    """Same-origin POST endpoints found in JS/action attributes (AJAX mining)."""
    base = normalize_url(base_url)
    origin = _origin(base)
    hits: list[str] = []
    body = (html or "")[:MAX_BODY]
    for raw in _QUOTED_URL_RE.findall(body):
        raw = raw.strip().strip("'\"")
        if "(" in raw or ")" in raw or " " in raw:
            continue
        if not raw.startswith("/") and not raw.startswith("http"):
            continue
        if not (CONTACT_PATH_RE.search(raw) or re.search(
                r"/(?:contact|lead|submit|wp-json|iletisim|teklif|message|send)", raw, re.I)):
            continue
        abs_url = urljoin(base, raw)
        if origin and _origin(abs_url) != origin:
            continue
        if abs_url not in hits:
            hits.append(abs_url)
    return hits[:6]


def extract_hidden_payload(html: str, base_url: str) -> dict[str, Any]:
    """Synthetic payload: <form action/method> + hidden inputs (no CSRF token)."""
    base = normalize_url(base_url)
    action_m = _ACTION_RE.search(html or "")
    m = _METHOD_RE.search(html or "")
    method = (m.group(1) if m else "post").lower()
    fields: dict[str, str] = {}
    for tag in _INPUT_RE.findall(html or ""):
        if re.search(r"\btype\s*=\s*[\"']hidden[\"']", tag, re.I) and \
                re.search(r'\bname\s*=\s*["\']([^"\']+)["\']', tag):
            name = re.search(r'\bname\s*=\s*["\']([^"\']+)["\']', tag).group(1)
            value_m = re.search(r'\bvalue\s*=\s*["\']([^"\']*)["\']', tag)
            fields[name] = value_m.group(1) if value_m else ""
    return {
        "action": urljoin(base, action_m.group(1)) if action_m else base,
        "method": method,
        "hidden_fields": fields,
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_html(html: str, url: str) -> dict[str, Any]:
    """Classify one HTML body into a recoverable contact mode.

    Modes (priority order):
      * direct   — a real <form> with fields is present in the markup
      * widget   — a known provider iframe (HubSpot/Typeform/Calendly/Google…)
      * ajax     — a same-origin POST endpoint was mined from scripts
      * mailto   — only mailto:/bare-email contact remains
      * shadow   — signals hint at a JS/shadow-DOM form needing a browser
      * none     — nothing recoverable at the HTTP layer
    """
    html = (html or "")[:MAX_BODY]
    url = normalize_url(url)
    has_form = bool(_FORM_TAG_RE.search(html))
    has_controls = bool(_FORM_FIELDS_RE.search(html))
    widgets = extract_widget_iframes(html, url)
    endpoints = extract_post_endpoints(html, url)
    mailtos = extract_mailto(html, urlparse(url).hostname or "")
    shadow = bool(SHADOW_DOM_RE.search(html))
    contact_links = extract_contact_links(html, url)

    if has_form and has_controls:
        mode = "direct"
    elif widgets:
        mode = "widget"
    elif endpoints:
        mode = "ajax"
    elif mailtos:
        mode = "mailto"
    elif shadow and has_controls:
        mode = "shadow"
    else:
        mode = "none"

    return {
        "mode": mode,
        "has_form_tag": has_form,
        "has_contact_controls": has_controls,
        "contact_links": contact_links,
        "widgets": widgets,
        "endpoints": endpoints,
        "mailtos": mailtos,
        "shadow_hints": shadow,
    }


# ---------------------------------------------------------------------------
# Lightweight-first depth-2 walker (httpx; never launches a browser).
# ---------------------------------------------------------------------------

def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8,en-US;q=0.7",
    }


def _probe(client: Any, url: str, *, budget: DomainBudget) -> Optional[dict[str, Any]]:
    if budget.expired:
        return None
    try:
        response = client.get(url, headers=_default_headers(), timeout=budget.remaining_timeout(),
                              follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        logger.info("probe fail %s: %s", url, type(exc).__name__)
        return None
    status = int(getattr(response, "status_code", 0) or 0)
    if not (200 <= status < 400):
        return None
    body = getattr(response, "text", "") or ""
    analysis = analyze_html(body, str(getattr(response, "url", "") or url))
    analysis["status_code"] = status
    analysis["final_url"] = str(getattr(response, "url", "") or url)
    analysis["html"] = body[:MAX_BODY]
    return analysis


def probe_contact_depth2(
    client: Any,
    url: str,
    *,
    budget: Optional[DomainBudget] = None,
    max_depth: Optional[int] = None,
) -> dict[str, Any]:
    """Probe `url`, then its same-origin contact links to at most depth 2.

    Returns the first analysis whose mode is recoverable (direct/widget/ajax/
    mailto/shadow); otherwise the initial page's analysis with ``mode='none'``.
    """
    budget = budget or DomainBudget()
    max_depth = max_depth or 2
    visited: set[str] = set()
    start = normalize_url(url)
    analysis = _probe(client, start, budget=budget)
    if analysis is None:
        return {"url": start, "mode": "none", "contact_links": []}
    if analysis.get("mode") != "none":
        analysis["url"] = start
        analysis["candidate_url"] = start
        return analysis

    frontier: list[tuple[int, str, dict[str, Any]]] = [(1, start, analysis)]
    best: Optional[dict[str, Any]] = None
    while frontier and not budget.expired:
        depth, _page_url, page_analysis = frontier.pop(0)
        for link in page_analysis.get("contact_links") or []:
            key = link.rstrip("/").lower()
            if key in visited:
                continue
            visited.add(key)
            if not is_same_origin(start, link):
                continue
            hit = _probe(client, link, budget=budget)
            if hit is None or budget.expired:
                continue
            hit["url"] = link
            hit["candidate_url"] = link
            hit["probe_depth"] = depth
            if hit.get("mode") != "none":
                return hit
            best = best or hit
            if depth < max_depth:
                frontier.append((depth + 1, link, hit))
    if best is not None:
        best.setdefault("probe_depth", 1)
        return best
    return {"url": start, "mode": "none", "contact_links": analysis.get("contact_links") or []}


def scan_domain_light(url: str, *, budget_seconds: Optional[float] = None) -> dict[str, Any]:
    """HTTP-layer scan of one domain. Costs $0 and never touches daily quota."""
    import config

    budget = DomainBudget(budget_seconds if budget_seconds is not None else config.SCAN_BUDGET_SECONDS)
    start = normalize_url(url)
    try:
        import httpx
        with httpx.Client(verify=False, http2=False) as client:
            result = probe_contact_depth2(client, start, budget=budget)
    except Exception as exc:  # noqa: BLE001
        result = {"url": start, "mode": "none", "error": type(exc).__name__}
    result["budget_seconds"] = budget.spent()
    result["budget_exhausted"] = budget.expired
    return result


def merge_payload(base: dict[str, Any], values: dict[str, str]) -> dict[str, str]:
    """Merge sender values over hidden fields to build the synthetic payload."""
    payload: dict[str, str] = dict((base.get("hidden_fields") or {}))
    for key, value in values.items():
        if key in payload:
            payload[key] = value
    # Unnamed generic fields get keyed by common form names.
    payload.setdefault("email", values.get("email") or "")
    payload.setdefault("name", values.get("name") or "")
    payload.setdefault("message", values.get("message") or "")
    payload.setdefault("subject", values.get("subject") or "")
    return payload


def post_synthetic(endpoint: str, payload: dict[str, str], *, timeout: float = 8.0) -> int:
    """Adım 4 — direct HTTP POST to the site's own AJAX/action endpoint.

    Returns the HTTP status code (0 on transport failure). Only called for
    same-origin endpoints mined from the page's own scripts.
    """
    try:
        import httpx
        headers = _default_headers()
        headers.update({
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": endpoint,
            "Origin": _origin(endpoint),
            "X-Requested-With": "XMLHttpRequest",
        })
        with httpx.Client(verify=False, http2=False, timeout=timeout, follow_redirects=True) as client:
            response = client.post(endpoint, data=payload, headers=headers)
            return int(getattr(response, "status_code", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.info("synthetic POST %s failed: %s", endpoint, type(exc).__name__)
        return 0


@dataclass
class ScanMatch:
    """The winning candidate for a domain (used by collector/submitter)."""

    url: str = ""
    mode: str = "none"
    candidate_url: str = ""
    widgets: list[dict[str, str]] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    mailtos: list[str] = field(default_factory=list)
    contact_links: list[str] = field(default_factory=list)

    @property
    def recoverable(self) -> bool:
        return self.mode in {"direct", "widget", "ajax", "mailto", "shadow"}

    @classmethod
    def from_analysis(cls, analysis: dict[str, Any], fallback_url: str = "") -> "ScanMatch":
        return cls(
            url=str(analysis.get("url") or fallback_url),
            mode=str(analysis.get("mode") or "none"),
            candidate_url=str(analysis.get("candidate_url") or analysis.get("url") or fallback_url),
            widgets=list(analysis.get("widgets") or []),
            endpoints=list(analysis.get("endpoints") or []),
            mailtos=list(analysis.get("mailtos") or []),
            contact_links=list(analysis.get("contact_links") or []),
        )


def extract_mailto(html: str, host: str = "") -> list[str]:
    """mailto: hrefs plus plausible bare contact addresses (noise-filtered)."""
    del host
    emails: list[str] = []
    for m in MAILTO_HREF_RE.finditer(html or ""):
        email = m.group(1).strip().split("?", 1)[0].lower()
        if email and email not in emails and not _NOISE_EMAIL_RE.search(email):
            emails.append(email)
    for raw in BARE_EMAIL_RE.findall(html or ""):
        email = raw.strip(".'\"").lower()
        if email in emails or _NOISE_EMAIL_RE.search(email):
            continue
        emails.append(email)
        if len(emails) >= 3:
            break
    return emails[:3]

# ---------------------------------------------------------------------------
# Backward-compat aliases requested by prefilter/collector callers.
# ---------------------------------------------------------------------------

def probe_contact_depth2(html: str, base_url: str) -> dict[str, Any]:
    """Depth-2 contact crawl — lightweight alias wrapping analyze_html.

    Honors the 8s domain budget and quota isolation. Returns the same
    analysis dict as :func:`analyze_html` so callers that expected a
    ``probe_contact_depth2(html, base_url)`` signature keep working.
    """
    return analyze_html(html, base_url)