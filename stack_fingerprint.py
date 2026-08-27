"""
Wappalyzer-style platform fingerprint from HTML source, script URLs, cookies
and response headers.

Visible page text is deliberately NOT a signal: a WooCommerce shop whose blog
mentions "Shopify" must never receive the Shopify pitch. A platform is only
reported as confirmed when its evidence outscores every rival by a margin,
so the contact-form hook can fall back to a platform-neutral variant instead
of guessing.
"""

from __future__ import annotations

import re
from typing import Any

# platform -> ((regex, weight, evidence label), ...)
# Weight 3 = source-exclusive artifact, 2 = strong, 1 = weak corroboration.
_RULES: dict[str, tuple[tuple[str, int, str], ...]] = {
    "Shopify": (
        (r"cdn\.shopify\.com", 3, "cdn.shopify.com"),
        (r"/cdn/shop(?:ifycloud)?/", 3, "/cdn/shop/ assets"),
        (r"\bShopify\.(?:theme|shop|routes|currency)\b", 3, "Shopify JS globals"),
        (r"\.myshopify\.com", 3, "myshopify.com host"),
        (r"x-shopid|x-shopify-stage", 3, "x-shopify header"),
        (r"_shopify_[ysd]\b|cart_currency=", 2, "Shopify cookie"),
        (r"shopify-features|shopify_pay|shop_pay", 2, "Shopify checkout script"),
        (r"monorail-edge\.shopifysvc\.com", 2, "Shopify telemetry"),
    ),
    "WooCommerce": (
        (r"wp-content/plugins/woocommerce", 3, "woocommerce plugin assets"),
        (r"wc_add_to_cart_params|woocommerce_params|wc-cart-fragments", 3, "wc JS params"),
        (r"\?wc-ajax=", 3, "wc-ajax endpoint"),
        (r"woocommerce-page|woocommerce-js|class=\"[^\"]*woocommerce", 2, "woocommerce body class"),
        (r"woocommerce_cart_hash|woocommerce_items_in_cart", 2, "woocommerce cookie"),
        (r"/wp-json/wc/v[123]", 3, "WooCommerce REST v3"),
    ),
    "WordPress": (
        (r"wp-content/themes/", 3, "wp-content/themes"),
        (r"wp-includes/js/", 3, "wp-includes assets"),
        (r"name=\"generator\"[^>]*WordPress", 3, "generator: WordPress"),
        (r"/wp-json/wp/v2", 2, "wp-json REST"),
        (r"wp-emoji-release|wp-block-library", 2, "wp core assets"),
    ),
    "Magento": (
        (r"static/version\d+/frontend/", 3, "Magento static/version"),
        (r"Magento_(?:Ui|Catalog|Checkout|Theme)", 3, "Magento_ modules"),
        (r"mage/cookies|mage-cache-storage|requirejs/mage", 3, "Magento mage/ JS"),
        (r"x-magento-|magento-vary", 3, "x-magento header"),
        (r"/pub/static/|/pub/media/", 2, "Magento pub paths"),
    ),
    "IdeaSoft": (
        (r"ideacdn\.net|ideasoft\.com\.tr", 3, "ideacdn.net assets"),
        (r"ideasoft", 2, "ideasoft marker"),
    ),
    "Ticimax": (
        (r"ticimax\.cloud|cdn\.ticimax|ticimaxcdn", 3, "ticimax CDN"),
        (r"ticimax", 2, "ticimax marker"),
    ),
    "T-Soft": (
        (r"tsoftcdn|t-soft\.com\.tr|/tsoft/", 3, "T-Soft assets"),
        (r"tsoft", 1, "tsoft marker"),
    ),
    "ikas": (
        (r"ikas\.com|ikascdn|myikas\.com", 3, "ikas CDN"),
    ),
    "Akinon": (
        (r"akinon(?:cloud)?\.(?:com|net)|akinoncdn", 3, "Akinon assets"),
    ),
    "PrestaShop": (
        (r"prestashop", 2, "prestashop marker"),
        (r"/modules/ps_|prestashop-\d", 3, "PrestaShop modules"),
    ),
    "OpenCart": (
        (r"catalog/view/(?:theme|javascript)/", 3, "OpenCart catalog/view"),
        (r"index\.php\?route=(?:product|checkout)", 3, "OpenCart route param"),
    ),
    "Wix": (
        (r"static\.parastorage\.com|wixstatic\.com", 3, "Wix static hosts"),
        (r"X-Wix-|wix-code", 3, "x-wix header"),
    ),
    "Squarespace": (
        (r"squarespace\.com|static1\.squarespace", 3, "Squarespace assets"),
    ),
    "BigCommerce": (
        (r"cdn\d*\.bigcommerce\.com|bigcommerce\.com/s-", 3, "BigCommerce CDN"),
    ),
    "Shopier": (
        (r"shopier\.com", 3, "Shopier host"),
    ),
}

# Payment / ops layer. Reported next to the platform, never as the platform.
_SECONDARY: tuple[tuple[str, str], ...] = (
    (r"iyzico|iyzipay", "iyzico"),
    (r"paytr\.com|paytr_", "PayTR"),
    (r"craftgate", "Craftgate"),
    (r"js\.stripe\.com|stripe\.com/v3", "Stripe"),
    (r"paypal\.com/sdk|paypalobjects", "PayPal"),
    (r"js\.hs-scripts\.com|hsforms\.net", "HubSpot"),
    (r"googletagmanager\.com/gtm", "GTM"),
    (r"/wp-json/|/graphql\b", "REST/GraphQL"),
)

# WooCommerce/Woo implies WordPress; do not let the parent outrank the child.
_IMPLIES: dict[str, tuple[str, ...]] = {
    "WooCommerce": ("WordPress",),
}

MIN_SCORE = 3
MIN_MARGIN = 2


def _blob(
    *,
    html: str = "",
    headers: Any = None,
    cookies: Any = None,
    scripts: Any = None,
    url: str = "",
) -> str:
    parts: list[str] = [url or "", html or ""]
    if headers:
        try:
            items = headers.items() if hasattr(headers, "items") else headers
            parts.append(" ".join(f"{k}:{v}" for k, v in items))
        except Exception:  # noqa: BLE001
            pass
    if cookies:
        try:
            for row in cookies:
                if isinstance(row, dict):
                    parts.append(f"{row.get('name', '')}={row.get('value', '')}")
                else:
                    parts.append(str(row))
        except Exception:  # noqa: BLE001
            pass
    if scripts:
        try:
            parts.append(" ".join(str(src) for src in scripts if src))
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(parts)


def fingerprint(
    *,
    html: str = "",
    headers: Any = None,
    cookies: Any = None,
    scripts: Any = None,
    url: str = "",
) -> dict[str, Any]:
    """Score every platform, then only confirm a clear winner.

    Returns platform ("" when unsure), confidence, evidence tokens and the
    secondary payment/ops layer.
    """
    blob = _blob(html=html, headers=headers, cookies=cookies, scripts=scripts, url=url)
    if not blob.strip():
        return {"platform": "", "confidence": 0, "evidence": [], "secondary": [], "scores": {}}

    scores: dict[str, int] = {}
    evidence: dict[str, list[str]] = {}
    for platform, rules in _RULES.items():
        total = 0
        hits: list[str] = []
        for pattern, weight, label in rules:
            if re.search(pattern, blob, re.I):
                total += weight
                hits.append(label)
        if total:
            scores[platform] = total
            evidence[platform] = hits

    for child, parents in _IMPLIES.items():
        if scores.get(child, 0) >= MIN_SCORE:
            for parent in parents:
                scores.pop(parent, None)
                evidence.pop(parent, None)

    secondary = [label for pattern, label in _SECONDARY if re.search(pattern, blob, re.I)]

    if not scores:
        return {"platform": "", "confidence": 0, "evidence": [], "secondary": secondary, "scores": {}}

    ranked = sorted(scores.items(), key=lambda item: -item[1])
    top, top_score = ranked[0]
    runner_score = ranked[1][1] if len(ranked) > 1 else 0
    confirmed = top_score >= MIN_SCORE and (top_score - runner_score) >= MIN_MARGIN

    return {
        "platform": top if confirmed else "",
        "candidate": top,
        "confidence": int(top_score),
        "evidence": evidence.get(top, [])[:4],
        "secondary": secondary[:4],
        "scores": dict(ranked[:4]),
    }


def hints_from(result: dict[str, Any]) -> list[str]:
    """Platform first, then payment/ops layer — the brief the closer reads."""
    out: list[str] = []
    platform = str(result.get("platform") or "")
    if platform:
        out.append(platform)
    for label in result.get("secondary") or []:
        if label not in out:
            out.append(label)
    return out[:6]


# A platform claim may only come from source evidence, never from page copy.
PLATFORM_WORDS = frozenset(
    {
        "shopify",
        "woocommerce",
        "wordpress",
        "magento",
        "prestashop",
        "opencart",
        "ideasoft",
        "ticimax",
        "t-soft",
        "ikas",
        "akinon",
        "wix",
        "squarespace",
        "bigcommerce",
        "shopier",
    }
)


def merge_hints(result: dict[str, Any], text_hints: list[str] | None) -> list[str]:
    out = hints_from(result)
    for hint in text_hints or []:
        if hint not in out and str(hint).lower() not in PLATFORM_WORDS:
            out.append(hint)
    return out[:6]
