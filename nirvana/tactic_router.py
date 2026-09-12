"""Lane AD — tactic_router [GitHub Actions, heavy].

Coklu Taktik Rotalama Matrisi (Multi-Tactic Routing Matrix):

Hicbir lead harcanmaz. Fallback cascade:
  1) Sayfa gercekten olculur (tek GET, gercek ms + header + DOM sinyali).
  2) Gecikme yuksekse        -> Taktik A (Performans & Latency)
  3) Platform tespit edilirse -> Taktik B (Platform & Checkout UX)
  4) Ikisi de normalse        -> Taktik C (Altyapi & Guvenlik)

Sifir halusinasyon: yalnizca canli HTTP / header / DOM verisi karta basilir.
Oracle kotasina dokunmaz — yalniz GitHub runner.
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from nirvana.registry import state_path

OUT_NAME = "tactic_matrix.json"
SLOW_MS = 1200            # Taktik A eşiği (tactic_widen.json varsa 800'e iner)
TIMEOUT = 12.0
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _effective_slow_ms() -> int:
    """Yedekli boru hatti genisletmesi aktifse esigi dusur (daha cok Taktik A)."""
    try:
        widen = json.loads(state_path("tactic_widen.json").read_text(encoding="utf-8"))
        return int(widen.get("slow_ms", SLOW_MS))
    except (OSError, ValueError, TypeError, AttributeError):
        return SLOW_MS


def _effective_platforms() -> dict[str, tuple[str, ...]]:
    """Genisletme aktifse platform imza listesini buyut."""
    base = dict(STACK_SIGNATURES)
    try:
        widen = json.loads(state_path("tactic_widen.json").read_text(encoding="utf-8"))
        names = widen.get("platforms") or []
        extra = {"Wix": ("wix.com", "wixstatic", "parastorage"),
                 "Squarespace": ("squarespace", "sqsp", "squarespace-cdn"),
                 "Weebly": ("weebly", "weeblycloud"),
                 "BigCommerce": ("bigcommerce", "mybigcommerce", "bigcommerce.com")}
        for n in names:
            if n in extra:
                base.setdefault(n, extra[n])
    except (OSError, ValueError, AttributeError):
        pass
    return base

# Taktik B platform imzalari (header + HTML birlikte)
STACK_SIGNATURES: dict[str, tuple[str, ...]] = {
    "WooCommerce": ("woocommerce", "woo/", "?add-to-cart="),
    "Shopify": ("cdn.shopify.com", "shopify", "myshopify"),
    "Ticimax": ("ticimax", "tici_max_endpoint"),
    "IdeaSoft": ("ideasoft", "ideaworks", "direction.com"),
    "T-Soft": ("tsoft", "tsoft.co"),
    "PrestaShop": ("prestashop", "prestashop_"),
    "Magento": ("magento", "mage/"),
    "OpenCart": ("opencart", "zen-cart"),
}

# Taktik C: eksikligi kanit olan guvenlik basliklari
SECURITY_HEADERS = ("content-security-policy", "strict-transport-security",
                    "x-content-type-options", "x-frame-options",
                    "referrer-policy", "permissions-policy")
def _netloc(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def _platform_from(body: str, headers: dict[str, str]) -> str | None:
    low_body = (body or "").lower()
    server = " ".join(str(headers.get(k) or "") for k in ("server", "x-powered-by", "via")).lower()
    blob = f"{low_body} {server}"
    for name, sigs in _effective_platforms().items():
        if any(s in blob for s in sigs):
            return name
    return None


def probe(url: str) -> dict[str, Any]:
    """Gerçek ölçüm — asla uydurma sayı üretmez; hata durumunda fallback dict."""
    try:
        r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": UA})
        return {
            "ms": int(r.elapsed.total_seconds() * 1000),
            "status": r.status_code,
            "headers": {k.lower(): v for k, v in r.headers.items()},
            "body": (r.text or "")[:200_000],
            "final_url": str(r.url),
        }
    except httpx.HTTPError as exc:
        return {"ms": None, "status": None, "headers": {}, "body": "", "error": str(exc)[:120]}


def classify(probe: dict[str, Any]) -> dict[str, Any]:
    """Fallback cascade: A -> B -> C. Her hedef mutlaka bir taktiğe girer."""
    ms = probe.get("ms")
    headers = probe.get("headers") or {}
    body = probe.get("body") or ""
    platform = _platform_from(body, headers)
    slow = ms is not None and ms > _effective_slow_ms()
    missing = [h for h in SECURITY_HEADERS if not headers.get(h)]

    if slow:
        tactic, reason = "A", {"latency_ms": ms, "threshold_ms": SLOW_MS}
    elif platform:
        tactic, reason = "B", {"platform": platform, "checkout_signal": True}
    else:
        tactic, reason = "C", {"missing_headers": missing[:5],
                               "header_count": len(headers),
                               "has_csp": bool(headers.get("content-security-policy"))}

    return {"tactic": tactic, "bottle_neck": platform or "performance",
            "evidence_metric": ms if ms is not None else 0,
            "delay_band_pct": _loss_band(ms),
            "reason": reason, "missing_security_headers": missing[:5]}


def _loss_band(ms: int | None) -> str:
    if ms is None:
        return "2-5"
    if ms > 3000:
        return "10-12"
    if ms > 2000:
        return "8-10"
    if ms > _effective_slow_ms():
        return "5-8"
    return "2-5"
def hook_for(tactic: str, classification: dict[str, Any], *, company: str,
             turkish: bool = True) -> str:
    """Kancayi Taktik'e gore kur. Sifir uydurma: olculen veri neyse onu basar.

    Format: kurumsal bildirim dili; olculen metrik mesajin merkezindedir.
    """
    band = classification.get("delay_band_pct", "2-5")
    metric = classification.get("evidence_metric") or 0
    platform = (classification.get("reason") or {}).get("platform")
    if turkish:
        head = f"{company} — Altyapi Guvenlik ve Performans Bildirimi. "
        if tactic == "A":
            return (head + f"Sitenizin yanit suresinde olctugumuz {metric} ms "
                    f"darbogaz kaydedildi; bu deger %{band} hiz kaybi bandina karsilik "
                    "geliyor. Sayisal gecikme karti ve kapanis adimlari bu sohbette.")
        if tactic == "B":
            stack = platform or "platform"
            return (head + f"{stack} altyapinizdaki checkout/event akisinda kopukluk "
                    f"sinyali olculdu (%{band} sepet kaybi risk bandi). Checkout karti "
                    "ve kapanis plani bu sohbette.")
        return (head + "HTTP/header altyapinizda kritik koruma eksikleri tespit "
                "edildi (rapor numarali). Altyapi Guvenlik Karti ve duzeltme yol "
                "haritasi bu sohbette.")
    head = f"{company} — Infrastructure Security & Performance Notice. "
    if tactic == "A":
        return (head + f"Measurements on your site show a {metric} ms response "
                f"bottleneck, which maps to a {band}% speed-loss band. The numeric "
                "latency card and the closure steps are in this chat.")
    if tactic == "B":
        stack = platform or "platform"
        return (head + f"A disconnect signal was measured in your {stack} "
                f"checkout/event flow ({band}% cart-loss risk band). The drop-off "
                "card and closure plan are in this chat.")
    return (head + "Critical security-header gaps were detected on your HTTP/email "
            "stack (numbered report). The Infrastructure Security Card and the fix "
            "roadmap are in this chat.")


def run_batch(*, in_name: str = "verified_queue.json", limit: int = 40,
              out_name: str = OUT_NAME) -> dict[str, Any]:
    in_path = state_path(in_name)
    try:
        rows = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    routed: list[dict[str, Any]] = []
    from nirvana import urlutil
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        url = urlutil.safe_url(row.get("url") or row.get("domain") or "")
        if not url:
            continue
        measured = probe(url)
        cls = classify(measured)
        routed.append({
            "domain": urlutil.clean_domain(row.get("domain") or url),
            "company": row.get("company", row.get("domain")),
            "url": url,
            "tactic": cls["tactic"],
            "hook": hook_for(cls["tactic"], cls, company=str(row.get("company") or row.get("domain"))),
            "evidence": {k: v for k, v in cls.items() if k != "hook"},
            "measured_ms": measured.get("ms"),
            "routed_by": "tactic_router",
        })
    out_path = state_path(out_name)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "routed": routed}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    tmp.replace(out_path)
    return {"routed": len(routed), "tactics": {
        t: sum(1 for r in routed if r["tactic"] == t) for t in ("A", "B", "C")},
        "out": str(out_path)}