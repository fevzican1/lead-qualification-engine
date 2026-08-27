"""Easy-submit score 0–100. Chromium only spends the hourly 20 on high scores."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

def from_contact_url(url: str) -> tuple[int, str]:
    """Score a public contact URL without fetching it (Common Crawl / feed)."""
    raw = (url or "").strip().lower()
    host = (urlparse(raw).hostname or "").lower().removeprefix("www.")
    path = urlparse(raw).path or "/"
    stack = "contact"
    score = 0
    if re.match(r"^[0-9a-z]{4,10}-[0-9a-z]{1,4}\.myshopify\.com$", host):
        return 40, "shopify-junk"
    if "demo" in host.split(".")[0] or "1001demo" in host:
        return 20, "demo"
    if "myshopify.com" in host or "/cdn/shop" in raw or "/pages/contact" in path:
        stack = "shopify"
        score = 86
    elif "hsforms" in raw or "hubspot" in raw:
        stack = "hubspot"
        score = 90
    elif "woocommerce" in raw or "wp-content" in raw or "/wp-json/" in raw:
        stack = "woocommerce"
        score = 88
    elif re.search(r"/(iletisim|iletişim)(/|$)", path):
        stack = "iletisim"
        score = 88 if host.endswith(".tr") else 84
    elif re.search(r"/(contact-us|contactus|get-in-touch|bize-ulasin)(/|$)", path):
        stack = "contact"
        score = 86
    elif re.search(r"/contact(/|$)", path):
        stack = "contact"
        score = 82
    if host.endswith(".com.tr"):
        score = min(100, score + 6)
    elif host.endswith(".tr"):
        score = min(100, score + 2)
    if host.endswith(".org.tr"):
        score = min(score, 78)
    return clamp(score), stack


def clamp(score: int) -> int:
    return max(0, min(100, int(score)))


def from_probe(item: dict[str, Any]) -> int:
    if item.get("captcha"):
        return 10
    if item.get("easy_form") and item.get("form_likely"):
        score = 90
    elif item.get("form_likely"):
        score = 75
    elif item.get("ok"):
        score = 40
    else:
        score = 20
    if item.get("waf_strict") and score > 35:
        score = 35
    host = (urlparse(str(item.get("url") or "")).hostname or "").lower()
    if host.endswith(".com.tr") or host.endswith(".tr"):
        score = min(100, score + 5)
    return clamp(score)


def from_head(*, contact_ok: bool, origin_ok: bool, sitemap_ok: bool = False) -> int:
    if contact_ok:
        return 70
    if sitemap_ok and origin_ok:
        return 55
    if origin_ok:
        return 40
    return 0


def from_lead(lead: dict[str, Any]) -> int:
    stored = lead.get("easy_score")
    if stored is not None:
        try:
            return clamp(int(stored))
        except (TypeError, ValueError):
            pass
    if lead.get("captcha_detected") or str(lead.get("status") or "") == "skipped_captcha":
        return 10
    form = (lead.get("contact_form") or {}).get("found")
    if form and not lead.get("waf_strict"):
        return 85
    if form:
        return 45
    return 30
