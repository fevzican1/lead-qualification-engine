"""
Qualify a collected site without burning DeepSeek on the form.

The contact-form note must stand alone: if it does not prove we looked at
their stack, they will not open Telegram. Copy is assembled from page
evidence + stack playbook. DeepSeek-R1:14B stays on the Telegram closer
so Ampere RAM is not shared with Chromium.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import config
import bounded_agents
import knowledge
import optout
import optimized_payload
import telegram_handoff
from site_signals import compact_excerpt, extract_stack_hints, high_value_score, looks_turkish

logger = logging.getLogger(__name__)

_JUNK_RE = re.compile(
    r"cookie|kvkk|copyright|all rights|lorem ipsum|privacy policy|"
    r"anasayfa|home page|add to cart|sepete ekle|sign in|giris yap",
    re.I,
)

# Buyer-facing pain (contact-form voice). Telegram can stay more technical.
_FORM_PAIN = {
    "IdeaSoft": {
        "tr": "siparis, iyzico/PayTR tahsilati ve stok cogu gece ayri ayri kapanip Excel'de birlesiyor",
        "en": "orders, payouts and stock often close in different places and get merged in Excel",
    },
    "T-Soft": {
        "tr": "pazaryeri siparisi ile panel siparisi ayni kayitta bulusmuyor",
        "en": "marketplace orders and panel orders do not land on one record",
    },
    "Ticimax": {
        "tr": "kargo ve odeme bildirimi siparis kaydina gec dusuyor",
        "en": "carrier and payment notices lag the order record",
    },
    "ikas": {
        "tr": "checkout ile stok guncellemesi yarisiyor, cift satis riski var",
        "en": "checkout and stock updates race, with a double-sell risk",
    },
    "Akinon": {
        "tr": "kanal stoku ve siparis tek olay akisinda degil",
        "en": "channel stock and orders are not on one event flow",
    },
    "iyzico": {
        "tr": "odeme onayi geldigi halde ERP/siparis satiri kaciyor veya iki kez yaziliyor",
        "en": "payment OK arrives but the order/ERP row is missed or written twice",
    },
    "PayTR": {
        "tr": "bildirim URL'si ile paneldeki siparis durumu kayiyor",
        "en": "the notify URL and the panel order status drift",
    },
    "Craftgate": {
        "tr": "birden fazla POS sonucu tek siparis kaydina inmiyor",
        "en": "multi-POS results do not collapse to one order row",
    },
    "WooCommerce": {
        "tr": "odeme alindi, CRM/ERP ve stok birkac dakika (veya bir gun) gec guncelleniyor",
        "en": "payment clears, then CRM/ERP and stock update minutes or a day later",
    },
    "Shopify": {
        "tr": "storefront siparisi fulfillment ve muhasebeye tek parca gitmiyor",
        "en": "storefront orders are not one piece into fulfillment and books",
    },
    "WordPress": {
        "tr": "form/checkout kaydi baska bir panele elle tasiniyor",
        "en": "form/checkout rows are still copied into another panel by hand",
    },
    "HubSpot": {
        "tr": "lead form ile operasyon (siparis/odeme) ayni id'de kilitli degil",
        "en": "the lead form and ops (order/pay) are not locked on one id",
    },
    "ERP": {
        "tr": "satis siparisi ERP'ye hâlâ CSV veya elle giriliyor",
        "en": "sales orders still reach ERP by CSV or by hand",
    },
    "Odoo": {
        "tr": "e-ticaret siparisi Odoo sale.order'a yarim map ediliyor",
        "en": "commerce orders map only halfway into Odoo sale.order",
    },
}


def qualify_lead(lead: dict[str, Any], *, model: Optional[str] = None) -> dict[str, Any]:
    del model  # Form copy is evidence-based; DeepSeek is reserved for Telegram.
    updated = dict(lead)
    if lead.get("status") == "failed" and not (lead.get("contact_form") or {}).get("found"):
        updated.update(
            {
                "fit_score": 0,
                "fit_rationale": "Collection failed; site was not analyzed.",
                "pain_points": [],
                "value_proposition": "",
                "form_subject": "",
                "should_contact": False,
                "risk_flags": ["collection_failed"],
            }
        )
        return updated

    hints = list(lead.get("stack_hints") or []) or extract_stack_hints(
        str(lead.get("company_name") or ""),
        str(lead.get("description") or ""),
        str(lead.get("page_excerpt") or ""),
    )
    updated["stack_hints"] = hints
    recon = bounded_agents.recon_context(updated)
    updated["agent_recon"] = recon
    updated["platform"] = recon["platform"]
    updated["platform_confidence"] = recon["confidence"]
    updated["platform_evidence"] = recon["evidence"]
    score = _heuristic_score(updated)
    turkish = looks_turkish(
        str(lead.get("description") or ""),
        str(lead.get("page_excerpt") or ""),
        " ".join(hints),
    )
    if lead.get("optimized_payload") and (lead.get("value_proposition") or "").strip():
        subject = str(lead.get("form_subject") or "")[:120]
        pitch = str(lead.get("value_proposition") or "")
        if "STOP" not in pitch:
            pitch = f"{pitch} {optout.form_courtesy_line(turkish=turkish)}"
    else:
        subject, pitch = _form_note(updated, turkish=turkish)
        courtesy = optout.form_courtesy_line(turkish=turkish)
        if "STOP" not in pitch:
            pitch = f"{pitch} {courtesy}"

    updated.update(
        {
            "fit_score": score,
            "fit_rationale": "Page evidence plus stack-specific operator note.",
            "pain_points": _pain_hints(updated),
            "value_proposition": pitch,
            "form_subject": subject,
            "should_contact": score >= config.MIN_FIT_SCORE,
            "risk_flags": [],
            "status": "qualified",
            "error": None,
        }
    )
    logger.info("Qualified %s score=%s stack=%s", updated.get("url"), score, hints[:3])
    return updated


def qualify_leads(leads: list[dict[str, Any]], *, model: Optional[str] = None) -> list[dict[str, Any]]:
    return [qualify_lead(lead, model=model) for lead in leads]


def _company(lead: dict[str, Any]) -> str:
    raw = (str(lead.get("company_name") or "").strip() or "").split("|")[0]
    raw = re.split(r"\s+[–\-]\s+", raw)[0]
    raw = raw.split(":")[0].strip()
    junk = {"home", "contact", "iletisim", "welcome", "index"}
    if raw.lower() in junk or len(raw) < 2:
        host = (lead.get("url") or "").replace("https://", "").replace("http://", "")
        raw = host.split("/")[0].removeprefix("www.")
    return raw[:48]


def _evidence(lead: dict[str, Any]) -> str:
    """One sentence that could only come from their page — not marketing fluff."""
    blob = compact_excerpt(
        str(lead.get("description") or ""),
        str(lead.get("page_excerpt") or ""),
        list(lead.get("stack_hints") or []),
        limit=700,
    )
    hints = [str(h).lower() for h in (lead.get("stack_hints") or [])]
    best = ""
    best_score = -1
    for raw in re.split(r"(?<=[.!?])\s+", blob):
        text = " ".join((raw or "").split())
        if len(text) < 48 or len(text) > 180:
            continue
        if _JUNK_RE.search(text):
            continue
        low = text.lower()
        score = 0
        if re.search(
            r"api|webhook|entegrasyon|integrat|odeme|ödeme|checkout|erp|stok|siparis|sipariş|kargo|crm",
            low,
        ):
            score += 3
        score += sum(2 for h in hints if h and h in low)
        if score > best_score:
            best_score = score
            best = text
    if best_score < 1:
        return ""
    return best.rstrip(".")


def _pain(hints: list[str], *, turkish: bool) -> str:
    lang = "tr" if turkish else "en"
    for hint in hints:
        row = _FORM_PAIN.get(hint)
        if row:
            return row[lang]
    return knowledge.bottleneck_for(hints, turkish=turkish)


def _form_note(lead: dict[str, Any], *, turkish: bool) -> tuple[str, str]:
    hints = list(lead.get("stack_hints") or [])
    who = _company(lead)
    quote = _evidence(lead)
    pain = _pain(hints, turkish=turkish)
    host = telegram_handoff._host(str(lead.get("url") or "")) or who
    token = ""
    try:
        token = telegram_handoff.remember(
            lead, company=who, pain=pain, quote=quote, turkish=turkish
        )
        lead["telegram_start"] = token
        hook = telegram_handoff.hook_for_lead(lead)
        lead["hook_variant"] = hook["variant"]
        lead["error_type"] = hook["error_type"] if turkish else hook["error_type_en"]
    except Exception:
        logger.exception("Telegram handoff remember failed for %s", host)
    link = config.telegram_deeplink(token)
    subject, note = telegram_handoff.form_copy(
        host=host,
        hints=hints,
        link=link,
        turkish=turkish,
        platform=str(lead.get("platform") or ""),
        confidence=int(lead.get("platform_confidence") or 0),
    )
    return subject, note


def _heuristic_score(lead: dict[str, Any]) -> int:
    blob = " ".join(
        [
            str(lead.get("company_name") or ""),
            str(lead.get("description") or ""),
            str(lead.get("page_excerpt") or ""),
        ]
    ).lower()
    score = 38
    score += high_value_score(list(lead.get("stack_hints") or []))
    form_ok = bool((lead.get("contact_form") or {}).get("found"))
    if form_ok:
        score += 16
    if form_ok and not lead.get("captcha_detected"):
        score += 12
    if lead.get("captcha_detected"):
        score -= 25
    if "example domain" in blob or "parked" in blob:
        score = 15
    return max(0, min(100, score))


def _pain_hints(lead: dict[str, Any]) -> list[str]:
    blob = f"{lead.get('description') or ''} {lead.get('page_excerpt') or ''}".lower()
    found: list[str] = []
    if "integrat" in blob or "entegrasyon" in blob:
        found.append("integrations")
    if "manual" in blob or "spreadsheet" in blob or "excel" in blob:
        found.append("manual work")
    if "checkout" in blob or "ödeme" in blob or "odeme" in blob:
        found.append("checkout")
    return found[:4]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    demo = {
        "url": "https://example.com",
        "company_name": "Example Shop | Home",
        "description": "IdeaSoft e-ticaret ve iyzico odeme.",
        "page_excerpt": (
            "Siparisler IdeaSoft API webhook ile iyzico tahsilatindan sonra ERP ye yazilir. "
            "Elle Excel kalir."
        ),
        "stack_hints": ["IdeaSoft", "iyzico"],
        "contact_form": {"found": True},
        "captcha_detected": False,
        "status": "collected",
    }
    print(json.dumps(qualify_lead(demo), indent=2, ensure_ascii=False))
