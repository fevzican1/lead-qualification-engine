"""Lightweight technical gap analysis from fetched HTML (no Playwright).

Designed to run on GitHub Actions so Oracle HTTP probe budget stays untouched.
"""

from __future__ import annotations

import re
from typing import Any

_CHECKOUT_RE = re.compile(
    r"/checkout|/cart|/sepet|\bcheckout\b|\bcart\b|add[-_ ]to[-_ ]cart|sepete ekle|buy now|satın al|"
    r"woocommerce-checkout|shopify-checkout|/odeme|/ödeme",
    re.I,
)
_PAYMENT_RE = re.compile(
    r"iyzico|paytr|craftgate|shopier|stripe\.com|paypal\.com|shopify\.payments|"
    r"klarna|adyen|braintree|mollie",
    re.I,
)
_SEO_TITLE_RE = re.compile(r"<title[^>]*>\s*([^<]{2,120})\s*</title>", re.I)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_CANONICAL_RE = re.compile(r'rel=["\']canonical["\']', re.I)
_H1_RE = re.compile(r"<h1[^>]*>([^<]{2,120})</h1>", re.I)


def analyze(
    html: str,
    *,
    headers: dict[str, str] | None = None,
    fingerprint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return structured technical gaps used to personalize outreach copy."""
    blob = html or ""
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    gaps: list[str] = []
    notes: list[str] = []

    title_match = _SEO_TITLE_RE.search(blob)
    if not title_match or len(title_match.group(1).strip()) < 4:
        gaps.append("missing_or_weak_title")
        notes.append("Sayfa başlığı zayıf veya eksik; organik tıklama ve güven sinyali düşük.")
    meta_match = _META_DESC_RE.search(blob)
    if not meta_match or len(meta_match.group(1).strip()) < 24:
        gaps.append("missing_meta_description")
        notes.append("Meta description yok; arama/snippet tarafında dönüşüm kaybı riski.")
    if not _CANONICAL_RE.search(blob):
        gaps.append("missing_canonical")
        notes.append("Canonical etiket yok; indeks çoğaltması SEO gürültüsü yaratabilir.")
    if not _H1_RE.search(blob):
        gaps.append("missing_h1")
        notes.append("Tek bir net H1 yok; landing/checkout mesajı dağınık kalabilir.")

    checkout_surface = bool(_CHECKOUT_RE.search(blob))
    payment_present = bool(_PAYMENT_RE.search(blob))
    if checkout_surface and not payment_present:
        gaps.append("checkout_without_clear_payment_marker")
        notes.append("Checkout yüzeyi var ama ödeme katmanı HTML'de net işaretlenmemiş.")
    if payment_present and checkout_surface:
        gaps.append("checkout_payment_stack_exposed")
        notes.append("Checkout + ödeme katmanı birlikte görünüyor; webhook/callback senkronu kritik.")

    fp = fingerprint or {}
    platform = str(fp.get("platform") or fp.get("candidate") or "").strip()
    confidence = int(fp.get("confidence") or 0)
    if platform and confidence >= 95:
        gaps.append(f"platform_{platform.lower()}_confirmed")
    elif platform:
        gaps.append("platform_unconfirmed")
        notes.append("Platform adı tek kaynak kanıtıyla doğrulanamadı; nötr teknik kanca kullanılacak.")

    cache_control = hdrs.get("cache-control", "")
    if "no-store" not in cache_control.lower() and checkout_surface:
        gaps.append("checkout_cache_headers_soft")
        notes.append("Checkout yüzeyinde agresif cache yok; oturum/idempotency riski artabilir.")

    return {
        "gaps": gaps[:12],
        "notes": notes[:6],
        "checkout_surface": checkout_surface,
        "payment_present": payment_present,
        "seo_score": max(0, 100 - len(gaps) * 8),
        "platform": platform,
        "platform_confidence": confidence,
    }
