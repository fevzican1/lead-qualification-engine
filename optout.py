"""
Suppression list for commercial outreach.

Anyone who says stop / unsubscribe is stored here and never contacted again
via Telegram or contact forms unless they explicitly resume.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import config

logger = logging.getLogger(__name__)

PATH = config.ROOT / "optouts.json"

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9-]+\.)+(?:com|io|ai|net|org|co|app|dev|so|gg)\b",
    re.I,
)

OPT_OUT_RE = re.compile(
    r"(?i)("
    r"\b(stop|unsubscribe|un-subscribe|opt[\s-]?out|remove me|don't contact|"
    r"do not contact|no more (emails?|messages?)|leave me alone)\b|"
    r"listeden\s*ç[ıi]k|abonelikten\s*ç[ıi]k|mesaj\s*atma|"
    r"bir\s*daha\s*(yaz|mail|e-?posta|mesaj)|"
    r"iletişim\s*kurma|rahat\s*b[ıi]rak"
    r")"
)

RESUME_RE = re.compile(
    r"(?i)(\b(resume|resubscribe|re-subscribe|opt[\s-]?in|start again)\b|"
    r"yeniden\s*yaz|tekrar\s*iletişim|abone\s*ol)"
)


def _blank() -> dict[str, Any]:
    return {"chat_ids": [], "emails": [], "domains": [], "events": []}


def load() -> dict[str, Any]:
    if not PATH.exists():
        return _blank()
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt %s — starting a fresh suppression list", PATH)
        return _blank()
    if not isinstance(data, dict):
        return _blank()
    for key in ("chat_ids", "emails", "domains", "events"):
        if not isinstance(data.get(key), list):
            data[key] = []
    return data


def save(data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    tmp = PATH.with_suffix(PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(PATH)


def _record(kind: str, value: str, *, reason: str) -> None:
    data = load()
    bucket = {
        "chat": "chat_ids",
        "email": "emails",
        "domain": "domains",
    }[kind]
    if value not in data[bucket]:
        data[bucket].append(value)
        data["events"].append(
            {
                "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "kind": kind,
                "value": value,
                "reason": reason,
            }
        )
        save(data)
        logger.info("Opt-out stored %s=%s", kind, value)


def add_chat(chat_id: int, *, reason: str = "unsubscribe") -> None:
    _record("chat", str(int(chat_id)), reason=reason)


def add_email(email: str, *, reason: str = "unsubscribe") -> None:
    email = (email or "").strip().lower()
    if email:
        _record("email", email, reason=reason)


def add_domain(domain: str, *, reason: str = "unsubscribe") -> None:
    domain = _norm_domain(domain)
    if domain:
        _record("domain", domain, reason=reason)


def add_url(url: str, *, reason: str = "unsubscribe") -> None:
    add_domain(_domain_from_url(url), reason=reason)


def is_chat_opted_out(chat_id: int) -> bool:
    return str(int(chat_id)) in set(load().get("chat_ids") or [])


def is_url_opted_out(url: str) -> bool:
    domain = _domain_from_url(url)
    if not domain:
        return False
    blocked = {_norm_domain(item) for item in load().get("domains") or []}
    return domain in blocked or any(domain.endswith("." + item) for item in blocked if item)


def harvest_from_text(text: str, *, reason: str = "unsubscribe") -> None:
    for email in _EMAIL_RE.findall(text or ""):
        add_email(email, reason=reason)
    for domain in _DOMAIN_RE.findall(text or ""):
        if domain.lower() in {"t.me", "telegram.org", "gmail.com", "outlook.com"}:
            continue
        add_domain(domain, reason=reason)


def remove_chat(chat_id: int) -> None:
    data = load()
    key = str(int(chat_id))
    data["chat_ids"] = [item for item in data["chat_ids"] if item != key]
    save(data)


def _domain_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _norm_domain(domain: str) -> str:
    host = (domain or "").strip().lower()
    host = host.removeprefix("https://").removeprefix("http://")
    host = host.split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def unsubscribe_footer() -> str:
    link = config.telegram_deeplink()
    email = config.SENDER_EMAIL or "hello@devsolvev2.com"
    return (
        f"One-time B2B note from {config.SENDER_COMPANY or 'DevSolve'}. "
        f"Unsubscribe: send STOP on Telegram {link} or email {email} "
        f"with the subject Unsubscribe."
    )


def form_courtesy_line(*, turkish: bool) -> str:
    """Short opt-out for contact forms — the long email footer looks like a mail blast."""
    link = config.telegram_deeplink()
    if turkish:
        return f"Istemiyorsaniz yok sayin. Durdurmak: Telegram {link} uzerinden STOP."
    return f"If this is not useful, ignore it. Stop: STOP on Telegram {link}."
