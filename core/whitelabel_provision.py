"""Aninda tedarik: PAID webhook -> partner kaydi + API anahtari + ilk is."""
from __future__ import annotations
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PARTNERS_NAME = "agency_partners.json"


def _path() -> Path:
    try:
        import config as _cfg  # type: ignore
        root = Path(str(_cfg.ROOT))
        p = root / "nirvana" / "state" / _PARTNERS_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        pass
    try:
        from nirvana.registry import state_path as _sp  # type: ignore
        return _sp(_PARTNERS_NAME)
    except Exception:
        p = Path(__file__).resolve().parents[1] / "nirvana" / "state" / _PARTNERS_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def _load() -> list:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(rows: list) -> None:
    try:
        p = _path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-500:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
    except Exception:
        logger.debug("partner kayit atlandi", exc_info=True)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def find_partner(chat_id: int = 0, agency: str = "") -> dict | None:
    rows = _load()
    for r in rows:
        try:
            if chat_id and int(r.get("chat_id") or 0) == int(chat_id):
                return r
        except (TypeError, ValueError):
            continue
    name = _clean(agency).lower()
    if name:
        for r in rows:
            if _clean(r.get("agency_name")).lower() == name:
                return r
    return None


def provision(chat_id: int = 0, *, agency: str = "", contact: str = "",
              amount: str = "", currency: str = "") -> dict:
    """Partner kaydi: idempotent (ayni chat iki kez anahtar uretmez)."""
    rows = _load()
    name = _clean(agency) or f"chat-{chat_id}"
    existing = find_partner(chat_id, name)
    if isinstance(existing, dict) and existing.get("api_key"):
        existing["agency_name"] = name or _clean(existing.get("agency_name"))
        if _clean(contact):
            existing["contact_info"] = _clean(contact)
        existing["provisioned_at"] = existing.get("provisioned_at") or _now()
        existing["amount"] = _clean(amount) or _clean(existing.get("amount"))
        existing["currency"] = _clean(currency) or _clean(existing.get("currency")) or "EUR"
        _save(rows)
        return {"ok": True, "reused": True, "partner": dict(existing)}
    api_key = "wl_" + secrets.token_hex(16)
    partner = {
        "chat_id": int(chat_id or 0),
        "agency_name": name,
        "contact_info": _clean(contact),
        "api_key": api_key,
        "amount": _clean(amount),
        "currency": _clean(currency) or "EUR",
        "provisioned_at": _now(),
        "docs": "Portal giris + API anahtari + ornek whitelabel raporu iletildi.",
    }
    rows.append(partner)
    _save(rows)
    return {"ok": True, "reused": False, "partner": dict(partner)}


def seed_delivery(chat_id: int, domain: str = "") -> dict:
    """Ilk teslimat isini acar (infra-sweep); domain yoksa beklersin."""
    from nirvana.forget_guard import domain_of
    dom = domain_of(_clean(domain))
    if not dom:
        return {"ok": False, "reason": "no_domain"}
    try:
        from nirvana import delivery_worker as _dw  # type: ignore
        return dict(_dw.start_job(int(chat_id), dom, "infra-sweep") or {})
    except Exception as exc:
        logger.warning("seed_delivery atlandi: %s", exc)
        return {"ok": False, "reason": "worker_error"}


def provision_paid_chat(chat_id: int, *, amount: str = "", currency: str = "") -> dict:
    """Webhook sonrasi tek cagri: tedarik + teslimat tohumu + kontenjan + bildirim."""
    out: dict[str, Any] = {"ok": True, "chat_id": int(chat_id)}
    agency = ""
    contact = ""
    domain = ""
    try:
        import telegram_sessions as _ts  # type: ignore
        row = _ts._row(int(chat_id)) or {}
        agency = _clean(row.get("company"))
        contact = _clean(row.get("contact") or row.get("username") or "")
        domain = _clean(row.get("host") or row.get("target_domain")
                        or row.get("company") or "")
    except Exception:
        pass
    try:
        prov = provision(int(chat_id), agency=agency, contact=contact,
                         amount=amount, currency=currency)
        out["provision"] = {"ok": prov.get("ok"), "reused": prov.get("reused")}
        out["agency_name"] = (prov.get("partner") or {}).get("agency_name", agency)
    except Exception as exc:
        out["provision"] = {"ok": False, "reason": str(exc)[:120]}
    try:
        out["delivery"] = seed_delivery(int(chat_id), domain)
    except Exception as exc:
        out["delivery"] = {"ok": False, "reason": str(exc)[:120]}
    try:
        from core import whitelabel_slots as _slots  # type: ignore
        seg = _slots.segment_of(out.get("agency_name") or agency)
        out["slot"] = _slots.claim(seg, out.get("agency_name") or agency)
    except Exception:
        pass
    try:
        import owner_notify as _on  # type: ignore
        _on.send(
            f"📦 TEDARIK TAMAMLANDI — chat {chat_id}\n"
            f"🏢 Ortak: {out.get('agency_name') or agency or '—'}\n"
            f"💰 {amount} {currency}\n"
            "🔑 Portal + API anahtari olusturuldu\n"
            f"🚚 Ilk teslimat isi: {out.get('delivery', {}).get('ok')}")
    except Exception:
        pass
    return out


