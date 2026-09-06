"""Inbound Telegram session state: proof, hot ping, 24h follow-up."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import config

logger = logging.getLogger(__name__)

PATH = config.ROOT / "telegram_sessions.json"
FOLLOWUP_AFTER = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        stamp = datetime.fromisoformat(text)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    except ValueError:
        return None


def _load() -> dict[str, Any]:
    if not PATH.exists():
        return {}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    tmp = PATH.with_suffix(PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(PATH)


def _row(chat_id: int) -> dict[str, Any]:
    data = _load()
    key = str(int(chat_id))
    row = data.get(key)
    return dict(row) if isinstance(row, dict) else {}


def _put(chat_id: int, **fields: Any) -> dict[str, Any]:
    data = _load()
    key = str(int(chat_id))
    row = dict(data.get(key) or {})
    row.update(fields)
    row["chat_id"] = int(chat_id)
    data[key] = row
    _save(data)
    return row


def touch_start(chat_id: int, *, company: str, turkish: bool, username: str = "") -> None:
    now = _now().isoformat()
    existing = _row(chat_id)
    payload: dict[str, Any] = {
        "company": (company or existing.get("company") or "").strip()[:48],
        "turkish": bool(turkish),
        "username": (username or existing.get("username") or "").lstrip("@"),
        "last_at": now,
    }
    if not existing.get("started_at"):
        payload["started_at"] = now
        payload["user_replies"] = 0
        payload["proof_sent"] = False
        payload["hot_pinged"] = False
        payload["warm_pinged"] = False
        payload["payment_sent"] = False
        payload["takeover"] = False
        payload["followup_sent"] = False
    _put(chat_id, **payload)


def touch_user(chat_id: int, text: str, *, username: str = "") -> dict[str, Any]:
    row = _row(chat_id)
    replies = int(row.get("user_replies") or 0) + 1
    return _put(
        chat_id,
        last_at=_now().isoformat(),
        last_user=(text or "")[:280],
        user_replies=replies,
        username=(username or row.get("username") or "").lstrip("@"),
        started_at=row.get("started_at") or _now().isoformat(),
    )


def mark_proof(chat_id: int) -> None:
    _put(chat_id, proof_sent=True, last_at=_now().isoformat())


def mark_hot(chat_id: int) -> None:
    _put(chat_id, hot_pinged=True, last_at=_now().isoformat())


def mark_declined(chat_id: int) -> None:
    _put(chat_id, declined=True, followup_sent=True, last_at=_now().isoformat())


def is_declined(chat_id: int) -> bool:
    return bool(_row(chat_id).get("declined"))


def should_send_proof(chat_id: int) -> bool:
    row = _row(chat_id)
    if row.get("proof_sent") or row.get("takeover") or row.get("declined"):
        return False
    return bool(row.get("started_at"))


def seconds_until_proof(chat_id: int) -> float:
    """Hold the card until ~45 seconds into the chat."""
    row = _row(chat_id)
    started = _parse(str(row.get("started_at") or "")) or _now()
    delay = int(getattr(config, "PROOF_CARD_DELAY_SECONDS", 45) or 45)
    wait = delay - (_now() - started).total_seconds()
    return max(0.0, wait)


def should_hot_ping(chat_id: int) -> bool:
    return not bool(_row(chat_id).get("hot_pinged"))


def should_warm_ping(chat_id: int) -> bool:
    return not bool(_row(chat_id).get("warm_pinged"))


def mark_warm(chat_id: int) -> None:
    _put(chat_id, warm_pinged=True, last_at=_now().isoformat())


def mark_payment(chat_id: int) -> None:
    _put(chat_id, payment_sent=True, followup_sent=True, last_at=_now().isoformat())


def mark_payment_confirmed(chat_id: int) -> None:
    """Compatibility: legacy customer confirmation is only a report, never verification."""
    mark_payment_reported(chat_id)


def mark_payment_reported(chat_id: int) -> None:
    _put(chat_id, payment_reported=True, payment_reported_at=_now().isoformat(),
         followup_sent=True, last_at=_now().isoformat())


def verify_payment(chat_id: int, *, amount: int, currency: str, reference: str, owner_id: int) -> None:
    reference = reference.strip()
    row = _row(chat_id)
    request = row.get("payment_request") or {}
    if (not owner_id or not reference or len(reference) > 160 or not row.get("payment_sent")
            or request.get("amount") != amount or request.get("currency") != currency):
        raise ValueError("Payment must match this chat's recorded request")
    if any(r.get("provider_payment_reference") == reference for r in _load().values() if isinstance(r, dict)):
        raise ValueError("Provider payment reference has already been used")
    _put(chat_id, payment_verified=True, payment_verified_at=_now().isoformat(),
         payment_verification_method="owner_provider_dashboard", verified_by_owner=int(owner_id),
         provider_payment_reference=reference[:160], payment_amount=amount, payment_currency=currency,
         followup_sent=True)


def approve_contract(chat_id: int, *, contract_ref: str, scope_ref: str, access_ref: str, owner_id: int) -> None:
    if not _row(chat_id).get("started_at") or not all((contract_ref, scope_ref, access_ref, owner_id)):
        raise ValueError("Existing chat, signed contract, scope and access references required")
    _put(chat_id, contract_signed=True, contract_reference=contract_ref[:160], scope_reference=scope_ref[:160],
         access_reference=access_ref[:160], contract_verified_by_owner=int(owner_id),
         contract_amount=config.PRICE_USD, contract_currency="USD", followup_sent=True)


def fulfillment_ready(chat_id: int) -> bool:
    row = _row(chat_id)
    return bool(row.get("payment_verified") and row.get("provider_payment_reference")
                and row.get("verified_by_owner") and row.get("contract_signed")
                and row.get("contract_reference") and row.get("scope_reference")
                and row.get("access_reference") and row.get("payment_amount") == row.get("contract_amount")
                and row.get("payment_currency") == row.get("contract_currency") and not row.get("declined"))


def is_payment_sent(chat_id: int) -> bool:
    return bool(_row(chat_id).get("payment_sent"))


def set_takeover(chat_id: int, on: bool) -> None:
    _put(chat_id, takeover=bool(on), last_at=_now().isoformat())


def is_takeover(chat_id: int) -> bool:
    return bool(_row(chat_id).get("takeover"))


def clear(chat_id: int) -> None:
    data = _load()
    data.pop(str(int(chat_id)), None)
    _save(data)


def due_followups() -> list[dict[str, Any]]:
    cutoff = _now() - FOLLOWUP_AFTER
    out: list[dict[str, Any]] = []
    for row in _load().values():
        if not isinstance(row, dict):
            continue
        if row.get("followup_sent") or row.get("hot_pinged") or row.get("payment_sent"):
            continue
        if row.get("takeover") or row.get("declined") or row.get("audience") == "enterprise":
            continue
        if int(row.get("user_replies") or 0) > 2:
            continue
        started = _parse(str(row.get("started_at") or ""))
        last = _parse(str(row.get("last_at") or "")) or started
        if started is None or last is None:
            continue
        if last > cutoff:
            continue
        out.append(row)
    return out


def mark_followup(chat_id: int) -> None:
    _put(chat_id, followup_sent=True)


def followup_text(row: dict[str, Any]) -> str:
    who = str(row.get("company") or "").strip() or "ekibiniz"
    enterprise = str(row.get("audience") or "") == "enterprise" or str(row.get("variant") or "") == "X"
    hidden = bool(getattr(config, "PRICE_HIDDEN", False)) or enterprise
    if row.get("turkish", True):
        if enterprise:
            return (
                f"Merhaba {who} — dün ilettiğim entegrasyon bulgusu geçerli. "
                "Pilot slot için bir satır yazmanız yeterli; değilse STOP."
            )
        price = "" if hidden else f" ${config.PRICE_USD}"
        return (
            f"Merhaba {who} — dün bıraktığım{price} köprü taslağı duruyor. "
            "Uygunsa bir satır yazmanız yeterli; değilse STOP."
        )
    if enterprise:
        return (
            f"Hi {who} — the integration finding from yesterday still stands. "
            "One line if you want the pilot slot; STOP if not."
        )
    price = "" if hidden else f" ${config.PRICE_USD}"
    return (
        f"Hi {who} — the{price} bridge sketch from yesterday is still here. "
        "One line if useful; STOP if not."
    )
