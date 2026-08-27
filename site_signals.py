"""
Cheap stack/language signals from public page text.

No Playwright, no Ollama — used so DeepSeek gets a short technical brief
instead of a noisy 6k excerpt (less CPU on Always Free Ampere).
"""

from __future__ import annotations

import re

# needle (lowercase substring) -> label shown to the model / subject line
_STACK = (
    ("ideasoft", "IdeaSoft"),
    ("t-soft", "T-Soft"),
    ("tsoft", "T-Soft"),
    ("ticimax", "Ticimax"),
    ("ikas", "ikas"),
    ("akinon", "Akinon"),
    ("iyzico", "iyzico"),
    ("paytr", "PayTR"),
    ("craftgate", "Craftgate"),
    ("shopier", "Shopier"),
    ("woocommerce", "WooCommerce"),
    ("shopify", "Shopify"),
    ("wordpress", "WordPress"),
    ("laravel", "Laravel"),
    ("magento", "Magento"),
    ("prestashop", "PrestaShop"),
    ("graphql", "GraphQL"),
    ("webhook", "webhooks"),
    ("restful", "REST API"),
    ("rest api", "REST API"),
    ("zapier", "Zapier"),
    ("n8n", "n8n"),
    ("odoo", "Odoo"),
    ("parasut", "Paraşüt"),
    ("paraşüt", "Paraşüt"),
    ("kolayik", "kolayIK"),
    ("logo yazılım", "Logo"),
    ("salesforce", "Salesforce"),
    ("hubspot", "HubSpot"),
    ("stripe", "Stripe"),
    ("paypal", "PayPal"),
    ("checkout", "checkout"),
    ("entegrasyon", "entegrasyon"),
    ("integration", "integrations"),
    ("e-ticaret", "e-ticaret"),
    ("ecommerce", "e-commerce"),
    ("e-commerce", "e-commerce"),
    ("crm", "CRM"),
    ("erp", "ERP"),
    ("custom api", "custom API"),
    ("api", "API"),
)

HIGH_VALUE = frozenset(
    {
        "IdeaSoft",
        "iyzico",
        "WordPress",
        "HubSpot",
        "WooCommerce",
        "Shopify",
        "T-Soft",
        "Ticimax",
        "ikas",
        "Akinon",
        "PayTR",
        "Craftgate",
        "Shopier",
        "ERP",
        "Odoo",
        "n8n",
        "custom API",
        "REST API",
    }
)

_TECH_SENTENCE = re.compile(
    r"api|webhook|integrat|entegrasyon|checkout|ödeme|odeme|"
    r"crm|erp|otomasyon|automation|rest|graphql|sipariş|siparis",
    re.I,
)


def extract_stack_hints(*texts: str) -> list[str]:
    blob = " ".join(part or "" for part in texts)
    low = blob.lower()
    found: list[str] = []
    seen: set[str] = set()
    for needle, label in _STACK:
        if needle not in low:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(label)
        if len(found) >= 6:
            break
    return found


def pick_stack_label(hints: list[str], *, turkish: bool) -> str:
    for hint in hints:
        if hint in HIGH_VALUE:
            return hint
    if hints:
        return hints[0]
    return "e-ticaret / API" if turkish else "API / automation"


def high_value_score(hints: list[str]) -> int:
    score = 0
    for hint in hints:
        if hint in HIGH_VALUE:
            score += 18
        else:
            score += 4
    return min(score, 54)


def looks_turkish(*texts: str) -> bool:
    blob = " ".join(part or "" for part in texts)
    if re.search(r"[çğıöşüÇĞİÖŞÜ]", blob):
        return True
    return bool(
        re.search(
            r"\b(iletişim|iletisim|entegrasyon|ödeme|odeme|e-ticaret|"
            r"hizmetler|sipariş|siparis|firması)\b",
            blob,
            re.I,
        )
    )


def compact_excerpt(description: str, page_excerpt: str, hints: list[str], *, limit: int = 900) -> str:
    """Keep description + sentences that mention stack/tech. Drops nav chrome."""
    parts: list[str] = []
    desc = (description or "").strip()
    if desc:
        parts.append(desc[:400])

    hint_low = [h.lower() for h in hints]
    sentences = re.split(r"(?<=[.!?])\s+", (page_excerpt or "").strip())
    ranked: list[tuple[int, str]] = []
    for sentence in sentences:
        text = " ".join(sentence.split())
        if len(text) < 40:
            continue
        score = 0
        low = text.lower()
        if _TECH_SENTENCE.search(low):
            score += 2
        score += sum(1 for hint in hint_low if hint and hint in low)
        if score:
            ranked.append((score, text))
    ranked.sort(key=lambda item: -item[0])
    for _, text in ranked:
        parts.append(text)
        if sum(len(p) for p in parts) >= limit:
            break

    joined = " ".join(parts).strip()
    if len(joined) < 80:
        joined = f"{joined} {(page_excerpt or '')[:400]}".strip()
    return joined[:limit]
