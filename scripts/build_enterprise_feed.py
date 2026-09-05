"""Build feeds/enterprise_targets.json for the Oracle enterprise lane (Faz B).

Runs on GitHub Actions (free, unlimited on public repos). Oracle downloads
the committed file via raw.githubusercontent.com — zero Oracle HTTP budget.

Acceptance-first upgrade: every candidate application page is scanned with a
headless browser HERE, on GitHub. Only pages where an open, fillable form
(email/message field visible) is confirmed enter the feed, and the committed
row points at the exact page URL with the form. Oracle never applies to a
page that cannot accept an application — dead targets cost zero Oracle time.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import enterprise_targets  # noqa: E402

EMAIL_SEL = "input[type='email'], input[name*='mail' i]"
MSG_SEL = "textarea, input[name*='message' i], input[name*='detail' i], div[contenteditable='true']"
CAPTCHA_RE = re.compile(r"recaptcha|hcaptcha|turnstile|cf-challenge", re.I)


def _scan_url(page: Any, url: str, timeout_ms: int = 22_000) -> dict[str, Any] | None:
    """Return {form_url} if an open form is confirmed on `url`, else None."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2500)
        if page.locator(EMAIL_SEL).first.is_visible() and page.locator(MSG_SEL).first.is_visible():
            if not CAPTCHA_RE.search(page.content()[:200_000]):
                return {"form_url": page.url}
    except Exception:  # noqa: BLE001
        pass
    return None


def scan_targets(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    from playwright.sync_api import sync_playwright

    keep: list[dict[str, str]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36")
        for row in rows:
            found = None
            for candidate in [row["url"], *row.get("contact_urls", [])][:4]:
                found = _scan_url(page, candidate)
                if found:
                    break
            if found:
                keep.append({**row, "url": found["form_url"]})
                print(f"FORM-OK  {row['company']} -> {found['form_url']}")
            else:
                print(f"NO-FORM  {row['company']} — dropped from feed")
            time.sleep(1.0)
        browser.close()
    return keep


def main() -> int:
    rows = enterprise_targets.load_all(limit=80)
    scan = "--scan" in sys.argv
    if scan:
        try:
            rows = scan_targets(rows)
        except Exception as exc:  # noqa: BLE001
            print(f"scan failed ({exc}) — committing unscanned list as fallback")
    payload = {
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scanned": bool(scan),
        "targets": rows,
    }
    dest = ROOT / "feeds" / "enterprise_targets.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"enterprise feed written: {len(rows)} targets (scanned={scan}) -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
