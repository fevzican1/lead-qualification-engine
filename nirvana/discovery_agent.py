"""Lane A — discovery_agent [GitHub Actions].

Enterprise companies that openly hire contractors/partners AND expose a
fillable application form. Sales / support / newsletter pages are filtered
with a hard blocklist so they never reach enrichment.
"""
from __future__ import annotations

import json
from typing import Any

import config
from nirvana.registry import state_path

# Pages whose only purpose is selling to us or pushing mail — never a target.
BLOCK_TOKENS = (
    "sales", "support", "newsletter", "unsubscribe", "promo", "deals",
    "pricing", "checkout", "affiliate-program", "press", "blog",
)
# Signals of an open, fillable contractor/partner/application channel.
FIT_TOKENS = (
    "partner", "partners", "contractor", "contractors", "vendor", "vendors",
    "supplier", "suppliers", "subcontractor", "freelance", "freelancer",
    "careers", "jobs", "apply", "application", "onboarding", "rfp", "rfx",
)

DEFAULT_FEED = config.ROOT / "feeds" / "enterprise_targets.json"
DEFAULT_OUT = "discovery.json"


def _tokens(text: str) -> str:
    return (text or "").lower()


def classify(row: dict[str, Any]) -> str:
    """Return 'fit' or a specific reject verdict. Pure, no I/O."""
    url = _tokens(row.get("url") or row.get("identity_url") or "")
    host = _tokens(row.get("domain") or row.get("host") or "")
    text = _tokens(row.get("page_text") or row.get("description") or row.get("notes") or "")
    if not host and not url:
        return "reject_no_target"
    if any(token in url or token in host for token in BLOCK_TOKENS):
        return "reject_blocked_page"
    if not any(token in url or token in text for token in FIT_TOKENS):
        return "reject_no_open_application"
    if (row.get("score") is not None) and int(row["score"]) < int(config.FEED_MIN_SCORE):
        return "reject_low_score"
    return "fit"


def run_batch(
    *,
    feed_path: Any = None,
    out_name: str = DEFAULT_OUT,
    limit: int = 500,
) -> dict[str, Any]:
    """Scan the enterprise feed, keep only fits, dedupe against prior output."""
    feed = feed_path or DEFAULT_FEED
    try:
        rows = json.loads(feed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    if isinstance(rows, dict):
        rows = rows.get("targets") or rows.get("rows") or []

    out_path = state_path(out_name)
    try:
        prior = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = []
    seen = {str(r.get("domain") or r.get("url") or "") for r in prior if isinstance(r, dict)}

    counts: dict[str, int] = {}
    accepted = list(prior)
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        verdict = classify(row)
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict != "fit":
            continue
        key = str(row.get("domain") or row.get("url") or "")
        if key in seen:
            continue
        seen.add(key)
        accepted.append({
            "domain": key,
            "company": row.get("company") or row.get("name") or key,
            "url": row.get("url") or row.get("identity_url") or "",
            "score": row.get("score"),
            "discovered_by": "discovery_agent",
        })

    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(accepted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"scanned": len(rows[:limit]), "accepted": len(accepted), "verdicts": counts,
            "out": str(out_path)}
