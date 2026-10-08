"""Agency partner state machine: INITIAL -> QUALIFIED -> PARTNER_ONBOARDED.

Webchat oturum satiri uzerinde `partner_state` + `partner_data` tasinir.
Tum gecisler idempotent ve hata-izoledir: bozuk veri akisi kirmaz.
"""
from __future__ import annotations
import logging
import re
from typing import Any
logger = logging.getLogger(__name__)
INITIAL = "INITIAL"
QUALIFIED = "QUALIFIED"
PARTNER_ONBOARDED = "PARTNER_ONBOARDED"
STATES: tuple[str, ...] = (INITIAL, QUALIFIED, PARTNER_ONBOARDED)
_AGENCY_RE = re.compile(r"(ajans|agency|ortak|partner|bayi|whitelabel|white[\s-]?label|retainer)", re.I)
_QUALIFY_RE = re.compile(r"(retainer|5000|whitelabel|white[\s-]?label|onayla|kabul|baslayal|odeme|fatura|invoice|ready|approve|accept|partner|ortak)", re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
def _clean(value: Any) -> str:
    return str(value or "").strip()
def normalize_state(raw: Any) -> str:
    text = _clean(raw).upper()
    return text if text in STATES else INITIAL
def get_state(session: dict[str, Any] | None) -> str:
    if not isinstance(session, dict):
        return INITIAL
    return normalize_state(session.get("partner_state"))
def get_data(session: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(session, dict):
        return {}
    data = session.get("partner_data")
    if not isinstance(data, dict):
        return {}
    return {"agency_name": _clean(data.get("agency_name")), "contact_info": _clean(data.get("contact_info")), "payment_checkout_link": _clean(data.get("payment_checkout_link"))}
def set_state(session: dict, state: str, data: dict | None = None) -> dict:
    row = session if isinstance(session, dict) else {}
    row["partner_state"] = normalize_state(state)
    if data is not None and isinstance(data, dict):
        merged = get_data(row)
        for key in ("agency_name", "contact_info", "payment_checkout_link"):
            val = _clean(data.get(key))
            if val:
                merged[key] = val[:500]
        row["partner_data"] = merged
    elif "partner_data" not in row:
        row["partner_data"] = get_data(row)
    return row
def _extract_contact(text: str) -> str:
    m = _EMAIL_RE.search(text or "")
    if m:
        return m.group(0).strip()
    p = _PHONE_RE.search(text or "")
    if p:
        return re.sub(r"\s+", " ", p.group(0)).strip()
    return ""
def _extract_link(text: str) -> str:
    m = _URL_RE.search(text or "")
    return m.group(0).strip() if m else ""
def extract_partner_fields(text: str, history: list | None = None, default_payment_link: str = "") -> dict:
    out = {"agency_name": "", "contact_info": "", "payment_checkout_link": ""}
    blob = _clean(text)
    if history:
        try:
            tail = " ".join(_clean(m.get("content")) for m in history[-6:] if isinstance(m, dict))
            blob = ("%s %s" % (tail, blob)).strip()
        except Exception:
            blob = _clean(text)
    contact = _extract_contact(_clean(text)) or _extract_contact(blob)
    if contact:
        out["contact_info"] = contact[:200]
    link = _extract_link(_clean(text)) or _extract_link(blob) or _clean(default_payment_link)
    if link:
        out["payment_checkout_link"] = link[:500]
    agency = ""
    m = re.search(r"(?:ajans(?:im(?:iz)?|imiz)?|agency|sirket|firma|company)\s*(?:ad[ıi]|ismi|name)?\s*[:\-\s]\s*([^\n,;]{2,80})", blob, re.I)
    if m:
        cand = m.group(1).strip().strip(":- ").strip()
        # "olarak ortak olmak istiyoruz" gibi cumle artiklarini temizle.
        cand = re.sub(r"\s+olarak.*$", "", cand, flags=re.I).strip()
        cand = re.sub(r"\s+(ortak|olmak|istiyoruz|istiyorum).*$", "", cand, flags=re.I).strip()
        if len(cand) >= 2:
            agency = cand
    if not agency:
        # Gecmiste gecen tırnakli / buyuk harfli ajans adayi.
        q = re.search(r"[\"'“”]([^\"'“”]{2,60})[\"'“”]", blob)
        if q:
            agency = q.group(1).strip()
    if not agency and len(_clean(text)) <= 60 and _clean(text) and "@" not in _clean(text) and "http" not in _clean(text).lower():
        cand = _clean(text)
        if len(cand.split()) <= 5 and not _AGENCY_RE.search(cand):
            agency = cand
    out["agency_name"] = agency[:120]
    return out
def _has_all_fields(data: dict) -> bool:
    return bool(_clean(data.get("agency_name")) and _clean(data.get("contact_info")))
def next_state(session: dict | None, user_text: str, default_payment_link: str = "") -> tuple:
    try:
        current = get_state(session)
        if current == PARTNER_ONBOARDED:
            return PARTNER_ONBOARDED, get_data(session), False
        merged = get_data(session)
        hist = (session or {}).get("history") if isinstance(session, dict) else None
        fresh = extract_partner_fields(user_text, hist, default_payment_link=default_payment_link)
        for k, v in fresh.items():
            if v and not merged.get(k):
                merged[k] = v
        text = _clean(user_text)
        if current == INITIAL and (_AGENCY_RE.search(text) or merged.get("agency_name")):
            if _has_all_fields(merged):
                return PARTNER_ONBOARDED, merged, True
            return QUALIFIED, merged, True
        if current == QUALIFIED and _has_all_fields(merged):
            return PARTNER_ONBOARDED, merged, True
        if current == INITIAL and _QUALIFY_RE.search(text) and _has_all_fields(merged):
            return PARTNER_ONBOARDED, merged, True
        changed = merged != get_data(session)
        return current, merged, changed
    except Exception:
        logger.debug("partner next_state fallback", exc_info=True)
        try:
            return get_state(session), get_data(session), False
        except Exception:
            return INITIAL, {}, False
def to_json_payload(data: dict) -> str:
    import json as _json
    return _json.dumps({"agency_name": _clean(data.get("agency_name")), "contact_info": _clean(data.get("contact_info")), "payment_checkout_link": _clean(data.get("payment_checkout_link"))}, ensure_ascii=False)

