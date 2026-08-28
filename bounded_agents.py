"""Small, bounded decision layer shared by recon, hooks, and the closer.

This module deliberately does not perform network requests or form submits.
It turns evidence already collected by the pipeline into an idempotent,
auditable decision that callers can use before spending browser or quota
resources.
"""

from __future__ import annotations

import hashlib
from typing import Any

import config
import optout
import stack_fingerprint
import telegram_handoff
import target_pool

PLATFORM_CONFIDENCE_THRESHOLD = stack_fingerprint.PLATFORM_CONFIDENCE_THRESHOLD
NEUTRAL_ENGINEERING_HOOK = telegram_handoff.NEUTRAL_ENGINEERING_HOOK


def recon_context(lead: dict[str, Any]) -> dict[str, Any]:
    """Return only source-backed platform context; never infer from prose."""
    platform = str(lead.get("platform") or "").strip()
    confidence = int(lead.get("platform_confidence") or 0)
    evidence = [str(item) for item in (lead.get("platform_evidence") or []) if item]
    confirmed = bool(
        platform
        and confidence >= PLATFORM_CONFIDENCE_THRESHOLD
        and len(evidence) >= 2
    )
    if not confirmed:
        platform = ""
        confidence = min(confidence, PLATFORM_CONFIDENCE_THRESHOLD - 1)
    return {
        "platform": platform,
        "confidence": confidence,
        "evidence": evidence[:4],
        "confirmed": confirmed,
        "hook": telegram_handoff.classify_hook(
            platform=platform,
            confidence=confidence,
        ),
    }


def outreach_gate(lead: dict[str, Any]) -> dict[str, Any]:
    """Check local safety gates before a caller attempts a form submit."""
    url = str(lead.get("url") or "")
    if not url or optout.is_url_opted_out(url):
        return {"allowed": False, "reason": "opted_out"}
    if lead.get("captcha_detected"):
        return {"allowed": False, "reason": "captcha"}
    if not (lead.get("contact_form") or {}).get("found"):
        return {"allowed": False, "reason": "no_public_form"}
    easy = int(lead.get("easy_score") or 0)
    if lead.get("authorized_contact") is not True and not target_pool.is_authorized(
        url, easy_score=easy
    ):
        return {"allowed": False, "reason": "below_auto_approve_score"}
    return {"allowed": True, "reason": "authorized"}


def closer_context(lead: dict[str, Any]) -> dict[str, Any]:
    """Create a stable closer context without invoking a model or sending mail."""
    recon = recon_context(lead)
    url = str(lead.get("url") or "")
    key = hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()[:16]
    return {
        "decision_key": key,
        "host": str(lead.get("host") or ""),
        "platform": recon["platform"],
        "platform_confirmed": recon["confirmed"],
        "platform_confidence": recon["confidence"],
        "platform_evidence": recon["evidence"],
        "hook": recon["hook"],
        "price": config.price_label(),
        "payment_link_allowed": False,
    }
