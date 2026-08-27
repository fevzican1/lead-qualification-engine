"""Harvest contact-form URLs from Common Crawl CDX.

Runs on GitHub Actions. Never uses Oracle HTTP probe budget.
CDX is often 503/slow — fail fast, keep prior feed, always exit 0.
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
QUERIES: tuple[tuple[str, str, int], ...] = (
    ("*.com.tr", r".*/iletisim(?:/|$|\?)", 8),
    ("*.com.tr", r".*/bize-ulasin(?:/|$|\?)", 5),
    ("*.net.tr", r".*/iletisim(?:/|$|\?)", 5),
    ("*.com.tr", r".*/contact(?:/|$|\?)", 4),
    ("*.myshopify.com", r".*/pages/contact(?:/|$|\?)", 3),
    ("*.com", r".*/contact-us(?:/|$|\?)", 3),
    ("*.io", r".*/contact(?:/|$|\?)", 2),
    ("*.co", r".*/contact(?:/|$|\?)", 2),
)
FALLBACK_CDX = (
    "https://index.commoncrawl.org/CC-MAIN-2026-34-index",
    "https://index.commoncrawl.org/CC-MAIN-2026-30-index",
    "https://index.commoncrawl.org/CC-MAIN-2026-26-index",
)


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _cdx_apis() -> list[str]:
    apis: list[str] = []
    try:
        response = httpx.get(COLLINFO, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
        for row in response.json()[:4]:
            api = str((row or {}).get("cdx-api") or "").strip()
            if api and api not in apis:
                apis.append(api)
                logger.info("CDX index %s", (row or {}).get("id"))
    except Exception as exc:  # noqa: BLE001
        logger.info("collinfo skipped: %s", exc)
    for api in FALLBACK_CDX:
        if api not in apis:
            apis.append(api)
    return apis[:3]


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
    """Short timeout + 2 tries. CDX 503 must not burn the Actions minute clock."""
    last: httpx.Response | None = None
    for attempt in range(1, 3):
        try:
            response = client.get(api, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.info("CDX transport fail attempt %s: %s", attempt, exc)
            time.sleep(1.5 * attempt)
            continue
        last = response
        if response.status_code in {429, 500, 502, 503}:
            logger.info("CDX HTTP %s attempt %s — skip page", response.status_code, attempt)
            time.sleep(1.2 * attempt)
            continue
        return response
    return last


def _ingest_lines(
    lines: list[str],
    by_host: dict[str, dict[str, str | int]],
) -> int:
    added = 0
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
        added += 1
    return added


def harvest(
    *,
    per_page: int = 100,
    max_pages: int = 8,
    sleep_s: float = 0.2,
    deadline_s: float = 660.0,
) -> list[dict[str, str | int]]:
    started = time.monotonic()
    by_host: dict[str, dict[str, str | int]] = {}
    timeout = httpx.Timeout(18.0, connect=8.0, read=18.0, write=8.0, pool=8.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for api in _cdx_apis():
            if time.monotonic() - started > deadline_s:
                logger.info("Harvest deadline — unique=%s", len(by_host))
                break
            logger.info("Harvesting %s", api)
            for wildcard, url_re, query_pages in QUERIES:
                if time.monotonic() - started > deadline_s:
                    break
                pages = max(1, min(int(query_pages), int(max_pages)))
                misses = 0
                for page in range(pages):
                    if time.monotonic() - started > deadline_s:
                        break
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
                        misses += 1
                        logger.info("CDX give up %s p%s", wildcard, page)
                        if misses >= 2:
                            break
                        continue
                    if response.status_code == 404:
                        break
                    if response.status_code >= 400:
                        misses += 1
                        logger.info("CDX HTTP %s %s p%s", response.status_code, wildcard, page)
                        if misses >= 2:
                            break
                        continue
                    misses = 0
                    lines = [ln for ln in response.text.splitlines() if ln.startswith("{")]
                    if not lines:
                        break
                    added_page = _ingest_lines(lines, by_host)
                    logger.info(
                        "CDX %s p%s lines=%s kept=%s total=%s",
                        wildcard,
                        page,
                        len(lines),
                        added_page,
                        len(by_host),
                    )
                    time.sleep(sleep_s)
            if len(by_host) >= 500:
                break
    rows = sorted(by_host.values(), key=lambda r: -int(r.get("easy_score") or 0))
    logger.info("Harvest unique hosts=%s elapsed=%.0fs", len(rows), time.monotonic() - started)
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
        url = str(row.get("url") or "")
        if not host or not _keep(url):
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
    parser.add_argument("--pages", type=int, default=8)
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--deadline", type=int, default=660)
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str | int]] = []
    try:
        rows = harvest(per_page=args.per_page, max_pages=args.pages, deadline_s=float(args.deadline))
    except Exception:
        logger.exception("Harvest aborted — keeping prior feed")
    payload = merge_feed(out, rows, cap=max(100, args.limit))
    tmp = out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"Feed {payload['count']} URL(s) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
