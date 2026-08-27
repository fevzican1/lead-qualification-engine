"""
Qualify a collected site without burning DeepSeek on the form.

The contact-form note must stand alone: if it does not prove we looked at
their stack, they will not open Telegram. Copy is assembled from page
evidence + stack playbook. DeepSeek-R1:14B stays on the Telegram closer
so Ampere RAM is not shared with Chromium.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Optional

import config
import knowledge
import optout
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
    score = _heuristic_score(updated)
    turkish = looks_turkish(
        str(lead.get("description") or ""),
        str(lead.get("page_excerpt") or ""),
        " ".join(hints),
    )
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
    stack = knowledge.stack_phrase(hints, turkish=turkish)
    pain = _pain(hints, turkish=turkish)
    sender = (config.SENDER_COMPANY or "DevSolve").strip()
    price = config.price_label()
    who = _company(lead)
    quote = _evidence(lead)
    host = str(lead.get("url") or "")
    token = ""
    try:
        token = telegram_handoff.remember(
            lead, company=who, pain=pain, quote=quote, turkish=turkish
        )
        lead["telegram_start"] = token
    except Exception:
        logger.exception("Telegram handoff remember failed for %s", host)
    link = config.telegram_deeplink(token)
    variant = int(hashlib.sha1(host.encode("utf-8", errors="ignore")).hexdigest(), 16) % 6

    if turkish:
        seen = f' Sayfanizda su satir duruyor: "{quote}."' if quote else ""
        subject = f"{who}: {stack} kopugu"[:80]
        notes = (
            (
                f"{who} ekibine not — {stack} net gorunuyor.{seen} "
                f"Sahada bu genelde soyle kiriliyor: {pain}. "
                f"{sender} platformu degistirmez; odeme onayi ile siparis/ERP satirini ayni id'de kilitler. "
                f"Is {price} flat, yalnizca 'yapalim' derseniz Payoneer. "
                f"{who} icin 8-10 dakikalik akisi Telegram'da cizerim: {link}"
            ),
            (
                f"Merhaba {who} — {stack} kurulumunuza baktim.{seen} "
                f"Darboğaz: {pain}. Bunu Excel veya gecikmeli webhook ile yasamak zorunda degilsiniz. "
                f"{sender} mevcut paneli birakir, sadece kopuk halkayi kapatir ({price}). "
                f"Taslagi gormek icin Telegram'dan yazmaniz yeterli: {link}"
            ),
            (
                f"{who} sitesi {stack} uzerinde.{seen} "
                f"Operasyon tarafinda {pain}. "
                f"Teklif net: yeni bir yazilim suite'i degil, sizin stack'inize ozel bir kopru, {price}. "
                f"10 dakikada kutulari cizerim — {link}"
            ),
            (
                f"{who} icin somut tespit: {stack}.{seen} "
                f"{pain}. {sender} bunu kaynak -> odeme -> hedef tek kayitta kapatir; "
                f"magazayi veya ERP'yi degistirtmeyiz. {price}, Payoneer sadece net niyet sonrasi. "
                f"Cizimi burada birakirim: {link}"
            ),
            (
                f"{who} — rakiplerinize 'entegrasyon yapariz' yazmak kolay. "
                f"Sizin sayfada {stack} var.{seen} "
                f"Ben {pain} problemini kastediyorum. Cozum {price}, kapsam 1 kopru. "
                f"Telegram: {link}"
            ),
            (
                f"{who} paneline disaridan baktim: {stack}.{seen} "
                f"Eksik halka {pain}. {sender} 10 dakikalik bir akis taslagi cikarir; "
                f"uygunsa is {price}. Baska bir form doldurtmam, sohbet Telegram'da: {link}"
            ),
        )
        note = notes[variant]
    else:
        seen = f' On the public page you currently say: "{quote}."' if quote else ""
        subject = f"{who}: {stack} gap"[:80]
        notes = (
            (
                f"Note for {who} — {stack} is clearly in use.{seen} "
                f"That usually breaks like this: {pain}. "
                f"{sender} will not rip out the platform; we lock payment OK and the order/ERP row on one id. "
                f"Job is {price} flat, Payoneer only after a clear yes. "
                f"I will sketch {who}'s flow in 8-10 minutes on Telegram: {link}"
            ),
            (
                f"Hi {who} — I looked at your {stack} setup.{seen} "
                f"The bottleneck is {pain}. You should not have to live that in Excel or a late webhook. "
                f"{sender} leaves the current panel in place and only closes the broken link ({price}). "
                f"If you want the sketch, just write on Telegram: {link}"
            ),
            (
                f"{who} is running {stack}.{seen} "
                f"On the ops side, {pain}. "
                f"The offer is not a new suite — one bridge for your stack, {price}. "
                f"I can draw the boxes in 10 minutes: {link}"
            ),
            (
                f"Concrete read on {who}: {stack}.{seen} "
                f"{pain}. {sender} closes source -> pay -> destination on one record; "
                f"we do not make you replace the shop or the ERP. {price}, Payoneer after intent. "
                f"Sketch lives here: {link}"
            ),
            (
                f"{who} — anyone can write 'we do integrations'. "
                f"Your page shows {stack}.{seen} "
                f"I mean this failure: {pain}. Fix is {price}, one bridge. "
                f"Telegram: {link}"
            ),
            (
                f"I looked at {who} from the outside: {stack}.{seen} "
                f"The missing link is {pain}. {sender} produces a 10-minute flow sketch; "
                f"if it fits, the job is {price}. I will not dump another form on you — Telegram: {link}"
            ),
        )
        note = notes[variant]
    return subject[:80], " ".join(note.split())


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
