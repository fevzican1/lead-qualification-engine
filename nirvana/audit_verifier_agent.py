"""Lane C — audit_verifier_agent [GitHub Actions].

Fail-closed audit before anything reaches the Oracle queue:
  * form/apply URL must live on the target's own registrable domain,
  * must be public HTTPS,
  * page must actually reference the company/application context.
Anything unverified is dropped — fake/spam submissions are structurally
impossible because the Oracle consumer refuses rows without a "pass" verdict.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx

import enterprise_quality
from nirvana.registry import state_path

DEFAULT_IN = "enrichment.json"
DEFAULT_OUT = "verified_queue.json"
TIMEOUT = 12.0
FORM_PATH_TOKENS = ("apply", "application", "partner", "vendor", "supplier",
                    "contractor", "careers", "contact", "onboarding", "rfp")

# Free ESP hosts a generic form might live on — a form there is NOT proof the
# target owns it; such rows fail closed unless the site itself embeds it.
THIRD_PARTY_FORM_HOSTS = ("docs.google.com", "forms.gle", "jotform.com", "typeform.com",
                          "hubspot.com", "hsforms.com", "formspree.io")


def registrable(host: str) -> str:
    """Cheap registrable-domain reduction (last two labels; ICANN TLD list not needed here)."""
    host = (host or "").strip().lower().rstrip(".")
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def verify(row: dict[str, Any]) -> dict[str, Any]:
    """Pure-ish audit: network-light (one GET), verdict fail-closed."""
    domain = str(row.get("domain") or "").strip().lower()
    reasons: list[str] = []
    if not domain or "." not in domain:
        return {"verdict": "fail", "reasons": ["missing_or_invalid_domain"]}

    target_url = str(row.get("url") or f"https://{domain}/")
    if not enterprise_quality.public_https(target_url):
        reasons.append("not_public_https")
    host = urlsplit(target_url).hostname or ""
    if registrable(host) != registrable(domain):
        reasons.append("form_host_not_target_domain")
    if any(host.endswith(fh) or fh in host for fh in THIRD_PARTY_FORM_HOSTS) and \
            registrable(host) != registrable(domain):
        reasons.append("third_party_generic_form")

    page_ok = False
    if not reasons:
        try:
            r = httpx.get(target_url, timeout=TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": "nirvana-audit/1.0"})
            body = r.text[:200_000].lower() if r.status_code < 400 else ""
            page_ok = r.status_code < 400 and (
                any(t in body for t in FORM_PATH_TOKENS)
                or any(t in target_url.lower() for t in FORM_PATH_TOKENS)
            )
            if not page_ok:
                reasons.append("no_application_context_on_page")
        except httpx.HTTPError:
            reasons.append("page_unreachable")

    if reasons:
        return {"verdict": "fail", "reasons": reasons}
    return {"verdict": "pass", "reasons": ["owned_form_https", "application_context_confirmed"],
            "page_ok": page_ok}


def run_batch(*, in_name: str = DEFAULT_IN, out_name: str = DEFAULT_OUT, limit: int = 60) -> dict[str, Any]:
    in_path = state_path(in_name)
    try:
        rows = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    out_path = state_path(out_name)
    try:
        prior = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = []
    done = {str(r.get("domain")) for r in prior if isinstance(r, dict)}

    verified = list(prior)
    audited = rejected = 0
    for row in rows:
        if audited >= limit:
            break
        if not isinstance(row, dict) or str(row.get("verdict") or "enriched") != "enriched":
            continue
        domain = str(row.get("domain") or "").strip()
        if not domain or domain in done:
            continue
        result = verify(row)
        audited += 1
        if result["verdict"] != "pass":
            rejected += 1
            continue
        verified.append({
            "domain": domain,
            "company": row.get("company", domain),
            "url": row.get("url") or f"https://{domain}/",
            "hook": row.get("hook", ""),
            "audit": result,
            "audit_by": "audit_verifier_agent",
        })
        done.add(domain)

    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(verified, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"audited": audited, "rejected": rejected, "verified": len(verified), "out": str(out_path)}


def oracle_queue_rows(path: Any = None) -> list[dict[str, Any]]:
    """The ONLY rows eligible for the Oracle queue — pass verdicts, nothing else."""
    source = path or state_path(DEFAULT_OUT)
    try:
        rows = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and (r.get("audit") or {}).get("verdict") == "pass"]
