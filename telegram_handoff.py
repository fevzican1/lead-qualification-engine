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
        "excerpt": str(lead.get("page_excerpt") or lead.get("description") or "").strip()[:180],
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
    excerpt = str(row.get("excerpt") or "").strip()
    seen = f' Public page line: "{quote}"' if quote else ""
    extra = f" Page excerpt: {excerpt}" if excerpt and excerpt != quote else ""
    hints = ", ".join(str(h) for h in (row.get("hints") or []) if h)
    return (
        f"Inbound from our contact-form note (this exact lead). "
        f"Company: {row.get('company') or row.get('host')}. Host: {row.get('host')}. "
        f"URL: {row.get('url')}. Stack phrase: {row.get('stack') or hints}. "
        f"Detected hints: {hints or 'none'}. Likely break: {row.get('pain')}.{seen}{extra} "
        f"Do not re-ask which platform they use. Do not invent a different stack. "
        f"Treat this as already seen from the form they received."
    )


def opener(row: dict[str, Any]) -> str:
    who = str(row.get("company") or row.get("host") or "ekibiniz")
    stack = str(row.get("stack") or "altyapınız")
    pain = str(row.get("pain") or "").rstrip(".")
    quote = str(row.get("quote") or "").strip()
    seen = f' Sayfanızdaki satır: "{quote}".' if quote else ""
    if row.get("turkish"):
        return (
            f"{who} — iletişim formundaki notun devamı. {stack} duruyor.{seen} "
            f"Kopuk: {pain}. Panel durur; 8–10 dakikada kaynak → ödeme onayı → ERP/stok'u tek id'de çizerim. "
            f"Şu an operasyonda en çok yanan hangisi: callback, stok yarışı, kargo, yoksa Excel?"
        )
    seen_en = f' Line on your page: "{quote}".' if quote else ""
    return (
        f"{who} — this continues the note on your contact form. {stack} is in use.{seen_en} "
        f"Break: {pain}. Panel stays; in 8–10 minutes I sketch source → payment OK → ERP/stock on one id. "
        f"What is burning in ops: callback, stock race, shipping, or Excel?"
    )
