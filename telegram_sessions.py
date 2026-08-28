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
    """Hold the card until ~75 seconds into the chat."""
    row = _row(chat_id)
    started = _parse(str(row.get("started_at") or "")) or _now()
    wait = 75 - (_now() - started).total_seconds()
    return max(0.0, wait)


def should_hot_ping(chat_id: int) -> bool:
    return not bool(_row(chat_id).get("hot_pinged"))


def mark_payment(chat_id: int) -> None:
    _put(chat_id, payment_sent=True, followup_sent=True, last_at=_now().isoformat())


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
        if row.get("takeover") or row.get("declined"):
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
    price = f"${config.PRICE_USD}"
    if row.get("turkish", True):
        return (
            f"Merhaba {who} — dün bıraktığım {price} köprü taslağı duruyor. "
            "Uygunsa bir satır yazmanız yeterli; değilse STOP."
        )
    return (
        f"Hi {who} — the {price} bridge sketch from yesterday is still here. "
        "One line if useful; STOP if not."
    )
