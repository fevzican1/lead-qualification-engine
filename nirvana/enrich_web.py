"""Rapor 2.1 — $0 dis kaynak zenginlestirme: yerel SearXNG + Crawl4AI.

Akis: sorgu -> SearXNG (meta arama) -> en iyi URL -> Crawl4AI (temiz markdown)
-> sirket profili. Saglayicilar yoksa/sagliksizsa sessizce {} doner (fail-open);
satis hatti ASLA bu yuzden durmaz. Sonuclar dosya onbelleginde TTL ile tutulur
(Rapor 3: Redis yerine $0 dosya cache; Redis varsa da ayni arayuz korunur).

Kullanim:
    python -m nirvana.enrich_web --domain ornek.com
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)

CACHE_PATH = config.ROOT / "nirvana" / "state" / "enrich_cache.json"
CACHE_TTL_S = float(os.getenv("ENRICH_CACHE_TTL_HOURS", "168") or 168) * 3600.0
MAX_URLS = int(os.getenv("ENRICH_MAX_URLS", "2") or 2)


def _searxng() -> str:
    return str(getattr(config, "SEARXNG_URL", "") or "").rstrip("/")


def _crawl4ai() -> str:
    return str(getattr(config, "CRAWL4AI_URL", "") or "").rstrip("/")


def search(query: str, *, limit: int = 4, timeout: float = 20.0) -> list[dict[str, Any]]:
    base = _searxng()
    if not base or not query.strip():
        return []
    try:
        response = httpx.get(
            f"{base}/search",
            params={"q": query, "format": "json", "language": "en", "safesearch": "1"},
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        rows = response.json().get("results") or []
    except Exception as exc:  # noqa: BLE001
        logger.info("SearXNG erisilemedi: %s", exc)
        return []
    return [row for row in rows[:limit] if isinstance(row, dict) and row.get("url")]


def crawl(url: str, *, timeout: float = 45.0) -> str:
    base = _crawl4ai()
    if not base or not url:
        return ""
    try:
        response = httpx.post(
            f"{base}/crawl",
            json={"urls": [url], "params": {"pageOptions": {"waitFor": 1000, "timeout": 30000}}},
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("Crawl4AI erisilemedi: %s", exc)
        return ""
    row = body
    if isinstance(body, dict) and isinstance(body.get("results"), list) and body["results"]:
        row = body["results"][0]
    if isinstance(row, dict):
        return str(row.get("markdown") or row.get("markdown_v2") or "")[:12000]
    return ""


def _cache_load() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_save(data: dict[str, Any]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except OSError:
        pass


def profile(domain: str, *, company: str = "", fresh: bool = False) -> dict[str, Any]:
    """Sirket profili (onbellekli). Cikti: {domain, urls, markdown, fetched_at}."""
    key = (domain or company or "").strip().lower()
    if not key:
        return {}
    cache = _cache_load()
    row = cache.get(key)
    if row and not fresh and (time.time() - float(row.get("at") or 0)) < CACHE_TTL_S:
        return row
    query = f"{company or domain} company services about".strip()
    urls = [r["url"] for r in search(query, limit=MAX_URLS)]
    if not urls and domain:
        urls = [f"https://{domain}"]
    markdown = ""
    for url in urls[:MAX_URLS]:
        markdown = crawl(url)
        if len(markdown) > 400:
            break
    result = {
        "domain": key,
        "urls": urls,
        "markdown": markdown[:12000],
        "at": time.time(),
    }
    cache[key] = result
    _cache_save(cache)
    return result


def run_batch(*, domain: str = "", company: str = "") -> dict[str, Any]:
    return profile(domain=domain, company=company)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="")
    ap.add_argument("--company", default="")
    args = ap.parse_args()
    print(json.dumps(run_batch(domain=args.domain, company=args.company), ensure_ascii=False)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
