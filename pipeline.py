"""
Pipeline orchestrator.

Sequence:
  1. HTTP prefilter (no Chromium): skip captcha / no-form, rank high-value stacks first
  2. Collect with resource-blocked Playwright
  3. Template-qualify (DeepSeek stays on Telegram — Ampere RAM)
  4. Submit with adaptive jitter; collect the next site during that wait
  5. Stop submits at daily/hourly caps so the Oracle IP stays clean

Usage:
    python pipeline.py --targets targets.txt
    python pipeline.py --targets targets.txt --submit
    python pipeline.py --targets targets.txt --limit 5 --headful
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Page
from playwright.sync_api import sync_playwright

import browser
import bounded_agents
import config
import domain_store
import knowledge
import optout
import owner_notify
import pacing
import prefilter
import easy_score
from collector import collect_error_status, scan_one
from form_submitter import submit_lead
from qualification_analyzer import qualify_lead

logger = logging.getLogger(__name__)

DONE_STATUSES = {
    "submitted",
    "submitted_confirmed",
    "submitted_unconfirmed",
    "skipped_captcha",
    "skipped_no_form",
    "skipped_no_open_form",
    "skipped_unsubscribed",
    "skipped_submit_failed",
    "skipped_unreachable",
    "skipped_enterprise",
    "skipped_unauthorized",
}


def _is_enterprise(url: str) -> bool:
    return domain_store.is_enterprise(url)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_targets(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(
            f"Targets file not found: {path}. Copy targets.example.txt to targets.txt."
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    urls = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    if not urls:
        raise ValueError(f"No target URLs in {path}")
    return urls


def authorize_target_rows(leads: list[dict[str, Any]], target_urls: list[str]) -> int:
    """Apply the operator-owned target allowlist to existing lead rows.

    Discovery rows stay analysis-only. This only upgrades rows whose host is
    explicitly present in the configured targets file and never clears an
    opt-out or changes a submission status.
    """
    target_keys = {_url_key(url) for url in target_urls if _url_key(url)}
    changed = 0
    for lead in leads:
        key = _url_key(str(lead.get("url") or ""))
        if (
            key in target_keys
            and not optout.is_url_opted_out(key)
            and lead.get("authorized_contact") is not True
        ):
            lead["authorized_contact"] = True
            if str(lead.get("status") or "") == "skipped_unauthorized":
                domain_store.unmark(key)
                lead["status"] = "queued"
                lead["error"] = None
            changed += 1
    return changed


def refresh_retryable_targets(target_urls: list[str]) -> set[str]:
    """Reopen one cooled-down no-send attempt from the explicit target list."""
    reopened: set[str] = set()
    for url in target_urls:
        if domain_store.requeue_if_retryable(url):
            key = _url_key(url)
            if key:
                reopened.add(key)
    return reopened


def load_leads(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Corrupt leads file {path}: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"{path} must contain a JSON array")
    return [item for item in data if isinstance(item, dict)]


def save_leads(path: Path, leads: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(leads, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def upsert(leads: list[dict[str, Any]], lead: dict[str, Any]) -> list[dict[str, Any]]:
    key = (lead.get("url") or "").rstrip("/").lower()
    lead["updated_at"] = utc_now()
    for index, existing in enumerate(leads):
        if (existing.get("url") or "").rstrip("/").lower() == key:
            merged = dict(existing)
            merged.update(lead)
            leads[index] = merged
            return leads
    leads.append(lead)
    return leads


def eligible_for_submit(lead: dict[str, Any], min_score: int) -> bool:
    url = str(lead.get("url") or "")
    if (
        optout.is_url_opted_out(url)
        or domain_store.is_deferred(url)
        or not bounded_agents.outreach_gate(lead).get("allowed")
    ):
        return False
    status = str(lead.get("status") or "")
    if status.startswith("submitted"):
        return False
    if status in domain_store.DEAD_QUEUE or status == "skipped_submit_failed":
        return False
    if lead.get("captcha_detected") or status == "skipped_captcha":
        return False
    if not (lead.get("contact_form") or {}).get("found"):
        return False
    score = int(lead.get("fit_score") or 0)
    if score < min_score:
        return False
    if not (lead.get("value_proposition") or "").strip():
        return False
    attempts = int(lead.get("submit_attempts") or 0)
    err = str(lead.get("error") or "").lower()
    if attempts >= 2:
        return False
    if attempts >= 1 and "timeout" in err:
        return False
    if easy_score.from_lead(lead) < int(getattr(config, "EASY_SCORE_MIN", 55) or 55):
        return False
    return True


def submitted_today(leads: list[dict[str, Any]]) -> int:
    today, _hour = knowledge.submit_counts(leads)
    return today


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        path = config.ROOT / path
    return path


def _ready_submit_jobs(leads: list[dict[str, Any]], min_score: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """Already-qualified form leads — Chromium submit with zero HTTP probes."""
    jobs: list[dict[str, Any]] = []
    for lead in leads:
        if not eligible_for_submit(lead, min_score):
            continue
        if lead.get("status") == "failed" and int(lead.get("submit_attempts") or 0) >= 2:
            continue
        if "timeout" in str(lead.get("error") or "").lower() and int(lead.get("submit_attempts") or 0) >= 1:
            continue
        jobs.append(
            {
                "url": lead.get("url"),
                "priority": int(lead.get("priority") or 40),
                "easy_score": easy_score.from_lead(lead),
                "waf_strict": bool(lead.get("waf_strict")),
                "stack_hints": list(lead.get("stack_hints") or []),
                "form_likely": True,
                "ok": True,
                "authorized_contact": bool(lead.get("authorized_contact")),
            }
        )
        if len(jobs) >= limit * 3:
            break
    jobs.sort(key=lambda row: (-int(row.get("easy_score") or 0), -int(row.get("priority") or 0)))
    return jobs[:limit]


def _queue_direct_jobs(
    leads: list[dict[str, Any]],
    *,
    seen: set[str],
    min_score: int,
    limit: int,
) -> list[dict[str, Any]]:
    """High easy_score queue rows → Chromium, zero HTTP prefilter.
    Prefer Common Crawl feed score>=80, then 65+ so the hour does not stall.
    """
    premium = int(getattr(config, "FEED_MIN_SCORE", 80) or 80)
    floor = int(getattr(config, "CHROMIUM_DIRECT_MIN", 65) or 65)
    known = {_url_key(item.get("url") or ""): item for item in leads}

    def _collect(min_easy: int, max_easy: int | None, need: int) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        if need <= 0:
            return jobs
        for row in domain_store.pending_rows(
            limit=max(80, need * 4), min_easy=min_easy, max_easy=max_easy
        ):
            url = str(row.get("url") or "")
            key = _url_key(url)
            if not url or key in seen:
                continue
            lead = known.get(key) or {}
            if lead.get("status") in DONE_STATUSES or lead.get("status") == "failed":
                continue
            if optout.is_url_opted_out(url) or _is_enterprise(url) or domain_store.is_processed(url):
                continue
            if domain_store.is_noise(url):
                continue
            if str(row.get("source") or "") == "catalog":
                continue
            if lead and eligible_for_submit(lead, min_score):
                continue
            jobs.append(
                {
                    "url": url,
                    "priority": 80 if min_easy >= premium else 50,
                    "easy_score": int(row.get("easy_score") or min_easy),
                    "waf_strict": False,
                    "stack_hints": list(lead.get("stack_hints") or []),
                    "form_likely": True,
                    "ok": True,
                    "authorized_contact": bool(
                        lead.get("authorized_contact")
                        or str(row.get("source") or "") == "targets.txt"
                    ),
                }
            )
            if len(jobs) >= need:
                break
        return jobs

    first = _collect(premium, None, limit)
    if len(first) >= limit:
        return first
    first.extend(_collect(floor, premium, limit - len(first)))
    return first


def _needs_http_probe(url: str, row: dict[str, Any], min_score: int) -> bool:
    del url
    if not row:
        return True
    if row.get("status") in DONE_STATUSES:
        return False
    if eligible_for_submit(row, min_score):
        return False
    if (row.get("contact_form") or {}).get("found") and not row.get("captcha_detected"):
        return False
    return True


def _reusable_lead(leads: list[dict[str, Any]], url: str, min_score: int) -> dict[str, Any] | None:
    key = _url_key(url)
    for lead in leads:
        if _url_key(lead.get("url") or "") != key:
            continue
        if lead.get("status") not in {"qualified", "queued_daily_cap", "queued_hourly_cap", "failed"}:
            continue
        if not eligible_for_submit(lead, min_score):
            return None
        return dict(lead)
    return None


def _collect_one(page: Page, url: str, probe: dict[str, Any]) -> dict[str, Any]:
    try:
        lead = scan_one(page, url, timeout_ms=config.NAV_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Collector failed for %s", url)
        return {
            "url": url,
            "status": collect_error_status(str(exc)),
            "error": str(exc),
            "contact_form": {"found": False},
            "captcha_detected": False,
            "waf_strict": bool(probe.get("waf_strict")),
            "easy_score": 10,
        }
    lead["waf_strict"] = bool(probe.get("waf_strict") or lead.get("waf_strict"))
    lead["priority"] = int(probe.get("priority") or lead.get("priority") or 0)
    lead["authorized_contact"] = bool(probe.get("authorized_contact"))
    lead["easy_score"] = int(
        probe.get("easy_score")
        or lead.get("easy_score")
        or easy_score.from_lead(lead)
    )
    if (lead.get("contact_form") or {}).get("found") and not lead.get("captcha_detected"):
        lead["easy_score"] = max(int(lead.get("easy_score") or 0), 85)
    if probe.get("stack_hints") and not lead.get("stack_hints"):
        lead["stack_hints"] = list(probe.get("stack_hints") or [])
    return lead


def _skip_no_form(item: dict[str, Any]) -> dict[str, Any]:
    skipped = dict(item)
    skipped.update(
        {
            "status": "skipped_no_open_form",
            "fit_score": int(item.get("fit_score") or 0),
            "value_proposition": "",
            "should_contact": False,
            "fit_rationale": "No public form without CAPTCHA — Chromium moved on.",
        }
    )
    return skipped


def run_pipeline(args: argparse.Namespace) -> int:
    leads_path = _resolve(args.leads)
    min_score = args.min_score if args.min_score is not None else config.MIN_FIT_SCORE
    headless = not args.headful
    submitting = bool(args.submit)

    config.require_pipeline_keys(submitting=submitting)
    leads = load_leads(leads_path)
    n_old = 0
    for lead in leads:
        if str(lead.get("status") or "") != "failed":
            continue
        if (lead.get("contact_form") or {}).get("found"):
            lead["status"] = "skipped_submit_failed"
        else:
            lead["status"] = "skipped_unreachable"
        n_old += 1
    if n_old:
        logger.info("Reclassified %s old failed rows to skip statuses", n_old)
    pruned = domain_store.prune_dead_queue(leads)
    if pruned:
        logger.info("Pruned %s dead captcha/no-form host(s) from queue", pruned)
    leads = _retry_map_fails(leads)
    try:
        file_targets = load_targets(_resolve(args.targets))
    except (FileNotFoundError, ValueError):
        file_targets = []
    authorized_rows = authorize_target_rows(leads, file_targets)
    reopened_targets = refresh_retryable_targets(file_targets)
    if reopened_targets:
        for lead in leads:
            if _url_key(str(lead.get("url") or "")) in reopened_targets:
                lead["status"] = "queued"
                lead["error"] = None
    for url in file_targets:
        domain_store.enqueue(url, source="targets.txt")
    save_leads(leads_path, leads)
    if authorized_rows:
        logger.info("Authorized %s existing lead row(s) from targets allowlist", authorized_rows)
    if reopened_targets:
        logger.info("Reopened %s cooled-down target row(s) for one retry", reopened_targets)
    domain_store.hydrate_from_leads(leads)
    knowledge.refresh(leads=leads)

    cap_daily = knowledge.daily_cap()
    cap_hourly = knowledge.hourly_cap()
    probe_n = args.limit or config.HTTP_PROBE_BATCH
    max_probes = int(args.limit or getattr(config, "MAX_PIPELINE_PROBES", 120) or 120)
    if args.limit:
        max_probes = max(1, int(args.limit))

    all_processed: list[dict[str, Any]] = []
    target_keys: set[str] = set()
    seen: set[str] = set()
    probes_used = 0

    def _pending_slice(take: int) -> list[str]:
        raw = domain_store.pending_rows(
            limit=min(250, take + len(seen) + 30),
            min_easy=int(getattr(config, "EASY_SCORE_MIN", 55) or 55),
            max_easy=int(getattr(config, "CHROMIUM_DIRECT_MIN", 65) or 65),
        )
        out: list[str] = []
        known = {_url_key(item.get("url") or ""): item for item in load_leads(leads_path)}
        for row in raw:
            url = str(row.get("url") or "")
            key = _url_key(url)
            if key in seen:
                continue
            lead = known.get(key) or {}
            if lead.get("status") in DONE_STATUSES or lead.get("status") == "failed":
                continue
            if optout.is_url_opted_out(url) or _is_enterprise(url) or domain_store.is_processed(url):
                continue
            if domain_store.is_noise(url):
                continue
            if not _needs_http_probe(url, lead, min_score):
                continue
            out.append(url)
            if len(out) >= take:
                break
        return out

    def _run_slice(pending: list[str]) -> list[dict[str, Any]]:
        nonlocal leads
        logger.info("HTTP prefilter on %s target(s); queue_depth=%s", len(pending), domain_store.queue_depth())
        jobs, http_skips = prefilter.split_and_rank(pending)
        for item in http_skips:
            leads = upsert(leads, item)
            domain_store.mark(str(item.get("url") or ""), str(item.get("status") or "skipped_no_form"))
            target_keys.add(_url_key(str(item.get("url") or "")))
            all_processed.append(item)
        save_leads(leads_path, leads)
        if not jobs:
            logger.info("Slice: 0 form-likely after prefilter (fast-fail, next slice)")
            return []
        logger.info(
            "Prefilter kept %s for Chromium, skipped %s (captcha/no-form/dead)",
            len(jobs),
            len(http_skips),
        )
        return _run_browser_pipeline(
            jobs,
            leads,
            leads_path,
            min_score=min_score,
            headless=headless,
            submitting=submitting,
        )

    while True:
        leads = load_leads(leads_path)
        today_n, hour_n = knowledge.submit_counts(leads)
        if submitting and (today_n >= cap_daily or hour_n >= cap_hourly):
            logger.info("Stop pull — today %s/%s hour %s/%s", today_n, cap_daily, hour_n, cap_hourly)
            break

        if submitting and hour_n < cap_hourly:
            reuse = [
                job
                for job in _ready_submit_jobs(leads, min_score, limit=cap_hourly - hour_n)
                if _url_key(str(job.get("url") or "")) not in seen
            ]
            if reuse:
                for job in reuse:
                    seen.add(_url_key(str(job.get("url") or "")))
                logger.info("Submit-ready reuse %s lead(s) — 0 HTTP", len(reuse))
                processed = _run_browser_pipeline(
                    reuse,
                    leads,
                    leads_path,
                    min_score=min_score,
                    headless=headless,
                    submitting=submitting,
                )
                all_processed.extend(processed)
                leads = load_leads(leads_path)
                hit_cap = False
                for item in processed:
                    target_keys.add(_url_key(item.get("url") or ""))
                    status = str(item.get("status") or "")
                    if status in DONE_STATUSES or status in domain_store.TERMINAL:
                        domain_store.mark(str(item.get("url") or ""), status)
                    if status in {"queued_hourly_cap", "queued_daily_cap"}:
                        hit_cap = True
                if hit_cap:
                    break
                _, hour_n = knowledge.submit_counts(leads)
                if hour_n >= cap_hourly:
                    break
                continue

            direct = _queue_direct_jobs(
                leads,
                seen=seen,
                min_score=min_score,
                limit=_visit_budget(cap_hourly - hour_n),
            )
            if direct:
                for job in direct:
                    seen.add(_url_key(str(job.get("url") or "")))
                logger.info("Chromium-direct %s lead(s) — 0 HTTP prefilter", len(direct))
                processed = _run_browser_pipeline(
                    direct,
                    leads,
                    leads_path,
                    min_score=min_score,
                    headless=headless,
                    submitting=submitting,
                )
                all_processed.extend(processed)
                leads = load_leads(leads_path)
                hit_cap = False
                for item in processed:
                    target_keys.add(_url_key(item.get("url") or ""))
                    status = str(item.get("status") or "")
                    if status in DONE_STATUSES or status in domain_store.TERMINAL:
                        domain_store.mark(str(item.get("url") or ""), status)
                    if status in {"queued_hourly_cap", "queued_daily_cap"}:
                        hit_cap = True
                if hit_cap:
                    break
                _, hour_n = knowledge.submit_counts(leads)
                if hour_n >= cap_hourly:
                    break
                continue

        http_left = domain_store.http_budget_remaining()
        _, hour_n = knowledge.submit_counts(leads)
        if submitting and hour_n < cap_hourly:
            extra = _queue_direct_jobs(
                leads,
                seen=seen,
                min_score=min_score,
                limit=_visit_budget(cap_hourly - hour_n),
            )
            if extra:
                for job in extra:
                    seen.add(_url_key(str(job.get("url") or "")))
                logger.info(
                    "Chromium-direct extra %s (HTTP %s) hour %s/%s",
                    len(extra),
                    domain_store.http_budget_label(),
                    hour_n,
                    cap_hourly,
                )
                processed = _run_browser_pipeline(
                    extra,
                    leads,
                    leads_path,
                    min_score=min_score,
                    headless=headless,
                    submitting=submitting,
                )
                all_processed.extend(processed)
                leads = load_leads(leads_path)
                hit_cap = False
                for item in processed:
                    target_keys.add(_url_key(item.get("url") or ""))
                    status = str(item.get("status") or "")
                    if status in DONE_STATUSES or status in domain_store.TERMINAL:
                        domain_store.mark(str(item.get("url") or ""), status)
                    if status in {"queued_hourly_cap", "queued_daily_cap"}:
                        hit_cap = True
                if hit_cap:
                    break
                _, hour_n = knowledge.submit_counts(leads)
                if hour_n >= cap_hourly:
                    break
                continue
        if probes_used >= max_probes or http_left < 1:
            logger.info(
                "Stop new probes — used %s/%s http_left=%s (%s) hour %s/%s",
                probes_used,
                max_probes,
                http_left,
                domain_store.http_budget_label(),
                hour_n,
                cap_hourly,
            )
            break
        take = min(probe_n, max_probes - probes_used, http_left)
        pending = _pending_slice(take)
        if not pending:
            logger.info("Queue has no more unprocessed candidates that need HTTP")
            break
        for url in pending:
            seen.add(_url_key(url))
        probes_used += len(pending)
        processed = _run_slice(pending)
        all_processed.extend(processed)
        leads = load_leads(leads_path)
        hit_cap = False
        for item in processed:
            target_keys.add(_url_key(item.get("url") or ""))
            status = str(item.get("status") or "")
            if status in DONE_STATUSES or status in domain_store.TERMINAL:
                domain_store.mark(str(item.get("url") or ""), status)
            if status in {"queued_hourly_cap", "queued_daily_cap"}:
                hit_cap = True
        if not submitting:
            break
        if hit_cap:
            break
        _, hour_n = knowledge.submit_counts(leads)
        if hour_n >= cap_hourly:
            break

    if not all_processed and not target_keys:
        logger.info("Nothing new to process")
        _summarize(leads, set(), min_score)
        return 0
    _summarize(leads, target_keys, min_score)
    return 0


def _visit_budget(remain: int) -> int:
    """Visit extra hosts so CAPTCHA/skip does not drop the hour below the floor.

    Roughly a third of contact pages are CAPTCHA or have no open form, so the
    hour needs about three visits per post it wants to land.
    """
    if remain <= 0:
        return 0
    return min(96, max(remain * 3, 24 if remain >= 8 else remain * 4))


def _run_browser_pipeline(
    jobs: list[dict[str, Any]],
    leads: list[dict[str, Any]],
    leads_path: Path,
    *,
    min_score: int,
    headless: bool,
    submitting: bool,
) -> list[dict[str, Any]]:
    min_easy = int(getattr(config, "EASY_SCORE_MIN", 55) or 55)
    jobs.sort(
        key=lambda row: (
            not bool(row.get("authorized_contact")),
            -int(row.get("easy_score") or 0),
        )
    )
    jobs = [job for job in jobs if int(job.get("easy_score") or 0) >= min_easy]
    if submitting:
        _today_n, hour_n = knowledge.submit_counts(leads)
        remain = max(0, int(knowledge.hourly_cap()) - hour_n)
        jobs = jobs[: _visit_budget(remain)]
    else:
        jobs = jobs[: int(getattr(config, "CHROMIUM_BATCH", 32) or 32)]
    if not jobs:
        logger.info("No easy-score>=%s jobs this slice — Chromium skipped", min_easy)
        return []
    urls = [str(job["url"]) for job in jobs]
    meta = {str(job["url"]): job for job in jobs}
    idx = [0]
    prefetch: dict[str, Any] = {"lead": None}
    processed: list[dict[str, Any]] = []
    daily_stop = False
    cap_daily = knowledge.daily_cap()
    cap_hourly = knowledge.hourly_cap()

    with sync_playwright() as playwright:
        chromium = browser.launch_browser(playwright, headless=headless)
        collect_ctx = browser.collect_context(chromium)
        collect_page = browser.new_page(collect_ctx)
        submit_ctx = browser.submit_context(chromium) if submitting else None
        submit_page = browser.new_page(submit_ctx) if submit_ctx else None
        try:
            while (idx[0] < len(urls) or prefetch["lead"] is not None) and not daily_stop:
                if prefetch["lead"] is not None:
                    item = prefetch["lead"]
                    prefetch["lead"] = None
                else:
                    url = urls[idx[0]]
                    idx[0] += 1
                    reused = _reusable_lead(leads, url, min_score)
                    if reused:
                        logger.info("Reuse qualified %s (no extra Chromium)", url)
                        item = reused
                    else:
                        logger.info("Collecting %s", url)
                        item = _collect_one(collect_page, url, meta.get(url) or {})

                leads = upsert(leads, item)
                save_leads(leads_path, leads)

                if item.get("status") in domain_store.DEAD_QUEUE or item.get("status") == "failed":
                    if item.get("status") == "failed":
                        item["status"] = collect_error_status(str(item.get("error") or "collect"))
                        leads = upsert(leads, item)
                        save_leads(leads_path, leads)
                    processed.append(item)
                    logger.info("Skip %s status=%s", item.get("url"), item.get("status"))
                    continue
                form_ok = bool((item.get("contact_form") or {}).get("found")) and not item.get(
                    "captcha_detected"
                )
                if item.get("status") == "skipped_captcha" or (
                    item.get("status") not in {"failed"} and not form_ok
                ):
                    if item.get("status") != "skipped_captcha":
                        item = _skip_no_form(item)
                        leads = upsert(leads, item)
                        save_leads(leads_path, leads)
                    processed.append(item)
                    logger.info("Skip %s status=%s", item.get("url"), item.get("status"))
                    continue

                qualified = qualify_lead(item)
                if submitting and qualified.get("authorized_contact") is not True:
                    qualified["status"] = "skipped_unauthorized"
                    qualified["error"] = "Explicit authorization required"
                leads = upsert(leads, qualified)
                save_leads(leads_path, leads)
                processed.append(qualified)

                if not submitting:
                    continue
                if qualified.get("status") == "skipped_unauthorized":
                    continue
                if not eligible_for_submit(qualified, min_score):
                    continue
                today_n, hour_n = knowledge.submit_counts(leads)
                if today_n >= cap_daily:
                    logger.info("Daily submit cap %s reached — rest waits until tomorrow", cap_daily)
                    qualified["status"] = "queued_daily_cap"
                    leads = upsert(leads, qualified)
                    save_leads(leads_path, leads)
                    daily_stop = True
                    continue
                if hour_n >= cap_hourly:
                    logger.info("Hourly submit cap %s reached — rest waits (~1h), not a dump", cap_hourly)
                    qualified["status"] = "queued_hourly_cap"
                    leads = upsert(leads, qualified)
                    save_leads(leads_path, leads)
                    daily_stop = True
                    continue
                allowed, pace_reason = pacing.can_submit(qualified)
                if not allowed:
                    logger.info(
                        "Pace skip %s (%s) — defer this host, pull others this hour",
                        qualified.get("url"),
                        pace_reason,
                    )
                    domain_store.defer(
                        str(qualified.get("url") or ""),
                        hours=50 / 60,
                        reason=str(pace_reason),
                        count_fail=False,
                    )
                    continue

                waf = bool(qualified.get("waf_strict"))

                def during_delay() -> None:
                    if idx[0] >= len(urls):
                        return
                    nurl = urls[idx[0]]
                    idx[0] += 1
                    logger.info("Prefetch collect during WAF jitter: %s", nurl)
                    prefetch["lead"] = _collect_one(collect_page, nurl, meta.get(nurl) or {})

                # Persist a short lease before entering Playwright. If the
                # outer runner kills a hung site, the next cycle must not pick
                # the same host again immediately and starve the whole batch.
                domain_store.defer(
                    str(qualified.get("url") or ""),
                    reason="submit_in_progress_guard",
                    count_fail=False,
                    easy_score=easy_score.from_lead(qualified),
                )
                logger.info("Submitting %s", qualified.get("url"))
                submitted = submit_lead(
                    qualified,
                    page=submit_page,
                    during_delay=during_delay if waf else None,
                )
                if str(submitted.get("status") or "") in {"failed", "skipped_submit_failed"}:
                    attempts = int(qualified.get("submit_attempts") or submitted.get("submit_attempts") or 0) + 1
                    submitted["submit_attempts"] = attempts
                    submitted["status"] = "skipped_submit_failed"
                    logger.info("Drop submit %s after %s try (%s)", submitted.get("url"), attempts, str(submitted.get("error") or "")[:80])
                pacing.record_submit(submitted, status=str(submitted.get("status") or ""))
                leads = upsert(leads, submitted)
                save_leads(leads_path, leads)
                processed.append(submitted)
                gc.collect()
        finally:
            collect_ctx.close()
            if submit_ctx is not None:
                submit_ctx.close()
            chromium.close()

    return processed


def _url_key(url: str) -> str:
    url = (url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/").lower()


def _retry_map_fails(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One-shot: give earlier 'could not map fields' hosts another try with the new filler."""
    flag = config.ROOT / ".reclaim_submit_map_v1"
    if flag.exists():
        return leads
    n = 0
    for lead in leads:
        if str(lead.get("status") or "") != "skipped_submit_failed":
            continue
        err = str(lead.get("error") or "")
        if "map any visible" not in err and "No visible submit" not in err:
            continue
        lead["status"] = "qualified"
        lead["submit_attempts"] = 0
        url = str(lead.get("url") or "")
        domain_store.unmark(url)
        domain_store.enqueue(url, source="submit-retry")
        n += 1
    try:
        flag.write_text(str(n), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if n:
        logger.info("Retrying %s submit-map failures with deeper DOM fill", n)
    return leads


def _summarize(leads: list[dict[str, Any]], target_keys: set[str], min_score: int) -> None:
    scoped = [lead for lead in leads if _url_key(lead.get("url") or "") in target_keys]
    counts: dict[str, int] = {}
    for lead in scoped:
        status = str(lead.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    logger.info("Run summary for %s lead(s): %s", len(scoped), counts)
    eligible = sum(1 for lead in scoped if eligible_for_submit(lead, min_score))
    today_n, hour_n = knowledge.submit_counts(leads)
    logger.info(
        "Eligible now (score>=%s, form, no captcha): %s | today %s/%s | hour %s/%s",
        min_score,
        eligible,
        today_n,
        knowledge.daily_cap(),
        hour_n,
        knowledge.hourly_cap(),
    )
    for lead in scoped:
        logger.info(
            "  %s  score=%s  status=%s  form=%s",
            lead.get("url"),
            lead.get("fit_score"),
            lead.get("status"),
            bool((lead.get("contact_form") or {}).get("found")),
        )
    submitted = sum(
        1
        for lead in scoped
        if str(lead.get("status") or "") in knowledge.CONFIRMED_SUBMIT_STATUSES
    )
    skip = sum(
        1
        for lead in scoped
        if str(lead.get("status") or "")
        in domain_store.DEAD_QUEUE | {"skipped_submit_failed", "skipped_unsubscribed"}
    )
    owner_notify.notify_pipeline(counts, submitted=submitted, scoped=len(scoped), skipped=skip)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan, qualify, and optionally contact authorized B2B targets."
    )
    parser.add_argument(
        "--targets",
        default=str(config.TARGETS_PATH),
        help="Text file with one URL per line (default: targets.txt)",
    )
    parser.add_argument(
        "--leads",
        default=str(config.LEADS_PATH),
        help="JSON state file (default: leads.json)",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help=f"Minimum fit_score to submit (default: {config.MIN_FIT_SCORE})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N targets",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Also submit qualified pitches to discovered contact forms",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Show the browser window (useful when debugging form fills)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    args = parse_args(argv)
    try:
        import os

        os.chdir(config.ROOT)
        return run_pipeline(args)
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
