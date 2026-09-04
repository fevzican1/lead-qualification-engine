"""Curated enterprise contractor channels (Faz A).

Every entry is a PUBLIC partner/contractor/expert application channel of a
large platform — the exact lanes a freelance contractor uses without going
through HR screening. The submitter treats each like any other form: DOM
fingerprint first, no CAPTCHA solving, single attempt per target per window.
"""

from __future__ import annotations

import json
from typing import Any

import config

# (company, application URL, platform hint, notes)
TARGETS: list[dict[str, str]] = [
    {
        "company": "Shopify Partners",
        "url": "https://www.shopify.com/partners",
        "contact_urls": [
            "https://www.shopify.com/partners/apply",
            "https://www.shopify.com/contact",
        ],
        "platform": "Shopify",
        "lane": "partner-expert",
    },
    {
        "company": "Webflow Experts",
        "url": "https://webflow.com/experts",
        "contact_urls": ["https://webflow.com/contact"],
        "platform": "Webflow",
        "lane": "experts-directory",
    },
    {
        "company": "Wix Marketplace",
        "url": "https://www.wix.com/marketplace",
        "contact_urls": [
            "https://www.wix.com/marketplace/agencies",
            "https://support.wix.com/en/article/contact-wix",
        ],
        "platform": "Wix",
        "lane": "agency-listing",
    },
    {
        "company": "Cloudflare Partner Program",
        "url": "https://www.cloudflare.com/partners/",
        "contact_urls": [
            "https://www.cloudflare.com/partners/become-a-partner/",
            "https://www.cloudflare.com/contact-sales/",
        ],
        "platform": "Cloudflare",
        "lane": "consulting-partner",
    },
    {
        "company": "HubSpot Solutions Partner",
        "url": "https://www.hubspot.com/partners",
        "contact_urls": [
            "https://www.hubspot.com/partners/apply",
            "https://www.hubspot.com/contact-sales",
        ],
        "platform": "HubSpot",
        "lane": "solutions-partner",
    },
    {
        "company": "Zoho Partner Program",
        "url": "https://www.zoho.com/partners.html",
        "contact_urls": [
            "https://www.zoho.com/partner/partner-application.html",
            "https://www.zoho.com/partner/",
        ],
        "platform": "Zoho",
        "lane": "consulting-partner",
    },
    {
        "company": "Zendesk Partner",
        "url": "https://www.zendesk.com/partners/",
        "contact_urls": [
            "https://www.zendesk.com/partners/join-a-program/",
            "https://www.zendesk.com/contact/",
        ],
        "platform": "Zendesk",
        "lane": "consulting-partner",
    },
    {
        "company": "BigCommerce Partners",
        "url": "https://www.bigcommerce.com/partners/",
        "contact_urls": [
            "https://www.bigcommerce.com/partners/register/",
            "https://www.bigcommerce.com/contact/",
        ],
        "platform": "BigCommerce",
        "lane": "agency-partner",
    },
    {
        "company": "Twilio Consulting Partner",
        "url": "https://www.twilio.com/partners",
        "contact_urls": [
            "https://www.twilio.com/partners/consulting-partners",
            "https://www.twilio.com/contact-sales",
        ],
        "platform": "Twilio",
        "lane": "consulting-partner",
    },
    {
        "company": "Atlassian Solution Partner",
        "url": "https://www.atlassian.com/partners",
        "contact_urls": [
            "https://www.atlassian.com/partners/apply",
            "https://www.atlassian.com/company/contact",
        ],
        "platform": "Atlassian",
        "lane": "solution-partner",
    },
    {
        "company": "Pipedrive Partners",
        "url": "https://www.pipedrive.com/en/partners",
        "contact_urls": [
            "https://www.pipedrive.com/en/partners/apply",
        ],
        "platform": "Pipedrive",
        "lane": "consulting-partner",
    },
    {
        "company": "Freshworks Partner",
        "url": "https://www.freshworks.com/partners/",
        "contact_urls": [
            "https://www.freshworks.com/partners/new/",
            "https://www.freshworks.com/contact/",
        ],
        "platform": "Freshworks",
        "lane": "consulting-partner",
    },
]


def load_all(limit: int = 60) -> list[dict[str, str]]:
    """Curated targets + GitHub-harvested feed targets, deduped by URL.

    The feed (feeds/enterprise_targets.json) is produced by the
    enterprise-discovery GitHub Actions workflow and only contains pages that
    responded with a form-bearing HTML body during the Actions run — so Oracle
    spends zero discovery HTTP on targets that cannot accept an application.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _push(row: Any) -> None:
        if not isinstance(row, dict):
            return
        url = str(row.get("url") or "").strip()
        company = str(row.get("company") or "").strip()
        if not url or not company or url in seen:
            return
        if not url.lower().startswith("https://"):
            return
        seen.add(url)
        out.append(
            {
                "company": company[:64],
                "url": url,
                "platform": str(row.get("platform") or "")[:40],
                "lane": str(row.get("lane") or "partner-expert")[:40],
                "contact_urls": [c for c in (row.get("contact_urls") or []) if str(c).startswith("https://")][:4],
            }
        )

    for row in TARGETS:
        _push(row)
        if len(out) >= limit:
            return out

    feed_path = config.ROOT / "feeds" / "enterprise_targets.json"
    if feed_path.exists():
        try:
            payload = json.loads(feed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        for row in (payload.get("targets") or []) if isinstance(payload, dict) else []:
            _push(row)
            if len(out) >= limit:
                break
    return out


def target_lead(row: dict[str, str]) -> dict[str, Any]:
    """Lead dict shaped for form_submitter + telegram_handoff (enterprise lane)."""
    return {
        "url": row["url"],
        "final_url": row["url"],
        "company_name": row["company"],
        "platform": row.get("platform", ""),
        "platform_confidence": 95,
        "stack_hints": [row.get("platform", "").lower()] if row.get("platform") else [],
        "contact_form": {"found": True, "page_url": row["url"]},
        "audience": "enterprise",
        "enterprise": True,
        "description": f"Public {row.get('lane', 'partner')} application channel",
    }
