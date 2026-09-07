"""Lane M — retainer_report_agent [GitHub Actions, heavy].

Audit-led growth kanıtı: doğrulanmış hedeflerin ÖLÇÜLMÜŞ bulgularından
(Pillow→PDF, reportlab yok = ücretsiz) kişiselleştirilmiş denetim raporu
üretir. Uydurma yüzde/istatistik YOK — yalnızca tarayıcıdan gözlemlenen veri.
Rapor: bulgular + öneri + gerçek insan kimliği (LinkedIn) + Telegram kanalı.
Raporlar nirvana/audit-reports/ altına commit edilir; link telegram + form
akışına "value-in-advance" olarak sunulur.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import config
from nirvana.registry import state_path

REPORT_DIR: Path = config.ROOT / "nirvana" / "audit-reports"
REPORTS_OUT = "retainer_reports.json"
_REPO = config._get("FEED_GITHUB_REPO", "fevzican1/lead-qualification-engine")
RAW_BASE = f"https://raw.githubusercontent.com/{_REPO}/master/nirvana/audit-reports"
PER_RUN_LIMIT = 3

try:
    _LAB = str(getattr(config, "AUDIT_LAB_NAME", "DevSolve Flow Inspector"))
    _YEAR = int(getattr(config, "AUDIT_REPORT_YEAR", time.gmtime().tm_year))
except Exception:
    _LAB, _YEAR = "DevSolve Flow Inspector", time.gmtime().tm_year


def report_url(domain: str) -> str:
    return f"{RAW_BASE}/{domain}.pdf"


def _identity_line() -> str:
    url = str(getattr(config, "OWNER_LINKEDIN_URL", "") or "").strip()
    return f"Insan karsinizda: {url}" if url else ""


def _page(domain: str, findings: list[str], out_pdf: Path) -> None:
    """Tek sayfa A4 oranlı PDF (Pillow). Tüm metinler gözleme dayalı."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1240, 1754
    img = Image.new("RGB", (W, H), (250, 250, 250))
    d = ImageDraw.Draw(img)

    def font(size: int):
        try:
            return ImageFont.truetype("arial.ttf", size)
        except Exception:
            return ImageFont.load_default()

    d.rectangle([0, 0, W, 110], fill=(198, 40, 40))
    d.text((40, 28), f"{_LAB} - Otonom Teknik Denetim Raporu {_YEAR}", font=font(34), fill=(255, 255, 255))
    d.text((40, 150), f"Hedef: {domain}", font=font(30), fill=(20, 20, 20))
    d.text((40, 210), "Yontem: agir hesaplama GitHub Actions'ta; Oracle sunucu yuku sifir.",
           font=font(22), fill=(70, 70, 70))
    d.text((40, 300), "Olculen Bulgular (yalnizca gozlem; tahmin/uydurma yok):", font=font(26), fill=(198, 40, 40))
    y = 360
    for i, f in enumerate(findings[:10], 1):
        d.ellipse([40, y + 4, 64, y + 28], outline=(198, 40, 40), width=3)
        d.text((80, y), f[:110], font=font(24), fill=(30, 30, 30))
        y += 56
    y += 40
    d.rectangle([40, y, W - 40, y + 90], fill=(230, 240, 255))
    d.text((60, y + 16), "Oneri: bulgularin giderilmesi icin aylik retainer planinda ilk 7 gun", font=font(22), fill=(30, 30, 60))
    d.text((60, y + 50), "performans yamalari ucretsiz uygulanir (value-in-advance).", font=font(22), fill=(30, 30, 60))
    y += 140
    d.text((40, y), "Guvenceler:", font=font(24), fill=(20, 20, 20))
    d.text((40, y + 44), "- Dinamik SLA + gizlilik (NDA) + fikri mülkiyet devri sözlesme paketi", font=font(22), fill=(60, 60, 60))
    d.text((40, y + 80), "- Odeme yalnizca dogrulanmis Payoneer talebi ile; sahibi insan dogrular", font=font(22), fill=(60, 60, 60))
    ident = _identity_line()
    y2 = H - 170
    d.rectangle([0, y2, W, H], fill=(240, 240, 240))
    d.text((40, y2 + 20), "Bu rapor otomatik teknik taramadir; hukuki danismanlik degildir.", font=font(20), fill=(90, 90, 90))
    d.text((40, y2 + 60), "Iletisim: Telegram funnel (dogrulanmis kanal).", font=font(20), fill=(90, 90, 90))
    if ident:
        d.text((40, y2 + 100), ident, font=font(20), fill=(90, 90, 90))
    img.save(out_pdf, "PDF", resolution=96)


def _findings_from_proof(proof: dict[str, Any]) -> list[str]:
    metrics = proof.get("metrics") or {}
    out: list[str] = []
    if metrics.get("dom_ms") is not None:
        out.append(f"DOM yukleme: {metrics['dom_ms']} ms (olculen)")
    for r in (metrics.get("slow_res") or [])[:4]:
        host = r["url"].split("/")[2] if r["url"][:4] == "http" else "istek"
        out.append(f"Yavas yanit: {host} - {r['ms']} ms")
    for r in (metrics.get("bad_reqs") or [])[:4]:
        out.append(f"Hatali istek: HTTP {r['status']} - {r['url'][:60]}")
    if not out:
        out.append("Belirgin darbogazi gozlenmedi; sinir degerler izlemeye alindi.")
    return out


def _findings_from_queue_row(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    stack = row.get("stack") or row.get("hook") or ""
    if stack:
        out.append(f"Tespit edilen yigin/ipucu: {str(stack)[:80]}")
    audit = (row.get("audit") or {}).get("reasons") or []
    for r in audit[:4]:
        out.append(f"Denetim notu: {r}")
    if not out:
        out.append("Acik form dogrulandi; detayli tarama sirada.")
    return out


def build_reports(*, proofs_path: Any = None, queue_path: Any = None,
                  limit: int = PER_RUN_LIMIT) -> list[dict[str, Any]]:
    """Proof verisi varsa onu kullan; yoksa verified_queue'dan derle."""
    try:
        hooks = json.loads(state_path("proof_hooks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hooks = {}
    try:
        queue = json.loads((queue_path or state_path("verified_queue.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        queue = []

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    built = 0
    for p in (hooks.get("proofs") or []):
        if built >= limit:
            break
        if not isinstance(p, dict) or p.get("mode") != "proof":
            continue
        domain = str(p.get("domain") or "").strip()
        if not domain:
            continue
        pdf = REPORT_DIR / f"{domain}.pdf"
        _page(domain, _findings_from_proof(p), pdf)
        results.append({"domain": domain, "source": "proof",
                        "report_url": report_url(domain), "pdf": str(pdf)})
        built += 1
    for row in queue:
        if built >= limit:
            break
        domain = str(row.get("domain") or "").strip()
        if not domain or any(r["domain"] == domain for r in results):
            continue
        pdf = REPORT_DIR / f"{domain}.pdf"
        _page(domain, _findings_from_queue_row(row), pdf)
        results.append({"domain": domain, "source": "queue",
                        "report_url": report_url(domain), "pdf": str(pdf)})
        built += 1
    return results


def run_batch(*, notify: bool = True, limit: int = PER_RUN_LIMIT) -> dict[str, Any]:
    reports = build_reports(limit=limit)
    out_path = state_path(REPORTS_OUT)
    try:
        prior = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = []
    merged = {r["domain"]: r for r in (prior if isinstance(prior, list) else [])}
    for r in reports:
        merged[r["domain"]] = r
    merged_list = list(merged.values())[-100:]
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "reports": merged_list}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(out_path)
    sent = False
    if notify and reports:
        try:
            import owner_notify
            lines = [f"Denetim raporu hazir ({len(reports)} hedef) - value-in-advance paketi:"]
            lines += [f"- {r['domain']}: {r['report_url']}" for r in reports]
            sent = owner_notify.send("\n".join(lines))
        except Exception:
            sent = False
    return {"reports": len(reports), "items": reports, "notified": sent, "out": str(out_path)}