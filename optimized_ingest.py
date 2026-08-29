"""Ingest GitHub-optimized target batches pushed to /api/v1/ingest."""

from __future__ import annotations

import logging
from typing import Any

import domain_store
import optimized_payload
import target_pool
import telegram_handoff

logger = logging.getLogger(__name__)


def ingest_batch(payload: dict[str, Any]) -> dict[str, int]:
    rows = [row for row in (payload.get("targets") or []) if isinstance(row, dict)]
    if not rows:
        return {"accepted": 0, "cached": 0, "queued": 0, "handoffs": 0}

    handoffs: dict[str, dict[str, Any]] = {}
    cache_rows: list[dict[str, Any]] = []
    queued = 0
    for row in rows:
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        score = int(row.get("easy_score") or 0)
        token = str(row.get("telegram_token") or row.get("telegram_start") or "").strip()
        handoff = row.get("handoff")
        if token and isinstance(handoff, dict):
            handoffs[token] = handoff
        cache_rows.append(row)
        target_pool.stage_candidate(
            url,
            easy_score=score,
            source=str(row.get("source") or "payload-optimizer"),
            profile=str(row.get("profile") or "optimizer"),
        )
        if domain_store.enqueue(
            url,
            source=str(row.get("source") or "payload-optimizer"),
            easy_score=score,
            authorized_contact=True,
            form_verified=True,
        ):
            queued += 1

    cached = optimized_payload.upsert_many(cache_rows)
    handoff_n = telegram_handoff.import_handoffs(handoffs)
    approved = target_pool.auto_approve()
    logger.info(
        "Optimized ingest accepted=%s cached=%s queued=%s handoffs=%s auto_approved=%s",
        len(rows),
        cached,
        queued,
        handoff_n,
        approved,
    )
    return {
        "accepted": len(rows),
        "cached": cached,
        "queued": queued,
        "handoffs": handoff_n,
        "auto_approved": approved,
    }
