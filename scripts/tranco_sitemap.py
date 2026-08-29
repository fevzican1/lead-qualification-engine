"""Discover public contact URLs from a bounded Tranco sitemap sample.

This is discovery only: it never submits forms and never runs on Oracle.
Requests are sequential, delayed, and capped to keep the source sites' load low.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import cc_discover  # noqa: E402
import domain_store  # noqa: E402
import easy_score  # noqa: E402
import form_preflight  # noqa: E402

logger = logging.getLogger(__name__)

TRANCO_URL = "https://tranco-list.eu/download/latest/1000000/short.csv"
LOC_RE = re.compile(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", re.I)
SITEMAP_RE = re.compile(r"(?im)^\s*sitemap:\s*(https?://\S+)")


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _domains(payload: bytes, limit: int) -> list[str]:
    raw = payload
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if names:
                raw = archive.read(names[0])
    except zipfile.BadZipFile:
        pass
    out: list[str] = []
    for row in csv.reader(io.StringIO(raw.decode("utf-8", errors="replace"))):
        if not row:
            continue
        candidate = row[-1].strip().lower()
        if candidate in {"domain", "domains"} or "." not in candidate:
            continue
        host = (urlparse("https://" + candidate).hostname or "").lower().removeprefix("www.")
        if host and host not in out:
            out.append(host)
        if len(out) >= limit:
            break
    return out


def _candidate_urls(text: str) -> list[str]:
    urls = SITEMAP_RE.findall(text)
    urls.extend(LOC_RE.findall(text))
    return [url.rstrip(").,;") for url in urls]


def harvest(*, max_domains: int, deadline_s: float, delay_s: float) -> list[dict[str, str | int]]:
    started = time.monotonic()
    timeout = httpx.Timeout(8.0, connect=4.0, read=8.0, write=4.0, pool=4.0)
    headers = {"User-Agent": "devsolve-public-discovery/1.0 (+contact-page-indexing)"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = client.get(TRANCO_URL)
        response.raise_for_status()
        domains = _domains(response.content, max_domains)
        by_host: dict[str, dict[str, str | int]] = {}
        for index, host in enumerate(domains, start=1):
            if time.monotonic() - started >= deadline_s:
                break
            sitemap_urls: list[str] = []
            for path in ("/robots.txt", "/sitemap.xml"):
                try:
                    result = client.get(f"https://{host}{path}")
                    if result.status_code < 400:
                        sitemap_urls.extend(_candidate_urls(result.text))
                except httpx.HTTPError:
                    pass
                if sitemap_urls:
                    break
            for sitemap_url in sitemap_urls[:2]:
                if time.monotonic() - started >= deadline_s:
                    break
                try:
                    result = client.get(sitemap_url)
                    if result.status_code >= 400:
                        continue
                except httpx.HTTPError:
                    continue
                for raw_url in _candidate_urls(result.text):
                    if not cc_discover._keep(raw_url):
                        continue
                    host_key = domain_store.host_of(raw_url)
                    score, stack = easy_score.from_contact_url(raw_url)
                    if not host_key or host_key in by_host and int(by_host[host_key]["easy_score"]) >= score:
                        continue
                    by_host[host_key] = {
                        "url": cc_discover._origin_contact(raw_url),
                        "easy_score": int(score),
                        "stack": stack,
                        "host": host_key,
                    }
            if delay_s > 0:
                time.sleep(delay_s)
            if index % 100 == 0:
                logger.info("Tranco sitemap progress %s/%s hosts=%s", index, len(domains), len(by_host))
        rows = sorted(by_host.values(), key=lambda row: (-int(row["easy_score"]), str(row["host"])))
        budget = max(10.0, deadline_s - (time.monotonic() - started))
        if rows and budget >= 10:
            rows = form_preflight.filter_verified_rows(
                rows,
                client=client,
                deadline_s=budget,
                workers=6,
                timeout=8.0,
            )
        return rows
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-domains", type=int, default=400)
    parser.add_argument("--deadline", type=int, default=480)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--limit", type=int, default=4000)
    args = parser.parse_args()
    try:
        rows = harvest(
            max_domains=max(50, min(args.max_domains, 1000)),
            deadline_s=max(60.0, float(args.deadline)),
            delay_s=max(0.1, args.delay),
        )
    except Exception:
        logger.exception("Tranco sitemap harvest failed")
        out = Path(args.out)
        if out.exists():
            print(f"Keeping previous shard after harvest failure: {out}")
            return 0
        rows = []
    rows = rows[: max(100, args.limit)]
    payload = {
        "version": 1,
        "source": "tranco-sitemap",
        "profile": "tranco-sitemap",
        "updated_at": _utc(),
        "count": len(rows),
        "urls": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"Shard tranco-sitemap: {len(rows)} URL(s) -> {out}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
