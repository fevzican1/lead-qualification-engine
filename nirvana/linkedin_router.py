"""Lane J — linkedin_router [Oracle VM, günlük].

Captcha / açık formu olmayan hedefler için insan-onaylı LinkedIn akışı:
hedefi OTONOM tarar, LinkedIn şirket sayfası varlığını kontrol eder (1 hafif
istek, girişsiz, scrapesiz) ve TEK Telegram inceleme kartı üretir. LinkedIn
otomasyonu yapılmaz — karar ve iletişim her zaman insanda; bu modül sadece
adayı doğru linkle masaya getirir. Günlük cap: 10 hedef.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

import config
import owner_notify
from nirvana.registry import state_path

CANDIDATE_STATUSES = {"skipped_captcha", "skipped_no_open_form"}
ROUTED_NAME = "linkedin_routed.json"
REPORTS_NAME = "retainer_reports.json"
DAILY_LIMIT = 10
TIMEOUT = 10.0


def domain_token(domain: str) -> str:
    """acme.com -> acme, www.acme.co.uk -> acme (LinkedIn slug tahmini)."""
    host = (domain or "").strip().lower().removeprefix("www.")
    return host.split(".")[0] if host else ""


def linkedin_guess(domain: str) -> str:
    token = domain_token(domain)
    return f"https://www.linkedin.com/company/{token}/" if token else ""


def search_link(domain: str) -> str:
    import urllib.parse
    return "https://www.google.com/search?q=" + urllib.parse.quote(
        f"site:linkedin.com/company {domain}")


def check_linkedin(url: str) -> str:
    """'found' | 'unknown' — tek girişsiz istek; authwall/scrape denemesi yok."""
    if not url:
        return "unknown"
    try:
        r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; nirvana-router/1.0)"})
        body = r.text[:2000].lower()
        if r.status_code == 200 and "authwall" not in r.url.host and "page not found" not in body:
            return "found"
    except httpx.HTTPError:
        pass
    return "unknown"


def candidates(leads_path: Any = None, *, limit: int = DAILY_LIMIT) -> list[dict[str, Any]]:
    """Captcha/no-form kilitli, henüz yönlendirilmemiş hedefler."""
    source = leads_path or config.ROOT / "leads.json"
    try:
        rows = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    routed_path = state_path(ROUTED_NAME)
    try:
        routed = set(json.loads(routed_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        routed = set()

    out: list[dict[str, Any]] = []
    for row in reversed(rows):  # en yeni önce
        if len(out) >= limit:
            break
        if not isinstance(row, dict) or str(row.get("status")) not in CANDIDATE_STATUSES:
            continue
        domain = str(row.get("host") or row.get("domain") or "").strip().lower()
        if not domain or domain in routed:
            continue
        out.append({"domain": domain, "status": row.get("status"),
                    "company": row.get("company") or domain})
    return out


def find_report_url(domain: str) -> str | None:
    """Oracle'daki retainer raporlarından domain eşleşmesi (kullanıcıya gösterilecek kanıt)."""
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain:
        return None
    try:
        data = json.loads(state_path(REPORTS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for row in (data.get("reports") or []):
        if str(row.get("domain") or "").strip().lower().removeprefix("www.") == domain:
            return str(row.get("report_url") or "") or None
    return None


def build_card(items: list[dict[str, Any]]) -> str:
    lines = [f"LinkedIn inceleme kartı — {len(items)} captcha'lı hedef (manuel karar senin):"]
    for i, it in enumerate(items, 1):
        lines.append(
            f"{i}. {it['company']} ({it['domain']})\n"
            f"   LinkedIn: {it.get('linkedin_url') or '-'} [{it.get('linkedin_state', 'unknown')}]\n"
            f"   Arama: {search_link(it['domain'])}"
        )
    lines.append("Form kilitli; kanal: LinkedIn manuel incele → Telegram funnel ile devam.")
    return "\n".join(lines)


def build_outreach_draft(domain: str, company: str = "", *, turkish: bool = True) -> str:
    """Captcha'lı hedefe LinkedIn'ten gönderilecek hazır outreach mesajı.

    Otomasyon yok — bu metin insan tarafından kopyalanıp gönderilir. Audit raporu
    varsa kanıt linkini içerir; yoksa generic değer önerisi sunar.
    """
    company = company.strip() or domain.strip()
    report_url = find_report_url(domain)
    proof_line = f"\n\nSizin için hazırladığım teknik denetim raporu: {report_url}" if report_url else ""
    ident = str(getattr(config, "OWNER_LINKEDIN_URL", "") or "").strip()
    ident_line = f"\n— {ident}" if ident else ""
    retainer = "2.500 EUR/ay"
    if turkish:
        return (
            f"Merhaba {company} ekibi,\n\n"
            f"Sizinle kısa bir teknik bulgu paylaşmak istedim: dijital altyapınızda "
            f"müşteri deneyimini ve dönüşümünü doğrudan etkileyen bir darboğaz tespit ettim."
            f"{proof_line}\n\n"
            f"Bu darboğazı 7 gün içinde ücretsiz yamalayabiliriz — sonrasında aylık "
            f"{retainer} değerinde sürekli izleme ve optimizasyon retainer'ımızı "
            f"değerlendirebiliriz. İlk turu sizin için hazırladım; onayınızla başlarız.\n\n"
            f"İlgilenir misiniz?{ident_line}"
        )
    return (
        f"Hi {company} team,\n\n"
        f"I'd like to share a quick technical finding: I detected a bottleneck in your "
        f"digital infrastructure that directly impacts user experience and conversions."
        f"{proof_line}\n\n"
        f"We can patch it within 7 days at no cost — then evaluate a monthly "
        f"{retainer} retainer for continuous monitoring and optimization. I've "
        f"prepared the first sweep for you; it starts on your approval.\n\n"
        f"Interested?{ident_line}"
    )


def run_batch(*, leads_path: Any = None, notify: bool = True, limit: int = DAILY_LIMIT) -> dict[str, Any]:
    items = candidates(leads_path, limit=limit)
    for it in items:
        it["linkedin_url"] = linkedin_guess(it["domain"])
        it["linkedin_state"] = check_linkedin(it["linkedin_url"])

    routed_path = state_path(ROUTED_NAME)
    try:
        routed = json.loads(routed_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        routed = []
    routed.extend(it["domain"] for it in items)
    tmp = routed_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(routed[-2000:], indent=2) + "\n", encoding="utf-8")
    tmp.replace(routed_path)

    card = build_card(items) if items else ""
    drafts = [
        {"domain": it["domain"], "company": it["company"],
         "draft": build_outreach_draft(it["domain"], it["company"])}
        for it in items
    ]
    sent = False
    if notify and card:
        sent = owner_notify.send(card)
    return {"routed": len(items), "card": card, "drafts": drafts, "notified": sent,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
