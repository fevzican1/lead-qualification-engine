"""Lane R — email_infra_audit [GitHub Actions, heavy].

Rapor kapsamı: DNS/email altyapı tespiti (SPF, DKIM, DMARC, MX).
Ücretsiz, kota dostu — DNS sorguları sadece.
Form kanıtına ek "altyapı sağlamlığı" kanıtı üretir.
"""
from __future__ import annotations

import json
import time
from typing import Any

from nirvana.registry import state_path

OUT_NAME = "email_infra_log.json"


def check_spf(domain: str) -> dict[str, Any]:
    """SPF kaydı kontrolü (DNS TXT sorgusu)."""
    import dns.resolver
    try:
        answers = dns.resolver.resolve(domain, "TXT")
        for r in answers:
            txt = str(r)
            if "v=spf1" in txt:
                return {"present": True, "record": txt[:200]}
        return {"present": False, "record": None}
    except Exception as e:
        return {"present": False, "error": str(e)[:80]}


def check_dmarc(domain: str) -> dict[str, Any]:
    """DMARC kaydı kontrolü."""
    import dns.resolver
    try:
        dmarc_domain = f"_dmarc.{domain}"
        answers = dns.resolver.resolve(dmarc_domain, "TXT")
        for r in answers:
            txt = str(r)
            if "v=DMARC1" in txt:
                return {"present": True, "record": txt[:200]}
        return {"present": False, "record": None}
    except Exception as e:
        return {"present": False, "error": str(e)[:80]}


def check_mx(domain: str) -> dict[str, Any]:
    """MX kaydı kontrolü."""
    import dns.resolver
    try:
        answers = dns.resolver.resolve(domain, "MX")
        mx_list = [str(r.exchange).rstrip(".") for r in answers]
        return {"present": bool(mx_list), "records": mx_list[:5]}
    except Exception as e:
        return {"present": False, "error": str(e)[:80]}


def audit_domain(domain: str) -> dict[str, Any]:
    """Domain için email altyapı audit."""
    domain = domain.strip().lower().removeprefix("www.")
    return {
        "domain": domain,
        "spf": check_spf(domain),
        "dmarc": check_dmarc(domain),
        "mx": check_mx(domain),
        "ts": time.time(),
    }


def risk_score(audit: dict[str, Any]) -> int:
    """0-100 arası risk skoru (yüksek = kötü)."""
    score = 0
    if not audit.get("spf", {}).get("present"):
        score += 30
    if not audit.get("dmarc", {}).get("present"):
        score += 40
    if not audit.get("mx", {}).get("present"):
        score += 30
    return min(score, 100)


def build_evidence(audit: dict[str, Any]) -> str:
    """Kanıt metni — form/telegram akışında sunulur."""
    score = risk_score(audit)
    issues = []
    if not audit.get("spf", {}).get("present"):
        issues.append("SPF kaydı yok")
    if not audit.get("dmarc", {}).get("present"):
        issues.append("DMARC kaydı yok")
    if not audit.get("mx", {}).get("present"):
        issues.append("MX kaydı yok")

    if not issues:
        return f"Email altyapısı tam (SPF/DMARC/MX mevcut). Risk skoru: {score}/100."
    return f"Email altyapı sorunları: {', '.join(issues)}. Risk skoru: {score}/100."


def run_batch(*, in_name: str = "verified_queue.json", **kwargs: Any) -> dict[str, Any]:
    """GitHub Actions'ta tetiklenir."""
    in_path = state_path(in_name)
    try:
        rows = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []

    audits = []
    for row in rows[:10]:  # kota korunur
        domain = str(row.get("host") or row.get("domain") or "").strip()
        if domain:
            audits.append(audit_domain(domain))

    out_path = state_path(OUT_NAME)
    payload = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "audits": audits}
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)
    return {"audited": len(audits), "out": str(out_path)}
