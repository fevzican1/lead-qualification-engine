"""Harvest contact-form URLs from Common Crawl CDX.

Runs on GitHub Actions (or a laptop). Never uses Oracle HTTP probe budget.
Does not download WARC bodies — CDX URL + status only, then local path scoring.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import domain_store  # noqa: E402
import easy_score  # noqa: E402

logger = logging.getLogger(__name__)

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
CONTACT_PATH_RE = re.compile(
    r"(?i)/(iletisim|iletişim|contact-us|contactus|get-in-touch|"
    r"bize-ulasin|bizeulasin|bize-ulaşın|pages/contact)(/|$|\?)"
    r"|/(contact)(/|$|\?)"
)
NEWS_HOST_RE = re.compile(
    r"haber|gazete|news|gundem|magazin|spor|tv\d|radyo|blog\.",
    re.I,
)
NEWS_PATH_RE = re.compile(
    r"/haber|/duyuru|/baskan|/news/|/blog/|iletisim-baskan",
    re.I,
)
# (wildcard, url regex, max pages). TR contact pages first; skip .org.tr NGOs.
QUERIES: tuple[tuple[str, str, int], ...] = (
    ("*.com.tr", r".*/iletisim(?:/|$|\?)", 12),
    ("*.com.tr", r".*/bize-ulasin(?:/|$|\?)", 8),
    ("*.com.tr", r".*/contact(?:/|$|\?)", 6),
    ("*.net.tr", r".*/iletisim(?:/|$|\?)", 8),
    ("*.myshopify.com", r".*/pages/contact(?:/|$|\?)", 4),
    ("*.com", r".*/contact-us(?:/|$|\?)", 4),
    ("*.io", r".*/contact(?:/|$|\?)", 3),
    ("*.co", r".*/contact(?:/|$|\?)", 3),
)


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


FALLBACK_CDX = "https://index.commoncrawl.org/CC-MAIN-2026-34-index"


def _latest_cdx() -> str:
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = httpx.get(COLLINFO, timeout=60.0, follow_redirects=True)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                raise RuntimeError("Common Crawl collinfo.json empty")
            api = str((rows[0] or {}).get("cdx-api") or "").strip()
            if not api:
                raise RuntimeError("No cdx-api in collinfo")
            logger.info("Using crawl %s", (rows[0] or {}).get("id"))
            return api
        except Exception as exc:  # noqa: BLE001
            last = exc
            logger.info("collinfo attempt %s failed: %s", attempt, exc)
            time.sleep(2 * attempt)
    logger.info("collinfo failed — falling back to %s (%s)", FALLBACK_CDX, last)
    return FALLBACK_CDX


def _is_contact_url(url: str) -> bool:
    path = (urlparse(url).path or "/").lower()
    if path in {"/", ""}:
        return False
    if "/password" in path or "/cgi-sys/" in path:
        return False
    if NEWS_PATH_RE.search(path):
        return False
    return bool(CONTACT_PATH_RE.search(path))


def _keep(url: str) -> bool:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return False
    host = domain_store.host_of(url)
    if not host or domain_store.is_noise(url) or domain_store.is_enterprise(url):
        return False
    if NEWS_HOST_RE.search(host):
        return False
    if host.endswith(".org.tr"):
        return False
    if not _is_contact_url(url):
        return False
    score, _stack = easy_score.from_contact_url(url)
    return score >= 80


def _origin_contact(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    path = parsed.path or "/"
    match = CONTACT_PATH_RE.search(path)
    if not match:
        return domain_store.origin_url(url)
    kept = path[: match.end()]
    return f"{parsed.scheme}://{parsed.netloc}{kept}".rstrip("/")


def _cdx_get(client: httpx.Client, api: str, params: list[tuple[str, str]]):
    last: httpx.Response | None = None
    for attempt in range(1, 4):
        try:
            response = client.get(api, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.info("CDX transport fail attempt %s: %s", attempt, exc)
            time.sleep(2.0 * attempt)
            continue
        last = response
        if response.status_code in {429, 500, 502, 503}:
            logger.info("CDX HTTP %s attempt %s — retry", response.status_code, attempt)
            time.sleep(2.5 * attempt)
            continue
        return response
    return last


def harvest(
    *,
    per_page: int = 100,
    max_pages: int = 12,
    sleep_s: float = 0.35,
) -> list[dict[str, str | int]]:
    api = _latest_cdx()
    by_host: dict[str, dict[str, str | int]] = {}
    with httpx.Client(timeout=90.0, follow_redirects=True) as client:
        for wildcard, url_re, query_pages in QUERIES:
            pages = max(1, min(int(query_pages), int(max_pages)))
            for page in range(pages):
                params: list[tuple[str, str]] = [
                    ("url", wildcard),
                    ("output", "json"),
                    ("filter", "=status:200"),
                    ("filter", f"~url:{url_re}"),
                    ("filter", "=mime:text/html"),
                    ("limit", str(per_page)),
                    ("page", str(page)),
                ]
                response = _cdx_get(client, api, params)
                if response is None:
                    logger.info("CDX give up %s page %s", wildcard, page)
                    continue
                if response.status_code == 404:
                    break
                if response.status_code >= 400:
                    logger.info("CDX HTTP %s %s p%s — next page", response.status_code, wildcard, page)
                    time.sleep(2.0)
                    continue
                lines = [ln for ln in response.text.splitlines() if ln.startswith("{")]
                if not lines:
                    break
                added_page = 0
                for line in lines:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    raw = str(row.get("url") or "")
                    if not _keep(raw):
                        continue
                    url = _origin_contact(raw)
                    host = domain_store.host_of(url)
                    if not host:
                        continue
                    score, stack = easy_score.from_contact_url(url)
                    prev = by_host.get(host)
                    if prev and int(prev.get("easy_score") or 0) >= score:
                        continue
                    by_host[host] = {
                        "url": url,
                        "easy_score": int(score),
                        "stack": stack,
                        "host": host,
                    }
                    added_page += 1
                logger.info(
                    "CDX %s p%s lines=%s kept_hosts=%s total=%s",
                    wildcard,
                    page,
                    len(lines),
                    added_page,
                    len(by_host),
                )
                if len(lines) < per_page:
                    break
                time.sleep(sleep_s)
    rows = sorted(by_host.values(), key=lambda r: -int(r.get("easy_score") or 0))
    logger.info("Harvest unique hosts=%s", len(rows))
    return rows


def merge_feed(path: Path, rows: list[dict[str, str | int]], *, cap: int = 12000) -> dict:
    existing: dict[str, dict] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for item in payload.get("urls") or []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "")
                if not _keep(url):
                    continue
                host = str(item.get("host") or domain_store.host_of(url))
                if host:
                    score, stack = easy_score.from_contact_url(url)
                    item = dict(item)
                    item["easy_score"] = int(score)
                    item["stack"] = stack
                    item["host"] = host
                    existing[host] = item
        except json.JSONDecodeError:
            existing = {}
    for row in rows:
        host = str(row.get("host") or "")
        if not host:
            continue
        url = str(row.get("url") or "")
        if not _keep(url):
            continue
        prev = existing.get(host)
        if prev and int(prev.get("easy_score") or 0) > int(row.get("easy_score") or 0):
            continue
        existing[host] = row
    ranked = sorted(existing.values(), key=lambda r: -int(r.get("easy_score") or 0))[:cap]
    return {
        "version": 1,
        "source": "commoncrawl-cdx",
        "updated_at": _utc(),
        "count": len(ranked),
        "urls": ranked,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "feeds" / "ready_queue.json"))
    parser.add_argument("--limit", type=int, default=12000)
    parser.add_argument("--pages", type=int, default=12)
    parser.add_argument("--per-page", type=int, default=100)
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = harvest(per_page=args.per_page, max_pages=args.pages)
    payload = merge_feed(out, rows, cap=max(100, args.limit))
    tmp = out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"Feed {payload['count']} URL(s) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
