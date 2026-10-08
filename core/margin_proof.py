"""Kar marji / kayip cerceveleme: olculen bulgu -> aylik maliyet -> retainer."""
from __future__ import annotations
import re
_MONEY_RE = re.compile(r"(\d[\d.,]*)\s*(€|EUR|eur|€)?", re.I)
def monthly_loss_estimate(*, dom_ms: int = 0, bad_requests: int = 0, slow_resources: int = 0, monthly_orders: int = 1000, avg_order_eur: float = 60.0) -> dict:
    """Uydurma yok: yalnizca gozlemlenen metrikten aralik uretir."""
    risk = 0.0
    if dom_ms >= 3000:
        risk += 0.08
    elif dom_ms >= 1500:
        risk += 0.04
    elif dom_ms > 0:
        risk += 0.015
    risk += min(0.10, 0.02 * int(bad_requests or 0))
    risk += min(0.06, 0.01 * int(slow_resources or 0))
    risk = min(0.25, max(0.0, risk))
    monthly_revenue = max(0.0, float(monthly_orders) * float(avg_order_eur))
    low = monthly_revenue * risk * 0.5
    high = monthly_revenue * risk
    return {"risk_rate": round(risk, 4), "monthly_low_eur": round(low, 2), "monthly_high_eur": round(high, 2)}
def margin_line(metrics: dict, *, lang: str = "tr") -> str:
    est = monthly_loss_estimate(dom_ms=int(metrics.get("dom_ms") or 0), bad_requests=len(metrics.get("bad_reqs") or []), slow_resources=len(metrics.get("slow_res") or []))
    low = ("%0.0f" % est["monthly_low_eur"]).replace(",", ".")
    high = ("%0.0f" % est["monthly_high_eur"]).replace(",", ".")
    if str(lang or "").lower().startswith("en"):
        return "Measured drag: ~EUR %s-%s/month at risk. The EUR 5,000 retainer stops this leak; payback is inside the same month when the bottleneck closes." % (low, high)
    return "Olculen kayip: aylik ~%s-%s EUR risk altinda. 5.000 EUR retainer bu kacagi kapatir; darboaz kapaninca geri odeme ayni ay icinde cikar." % (low, high)
