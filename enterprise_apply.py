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
import random
import time
from datetime import datetime, timezone
from typing import Any

import config
import enterprise_targets
import knowledge
import optout
import telegram_handoff

logger = logging.getLogger(__name__)

STATE_PATH = config.ROOT / "enterprise_applications.json"
COOLDOWN_DAYS = 21
_RESULT_STATUSES = {"submitted_confirmed", "skipped_submit_failed", "skipped_no_open_form", "skipped_captcha"}


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(data: dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def enterprise_counts(state: dict[str, Any] | None = None) -> tuple[int, int]:
    """(applications_today, applications_last_hour) from the local ledger."""
    state = state or _load_state()
    now = datetime.now(timezone.utc)
    hour_ago = now.timestamp() - 3600
    today = _today()
    day_n = 0
    hour_n = 0
    for row in state.values():
        if not isinstance(row, dict):
            continue
        if str(row.get("last_status") or "") != "submitted_confirmed":
            continue
        stamp = str(row.get("last_at") or "")
        if not stamp.startswith(today):
            continue
        day_n += 1
        try:
            ts = datetime.fromisoformat(stamp).timestamp()
        except ValueError:
            continue
        if ts >= hour_ago:
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
    state = _load_state()
    now = time.time()
    out: list[dict[str, str]] = []
    for row in enterprise_targets.TARGETS:
        prior = state.get(row["url"])
        if isinstance(prior, dict):
            if str(prior.get("last_status") or "") == "submitted_confirmed":
                continue  # applied already; retainer conversation happens in Telegram
            last_ts = 0.0
            try:
                last_ts = datetime.fromisoformat(str(prior.get("last_at") or "")).timestamp()
            except ValueError:
                pass
            if last_ts and (now - last_ts) < COOLDOWN_DAYS * 86400:
                continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _pain() -> str:
    return (
        "KontratlÄ± entegrasyon mÃ¼hendisi kancasÄ±: Ã¶deme akÄ±ÅŸÄ± / formâ†’CRM "
        "entegrasyonu iÃ§in kanÄ±tlÄ± Ã¶n Ã§alÄ±ÅŸma hazÄ±r."
    )


def run_batch(*, budget: int | None = None) -> dict[str, Any]:
    """Apply to up to the allowed number of enterprise channels this hour.

    `budget` (seconds) bounds the whole batch when the caller shares one
    Playwright window; None = standalone with its own browser per submit.
    """
    if not getattr(config, "ENTERPRISE_MODE", True):
        return {"ran": False, "why": "enterprise mode off"}

    daily_cap = int(getattr(config, "ENTERPRISE_DAILY_CAP", 4) or 4)
    hourly_cap = int(getattr(config, "ENTERPRISE_HOURLY_CAP", 2) or 2)
    ent_today, ent_hour = enterprise_counts()
    if ent_today >= daily_cap:
        return {"ran": False, "why": f"daily sub-cap {ent_today}/{daily_cap}"}
    if ent_hour >= hourly_cap:
        return {"ran": False, "why": f"hourly sub-cap {ent_hour}/{hourly_cap}"}

    # Global Oracle quota check â€” enterprise shares the same ceiling.
    today_n, hour_n = knowledge.submit_counts()
    daily_cap_all = knowledge.daily_cap()
    hourly_cap_all = knowledge.hourly_cap()
    room_today = daily_cap_all - today_n - max(0, daily_cap - ent_today)
    room_hour = hourly_cap_all - hour_n - max(0, hourly_cap - ent_hour)
    allowed = max(0, min(daily_cap - ent_today, hourly_cap - ent_hour, room_today, room_hour, 2))
    if allowed <= 0:
        return {"ran": False, "why": "global quota room reserved for pipeline"}

    targets = eligible_targets(allowed)
    if not targets:
        return {"ran": False, "why": "no eligible targets (cooldown/already applied)"}

    results: list[dict[str, Any]] = []
    state = _load_state()
    for target in targets:
        lead = enterprise_targets.target_lead(target)
        if optout.is_url_opted_out(str(lead["url"])):
            continue
        turkish = False
        token = ""
        try:
            token = telegram_handoff.remember(
                lead, company=target["company"], pain=_pain(), quote="", turkish=turkish
            )
            subject, note = telegram_handoff.form_copy(
                host=target["company"].lower().replace(" ", "") + ".example",
                hints=[],
                link=config.telegram_deeplink(token),
                turkish=turkish,
                platform=target.get("platform", ""),
                confidence=95,
                audience="enterprise",
            )
        except Exception:
            logger.exception("Enterprise handoff build failed for %s", target["url"])
            continue
        lead["value_proposition"] = note
        lead["form_subject"] = subject
        lead["hook_variant"] = "X"
        if budget is not None and budget <= 0:
            break

        started = time.monotonic()
        try:
            import form_submitter

            result = form_submitter.submit_lead(lead, headless=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Enterprise submit crashed for %s", target["url"])
            result = {**lead, "status": "failed", "error": str(exc)[:200]}
        spent = time.monotonic() - started
        if budget is not None:
            budget -= spent

        status = str(result.get("status") or "unknown")
        entry = {
            "company": target["company"],
            "url": target["url"],
            "lane": target.get("lane", ""),
            "report_id": telegram_handoff.report_id(target["company"]),
            "token": token,
            "last_status": status if status in _RESULT_STATUSES else "failed",
            "last_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        state[target["url"]] = entry
        _save_state(state)
        results.append(entry)
        if status == "submitted_confirmed":
            append_to_leads(
                {
                    "url": target["url"],
                    "company_name": target["company"],
                    "status": "submitted_confirmed",
                    "audience": "enterprise",
                    "form_subject": subject,
                    "updated_at": entry["last_at"],
                }
            )
        logger.info(
            "Enterprise application %s -> %s (%s)", target["company"], target["url"], status
        )
        time.sleep(random.uniform(6.0, 14.0))  # anti-spam pacing lane

    applied = sum(1 for r in results if r["last_status"] == "submitted_confirmed")
    skipped = sum(1 for r in results if r["last_status"] != "submitted_confirmed")
    if results:
        try:
            import owner_notify

            lines = ["Kurumsal baÅŸvuru turu (Faz A):"]
            for r in results:
                mark = "OK" if r["last_status"] == "submitted_confirmed" else "--"
                lines.append(
                    f"{mark} {r['company']} â€” {r['last_status']} â€” Rapor No {r['report_id']}"
                )
            owner_notify.send("\n".join(lines))
        except Exception:
            logger.exception("Owner notify failed after enterprise batch")
    return {"ran": bool(results), "applied": applied, "skipped": skipped, "results": results}
