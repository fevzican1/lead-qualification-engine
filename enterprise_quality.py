"""Evidence gates shared by GitHub discovery and Oracle. No network calls."""
from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

CONTRACT = re.compile(r"\b(contract(?:or|ual)?|freelance|fractional)\b", re.I)
SKILL = re.compile(r"\b(automation|integration|python|api|webhook|workflow|ai agent)\b", re.I)
URGENT = re.compile(r"\b(urgent|immediate(?:ly)?|asap|start immediately)\b", re.I)
APPLY = re.compile(r"\b(apply(?: now| for)?|application|submit your application)\b", re.I)
CLOSED = re.compile(r"no longer accepting|position (?:is )?closed|job (?:has )?expired|not accepting applications", re.I)
SALES = re.compile(r"contact.sales|request.a.demo|book.a.demo|customer.support|sales.enquir", re.I)


def public_https(url: str) -> bool:
    try:
        p = urlsplit(url)
        host = p.hostname or ""
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
            return False
        if "." not in host or host.endswith((".local", ".internal", ".localhost")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def fresh(raw: str, hours: int = 48, *, now: datetime | None = None) -> bool:
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
        return 0 <= age <= hours * 3600
    except (ValueError, TypeError):
        return False


def company_key(row: dict[str, Any]) -> str:
    # A company may have several ATS hosts/URLs. Never key on the shared ATS host.
    name = re.sub(r"\W+", "", str(row.get("company") or row.get("company_name") or "").casefold())
    return name or (urlsplit(str(row.get("url") or "")).hostname or "").removeprefix("www.")


def identity_url(row: dict[str, Any]) -> str:
    digest = hashlib.sha256(company_key(row).encode()).hexdigest()[:24]
    return f"https://{digest}.enterprise.invalid"


def eligible(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        return False
    quote = str(evidence.get("demand_quote") or "")
    channel = str(evidence.get("channel_quote") or "")
    url = str(row.get("url") or "")
    return bool(
        row.get("company") and row.get("form_verified") is True
        and row.get("channel_purpose") == "contractor_application"
        and public_https(url) and evidence.get("form_url") == url
        and public_https(str(evidence.get("form_action") or ""))
        and public_https(str(evidence.get("source_url") or ""))
        and fresh(evidence.get("scanned_at", ""))
        and fresh(evidence.get("published_at", ""), 30 * 24)
        and CONTRACT.search(quote) and SKILL.search(quote)
        and APPLY.search(channel) and not SALES.search(channel + " " + url)
        and not CLOSED.search(quote + " " + channel)
        and row.get("location_eligible") is True
    )


def valid_payload(payload: Any) -> bool:
    return bool(isinstance(payload, dict) and payload.get("schema_version") == 2
                and payload.get("scanned") is True and fresh(payload.get("updated_at", ""))
                and isinstance(payload.get("targets"), list)
                and len(payload["targets"]) <= 80
                and all(eligible(r) for r in payload["targets"]))