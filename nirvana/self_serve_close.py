"""Self-Serve Close (SSC) — insan müdahalesi olmadan profesyonel kapanış.

Kurallar (hepsi sağlanmazsa link GÖNDERİLMEZ, owner-gated yola düşer):
  1) Açık satın alma niyeti (bot tarafında _wants_to_buy zaten süzülür)
  2) Sözleşme şartlarının onayı (bu mesajda veya oturumda kayıtlı)
  3) Kanıt artefaktı: hedefe ait denetim raporu/rapor no mevcut
  4) Opt-out temiz, ödeme akışı daha önce başlamamış
Tahsilat doğrulaması YİNE insanda kalır — SSC yalnızca linki profesyonelce iletir.
"""
from __future__ import annotations

import json
import re
from typing import Any

import config
from nirvana.payment import payment_link, retainer_label

TERMS_RE = re.compile(
    r"(şartlar[ıi]\s*kabul|koşullar[ıi]\s*kabul|sözleşmey[ei]\s*kabul|kabul\s+ediyorum|"
    r"terms\s+(are\s+)?accepted|accept\s+the\s+terms|i\s+accept)",
    re.I,
)

REPORTS_NAME = "retainer_reports.json"


def terms_acknowledged(text: str) -> bool:
    return bool(TERMS_RE.search(text or ""))


def find_report_url(domain: str) -> str | None:
    """Oracle'a deploy edilen retainer raporlarından domain eşleşmesi."""
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain:
        return None
    try:
        from nirvana.registry import state_path
        data = json.loads(state_path(REPORTS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for row in (data.get("reports") or []):
        if str(row.get("domain") or "").strip().lower().removeprefix("www.") == domain:
            return str(row.get("report_url") or "") or None
    return None


def _artifact(brief: dict[str, Any] | None, row: dict[str, Any]) -> tuple[bool, str | None]:
    """Kanıt artefaktı: form akışındaki rapor no ya da domaine ait denetim raporu.

    Anti-karışıklık: rapor no varsa brief'in domain'i session COMPANY ile aynı olmalı;
    aksi halde artefakt sayılmaz (geri döner: confusion).
    """
    brief = brief or {}
    session_company = str(row.get("company") or "").strip().lower()
    brief_company = str(brief.get("company") or brief.get("host") or "").strip().lower()
    if session_company and brief_company and session_company != brief_company:
        return False, None
    if brief.get("report_id"):
        form_host = str(brief.get("host") or brief.get("target_domain") or brief.get("url") or "")
        if form_host:
            from nirvana.forget_guard import domain_of, proof_matches_form
            if not proof_matches_form(domain_of(form_host)):
                return False, None
        return True, None
    url = find_report_url(str(brief.get("host") or brief.get("domain") or ""))
    if url:
        # Rapor gerçekten bu domain'e ait mi (+ kanıt PNG'si de var mı)?
        from nirvana.forget_guard import proof_matches_form
        if proof_matches_form(str(brief.get("host") or brief.get("domain") or "")):
            return True, url
        return True, None
    url = find_report_url(str(row.get("company") or row.get("host") or ""))
    if url:
        return True, url
    return False, None


def evaluate(chat_id: int, text: str, *, brief: dict[str, Any] | None = None,
             row: dict[str, Any] | None = None, allowed: bool = True,
             link: str | None = None) -> dict[str, Any]:
    """SSC kapı değerlendirmesi. ok=False ise reason şunlardan biri:
    'not_allowed' | 'already' | 'terms' | 'artifact' | 'confusion' | 'no_link'."""
    row = row or {}
    brief = brief or {}
    if not allowed:
        return {"ok": False, "reason": "not_allowed"}
    if row.get("payment_reported") or row.get("payment_sent") or row.get("self_serve_link_sent"):
        return {"ok": False, "reason": "already"}
    # Anti-karışıklık kapısı: oturum şirketi ≠ form şirketi → link YOK.
    session_company = str(row.get("company") or "").strip()
    brief_company = str(brief.get("company") or brief.get("host") or "").strip()
    if session_company and brief_company and session_company.lower() != brief_company.lower():
        return {"ok": False, "reason": "confusion"}
    try:
        link = link or payment_link()
    except Exception:
        return {"ok": False, "reason": "no_link"}
    terms_ok = bool(row.get("terms_acknowledged")) or terms_acknowledged(text)
    if not terms_ok:
        return {"ok": False, "reason": "terms"}
    artifact_ok, report_url = _artifact(brief, row)
    if not artifact_ok:
        return {"ok": False, "reason": "artifact"}
    turkish = bool(re.search(r"[çğıöşüÇĞİÖŞÜ]", text or "")) or bool(row.get("turkish"))
    return {"ok": True, "reason": "pass", "link": link, "report_url": report_url,
            "message": _message(link, retainer_label(), report_url, turkish)}


def terms_presentation(company: str = "", *, turkish: bool = True) -> str:
    """Şartlar sorulduğunda/eksik onayda gönderilen profesyonel sözleşme özeti."""
    try:
        from nirvana.contract_pack import build_pack
        pack = build_pack(company=company)
    except Exception:
        return "Sözleşme paketi geçici olarak hazır değil; birazdan tekrar deneyin."
    ident = str(getattr(config, "OWNER_LINKEDIN_URL", "") or "").strip()
    ident_line = f"\nİnsan karşınızda: {ident}" if ident else ""
    if turkish:
        return (f"{pack['sla']}\n\n{pack['nda'][:240]}\n\n{pack['disclaimer']}{ident_line}\n\n"
                "Onaylıyorsanız 'kabul ediyorum' yazın — doğrulanmış ödeme talebini hemen iletirim.")
    return (f"{pack['sla']}\n\n{pack['nda'][:240]}\n\n{pack['disclaimer']}{ident_line}\n\n"
            "Reply 'I accept the terms' and I will send the verified payment request right away.")


def _identity() -> str:
    url = str(getattr(config, "OWNER_LINKEDIN_URL", "") or "").strip()
    return f"\nİnsan karşınızda: {url}" if url else ""


def _message(link: str, retainer: str, report_url: str | None, turkish: bool) -> str:
    proof_line = f"\nSizin için hazırlanan denetim raporu: {report_url}" if report_url else ""
    if turkish:
        return (
            f"Anlaştık — aylık {retainer} retainer. Doğrulanmış ödeme talebi:\n{link}\n"
            "Ödeme öncesi alıcı adı ve tutarı Payoneer panelinde kontrol edin.\n"
            "Ödemeniz yerleşince insan doğrulaması yapılır; 24 saat içinde ilk tur başlar,"
            " raporlar bu sohbete düşer." + proof_line + _identity()
        )
    return (
        f"Agreed — {retainer} monthly retainer. Verified payment request:\n{link}\n"
        "Check the recipient and amount on Payoneer before paying.\n"
        "Once settled, a human verifies it; first sweep starts within 24h and reports land here." + proof_line + _identity()
    )
