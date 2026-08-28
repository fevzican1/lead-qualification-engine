"""Cache of GitHub-optimized form/Telegram payloads for Oracle submit runs."""

from __future__ import annotations

import json
import logging
from typing import Any

import config
import domain_store

logger = logging.getLogger(__name__)

CACHE_PATH = config.ROOT / "feeds" / "optimized_cache.json"


def _load() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {"hosts": {}, "updated_at": domain_store.utc_now()}
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt %s — resetting", CACHE_PATH.name)
        return {"hosts": {}, "updated_at": domain_store.utc_now()}
    if not isinstance(payload, dict):
        return {"hosts": {}, "updated_at": domain_store.utc_now()}
    hosts = payload.get("hosts")
    if not isinstance(hosts, dict):
        payload["hosts"] = {}
    return payload


def _save(payload: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = domain_store.utc_now()
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CACHE_PATH)


def get_for_url(url: str) -> dict[str, Any] | None:
    host = domain_store.host_of(url)
    if not host:
        return None
    row = (_load().get("hosts") or {}).get(host)
    return dict(row) if isinstance(row, dict) else None


def merge_into_lead(lead: dict[str, Any]) -> dict[str, Any]:
    """Apply a cached optimizer payload onto a collected lead row."""
    cached = get_for_url(str(lead.get("url") or ""))
    if not cached:
        return lead
    merged = dict(lead)
    for key in (
        "stack_hints",
        "platform",
        "platform_confidence",
        "platform_evidence",
        "payment_stack",
        "technical_gaps",
        "form_subject",
        "value_proposition",
        "telegram_start",
        "telegram_deeplink",
        "page_excerpt",
        "description",
        "company_name",
    ):
        if cached.get(key) not in (None, "", []):
            merged[key] = cached[key]
    if cached.get("easy_score") is not None:
        merged["easy_score"] = int(cached["easy_score"])
    merged["optimized_payload"] = True
    merged["authorized_contact"] = True
    return merged


def upsert_many(rows: list[dict[str, Any]]) -> int:
    payload = _load()
    hosts: dict[str, Any] = dict(payload.get("hosts") or {})
    n = 0
    for row in rows:
        url = str(row.get("url") or "")
        host = domain_store.host_of(url)
        if not host:
            continue
        hosts[host] = row
        n += 1
    if n:
        payload["hosts"] = hosts
        _save(payload)
        logger.info("Optimized cache upsert %s host(s)", n)
    return n
