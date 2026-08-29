"""Review queue -> authorized targets with instant auto-approve for high scores.

High-scoring public discovery rows (easy_score >= FEED_MIN_SCORE) are staged in
review_queue.json and promoted to authorized_targets.txt with zero delay. Spam
controls remain: daily/hourly caps, provider pacing, opt-out, CAPTCHA, timeouts.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import config
import domain_store
import optout

logger = logging.getLogger(__name__)

REVIEW_QUEUE_PATH = config.ROOT / "review_queue.json"
AUTHORIZED_TARGETS_PATH = config.ROOT / "authorized_targets.txt"


def _min_auto_score() -> int:
    return int(getattr(config, "FEED_MIN_SCORE", 80) or 80)


def _load_review() -> dict[str, Any]:
    if not REVIEW_QUEUE_PATH.exists():
        return {"urls": [], "updated_at": domain_store.utc_now()}
    try:
        payload = json.loads(REVIEW_QUEUE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt %s — resetting", REVIEW_QUEUE_PATH.name)
        return {"urls": [], "updated_at": domain_store.utc_now()}
    if not isinstance(payload, dict):
        return {"urls": [], "updated_at": domain_store.utc_now()}
    rows = payload.get("urls")
    if not isinstance(rows, list):
        payload["urls"] = []
    return payload


def _save_review(payload: dict[str, Any]) -> None:
    payload["updated_at"] = domain_store.utc_now()
    tmp = REVIEW_QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(REVIEW_QUEUE_PATH)


def _load_authorized_urls() -> set[str]:
    hosts: set[str] = set()
    for path in (AUTHORIZED_TARGETS_PATH, config.TARGETS_PATH):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            host = domain_store.host_of(text)
            if host:
                hosts.add(host)
    return hosts


def _append_authorized(url: str) -> bool:
    canonical = domain_store.origin_url(url)
    host = domain_store.host_of(canonical)
    if not canonical or not host:
        return False
    if host in _load_authorized_urls():
        return False
    AUTHORIZED_TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUTHORIZED_TARGETS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{canonical}\n")
    return True


def stage_candidate(
    url: str,
    *,
    easy_score: int,
    source: str = "public-discovery",
    profile: str = "",
    form_verified: bool = False,
) -> None:
    """Add or refresh a discovery row in the review queue."""
    canonical = domain_store.origin_url(url)
    host = domain_store.host_of(canonical)
    if not canonical or not host:
        return
    if optout.is_url_opted_out(canonical) or domain_store.is_enterprise(canonical):
        return
    payload = _load_review()
    rows = payload.setdefault("urls", [])
    merged = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if domain_store.host_of(str(row.get("url") or "")) != host:
            continue
        row["url"] = canonical
        row["easy_score"] = max(int(row.get("easy_score") or 0), int(easy_score))
        row["source"] = str(source or row.get("source") or "public-discovery")[:80]
        if profile:
            row["profile"] = str(profile)[:40]
        if form_verified:
            row["form_verified"] = True
        merged = True
        break
    if not merged:
        entry: dict[str, Any] = {
            "url": canonical,
            "easy_score": int(easy_score),
            "source": str(source or "public-discovery")[:80],
            "profile": str(profile or "")[:40],
            "staged_at": domain_store.utc_now(),
        }
        if form_verified:
            entry["form_verified"] = True
        rows.append(entry)
    _save_review(payload)


def auto_approve(*, limit: int | None = None) -> int:
    """Promote score>=FEED_MIN_SCORE review rows to authorized_targets instantly."""
    threshold = _min_auto_score()
    payload = _load_review()
    rows = [row for row in (payload.get("urls") or []) if isinstance(row, dict)]
    approved = 0
    keep: list[dict[str, Any]] = []
    for row in rows:
        url = str(row.get("url") or "")
        score = int(row.get("easy_score") or 0)
        if score < threshold:
            keep.append(row)
            continue
        if limit is not None and approved >= int(limit):
            keep.append(row)
            continue
        if optout.is_url_opted_out(url) or domain_store.is_noise(url):
            continue
        if _append_authorized(url):
            approved += 1
        domain_store.enqueue(
            url,
            source=str(row.get("source") or "authorized-discovery"),
            easy_score=score,
            authorized_contact=True,
            form_verified=bool(row.get("form_verified")),
        )
    if approved or len(keep) != len(rows):
        payload["urls"] = keep
        _save_review(payload)
        logger.info(
            "Auto-approved %s review row(s) at score>=%s (review_left=%s)",
            approved,
            threshold,
            len(keep),
        )
    return approved


def is_authorized(url: str, *, easy_score: int = 0) -> bool:
    """Return whether outreach is allowed before opt-out/CAPTCHA gates."""
    canonical = domain_store.origin_url(url)
    if not canonical or optout.is_url_opted_out(canonical):
        return False
    host = domain_store.host_of(canonical)
    if not host:
        return False
    if host in _load_authorized_urls():
        return True
    return int(easy_score) >= _min_auto_score()


def promote_queue_authorization() -> int:
    """Upgrade live queue rows that already meet the auto-approve threshold."""
    threshold = _min_auto_score()
    data = domain_store._queue()
    changed = 0
    for row in data.get("urls") or []:
        if not isinstance(row, dict):
            continue
        score = int(row.get("easy_score") or 0)
        if score < threshold:
            continue
        url = str(row.get("url") or "")
        if not is_authorized(url, easy_score=score):
            continue
        if row.get("authorized_contact") is not True:
            row["authorized_contact"] = True
            changed += 1
        stage_candidate(url, easy_score=score, source=str(row.get("source") or "queue-resync"))
        _append_authorized(url)
    if changed:
        data["updated_at"] = domain_store.utc_now()
        domain_store._save_json(domain_store.QUEUE_PATH, data)
        logger.info("Promoted %s queued row(s) to authorized_contact=true", changed)
    return changed


def sync(*, review_limit: int | None = None) -> dict[str, int]:
    """Run the zero-delay review->authorized worker and refresh queue flags."""
    approved = auto_approve(limit=review_limit)
    promoted = promote_queue_authorization()
    return {"approved": approved, "promoted": promoted}
