"""URL/domain normalization helpers shared by the discovery->enrichment->audit->tactic->stealth chain.

Prevents the classic "double-scheme" corruption where a ``domain`` field already
carries a full URL and a caller then builds ``f"https://{domain}/"`` producing
``https://https://host/path`` — which then fails DNS in every downstream probe.

Every producer in the chain must route its URL and domain fields through here so
state files only ever store clean, single-scheme public HTTPS URLs and bare
hostnames.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_DOUBLE_SCHEME = re.compile(r"^(https?://)+", re.I)
_ALLOWED_SCHEMES = ("http://", "https://")


def clean_url(raw: str | None) -> str:
    """Return a single-scheme http(s) URL or ''.

    * strips whitespace and dangling '/'-only tails
    * collapses repeated schemes (https://https://... -> https://...)
    * prepends https:// when no scheme is present
    * rejects anything that is not a parseable public URL
    """
    value = (raw or "").strip()
    if not value:
        return ""
    # Collapse any number of repeated scheme prefixes.
    lowered_start = value.lower()
    if lowered_start.startswith(("http://", "https://")):
        rest = _DOUBLE_SCHEME.sub("", value, count=1)
        scheme = "https" if lowered_start.startswith("https") else "http"
        value = f"{scheme}://{rest.lstrip('/')}"
    elif value.startswith("//"):
        value = "https:" + value
    else:
        value = "https://" + value
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    host = parts.hostname or ""
    if not host or "." not in host:
        return ""
    # Rebuild canonical, minimal URL.
    host_port = host if not parts.port else f"{host}:{parts.port}"
    path = parts.path or "/"
    # Keep the trailing slash only when the path is exactly "/".
    if path == "/":
        path = "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme}://{host_port}{path}{query}"


def clean_domain(raw: str | None) -> str:
    """Bare hostname (no scheme, no path, no www) from a URL or domain string.

    Tolerates repeated scheme prefixes (https://https://...) left behind by
    legacy state files.
    """
    value = (raw or "").strip().lower()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    # urlparse treats "https://https://host/x" as netloc "https:" — collapse
    # repeated schemes first so the hostname is actually parsed.
    if value.startswith(("http://", "https://", "//")) and (not parts.hostname or "." not in (parts.hostname or "")):
        collapsed = clean_url(value)
        if collapsed:
            parts = urlsplit(collapsed)
    elif not value.startswith(("http://", "https://", "//")):
        parts = urlsplit("https://" + value)
    try:
        return (parts.hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def is_http_url(raw: str | None) -> bool:
    """True only for a parseable http(s) URL with a dotted host."""
    value = (raw or "").strip().lower()
    if not value.startswith(_ALLOWED_SCHEMES):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    host = parts.hostname or ""
    return bool(parts.scheme and host and "." in host)


def safe_url(raw: str | None) -> str:
    """Best-effort clean URL: clean_url when possible, else ''."""
    cleaned = clean_url(raw)
    return cleaned if is_http_url(cleaned) else ""