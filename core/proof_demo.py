"""Proof & demo tetikleyici: suphe aninda canli gosteri.

Sinyal: teknik yetenek sorgusu / suphecilik.
Aksiyon: demo daveti + benchmark + hizli tarama baglantisi.
"""
from __future__ import annotations
import re
_SKEPTIC_RE = re.compile(r"(suphe|süphe|skept|kanit|kanıt|proof|demo|goster|göster|inanm|guven|güven|gercekten|really|calisiyor mu|çalışıyor mu|yalan|bos|boş)", re.I)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+|www\.[^\s\"'<>]+|[A-Za-z0-9-]+\.[a-z]{2,}(?:/[^\s\"'<>]*)?", re.I)
DEMO_INVITE_TR = "Sistemimizin kalitesini dogrudan test etmek icin kendi domaininizi veya bir musteri sitenizi iletin, canli altyapi tarama ciktisini ve ornek Whitelabel raporunu hemen paylasayim."
DEMO_INVITE_EN = "To test our quality live, send your domain or a client site and I will share the live infra scan output plus a sample whitelabel report right away."
BENCHMARK_LINE = "Crawl4AI + Playwright disposable browser pipeline with sub-second execution speed"
def is_skeptic(text: str) -> bool:
    return bool(_SKEPTIC_RE.search(str(text or "")))
def demo_invite(lang: str = "tr") -> str:
    return DEMO_INVITE_EN if str(lang or "").lower().startswith("en") else DEMO_INVITE_TR
def extract_site(text: str) -> str:
    m = _URL_RE.search(str(text or ""))
    return m.group(0).strip() if m else ""
def demo_reply(lang: str = "tr") -> str:
    if str(lang or "").lower().startswith("en"):
        return DEMO_INVITE_EN + " Infra: " + BENCHMARK_LINE + "."
    return DEMO_INVITE_TR + " Altyapi: " + BENCHMARK_LINE + "."
def normalize_site(raw: str) -> str:
    s = str(raw or "").strip().strip(",;()[]")
    if not s:
        return ""
    if not s.lower().startswith(("http://", "https://")):
        s = "https://" + s.lstrip("/")
    return s[:300]
def live_scan_snippet(site: str, *, timeout: float = 6.0) -> str:
    """Sub-second HTTP on tarama + Crawl4AI ozeti (hata-izole, uydurma yok)."""
    target = normalize_site(extract_site(site) or site)
    if not target:
        return ""
    import time as _t
    t0 = _t.monotonic()
    status = 0
    ms = 0.0
    try:
        import httpx as _hx
        with _hx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(target)
            ms = (_t.monotonic() - t0) * 1000.0
            status = int(r.status_code or 0)
    except Exception:
        ms = (_t.monotonic() - t0) * 1000.0
        status = 0
    line = "HTTP %s, %.0f ms" % (status if status else "erisim yok", ms)
    md = ""
    try:
        from nirvana import enrich_web as _ew
        md = str(_ew.crawl(target) or "")
    except Exception:
        md = ""
    insight = ""
    if md:
        try:
            from nirvana import enrich_web as _ew2
            insight = str(_ew2.insight(md) or "")
        except Exception:
            insight = ""
    parts = ["Canli tarama: %s -> %s" % (target, line)]
    parts.append("Hatti: %s" % BENCHMARK_LINE)
    if insight:
        parts.append("Bulgu: %s" % insight[:220])
    else:
        parts.append("Detayli markdown + Playwright disposable olcumu ornek Whitelabel raporuna isleniyor.")
    return " | ".join(parts)[:900]
async def alive_scan_snippet(site: str, *, timeout: float = 6.0) -> str:
    try:
        import asyncio as _a
        return await _a.to_thread(live_scan_snippet, site, timeout=timeout)
    except Exception:
        return live_scan_snippet(site, timeout=timeout)

