"""Curated enterprise contractor channels (Faz A).

Every entry is a PUBLIC partner/contractor/expert application channel of a
large platform — the exact lanes a freelance contractor uses without going
through HR screening. The submitter treats each like any other form: DOM
fingerprint first, no CAPTCHA solving, single attempt per target per window.
"""

from __future__ import annotations

from typing import Any

# (company, application URL, platform hint, notes)
TARGETS: list[dict[str, str]] = [
    {
        "company": "Shopify Partners",
        "url": "https://www.shopify.com/partners",
        "platform": "Shopify",
        "lane": "partner-expert",
    },
    {
        "company": "Webflow Experts",
        "url": "https://webflow.com/experts",
        "platform": "Webflow",
        "lane": "experts-directory",
    },
    {
        "company": "Wix Marketplace",
        "url": "https://www.wix.com/marketplace",
        "platform": "Wix",
        "lane": "agency-listing",
    },
    {
        "company": "Cloudflare Partner Program",
        "url": "https://www.cloudflare.com/partners/",
        "platform": "Cloudflare",
        "lane": "consulting-partner",
    },
    {
        "company": "HubSpot Solutions Partner",
        "url": "https://www.hubspot.com/partners",
        "platform": "HubSpot",
        "lane": "solutions-partner",
    },
    {
        "company": "Zoho Partner Program",
        "url": "https://www.zoho.com/partners.html",
        "platform": "Zoho",
        "lane": "consulting-partner",
    },
    {
        "company": "Zendesk Partner",
        "url": "https://www.zendesk.com/partners/",
        "platform": "Zendesk",
        "lane": "consulting-partner",
    },
    {
        "company": "BigCommerce Partners",
        "url": "https://www.bigcommerce.com/partners/",
        "platform": "BigCommerce",
        "lane": "agency-partner",
    },
    {
        "company": "Twilio Consulting Partner",
        "url": "https://www.twilio.com/partners",
        "platform": "Twilio",
        "lane": "consulting-partner",
    },
    {
        "company": "Atlassian Solution Partner",
        "url": "https://www.atlassian.com/partners",
        "platform": "Atlassian",
        "lane": "solution-partner",
    },
    {
        "company": "Pipedrive Partners",
        "url": "https://www.pipedrive.com/en/partners",
        "platform": "Pipedrive",
        "lane": "consulting-partner",
    },
    {
        "company": "Freshworks Partner",
        "url": "https://www.freshworks.com/partners/",
        "platform": "Freshworks",
        "lane": "consulting-partner",
    },
]


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
