"""Build optimized form/Telegram payloads from fetched HTML (GitHub-side)."""

from __future__ import annotations

from typing import Any

import config
import form_preflight
import payload_analyzer
import stack_fingerprint
import telegram_handoff
from site_signals import compact_excerpt, extract_stack_hints, looks_turkish


def build_target(
    *,
    url: str,
    html: str,
    headers: dict[str, str] | None,
    easy_score: int,
    source: str = "payload-optimizer",
    profile: str = "",
) -> dict[str, Any] | None:
    """Return an ingest-ready optimized target row."""
    host = telegram_handoff._host(url)
    if not host:
        return None
    header_blob = " ".join(f"{k}:{v}" for k, v in (headers or {}).items()).lower()
    preflight = form_preflight.analyze_html(html, header_blob=header_blob)
    if not preflight.get("form_verified"):
        return None

    fp = stack_fingerprint.fingerprint(html=html, headers=headers or {})
    analysis = payload_analyzer.analyze(html, headers=headers, fingerprint=fp)
    hints = list(dict.fromkeys(extract_stack_hints(host, html[:8000], html[:8000])))
    if fp.get("platform") and fp["platform"] not in hints:
        hints.insert(0, str(fp["platform"]))
    excerpt = compact_excerpt("", html[:12000], hints, limit=420)

    turkish = looks_turkish(excerpt, host, " ".join(hints))
    lead: dict[str, Any] = {
        "url": url,
        "company_name": host,
        "description": " ".join(analysis.get("notes") or [])[:240],
        "page_excerpt": excerpt,
        "stack_hints": hints[:8],
        "platform": fp.get("platform") or "",
        "platform_confidence": int(fp.get("confidence") or 0),
        "platform_evidence": list(fp.get("evidence") or [])[:4],
        "payment_stack": [h for h in hints if h.lower() in {"iyzico", "paytr", "stripe", "paypal"}][:4],
        "technical_gaps": list(analysis.get("gaps") or [])[:12],
        "contact_form": {"found": True},
        "captcha_detected": False,
    }

    token = telegram_handoff.token_for(url)
    pain = " ".join(analysis.get("notes") or [])[:180]
    quote = excerpt[:180]
    handoff = telegram_handoff.build_handoff_record(
        lead,
        token=token,
        company=host,
        pain=pain,
        quote=quote,
        turkish=turkish,
        gap_notes=list(analysis.get("gaps") or [])[:12],
    )
    link = config.telegram_deeplink(token)
    subject, body = telegram_handoff.form_copy(
        host=host,
        hints=hints,
        link=link,
        turkish=turkish,
        platform=str(fp.get("platform") or ""),
        confidence=int(fp.get("confidence") or 0),
    )
    if analysis.get("notes"):
        extra = analysis["notes"][0]
        body = f"{body} Teknik not: {extra}"

    return {
        "url": url,
        "easy_score": int(easy_score),
        "authorized_contact": True,
        "source": source[:80],
        "profile": profile[:40],
        "platform": lead["platform"],
        "platform_confidence": lead["platform_confidence"],
        "platform_evidence": lead["platform_evidence"],
        "stack_hints": hints[:8],
        "payment_stack": lead["payment_stack"],
        "technical_gaps": lead["technical_gaps"],
        "seo_score": int(analysis.get("seo_score") or 0),
        "form_subject": subject,
        "value_proposition": body,
        "telegram_token": token,
        "telegram_start": token,
        "telegram_deeplink": link,
        "handoff": handoff,
        "turkish": bool(turkish),
        "page_excerpt": excerpt,
        "description": lead["description"],
        "company_name": host,
        "form_verified": True,
    }
