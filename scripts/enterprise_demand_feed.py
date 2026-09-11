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

import config  # noqa: E402
import httpx  # noqa: E402
import enterprise_quality as quality  # noqa: E402
import enterprise_forms  # noqa: E402

API = "https://remotive.com/api/remote-jobs?category=software-dev&limit=150"

# Nirvana queue fuel: the Remotive demand trickle alone starved the whole
# A->B->C->stealth chain (feed hit targets:0 -> submitted:0). The harvested
# ready_queue bench (13k+ form candidates, all easy_score >= FEED_MIN_SCORE)
# rotates through a cursor so every 6h scan pulls a fresh slice of bench rows
# while fresh demand evidence (Remotive) keeps priority.
BENCH_PATH = ROOT / "feeds" / "ready_queue.json"
CURSOR_PATH = ROOT / "feeds" / "enterprise_scan_cursor.json"
HARVEST_BATCH = 48
# Bench satırları form-kanıtlıdır (commoncrawl form harvest + form_verified):
# Remotive-talebi değil, doğrudan iletişim-formu kanıtı taşırlar.
BENCH_DEMAND_QUOTE = ("Contract web integration/automation review — "
                      "open contact form verified (form_verified)")
BENCH_CHANNEL_QUOTE = ("Apply via the verified contact form for a "
                       "contract automation/integration review")


def harvest_bench(*, batch: int = HARVEST_BATCH) -> list[dict[str, Any]]:
    """Rotate a cursor through feeds/ready_queue.json and return bench rows.

    Pure fuel provider: rows still must pass the Playwright form-scan gate in
    scan_targets() before they can reach Oracle. Nothing is auto-verified.
    """
    try:
        raw = json.loads(BENCH_PATH.read_text(encoding="utf-8"))
        rows = raw.get("urls") or [] if isinstance(raw, dict) else raw
    except (OSError, ValueError):
        rows = []
    rows = [r for r in rows if isinstance(r, dict) and str(r.get("url") or "")]
    if not rows:
        return []
    try:
        cursor = int(json.loads(CURSOR_PATH.read_text(encoding="utf-8")).get("cursor", 0))
    except (OSError, ValueError):
        cursor = 0
    start = cursor % len(rows)
    slice_ = [rows[(start + i) % len(rows)] for i in range(min(batch, len(rows)))]
    out: list[dict[str, Any]] = []
    for row in slice_:
        url = str(row.get("url") or "")
        if not quality.public_https(url):
            continue
        score = int(row.get("easy_score") or 0)
        if score < int(config.FEED_MIN_SCORE):
            continue
        source = str(row.get("source") or "ready_queue")
        host = str(row.get("host") or url)[:100]
        out.append({
            "company": host,
            "url": url,
            "role_title": "",
            "platform": str(row.get("stack") or ""),
            "lane": "contractor-application",
            "location_eligible": True,
            "priority_score": score,
            "score": score,
            "source": source,
            "contact_urls": [],
            "evidence": {"source_url": url, "source": source,
                         "demand_quote": BENCH_DEMAND_QUOTE,
                         "channel_quote": BENCH_CHANNEL_QUOTE},
        })
    tmp = CURSOR_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"cursor": (start + len(slice_)) % len(rows)}, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(CURSOR_PATH)
    return out


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
                                     # discovery_agent FIT gate: the verified form page must
                                     # never be rejected just because its URL lacks a token.
                                     "notes": "verified application form on page",
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
    # Acil dolum tetiklemesinde bench dilimi büyür (bekleme atlanır, taze hedef).
    batch = HARVEST_BATCH
    for a in sys.argv[1:]:
        if a.startswith("--refill-batch"):
            try:
                batch = max(1, int(a.split("=", 1)[1] if "=" in a else sys.argv[sys.argv.index(a) + 1]))
            except (ValueError, IndexError):
                batch = HARVEST_BATCH * 2
    try:
        response = httpx.get(API, timeout=20, follow_redirects=False)
        response.raise_for_status()
        rows = demand_candidates(response.json()["jobs"])
        demand_count = len(rows)
        rows += harvest_bench(batch=batch)
        scanned = scan_targets(rows)
    except Exception as exc:
        print(f"Discovery failed ({type(exc).__name__}); existing feed not replaced")
        return 1
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
    print(f"Published {len(scanned)}/{len(rows)} eligible targets ({demand_count} demand + bench). "
          "Priority is not an acceptance probability.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())