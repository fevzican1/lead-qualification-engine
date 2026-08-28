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


# Platform -> hook. Only a source-confirmed platform may open a named variant;
# anything unconfirmed falls to the platform-neutral variant so a WooCommerce
# shop can never receive Shopify copy.
_HOOKS: dict[str, dict[str, str]] = {
    "Shopify": {
        "variant": "B",
        "error_type": "Storefront API cart attribute payload uyuşmazlığı",
        "error_type_en": "Storefront API cart-attribute payload mismatch",
        "probe": "checkout script akışında cart attribute senkronu",
        "probe_en": "cart-attribute sync in the checkout script flow",
    },
    "WooCommerce": {
        "variant": "A",
        "error_type": "REST API v3 webhook retry delay / checkout zaman aşımı",
        "error_type_en": "REST API v3 webhook retry delay / checkout timeout",
        "probe": "wc-ajax checkout ucu ve /wp-json/wc/v3 webhook zinciri",
        "probe_en": "wc-ajax checkout endpoint and the /wp-json/wc/v3 webhook chain",
    },
    "WordPress": {
        "variant": "A",
        "error_type": "REST webhook retry delay / form-to-order zaman aşımı",
        "error_type_en": "REST webhook retry delay / form-to-order timeout",
        "probe": "/wp-json REST ucu ve form gönderim zinciri",
        "probe_en": "the /wp-json REST endpoint and the form submit chain",
    },
    "Magento": {
        "variant": "E",
        "error_type": "checkout quote-to-order dönüşümünde duplicate payload çakışması",
        "error_type_en": "duplicate payload collision on quote-to-order conversion",
        "probe": "Magento checkout quote uçları ve cache-vary başlıkları",
        "probe_en": "Magento checkout quote endpoints and cache-vary headers",
    },
}

# IdeaSoft / Ticimax / T-Soft / ikas / Akinon: TR panels, same callback problem.
_TR_PANELS = ("IdeaSoft", "Ticimax", "T-Soft", "ikas", "Akinon")
_TR_HOOK = {
    "variant": "D",
    "error_type": "ödeme callback / sipariş durumu senkron gecikmesi",
    "error_type_en": "payment callback / order-status sync delay",
    "probe": "panel ödeme callback ucu ve sipariş durum güncellemesi",
    "probe_en": "the panel payment callback endpoint and order-status update",
}
_NEUTRAL_HOOK = {
    "variant": "C",
    "error_type": "checkout pipeline session timeout & duplicate payload çakışması",
    "error_type_en": "checkout pipeline session timeout and duplicate payload collision",
    "probe": "iletişim/checkout POST akışı ve idempotency key",
    "probe_en": "the contact/checkout POST flow and the idempotency key",
}
NEUTRAL_ENGINEERING_HOOK = _NEUTRAL_HOOK
PLATFORM_CONFIDENCE_THRESHOLD = 95


def classify_hook(
    hints: list[str] | None = None,
    *,
    platform: str = "",
    confidence: int = 0,
) -> dict[str, str]:
    """Pick the hook from the source-confirmed platform, never from page copy.

    `platform` comes from stack_fingerprint. When it is empty the lead gets the
    neutral variant C: we describe the checkout POST flow without naming a
    platform we could not prove.
    """
    name = (platform or "").strip()
    confirmed = bool(name) and int(confidence or 0) >= PLATFORM_CONFIDENCE_THRESHOLD
    if not confirmed:
        name = ""

    if name in _HOOKS:
        hook = dict(_HOOKS[name])
        hook["stack_name"] = name
        hook["confirmed"] = "yes"
        hook["confidence"] = str(confidence or 0)
        return hook
    if name in _TR_PANELS:
        hook = dict(_TR_HOOK)
        hook["stack_name"] = name
        hook["confirmed"] = "yes"
        hook["confidence"] = str(confidence or 0)
        return hook

    hook = dict(_NEUTRAL_HOOK)
    hook["stack_name"] = ""
    hook["confirmed"] = "no"
    hook["confidence"] = str(confidence or 0)
    return hook


def hook_for_lead(lead: dict[str, Any], *, turkish: bool = True) -> dict[str, str]:
    del turkish
    return classify_hook(
        [str(h) for h in (lead.get("stack_hints") or []) if h],
        platform=str(lead.get("platform") or ""),
        confidence=int(lead.get("platform_confidence") or 0),
    )


def trust_note(turkish: bool) -> str:
    if turkish:
        return (
            "Güvenliğiniz için: bağlantı doğrudan resmi Telegram önizlemesidir, "
            "dosya indirmez ve giriş bilgisi istemez."
        )
    return (
        "For your safety: the link opens the official Telegram preview — "
        "no file download, no login requested."
    )


def form_copy(
    *,
    host: str,
    hints: list[str],
    link: str,
    turkish: bool,
    platform: str = "",
    confidence: int = 0,
) -> tuple[str, str]:
    hook = classify_hook(hints, platform=platform, confidence=confidence)
    domain = host or "siteniz"
    stack = hook.get("stack_name") or ""
    err = hook["error_type"] if turkish else hook["error_type_en"]
    probe = hook["probe"] if turkish else hook["probe_en"]
    price = config.price_label()
    variant = hook["variant"]

    if turkish:
        opening = f"Merhaba {domain} teknik ekibi,"
        if variant == "A":
            subject = f"[Sistem Bildirimi] {stack} REST webhook / checkout zaman aşımı"
            core = (
                f"{stack} altyapınızda {probe} üzerinde yaptığımız açık kaynak okumasında, "
                f"ödeme ağ geçidi ile REST katmanı arasındaki webhook retry gecikmesi "
                f"siparişleri Beklemede (Pending) bırakabilir ve sepet kaybı yaratabilir."
            )
        elif variant == "B":
            subject = "[Entegrasyon Uyarısı] Shopify cart attribute / checkout payload uyuşmazlığı"
            core = (
                f"Shopify altyapınızda {probe} üzerinde yaptığımız okumada, sepet → checkout "
                f"geçişinde cart attribute senkron gecikmesi dönüşümü (CVR) sallayabilir."
            )
        elif variant == "D":
            subject = f"[Entegrasyon Uyarısı] {stack} ödeme callback / sipariş senkron gecikmesi"
            core = (
                f"{stack} panelinizde {probe} tarafında, ödeme onayı ile sipariş durumunun "
                f"ayrışması siparişlerin elle takip edilmesine yol açabiliyor."
            )
        elif variant == "E":
            subject = "[Teknik Rapor] Magento quote-to-order duplicate payload çakışması"
            core = (
                f"Magento kurulumunuzda {probe} üzerinde, yüksek trafikte quote-to-order "
                f"adımında duplicate payload çakışması sessiz sipariş kaybı riski taşıyor."
            )
        else:
            subject = "[Teknik Rapor] Checkout pipeline session timeout & duplicate payload"
            core = (
                f"Sitenizde {probe} üzerinde yaptığımız okumada, yoğun trafikte session "
                f"timeout ve idempotency key eksikliği sessiz veri/sipariş kaybı riski taşıyor."
            )
        body = (
            f"{opening} {core} Tespit ettiğimiz akış ve {price} tek köprü çözümü için "
            f"hazırladığımız mimari analiz kartı (şablon diyagram, canlı admin ekranı değil) "
            f"Telegram'da hazır: {link} "
            f"Uygunsa bugün 2 saatlik uygulama slotu açabiliriz. {trust_note(True)}"
        )
    else:
        opening = f"Hello {domain} engineering,"
        if variant == "A":
            subject = f"[System notice] {stack} REST webhook / checkout timeout"
            core = (
                f"reading {probe} on your {stack} stack, a webhook retry delay between the "
                f"payment gateway and the REST layer can leave orders Pending and leak carts."
            )
        elif variant == "B":
            subject = "[Integration warning] Shopify cart attribute / checkout payload mismatch"
            core = (
                f"reading {probe} on your Shopify stack, cart → checkout can desync cart "
                f"attributes and wobble conversion."
            )
        elif variant == "D":
            subject = f"[Integration warning] {stack} payment callback / order-status delay"
            core = (
                f"on {probe} in your {stack} panel, payment approval and order status can "
                f"drift apart, pushing orders into manual follow-up."
            )
        elif variant == "E":
            subject = "[Technical report] Magento quote-to-order duplicate payload collision"
            core = (
                f"on {probe}, quote-to-order under load can collide on duplicate payloads "
                f"and drop orders silently."
            )
        else:
            subject = "[Technical report] Checkout pipeline session timeout & duplicate payload"
            core = (
                f"reading {probe}, session timeout and a missing idempotency key can drop "
                f"submissions silently under load."
            )
        body = (
            f"{opening} {core} The flow we mapped and the {price} single-bridge fix are on the "
            f"architecture card (a schematic, not a live admin screen) in Telegram: {link} "
            f"If it fits, we can open a 2-hour implementation slot today. {trust_note(False)} "
            f"({err})"
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
    hook = hook_for_lead(lead)
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
        "stack": hook.get("stack_name") or "",
        "variant": hook["variant"],
        "error_type": err,
        "probe": hook["probe"] if turkish else hook["probe_en"],
        "platform": str(lead.get("platform") or ""),
        "platform_confirmed": hook.get("confirmed") == "yes",
        "platform_evidence": [str(e) for e in (lead.get("platform_evidence") or [])][:4],
        "payment_stack": [str(p) for p in (lead.get("payment_stack") or [])][:4],
    }
    _save(data)
    return token


def lookup(token: str) -> dict[str, Any] | None:
    raw = re.sub(r"[^A-Za-z0-9_-]", "", (token or "").strip())[:64]
    if not raw:
        return None
    row = _load().get(raw)
    return dict(row) if isinstance(row, dict) else None


def import_handoffs(rows: dict[str, dict[str, Any]]) -> int:
    """Merge pre-built handoff rows from GitHub payload_optimizer."""
    if not rows:
        return 0
    data = _load()
    n = 0
    for token, row in rows.items():
        clean = re.sub(r"[^A-Za-z0-9_-]", "", str(token or "").strip())[:64]
        if not clean or not isinstance(row, dict):
            continue
        merged = dict(row)
        merged.setdefault("at", datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        data[clean] = merged
        n += 1
    if n:
        _save(data)
        logger.info("Imported %s Telegram handoff row(s) from optimizer", n)
    return n


def brief_block(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    quote = str(row.get("quote") or "").strip()
    excerpt = str(row.get("excerpt") or "").strip()
    seen = f' Public page line: "{quote}"' if quote else ""
    extra = f" Page excerpt: {excerpt}" if excerpt and excerpt != quote else ""
    hints = ", ".join(str(h) for h in (row.get("hints") or []) if h)
    evidence = ", ".join(str(e) for e in (row.get("platform_evidence") or []) if e)
    pay = ", ".join(str(p) for p in (row.get("payment_stack") or []) if p)
    if row.get("platform_confirmed"):
        stack_line = (
            f"Platform (confirmed from page source, evidence: {evidence or 'source markers'}): "
            f"{row.get('platform') or row.get('stack')}. "
            f"Speak about this platform only — never name a different one."
        )
    else:
        stack_line = (
            "Platform NOT confirmed from source. Never name a platform "
            "(no Shopify/WooCommerce/Magento guess). Stay on the generic checkout POST / "
            "idempotency flow, or ask them once which panel they run."
        )
    return (
        f"Inbound from our contact-form note (this exact lead). "
        f"Company/domain: {row.get('company') or row.get('host')}. Host: {row.get('host')}. "
        f"URL: {row.get('url')}. {stack_line} "
        f"Payment/ops layer seen: {pay or 'none'}. Detected hints: {hints or 'none'}. "
        f"Hook variant: {row.get('variant') or '?'}. Error type: {row.get('error_type') or row.get('pain')}. "
        f"What we actually inspected: {row.get('probe') or 'public checkout/contact flow'} "
        f"(public pages only — we have no access to their admin, server logs or database; "
        f"never claim we read their logs).{seen}{extra} "
        f"Do not re-ask which platform they use when it is confirmed above. "
        f"Do not invent a measured $ loss. "
        f"The {config.price_label()} figure is the scoped job fee for the checkout/payment bridge, "
        f"not a lab measurement of their GMV. "
        f"Closing goal: get a booked slot — 'bu akışı bugün 2 saatlik bir uygulama slotunda "
        f"kalıcı olarak kapatabiliriz, randevu oluşturalım mı?' — offered once the technical "
        f"point lands, never as pressure. "
        f"Treat this as already seen from the form they received."
    )


def opener(row: dict[str, Any]) -> str:
    """First bot message: name the site, name the proven stack, promise the card."""
    who = str(row.get("company") or row.get("host") or "").strip()
    host = str(row.get("host") or "").strip()
    label = who or host or "ekibiniz"
    confirmed = bool(row.get("platform_confirmed"))
    stack = str(row.get("platform") or row.get("stack") or "").strip()
    err = str(row.get("error_type") or row.get("pain") or "checkout kopuğu").rstrip(".")
    probe = str(row.get("probe") or "").strip()
    price = config.price_label()

    if row.get("turkish", True):
        head = f"Merhaba {label} yetkilisi — hoş geldiniz."
        if confirmed and stack:
            body = (
                f"{host or 'siteniz'} için {stack} altyapınızda tespit ettiğimiz "
                f"{err} kaydına ait teknik analiz ve {price} iyileştirme raporu hazırlanıyor."
            )
        else:
            body = (
                f"{host or 'siteniz'} için checkout/form POST akışında tespit ettiğimiz "
                f"{err} kaydına ait teknik analiz ve {price} iyileştirme raporu hazırlanıyor."
            )
        tail = (
            f"İncelediğimiz nokta: {probe}. " if probe else ""
        ) + (
            "Mimari kart ~1 dakika içinde bu sohbete düşecek — şablon diyagramdır, "
            "canlı admin ekranı değil. Bu arada sorunuz varsa yazabilirsiniz."
        )
        return f"{head} {body} {tail}"

    head = f"Hello {label} team — welcome."
    if confirmed and stack:
        body = (
            f"The technical analysis and {price} improvement note for the {err} we mapped "
            f"on your {stack} stack ({host or 'your site'}) is being prepared."
        )
    else:
        body = (
            f"The technical analysis and {price} improvement note for the {err} we mapped "
            f"on your checkout/form POST flow ({host or 'your site'}) is being prepared."
        )
    tail = (f"What we inspected: {probe}. " if probe else "") + (
        "The architecture card lands in this chat in about a minute — it is a schematic, "
        "not a live admin screen. Ask anything in the meantime."
    )
    return f"{head} {body} {tail}"
