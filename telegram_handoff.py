"""Map a form click to a Telegram /start payload.

Telegram start params are 1–64 chars [A-Za-z0-9_-]. We store the site
brief on disk so the closer already knows the stack — no extra model,
no extra Oracle shape.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import config
import knowledge

logger = logging.getLogger(__name__)

PATH = config.ROOT / "telegram_handoffs.json"
_MAX = 8000


def _host(url: str) -> str:
    raw = (url or "").strip()
    if raw and not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower().removeprefix("www.")
    return host


def classify_hook(hints: list[str] | None) -> dict[str, str]:
    """A Woo/WP, B Shopify, C Magento/custom — same hook on the form and in Telegram."""
    joined = " ".join(str(h) for h in (hints or []) if h)
    low = joined.lower()
    if "shopify" in low:
        return {
            "variant": "B",
            "stack_name": "Shopify",
            "error_type": "Storefront API Cart Attribute payload uyuşmazlığı",
            "error_type_en": "Storefront API cart-attribute payload mismatch",
        }
    if "woocommerce" in low:
        return {
            "variant": "A",
            "stack_name": "WooCommerce",
            "error_type": "REST API v3 webhook retry delay / checkout zaman aşımı",
            "error_type_en": "REST API v3 webhook retry delay / checkout timeout",
        }
    if "wordpress" in low or "woocommerce" in low:
        return {
            "variant": "A",
            "stack_name": "WordPress",
            "error_type": "REST API v3 webhook retry delay / checkout zaman aşımı",
            "error_type_en": "REST API v3 webhook retry delay / checkout timeout",
        }
    if "magento" in low:
        stack_name = "Magento"
    elif joined.strip():
        stack_name = knowledge.stack_phrase(list(hints or []), turkish=True) or "özel yazılım"
    else:
        stack_name = "özel yazılım"
    return {
        "variant": "C",
        "stack_name": stack_name,
        "error_type": "checkout pipeline session timeout & duplicate payload çakışması",
        "error_type_en": "checkout pipeline session timeout and duplicate payload collision",
    }


def form_copy(
    *,
    host: str,
    hints: list[str],
    link: str,
    turkish: bool,
) -> tuple[str, str]:
    hook = classify_hook(hints)
    domain = host or "siteniz"
    stack = hook["stack_name"]
    err = hook["error_type"] if turkish else hook["error_type_en"]
    price = config.price_label()
    variant = hook["variant"]
    if turkish:
        if variant == "A":
            subject = f"[Sistem Bildirimi] {stack} REST API v3 - Webhook / Checkout Zaman Aşımı Hatası"
            body = (
                f"Merhaba {domain} Teknik Ekibi, "
                f"sitenizdeki {stack} checkout adımlarında yaptığımız altyapı okumasında, "
                f"ödeme ağ geçidi ile REST API v3 arasındaki webhook retry delay nedeniyle "
                f"bazı siparişlerin Beklemede (Pending) statüsünde takılma ve sepet kaybı riski "
                f"görülüyor. Etkilenen order-payload şeması ve {price} teknik iyileştirme raporu "
                f"hazır. Mimari analiz kartı (şablon diyagram, canlı admin ekranı değil): {link}"
            )
        elif variant == "B":
            subject = "[Entegrasyon Uyarısı] Shopify Storefront API & Cart Attribute Payload Uyuşmazlığı"
            body = (
                f"Merhaba {domain} E-Ticaret Ekibi, "
                f"Shopify altyapınızdaki özelleştirilmiş sepet ve checkout geçişlerinde "
                f"Storefront API cart attribute senkron gecikmesi checkout dönüşümünü (CVR) "
                f"sallayabilir. Hatalı tetiklenen event akışı ve çözüm mimarisi Telegram'da. "
                f"Sitenize özel teknik analiz kartı: {link}"
            )
        else:
            subject = "[Teknik Rapor] Checkout Pipeline Session Timeout & Duplicate Payload Çakışması"
            body = (
                f"Merhaba {domain} Mühendislik Ekibi, "
                f"ödeme akışındaki (checkout pipeline) HTTP POST tarafında yüksek trafikte "
                f"session timeout ve idempotency key eksikliği kaynaklı sessiz veri kaybı riski "
                f"var ({stack}: {err}). Tespit edilen darboğaz ve mimari kart hazır. "
                f"Teknik detay: {link}"
            )
    else:
        if variant == "A":
            subject = f"[System notice] {stack} REST API v3 — webhook / checkout timeout"
            body = (
                f"Hello {domain} engineering, "
                f"on your {stack} checkout we read a webhook retry delay between the payment "
                f"gateway and REST API v3 — orders can sit Pending and leak the cart. "
                f"Order-payload sketch and {price} fix note: {link}"
            )
        elif variant == "B":
            subject = "[Integration warning] Shopify Storefront API & cart attribute mismatch"
            body = (
                f"Hello {domain} commerce team, "
                f"custom cart → checkout on Shopify can desync Storefront API cart attributes "
                f"and wobble CVR. Event flow and the architecture card: {link}"
            )
        else:
            subject = "[Technical report] Checkout pipeline session timeout & duplicate payload"
            body = (
                f"Hello {domain} engineering, "
                f"checkout HTTP POSTs under load can drop silently without an idempotency key "
                f"({stack}: {err}). Bottleneck note and architecture card: {link}"
            )
    return subject[:120], " ".join(body.split())


def token_for(url: str) -> str:
    host = _host(url) or "unknown"
    digest = hashlib.sha256(host.encode("utf-8")).hexdigest()[:10]
    return f"ds{digest}"


def _load() -> dict[str, Any]:
    if not PATH.exists():
        return {}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    if len(data) > _MAX:
        rows = sorted(
            data.items(),
            key=lambda item: str((item[1] or {}).get("at") or ""),
        )
        data = dict(rows[-_MAX:])
    tmp = PATH.with_suffix(PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(PATH)


def remember(
    lead: dict[str, Any],
    *,
    company: str,
    pain: str,
    quote: str,
    turkish: bool,
) -> str:
    url = str(lead.get("url") or "")
    token = token_for(url)
    hints = [str(h) for h in (lead.get("stack_hints") or []) if h][:6]
    hook = classify_hook(hints)
    err = hook["error_type"] if turkish else hook["error_type_en"]
    data = _load()
    data[token] = {
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "url": url,
        "host": _host(url),
        "company": (company or "").strip()[:48],
        "hints": hints,
        "pain": (pain or "").strip()[:240],
        "quote": (quote or "").strip()[:180],
        "excerpt": str(lead.get("page_excerpt") or lead.get("description") or "").strip()[:180],
        "turkish": bool(turkish),
        "stack": hook["stack_name"],
        "variant": hook["variant"],
        "error_type": err,
    }
    _save(data)
    return token


def lookup(token: str) -> dict[str, Any] | None:
    raw = re.sub(r"[^A-Za-z0-9_-]", "", (token or "").strip())[:64]
    if not raw:
        return None
    row = _load().get(raw)
    return dict(row) if isinstance(row, dict) else None


def brief_block(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    quote = str(row.get("quote") or "").strip()
    excerpt = str(row.get("excerpt") or "").strip()
    seen = f' Public page line: "{quote}"' if quote else ""
    extra = f" Page excerpt: {excerpt}" if excerpt and excerpt != quote else ""
    hints = ", ".join(str(h) for h in (row.get("hints") or []) if h)
    return (
        f"Inbound from our contact-form note (this exact lead). "
        f"Company/domain: {row.get('company') or row.get('host')}. Host: {row.get('host')}. "
        f"URL: {row.get('url')}. Stack: {row.get('stack') or hints}. "
        f"Hook variant: {row.get('variant') or '?'}. Error type: {row.get('error_type') or row.get('pain')}. "
        f"Detected hints: {hints or 'none'}.{seen}{extra} "
        f"Do not re-ask which platform they use. Do not invent a different stack or a measured $ loss. "
        f"The {config.price_label()} figure is the scoped job fee for the checkout/payment bridge, not a lab measurement of their GMV. "
        f"Treat this as already seen from the form they received."
    )


def opener(row: dict[str, Any]) -> str:
    who = str(row.get("host") or row.get("company") or "ekibiniz")
    stack = str(row.get("stack") or classify_hook(list(row.get("hints") or [])).get("stack_name") or "altyapınız")
    err = str(row.get("error_type") or row.get("pain") or "checkout kopuğu").rstrip(".")
    if row.get("turkish"):
        return (
            f"Merhaba {who} ekibi. {stack} altyapınızdaki {err} hatasına istinaden "
            f"oluşturduğumuz teknik rapor için geldiniz, hoş geldiniz. "
            f"Idempotency / webhook retry / order-id kilidi bu sohbette. "
            f"Kart ~1 dakikada gelir; şablon diyagramdır, canlı admin ekranı değil."
        )
    return (
        f"Hello {who} team. You are here for the technical note on {err} in your {stack} stack — welcome. "
        f"Idempotency / webhook retry / order-id lock live in this chat. "
        f"The card arrives in about a minute; it is a schematic, not a live admin screenshot."
    )
