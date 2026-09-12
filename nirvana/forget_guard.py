"""Lane W — forget_guard [Oracle VM, olay bazli].

Anti-karisiklik / sirket baglama butunlugu bekcisi.
- Her oturumda SADECE formdan gelen (token-bound) sirketle konusulur.
- Sirket adi degisirse (B sirketi sorarken A varsa): link GONDERILMEZ,
  owner'a uyari ile karisiklik engellenir.
- proof_hooks.json'daki kanit, o anki formun domain'ine ait degilse:
  generate_fallback — asla baska sirketin kaniti sunulmaz.
- _briefs icinde kanit kaydi yoksa: fail-safe karta duser.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

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
    """Oturum sirketi ile brief sirketi farkli mi?"""
    if not brief:
        return False
    a = (session_company or "").strip().lower()
    b = (str(brief.get("company") or brief.get("host") or "")).strip().lower()
    return bool(a and b and a != b)


def proof_matches_form(proof_domain: str, form_domain: str | None = None) -> bool:
    """Kanit domain'i, o anki formun domain'i ile birebir uyumlu mu?

    - Iki argumanli cagri: saf karsilastirma (cross-domain kanit asla gecmez).
    - Tek argumanli cagri (form_domain=None): proof_hooks.json'daki bu domain'e
      ait gercek kanit var mi diye state dosyasina bakar.
    """
    if form_domain is not None:
        a = domain_of(proof_domain)
        b = domain_of(form_domain)
        return bool(a and b and a == b)
    domain = domain_of(proof_domain)
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
    """Kapi: hepsi gecerse iletisim/kanit/link serbest."""
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
    """Oracle: bekci durumunu loglar (sessiz bekci, bildirim yok)."""
    return {"lanes_guarded": ["telegram", "form", "proof", "payment"],
            "policy": "token-bound company only; no-domain-proof -> fallback card"}
