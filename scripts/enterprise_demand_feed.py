"""Bounded GitHub discovery using Remotive's attributed public jobs API.

At most four scheduled pulls/day. No submissions, models or paid APIs.
Only worldwide contract roles and usable application forms reach Oracle.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
import enterprise_quality as quality
import enterprise_forms

API = "https://remotive.com/api/remote-jobs?category=software-dev&limit=150"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def demand_candidates(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = str(job.get("title") or "")
        description = unescape(re.sub(r"<[^>]+>", " ", str(job.get("description") or "")))
        text = " ".join(f"{title} {description}".split())
        kind = str(job.get("job_type") or "").lower()
        location = str(job.get("candidate_required_location") or "").strip().lower()
        published = str(job.get("publication_date") or "")
        if (kind not in {"contract", "freelance"} or not quality.SKILL.search(text)
                or quality.CLOSED.search(text) or not quality.fresh(published, 30 * 24)
                or location not in {"worldwide", "anywhere", "worldwide remote"}
                or not quality.public_https(str(job.get("url") or ""))):
            continue
        skill = quality.SKILL.search(text)
        quote = f"{title[:160]} | job_type: {kind} | " + text[max(0, skill.start()-100):skill.end()+250]
        rows.append({
            "company": str(job.get("company_name") or "")[:100],
            "url": job["url"], "role_title": title[:160], "platform": "",
            "lane": "contractor-application", "location_eligible": True,
            "priority_score": 70 + (10 if quality.URGENT.search(text) else 0),
            "source": "Remotive", "contact_urls": [],
            "evidence": {"source_url": job["url"], "published_at": published,
                         "demand_quote": quote, "source": "Remotive"},
        })
    return sorted(rows, key=lambda r: -r["priority_score"])[:16]


def _scan_url(page: Any, url: str, timeout_ms: int = 18_000) -> dict[str, Any] | None:
    if not quality.public_https(url):
        return None
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1500)
        if not quality.public_https(page.url):
            return None
        selected = enterprise_forms.application_form(page)
        if selected:
            return {**selected[1], "scanned_at": now_iso()}
    except Exception:
        return None
    return None


def scan_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    keep = []
    deadline = time.monotonic() + 600
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=pw.chromium.executable_path)
        try:
            for row in rows[:16]:
                if time.monotonic() >= deadline:
                    break
                context = browser.new_context()
                context.route("**/*", lambda route: route.abort() if (
                    route.request.resource_type in {"image", "media", "font"}
                    or not quality.public_https(route.request.url)) else route.continue_())
                page = context.new_page()
                page.set_default_timeout(2500)
                try:
                    found = _scan_url(page, row["url"])
                    if not found:
                        links = page.locator("a").evaluate_all(
                            "els => els.filter(e => /apply/i.test(e.textContent)).map(e => e.href).slice(0, 3)"
                        )
                        for link in dict.fromkeys(links):
                            if time.monotonic() >= deadline:
                                break
                            link = urljoin(page.url, link)
                            if link.split('#')[0] == page.url.split('#')[0]:
                                continue
                            found = _scan_url(page, link)
                            if found:
                                break
                    if found:
                        candidate = {**row, "url": found["form_url"], "form_verified": True,
                                     "channel_purpose": "contractor_application",
                                     "evidence": {**row["evidence"], **found}}
                        if quality.eligible(candidate):
                            keep.append(candidate)
                    print(f"{row['company']}: {'form found' if found else 'review/no application form'}")
                except Exception:
                    print(f"{row['company']}: scan failed; excluded")
                finally:
                    context.close()
        finally:
            browser.close()
    return keep


def main() -> int:
    if "--scan" not in sys.argv:
        print("Refusing to publish without --scan")
        return 2
    try:
        response = httpx.get(API, timeout=20, follow_redirects=False)
        response.raise_for_status()
        rows = demand_candidates(response.json()["jobs"])
        scanned = scan_targets(rows)
    except Exception as exc:
        print(f"Discovery failed ({type(exc).__name__}); existing feed not replaced")
        return 1
    payload = {"schema_version": 2, "updated_at": now_iso(), "scanned": True,
               "source": "Remotive", "source_url": "https://remotive.com",
               "candidates_considered": len(rows), "targets": scanned}
    if not quality.valid_payload(payload):
        return 1
    dest = ROOT / "feeds" / "enterprise_targets.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest)
    print(f"Published {len(scanned)}/{len(rows)} eligible targets. Priority is not an acceptance probability.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())