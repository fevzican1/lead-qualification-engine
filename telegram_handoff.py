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
    data = _load()
    data[token] = {
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "url": url,
        "host": _host(url),
        "company": (company or "").strip()[:48],
        "hints": hints,
        "pain": (pain or "").strip()[:240],
        "quote": (quote or "").strip()[:180],
        "turkish": bool(turkish),
        "stack": knowledge.stack_phrase(hints, turkish=turkish),
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
    seen = f' Public page line: "{quote}"' if quote else ""
    return (
        f"Inbound from our contact-form note. Company: {row.get('company') or row.get('host')}. "
        f"Stack: {row.get('stack') or ', '.join(row.get('hints') or [])}. "
        f"Likely break: {row.get('pain')}.{seen} "
        f"Do not re-ask which platform they use. Treat this as already seen."
    )


def opener(row: dict[str, Any]) -> str:
    who = str(row.get("company") or row.get("host") or "ekibiniz")
    stack = str(row.get("stack") or "altyapınız")
    pain = str(row.get("pain") or "").rstrip(".")
    price = config.price_label()
    if row.get("turkish"):
        return (
            f"{who} — form notunun devamı burası. {stack} tarafında genelde şu kopuk: {pain}. "
            f"Panel durur; 8–10 dakikada kaynak → ödeme onayı → ERP/stok kaydını çizerim. "
            f"İş {price} flat, Payoneer yalnız 'yapalım' derseniz. "
            f"Şu an en çok yanan hangisi: ödeme callback, stok, kargo, yoksa Excel?"
        )
    return (
        f"{who} — this chat continues the note we left on your form. On {stack} the usual break is: {pain}. "
        f"Your panel stays; in 8–10 minutes I sketch source → payment OK → ERP/stock on one id. "
        f"Job is {price} flat, Payoneer only after a clear yes. "
        f"What is burning now: payment callback, stock, shipping, or Excel?"
    )
