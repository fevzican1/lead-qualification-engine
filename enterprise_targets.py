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
    {
        "company": "Mailchimp Experts",
        "url": "https://mailchimp.com/partners/",
        "contact_urls": ["https://mailchimp.com/contact/"],
        "platform": "Mailchimp",
        "lane": "experts-directory",
    },
    {
        "company": "Zapier Experts",
        "url": "https://zapier.com/experts",
        "contact_urls": ["https://zapier.com/contact"],
        "platform": "Zapier",
        "lane": "experts-directory",
    },
    {
        "company": "Monday.com Partners",
        "url": "https://monday.com/partners",
        "contact_urls": ["https://monday.com/contact"],
        "platform": "monday.com",
        "lane": "consulting-partner",
    },
    {
        "company": "Airtable Consultants",
        "url": "https://airtable.com/partners",
        "contact_urls": ["https://airtable.com/contact"],
        "platform": "Airtable",
        "lane": "consulting-partner",
    },
    {
        "company": "Intercom Partner",
        "url": "https://www.intercom.com/partners",
        "contact_urls": ["https://www.intercom.com/contact"],
        "platform": "Intercom",
        "lane": "consulting-partner",
    },
    {
        "company": "GitLab Partners",
        "url": "https://about.gitlab.com/partners/",
        "contact_urls": ["https://about.gitlab.com/contact/"],
        "platform": "GitLab",
        "lane": "solution-partner",
    },
    {
        "company": "Datadog Partner Network",
        "url": "https://www.datadoghq.com/partners/",
        "contact_urls": ["https://www.datadoghq.com/about/contact/"],
        "platform": "Datadog",
        "lane": "consulting-partner",
    },
    {
        "company": "Elastic Partner",
        "url": "https://www.elastic.co/partners",
        "contact_urls": ["https://www.elastic.co/contact"],
        "platform": "Elastic",
        "lane": "consulting-partner",
    },
    {
        "company": "MongoDB Partner",
        "url": "https://www.mongodb.com/partners",
        "contact_urls": ["https://www.mongodb.com/contact"],
        "platform": "MongoDB",
        "lane": "consulting-partner",
    },
    {
        "company": "Klaviyo Partners",
        "url": "https://www.klaviyo.com/partners",
        "contact_urls": ["https://www.klaviyo.com/contact-us"],
        "platform": "Klaviyo",
        "lane": "agency-partner",
    },
    {
        "company": "Segment Partners",
        "url": "https://segment.com/partners/",
        "contact_urls": ["https://segment.com/contact/"],
        "platform": "Segment",
        "lane": "consulting-partner",
    },
    {
        "company": "Squarespace Circle",
        "url": "https://www.squarespace.com/circle",
        "contact_urls": ["https://www.squarespace.com/contact"],
        "platform": "Squarespace",
        "lane": "experts-directory",
    },
    {
        "company": "Notion Consultants",
        "url": "https://www.notion.so/consultants",
        "contact_urls": ["https://www.notion.so/contact-sales"],
        "platform": "Notion",
        "lane": "experts-directory",
    },
    {
        "company": "Miro Partner",
        "url": "https://miro.com/partners/",
        "contact_urls": ["https://miro.com/contact/"],
        "platform": "Miro",
        "lane": "consulting-partner",
    },
    {
        "company": "Asana Partners",
        "url": "https://asana.com/partners",
        "contact_urls": ["https://asana.com/contact"],
        "platform": "Asana",
        "lane": "consulting-partner",
    },
    {
        "company": "Xero Partner",
        "url": "https://www.xero.com/partners/",
        "contact_urls": ["https://www.xero.com/contact/"],
        "platform": "Xero",
        "lane": "consulting-partner",
    },
    {
        "company": "QuickBooks ProAdvisor",
        "url": "https://quickbooks.intuit.com/accountants/",
        "contact_urls": ["https://quickbooks.intuit.com/contact/"],
        "platform": "QuickBooks",
        "lane": "proadvisor",
    },
    {
        "company": "NetSuite Alliance Partner",
        "url": "https://www.netsuite.com/portal/partners/main.shtml",
        "contact_urls": ["https://www.netsuite.com/portal/contact-us.shtml"],
        "platform": "NetSuite",
        "lane": "alliance-partner",
    },
    {
        "company": "Sentry Partner",
        "url": "https://sentry.io/partners/",
        "contact_urls": ["https://sentry.io/contact/"],
        "platform": "Sentry",
        "lane": "consulting-partner",
    },
    {
        "company": "Braintree Partner",
        "url": "https://www.braintreepayments.com/partners",
        "contact_urls": ["https://www.braintreepayments.com/contact"],
        "platform": "Braintree",
        "lane": "integration-partner",
    },
    {
        "company": "Odoo Partner",
        "url": "https://www.odoo.com/become-a-partner",
        "contact_urls": ["https://www.odoo.com/contactus"],
        "platform": "Odoo",
        "lane": "consulting-partner",
    },
    {
        "company": "AWS Partner Network",
        "url": "https://aws.amazon.com/partners/",
        "contact_urls": ["https://aws.amazon.com/contact-us/"],
        "platform": "AWS",
        "lane": "consulting-partner",
    },
    {
        "company": "Google Cloud Partner",
        "url": "https://cloud.google.com/partners",
        "contact_urls": ["https://cloud.google.com/contact"],
        "platform": "Google Cloud",
        "lane": "consulting-partner",
    },
    {
        "company": "Salesforce Consulting Partner",
        "url": "https://partners.salesforce.com/",
        "contact_urls": ["https://www.salesforce.com/contact/"],
        "platform": "Salesforce",
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
