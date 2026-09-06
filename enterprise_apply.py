"""Autonomous enterprise contractor-application engine (Faz A).

Applies to curated global partner/contractor channels with the same
evidence-first form flow used for SMB leads (variant X). Hard rules:
- Sub-capped: ENTERPRISE_DAILY_CAP per UTC day, ENTERPRISE_HOURLY_CAP per hour.
- Counts toward the SAME global Oracle quota: results are appended to
  leads.json with a confirmed status, so knowledge.submit_counts() sees them.
- Same guards as every submit: DOM fingerprint check, no CAPTCHA solving,
  opt-out, jitter. No form on the page -> skipped, zero writes.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

import config
import enterprise_targets
import enterprise_quality
import knowledge
import optout
import telegram_handoff

logger = logging.getLogger(__name__)

STATE_PATH = config.ROOT / "enterprise_applications.json"
_RESULT_STATUSES = {"submitted_confirmed", "skipped_submit_failed", "skipped_no_open_form", "skipped_captcha"}


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Application ledger invalid; refusing to submit") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Application ledger invalid; refusing to submit")
    return data


def _save_state(data: dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def enterprise_counts(state: dict[str, Any] | None = None) -> tuple[int, int]:
    """(attempts_today, attempts_last_hour), including no-send and ambiguous writes."""
    state = _load_state() if state is None else state
    now = datetime.now(timezone.utc)
    hour_ago = now.timestamp() - 3600
    today = _today()
    day_n = 0
    hour_n = 0
    for row in state.values():
        if not isinstance(row, dict):
            continue
        for stamp in row.get("attempts_at") or [str(row.get("last_at") or "")]:
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                ts = parsed.timestamp()
            except ValueError:
                continue
            if parsed.astimezone(timezone.utc).date().isoformat() == today:
                day_n += 1
            if hour_ago <= ts <= now.timestamp():
                hour_n += 1
    return day_n, hour_n


def append_to_leads(row: dict[str, Any]) -> None:
    """Mirror the enterprise submit into leads.json so the global quota ledger
    (knowledge.submit_counts) counts it â€” one shared ceiling, no double spend."""
    path = config.LEADS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except json.JSONDecodeError:
        data = []
    if not isinstance(data, list):
        data = []
    row["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    data.append(row)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def eligible_targets(limit: int) -> list[dict[str, str]]:
    if limit <= 0:
        return []
    state = _load_state()
    now = time.time()
    out: list[dict[str, str]] = []
    for row in enterprise_targets.load_all():
        key = enterprise_quality.company_key(row)
        priors = [p for p in state.values() if isinstance(p, dict) and (
            enterprise_quality.company_key(p) == key or p.get("url") == row["url"])]
        # Legacy URL keys must not re-enter through a new form/ATS endpoint.
        if any(str(p.get("last_status")) in {
            "submitted_confirmed", "submitting", "skipped_submit_failed", "failed"
        } for p in priors):
            continue  # ambiguous writes need owner review, not an automatic retry
        prior = max(priors, key=lambda p: str(p.get("last_at", "")), default=None)
        if isinstance(prior, dict):
            # Only no-send outcomes reach the cooldown path. Ambiguous writes
            # are excluded above; a submission is not acceptance by the employer.
            if str(prior.get("last_status") or "") == "submitted_confirmed":
                continue  # applied already; retainer conversation happens in Telegram
            last_ts = 0.0
            try:
                last_ts = datetime.fromisoformat(str(prior.get("last_at") or "")).timestamp()
            except ValueError:
                pass
            if last_ts and (now - last_ts) < retry_days_for(prior) * 86400:
                continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def retry_days_for(prior: dict[str, Any]) -> float:
    """Confirmed applications never retry; failed reach attempts retry fast."""
    if str(prior.get("last_status") or "") == "submitted_confirmed":
        return 36500.0
    return max(0.0, float(getattr(config, "ENTERPRISE_RETRY_SKIP_DAYS", 3)))


def _pain(target: dict[str, str]) -> str:
    lane = str(target.get("lane") or "contractor")
    lane_txt = lane.replace("-", " ").replace("_", " ").strip().title()
    return (
        f"Application for {target.get('company', '')}: {lane_txt}. "
        "Proposed AI-assisted integration service; scope and acceptance criteria pending."
    )


def run_batch(*, budget: int | None = None) -> dict[str, Any]:
    """Serialize enterprise attempts across processes; OS releases lock on crash."""
    lock = STATE_PATH.with_suffix(".lock")
    with lock.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"ran": False, "why": "enterprise lane already running"}
        try:
            return _run_batch(budget=budget)
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run_batch(*, budget: int | None = None) -> dict[str, Any]:
    """Apply to up to the allowed number of enterprise channels this hour.

    `budget` (seconds) bounds the whole batch when the caller shares one
    Playwright window; None = standalone with its own browser per submit.
    """
    if not getattr(config, "ENTERPRISE_MODE", True):
        return {"ran": False, "why": "enterprise mode off"}
    # Separate legacy processes do not share this lock; do not run both lanes.
    if getattr(config, "SMB_LANE_ENABLED", False):
        return {"ran": False, "why": "legacy lane enabled; concurrent enterprise writes disabled"}

    daily_cap = max(0, min(4, int(getattr(config, "ENTERPRISE_DAILY_CAP", 4))))
    hourly_cap = max(0, min(2, int(getattr(config, "ENTERPRISE_HOURLY_CAP", 2))))
    ent_today, ent_hour = enterprise_counts()
    if ent_today >= daily_cap:
        return {"ran": False, "why": f"daily sub-cap {ent_today}/{daily_cap}"}
    if ent_hour >= hourly_cap:
        return {"ran": False, "why": f"hourly sub-cap {ent_hour}/{hourly_cap}"}

    # Global Oracle quota check â€” enterprise shares the same ceiling.
    today_n, hour_n = knowledge.submit_counts()
    daily_cap_all = knowledge.daily_cap()
    hourly_cap_all = knowledge.hourly_cap()
    room_today = daily_cap_all - today_n
    room_hour = hourly_cap_all - hour_n
    allowed = max(0, min(daily_cap - ent_today, hourly_cap - ent_hour, room_today, room_hour, 2))
    if allowed <= 0:
        return {"ran": False, "why": "global quota room reserved for pipeline"}

    targets = eligible_targets(allowed)
    if not targets:
        return {"ran": False, "why": "no eligible targets (cooldown/already applied)"}

    results: list[dict[str, Any]] = []
    state = _load_state()
    ent_ms = int(getattr(config, "ENTERPRISE_FINGERPRINT_MS", 9000) or 9000)
    for target in targets:
        today_n, hour_n = knowledge.submit_counts()
        if today_n >= knowledge.daily_cap() or hour_n >= knowledge.hourly_cap():
            break
        if budget is not None and budget < 30:
            break
        lead = enterprise_targets.target_lead(target)
        if optout.is_url_opted_out(str(lead["url"])):
            continue
        turkish = False
        token = ""
        try:
            token = telegram_handoff.remember(
                lead, company=target["company"], pain=_pain(target),
                quote=str(target["evidence"]["demand_quote"]), turkish=turkish
            )
            subject, note = telegram_handoff.form_copy(
                host=lead["identity_url"],
                hints=[],
                link=config.telegram_deeplink(token),
                turkish=turkish,
                platform=target.get("platform", ""),
                confidence=0,
                audience="enterprise",
                opportunity=target,
            )
        except Exception:
            logger.exception("Enterprise handoff build failed for %s", target["url"])
            continue
        lead["value_proposition"] = note
        lead["form_subject"] = subject
        lead["hook_variant"] = "X"
        lead["_enterprise_fingerprint_ms"] = ent_ms

        candidates = [lead["url"]]  # exact scanned form only; no sales/contact fallback
        key = enterprise_quality.company_key(target)
        previous = state.get(key) or {}
        attempts = list(previous.get("attempts_at") or ([previous["last_at"]] if previous.get("last_at") else []))
        attempts.append(datetime.now(timezone.utc).isoformat())
        state[key] = {"company": target["company"], "url": lead["url"],
                      "last_status": "submitting", "last_at": attempts[-1], "attempts_at": attempts}
        _save_state(state)  # reserve before a possible write; crash => manual review
        status = "failed"
        applied_url = lead["url"]
        for cand in candidates:
            if budget is not None and budget <= 0:
                break
            cl = dict(lead)
            cl["url"] = cand
            cl["final_url"] = cand
            cl["contact_form"] = {"found": True, "page_url": cand}
            started = time.monotonic()
            try:
                import form_submitter

                res = form_submitter.submit_lead(cl, headless=True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Enterprise submit crashed for %s", cand)
                res = {**cl, "status": "failed", "error": str(exc)[:200]}
            spent = time.monotonic() - started
            if budget is not None:
                budget -= spent
            status = str(res.get("status") or "unknown")
            applied_url = cand
            if status != "skipped_no_open_form":
                break  # the page has a form we could reach (or it's an unrecoverable fail)

        entry = {
            "company": target["company"],
            "url": applied_url,
            "lane": target.get("lane", ""),
            "report_id": telegram_handoff.report_id(lead["identity_url"]),
            "token": token,
            "attempts_at": attempts,
            "last_status": status if status in _RESULT_STATUSES else "failed",
            "last_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        state[key] = entry
        _save_state(state)
        results.append(entry)
        if status == "submitted_confirmed":
            append_to_leads(
                {
                    "url": applied_url,
                    "company_name": target["company"],
                    "status": "submitted_confirmed",
                    "audience": "enterprise",
                    "form_subject": subject,
                    "updated_at": entry["last_at"],
                }
            )
        logger.info(
            "Enterprise application %s -> %s (%s)", target["company"], applied_url, status
        )
        time.sleep(random.uniform(6.0, 14.0))  # anti-spam pacing lane

    applied = sum(1 for r in results if r["last_status"] == "submitted_confirmed")
    skipped = sum(1 for r in results if r["last_status"] != "submitted_confirmed")
    if results:
        try:
            import owner_notify

            lines = ["Kurumsal başvuru turu (Faz A):"]
            for r in results:
                mark = "OK" if r["last_status"] == "submitted_confirmed" else "--"
                lines.append(
                    f"{mark} {r['company']} — {r['last_status']} — Rapor No {r['report_id']}"
                )
            owner_notify.send("\n".join(lines))
        except Exception:
            logger.exception("Owner notify failed after enterprise batch")
    return {"ran": bool(results), "applied": applied, "skipped": skipped, "results": results}
