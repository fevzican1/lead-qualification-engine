"""
Outbound pacing — stay under daily/hourly caps AND avoid blasting one form ESP.

Does not dump one ESP. Daily/hourly caps live in config. Spreads posts so one HubSpot
or Formspree inbox does not see a burst from this IP.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import config

logger = logging.getLogger(__name__)

PATH = config.ROOT / "pacing_state.json"
MAX_PER_PROVIDER_HOUR = 3
MIN_GAP_SECONDS = 18.0

PROVIDERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hubspot", ("hsforms.com", "hubspot.com", "hs-scripts.com")),
    ("formspree", ("formspree.io",)),
    ("web3forms", ("web3forms.com", "api.web3forms.com")),
    ("getform", ("getform.io",)),
    ("basin", ("usebasin.com",)),
    ("formcarry", ("formcarry.com",)),
    ("jotform", ("jotform.com", "jotform.io")),
    ("typeform", ("typeform.com",)),
    ("google-forms", ("docs.google.com", "forms.gle")),
    ("wix", ("wix.com", "wixsite.com")),
    ("squarespace", ("squarespace.com",)),
    ("mailchimp", ("list-manage.com", "mailchimp.com")),
    ("brevo", ("brevo.com", "sendinblue.com", "sibforms.com")),
    ("webflow", ("webflow.com",)),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> dict[str, Any]:
    if not PATH.exists():
        return {"submits": []}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"submits": []}
    if not isinstance(data, dict):
        return {"submits": []}
    rows = data.get("submits")
    data["submits"] = rows if isinstance(rows, list) else []
    return data


def _save(data: dict[str, Any]) -> None:
    tmp = PATH.with_suffix(PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(PATH)


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


def provider_of(*urls: str) -> str:
    blob = " ".join((u or "").lower() for u in urls)
    for name, needles in PROVIDERS:
        if any(n in blob for n in needles):
            return name
    host = (urlparse(urls[0] if urls else "").hostname or "").lower().removeprefix("www.")
    return host.split(":")[0] or "site"


def _recent(rows: list[Any], *, since: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = _parse(str(row.get("at") or ""))
        if stamp is None or stamp < since:
            continue
        out.append(row)
    return out


def can_submit(lead: dict[str, Any]) -> tuple[bool, str]:
    """Return (allowed, reason). Caps in knowledge.py still apply in the pipeline."""
    form = lead.get("contact_form") or {}
    provider = provider_of(
        str(form.get("action") or ""),
        str(form.get("page_url") or ""),
        str(lead.get("url") or ""),
    )
    data = _load()
    now = utc_now()
    hour_ago = now - timedelta(hours=1)
    recent = _recent(data.get("submits") or [], since=hour_ago)
    same = [row for row in recent if str(row.get("provider") or "") == provider]
    if len(same) >= MAX_PER_PROVIDER_HOUR:
        logger.info("Pace: provider %s already %s this hour — skip", provider, len(same))
        return False, f"provider_hour:{provider}"
    return True, provider


def record_submit(lead: dict[str, Any], *, status: str) -> None:
    if not str(status or "").startswith("submitted"):
        return
    form = lead.get("contact_form") or {}
    provider = provider_of(
        str(form.get("action") or ""),
        str(form.get("page_url") or ""),
        str(lead.get("url") or ""),
    )
    data = _load()
    cutoff = utc_now() - timedelta(hours=36)
    kept = _recent(data.get("submits") or [], since=cutoff)
    kept.append(
        {
            "at": utc_now().replace(microsecond=0).isoformat(),
            "provider": provider,
            "host": (urlparse(str(lead.get("url") or "")).hostname or "").lower(),
            "status": status,
        }
    )
    data["submits"] = kept
    _save(data)
