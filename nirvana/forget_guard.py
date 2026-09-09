"""Lane W — forget_guard [Oracle VM, olay bazlı].

Anti-karışıklık / şirket bağlama bütünlüğü bekçisi.
- Her oturumda SADECE formdan gelen (token-bound) şirketle konuşulur.
- Şirket adı değişirse (B şirketi sorarken A varsa): link GÖNDERİLMEZ,
  owner'a uyarı → sahibi karışıklığı engeller.
- proof_hooks.json'daki kanıt, o anki formun domain'ine ait değilse:
  generate_fallback — asla başka şirketin kanıtı sunulmaz.
- _briefs içinde kanıt kaydı yoksa: model/LLM'e DEĞİL fail-safe karta düşer.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import config
from nirvana.registry import state_path

FORGET_LOG = "forget_guard.json"


def domain_of(url_or_host: str) -> str:
    raw = (url_or_host or "").strip().lower()
    if not raw:
        return ""
    try:
        host = urlsplit(raw).hostname if "://" in raw else raw.split("/")[0]
    except ValueError:
        return ""
    return (host or "").removeprefix("www.").strip()


def company_conflict(session_company: str, brief: dict[str, Any] | None) -> bool:
    """Oturumun şirketi ile brief'in şirketi farklı mı?"""
    if not brief:
        return False
    a = (session_company or "").strip().lower()
    b = (str(brief.get("company") or brief.get("host") or "")).strip().lower()
    return bool(a and b and a != b)


def proof_matches_form(domain: str) -> bool:
    """proof_hooks.json'daki bu domain'e ait GERÇEK kanıt var mı?"""
    domain = domain_of(domain)
    if not domain:
        return False
    try:
        hooks = json.loads(state_path("proof_hooks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for p in (hooks.get("proofs") or []):
        if not isinstance(p, dict) or p.get("mode") != "proof":
            continue
        if domain_of(p.get("domain") or "") == domain:
            if (p.get("hook") or "").strip() and (p.get("image_url") or "").strip():
                return True
    return False


def check(session_company: str, form_url: str, *, brief: dict[str, Any] | None = None) -> dict[str, Any]:
    """Kapı: hepsi geçerse iletişim/kanıt/link serbest."""
    domain = domain_of(form_url)
    conflict = company_conflict(session_company, brief)
    proof_ok = proof_matches_form(domain) if domain else False
    report_ok = False
    if domain:
        try:
            data = json.loads(state_path("retainer_reports.json").read_text(encoding="utf-8"))
            report_ok = any(
                domain_of(str(r.get("domain") or "")) == domain and (r.get("report_url") or "")
                for r in (data.get("reports") or []))
        except (OSError, ValueError):
            report_ok = False
    ok = bool(domain) and not conflict
    reasons: list[str] = []
    if not domain:
        reasons.append("no_form_domain")
    if conflict:
        reasons.append("company_conflict")
    if not proof_ok:
        reasons.append("no_domain_proof")
    return {"ok": ok, "domain": domain, "company_conflict": conflict,
            "proof_ok": proof_ok, "report_ok": report_ok, "reasons": reasons}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Oracle: bekçi durumunu loglar (bildirim ATMaz — sessiz bekçi)."""
    return {"lanes_guarded": ["telegram", "form", "proof", "payment"],
            "policy": "token-bound company only; no-domain-proof -> fallback card"}
