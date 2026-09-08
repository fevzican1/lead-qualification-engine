"""Lane S — tech_stack_detector [GitHub Actions, heavy].

Rapor kapsamı: Kullanılan teknolojileri tespit etmek.
Ücretsiz — HTTP başlıkları ve HTML imzaları.
Form kanıtına ek "teknik altyapı" kanıtı üretir.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
import config
from nirvana.registry import state_path

OUT_NAME = "tech_stack_log.json"

# Basit imza eşleştirmesi
SIGNATURES = {
    "WordPress": [r"wp-content", r"wp-includes", r"wordpress"],
    "WooCommerce": [r"woocommerce", r"wc-"],
    "Shopify": [r"cdn.shopify.com", r"shopify"],
    "React": [r"react", r"reactdom"],
    "Vue.js": [r"vue", r"__VUE__"],
    "Angular": [r"angular", r"ng-"],
    "Next.js": [r"__NEXT_DATA__", r"next/static"],
    "Nuxt.js": [r"__NUXT__", r"nuxt"],
    "jQuery": [r"jquery"],
    "Bootstrap": [r"bootstrap"],
    "Tailwind CSS": [r"tailwind"],
    "Google Analytics": [r"google-analytics", r"gtag"],
    "Google Tag Manager": [r"googletagmanager", r"gtm"],
    "Facebook Pixel": [r"facebook", r"fbq"],
    "Hotjar": [r"hotjar"],
    "Cloudflare": [r"cloudflare", r"cf-ray"],
    "Akamai": [r"akamai"],
    "Fastly": [r"fastly"],
    "Vercel": [r"vercel"],
    "Netlify": [r"netlify"],
}


def detect_from_headers(headers: dict[str, str]) -> list[str]:
    """HTTP başlıklarından tespit."""
    found = []
    server = headers.get("server", "").lower()
    powered = headers.get("x-powered-by", "").lower()
    via = headers.get("via", "").lower()

    if "nginx" in server:
        found.append("Nginx")
    if "apache" in server:
        found.append("Apache")
    if "iis" in server:
        found.append("IIS")
    if "php" in powered:
        found.append("PHP")
    if "asp.net" in powered:
        found.append("ASP.NET")
    if "express" in powered:
        found.append("Express.js")
    if "cloudflare" in via or "cloudflare" in server:
        found.append("Cloudflare")
    return found


def detect_from_html(html: str) -> list[str]:
    """HTML imzalarından tespit."""
    found = []
    html_lower = html.lower()
    for tech, patterns in SIGNATURES.items():
        for pat in patterns:
            if re.search(pat, html_lower):
                found.append(tech)
                break
    return found


def detect_stack(url: str) -> dict[str, Any]:
    """Tek stack tespiti."""
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
        headers = dict(r.headers)
        html = r.text or ""
        header_techs = detect_from_headers(headers)
        html_techs = detect_from_html(html)
        all_techs = list(set(header_techs + html_techs))
        return {
            "url": url,
            "status": r.status_code,
            "techs": all_techs,
            "server": headers.get("server", ""),
            "ts": time.time(),
        }
    except Exception as e:
        return {"url": url, "status": 0, "techs": [], "error": str(e)[:80], "ts": time.time()}


def build_evidence(stack: dict[str, Any]) -> str:
    """Kanıt metni."""
    techs = stack.get("techs", [])
    if not techs:
        return "Teknik stack tespit edilemedi."
    return f"Teknik stack: {', '.join(techs)}."


def run_batch(*, in_name: str = "verified_queue.json", **kwargs: Any) -> dict[str, Any]:
    """GitHub Actions'ta tetiklenir."""
    in_path = state_path(in_name)
    try:
        rows = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []

    stacks = []
    for row in rows[:10]:  # kota korunur
        url = str(row.get("url") or "").strip()
        if url.startswith("http"):
            stacks.append(detect_stack(url))

    out_path = state_path(OUT_NAME)
    payload = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stacks": stacks}
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)
    return {"scanned": len(stacks), "out": str(out_path)}
