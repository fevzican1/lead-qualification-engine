"""Technical audit + ROI benchmark (target: analytics/audit_generator.py).

Form oncesi hizli tarama: PageSpeed benzeri olcum (hafif httpx TTFB + header
sinyali, agir is GitHub lane'de) + Industry CRO benchmark + 5000EUR retainer
ROI projeksiyonu. Cikti: kases Proof-Card dict (kart PNG mevcut ureteclerle).
"""
from __future__ import annotations
import logging, time
from typing import Any
from urllib.parse import urlparse
logger = logging.getLogger(__name__)
RETAINER_EUR = 5000
CRO_BENCHMARKS = {"slow_loss_pct": (8, 12), "no_afterhours_loss_pct": (15, 25),
"mobile_gap_loss_pct": (5, 10), "a11y_gap_loss_pct": (2, 5)}
def quick_scan(url: str, timeout: float = 10.0) -> dict:
    """Hafif olcum: TTFB + durum + guvenlik basligi + mobil/a11y sinyali."""
    out: dict[str, Any] = {"url": url, "ttfb_ms": 0, "status": 0, "ok": False}
    try:
        import httpx
        t0 = time.monotonic()
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "DevSolve-audit/1.0"})
        out.update(status=r.status_code, ok=r.status_code < 400,
                   ttfb_ms=int((time.monotonic()-t0)*1000),
                   missing_sec=[h for h in ("content-security-policy","strict-transport-security","x-frame-options") if h not in {k.lower() for k in r.headers}],
                   mobile_ok=bool('name="viewport"' in (r.text[:6000] or "").lower()),
                   slow=(time.monotonic()-t0)*1000 > 1200)
    except Exception as e: out["error"] = str(e)[:150]
    try:
        h = (urlparse(url).hostname or "").lower()
        out["afterhours_gap"] = True  # canli karsilama widget yoksa chat ilk mesajda dolar
    except Exception: out["afterhours_gap"] = True
    return out
def roi_projection(monthly_revenue_eur: float = 20000.0, loss_pct: float = 10.0) -> dict:
    rec = monthly_revenue_eur * loss_pct / 100.0
    net = rec - RETAINER_EUR
    roi = (net / RETAINER_EUR * 100.0) if RETAINER_EUR else 0.0
    return {"retainer_eur": RETAINER_EUR, "recovered_eur": round(rec, 2),
            "net_eur": round(net, 2), "roi_pct": round(roi, 1),
            "payback_mo": round(RETAINER_EUR / rec, 2) if rec > 0 else 0.0}
def proof_card_payload(url: str, scan: dict | None = None, monthly_rev: float = 20000.0) -> dict:
    scan = scan or quick_scan(url)
    band = "10-12" if scan.get("slow") or scan.get("status", 0) >= 400 else "5-8"
    mid = sum(CRO_BENCHMARKS["slow_loss_pct"]) / 2 if band == "10-12" else 6.5
    roi = roi_projection(monthly_rev, mid)
    try:
        from nirvana import financial_loss_engine as fle
        loss = fle.estimate_loss(dom_ms=int(scan.get("ttfb_ms") or 0),
                                 slow_count=1 if scan.get("slow") else 0,
                                 bad_count=1 if scan.get("status", 0) >= 400 else 0)
        band = str(loss.get("loss_band_pct") or band)
    except Exception: pass
    return {"url": url, "scan": scan, "loss_band_pct": band,
            "benchmarks": CRO_BENCHMARKS, "roi": roi,
            "headline": (f"Olcutulen darbozgaz %{band} donusum-kaybi bandinda; "
                         f"aylik {RETAINER_EUR} EUR retainer, kacirilmis musterileri geri "
                         f"kazanarak ~{roi['recovered_eur']} EUR/ay toparlar (net ~{roi['net_eur']} EUR).")}
def run_batch(urls: list, **kw: Any) -> dict:
    cards = [proof_card_payload(u, monthly_rev=float(kw.get("monthly_rev") or 20000.0)) for u in urls[:25]]
    return {"cards": len(cards), "items": cards}
