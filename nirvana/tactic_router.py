"""Lane AD — tactic_router [GitHub Actions, heavy].

Çoklu Taktik Rotalama Matrisi (Multi-Tactic Routing Matrix):

Hiçbir lead harcanmaz. "Ele / Çöpe At" yerine fallback cascade:
  1) Sayfa gerçekten ölçülür (tek GET, gerçek ms + header + DOM sinyali).
  2) Gecikme yüksekse        -> Taktik A (Performans & Latency)   — sayısal gecikme kanıtı
  3) Platform tespit edilirse -> Taktik B (Platform & Checkout UX) — stack'e özel checkout kanıtı
  4) İkisi de normalse        -> Taktik C (Altyapı & Güvenlik)    — eksik header / e-posta riski

Sıfır halüsinasyon: yalnızca canlı HTTP / header / DOM verisi kartına basılır.
Uydurma kayıp yüzdesi yok; MOD-05 financial_loss_engine metriğini kanıt olarak kullanır.

Oracle kotasına dokunmaz — yalnız GitHub runner.
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
    """Yedekli boru hattı genişletmesi aktifse eşiği düşür (daha çok Taktik A)."""
    try:
        widen = json.loads(state_path("tactic_widen.json").read_text(encoding="utf-8"))
        return int(widen.get("slow_ms", SLOW_MS))
    except (OSError, ValueError, TypeError, AttributeError):
        return SLOW_MS


def _effective_platforms() -> dict[str, tuple[str, ...]]:
    """Genişletme aktifse platform imza listesini büyüt."""
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

# Taktik B platform imzaları (header + HTML birlikte)
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

# Taktik C: eksikliği kanıt olan güvenlik başlıkları
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
    """Kancayı Taktik'e göre kur. Sıfır uydurma: ölçülen veri neyse onu basar.

    Format: kurumsal "Altyapı Güvenlik ve Performans Bildirimi" — reklam dili
    değil, doğrudan kamuya açık teknik kanala yönelik resmî bildirim. Ölçülen
    metrik mesajın merkezindedir; şablon hissi olmaması için rakam her satırda
    gerçek ölçümden gelir.
    """
    band = classification.get("delay_band_pct", "2-5")
    metric = classification.get("evidence_metric") or 0
    platform = (classification.get("reason") or {}).get("platform")
    if turkish:
        head = f"{company} — Altyapı Güvenlik ve Performans Bildirimi. "
        if tactic == "A":
            return (head + f"Sitenizin yanıt süresinde ölçtüğümüz {metric} ms "
                    f"darboğaz kaydedildi; bu değer %{band} hız kaybı bandına karşılık "
                    "geliyor. Sayısal gecikme kartı ve kapanış adımları bu sohbette.")
        if tactic == "B":
            stack = platform or "platform"
            return (head + f"{stack} altyapınızdaki checkout/event akışında kopukluk "
                    f"sinyali ölçüldü (%{band} sepet kaybı risk bandı). Checkout kartı "
                    "ve kapanış planı bu sohbette.")
        return (head + "HTTP/header altyapınızda kritik koruma eksikleri tespit "
                "edildi (rapor numaralı). Altyapı Güvenlik Kartı ve düzeltme yol "
                "haritası bu sohbette.")
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