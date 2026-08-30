"""Harvest contact-form URLs from Common Crawl CDX.

Runs on GitHub Actions. Never uses Oracle HTTP probe budget.

The index holds billions of pages. A fixed page=0..N walk returns the same
alphabetical head every run, so the feed stopped growing. We ask CDX how many
pages a query has (showNumPages) and sample pages at random, which makes every
run land on a different slice of the crawl.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import domain_store  # noqa: E402
import easy_score  # noqa: E402
import form_preflight  # noqa: E402

logger = logging.getLogger(__name__)

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
CONTACT_PATH_RE = re.compile(
    r"(?i)/(iletisim|iletişim|contact-us|contactus|get-in-touch|"
    r"bize-ulasin|bizeulasin|bize-ulaşın|kontakt|contacto|contatti|"
    r"contactez-nous|kontakta-oss|pages/contact)(/|$|\?)"
    r"|/(contact)(/|$|\?)"
)
NEWS_HOST_RE = re.compile(
    r"haber|gazete|news|gundem|magazin|spor|tv\d|radyo|blog\.|wiki|forum",
    re.I,
)
NEWS_PATH_RE = re.compile(
    r"/haber|/duyuru|/baskan|/news/|/blog/|iletisim-baskan|/wp-admin|/author/",
    re.I,
)

# TLD wildcards × contact paths. TR first (highest close rate), then EU/global
# SMB space. Every entry is a separate CDX query with random page sampling.
TR_PATHS = (
    r".*/iletisim(?:/|$|\?)",
    r".*/bize-ulasin(?:/|$|\?)",
    r".*/contact(?:/|$|\?)",
)
EN_PATHS = (
    r".*/contact-us(?:/|$|\?)",
    r".*/contact(?:/|$|\?)",
    r".*/get-in-touch(?:/|$|\?)",
)
EU_PATHS = (
    r".*/kontakt(?:/|$|\?)",
    r".*/contacto(?:/|$|\?)",
    r".*/contatti(?:/|$|\?)",
)


def _queries(profile: str = "all") -> list[tuple[str, str, int]]:
    """(wildcard, url regex, weight). Weight = how many random pages to pull."""
    rows: list[tuple[str, str, int]] = []
    for path in TR_PATHS:
        rows.append(("*.com.tr", path, 6))
    rows.append(("*.net.tr", TR_PATHS[0], 3))
    rows.append(("*.tr", TR_PATHS[0], 3))
    for path in EN_PATHS:
        rows.append(("*.com", path, 4))
    rows.append(("*.myshopify.com", r".*/pages/contact(?:/|$|\?)", 4))
    rows.append(("*.co", EN_PATHS[0], 2))
    rows.append(("*.io", EN_PATHS[1], 2))
    rows.append(("*.net", EN_PATHS[0], 2))
    rows.append(("*.co.uk", EN_PATHS[0], 2))
    for wildcard in ("*.de", "*.nl", "*.pl", "*.se", "*.dk", "*.at", "*.ch"):
        rows.append((wildcard, EU_PATHS[0], 2))
    rows.append(("*.es", EU_PATHS[1], 2))
    rows.append(("*.it", EU_PATHS[2], 2))
    profile = (profile or "all").strip().lower()
    if profile == "tr":
        return [row for row in rows if row[0] in {"*.com.tr", "*.net.tr", "*.tr"}]
    if profile == "global":
        return [
            row
            for row in rows
            if row[0] in {"*.com", "*.co", "*.io", "*.net", "*.co.uk"}
            and row[0] != "*.myshopify.com"
        ]
    if profile == "eu":
        return [row for row in rows if row[0] in {"*.de", "*.nl", "*.pl", "*.se", "*.dk", "*.at", "*.ch", "*.es", "*.it"}]
    if profile == "platform":
        return [row for row in rows if row[0] == "*.myshopify.com"]
    if profile == "longtail":
        rows.extend(
            [
                ("*.biz", EN_PATHS[0], 2),
                ("*.info", EN_PATHS[0], 2),
                ("*.shop", EN_PATHS[0], 2),
                ("*.store", EN_PATHS[0], 2),
                ("*.online", EN_PATHS[0], 2),
            ]
        )
        return [row for row in rows if row[0] in {"*.biz", "*.info", "*.shop", "*.store", "*.online"}]
    return rows


FALLBACK_CDX = (
    "https://index.commoncrawl.org/CC-MAIN-2026-34-index",
    "https://index.commoncrawl.org/CC-MAIN-2026-30-index",
    "https://index.commoncrawl.org/CC-MAIN-2026-26-index",
)


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _cdx_apis(limit: int = 4, timeout_s: float = 20.0) -> list[str]:
    apis: list[str] = []
    try:
        response = httpx.get(COLLINFO, timeout=timeout_s, follow_redirects=True)
        response.raise_for_status()
        for row in response.json()[:6]:
            api = str((row or {}).get("cdx-api") or "").strip()
            if api and api not in apis:
                apis.append(api)
    except Exception as exc:  # noqa: BLE001
        logger.info("collinfo skipped: %s", exc)
    for api in FALLBACK_CDX:
        if api not in apis:
            apis.append(api)
    return apis[:limit]


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
    if host.endswith(".org.tr") or host.endswith(".gov.tr") or host.endswith(".edu.tr"):
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


def _base_params(wildcard: str, url_re: str) -> list[tuple[str, str]]:
    return [
        ("url", wildcard),
        ("output", "json"),
        # One matching contact page per host keeps a page from being consumed
        # by dozens of URLs from the same site.
        ("collapse", "host"),
        ("filter", "=status:200"),
        ("filter", f"~url:{url_re}"),
        ("filter", "=mime:text/html"),
    ]


def _get(
    client: httpx.Client,
    api: str,
    params: list[tuple[str, str]],
    tries: int = 2,
    timeout_s: float | None = None,
):
    """CDX answers 503 a lot and queues requests under load. Retry briefly,
    optionally with a longer per-request timeout, then move on."""
    last: httpx.Response | None = None
    request_timeout = httpx.Timeout(timeout_s, connect=5.0) if timeout_s else None
    for attempt in range(1, tries + 1):
        try:
            response = client.get(api, params=params, timeout=request_timeout)
        except Exception as exc:  # noqa: BLE001
            logger.info("CDX transport fail %s: %s", attempt, exc)
            time.sleep(1.2 * attempt)
            continue
        last = response
        if response.status_code in {429, 500, 502, 503, 504}:
            time.sleep(1.0 * attempt)
            continue
        return response
    return last


def _num_pages(client: httpx.Client, api: str, wildcard: str, *, tries: int = 2) -> int:
    """Page count depends only on the URL pattern; filters are applied per page.

    Passing the filters here makes CDX answer with 0 pages, which is what
    silently starved the feed.
    """
    params = [("url", wildcard), ("output", "json"), ("showNumPages", "true")]
    # showNumPages is tiny but queues behind CDX's request backlog; give it room.
    response = _get(client, api, params, tries=tries, timeout_s=40.0)
    if response is None or response.status_code >= 400:
        return 0
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return 0
    if isinstance(payload, dict):
        return int(payload.get("pages") or 0)
    return 0


def _ingest_lines(lines: list[str], by_host: dict[str, dict[str, str | int]]) -> int:
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
            "form_verified": True,
        }
        added += 1
    return added


def harvest(
    *,
    per_page: int = 1200,
    deadline_s: float = 480.0,
    seed: int | None = None,
    workers: int = 8,
    profile: str = "all",
    shard_index: int = 0,
    shard_count: int = 1,
    rotation: int | None = None,
) -> list[dict[str, str | int]]:
    started = time.monotonic()
    rng = random.Random(seed if seed is not None else int(time.time()))
    by_host: dict[str, dict[str, str | int]] = {}
    shard_count = max(1, int(shard_count))
    shard_index = int(shard_index)
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError(f"shard_index must be between 0 and {shard_count - 1}")
    timeout = httpx.Timeout(
        30.0 if shard_count > 1 else 30.0,
        connect=5.0 if shard_count > 1 else 10.0,
        # CDX pages routinely take 6-15s under load; a 12s read timeout made
        # most fleet slots bail with zero rows and starved the whole feed.
        read=25.0 if shard_count > 1 else 30.0,
        write=8.0 if shard_count > 1 else 10.0,
        pool=8.0 if shard_count > 1 else 10.0,
    )
    limits = httpx.Limits(max_connections=workers * 2, max_keepalive_connections=workers)
    queries = _queries(profile)
    if shard_count == 1:
        rng.shuffle(queries)
    elif not queries:
        return []
    apis = _cdx_apis(
        limit=2 if shard_count > 1 else 4,
        timeout_s=8.0 if shard_count > 1 else 20.0,
    )
    if profile == "longtail" and len(apis) > 1:
        apis = apis[1:] + apis[:1]
    if not apis:
        return []

    def _left() -> float:
        return deadline_s - (time.monotonic() - started)

    with httpx.Client(timeout=timeout, limits=limits, follow_redirects=True) as client:
        page_counts: dict[tuple[str, str], int] = {}
        tasks: list[tuple[str, str, str, int, int]] = []
        if shard_count > 1:
            # A fleet slot owns one query and one moving CDX page slice. This
            # keeps 250 logical shards bounded: each slot makes one count
            # request and one data request, rather than repeating the whole
            # profile. The rotation changes every 30-minute epoch so a slot
            # does not revisit the same alphabetical head on every run.
            query_number = shard_index % len(queries)
            slot_number = shard_index // len(queries)
            selected = [queries[query_number]]
        else:
            selected = queries
            query_number = 0
            slot_number = 0
        for local_number, (wildcard, url_re, weight) in enumerate(selected):
            # Collinfo is newest-first. Fall through to older indexes only when
            # the newest endpoint is unavailable or reports no pages.
            api = ""
            total = 0
            # A logical slot retries the primary index once and falls through
            # to the next index on failure. Only the first API was tried here,
            # so one slow CDX endpoint zeroed every shard in the fleet.
            candidates = apis[:2] if shard_count > 1 else apis[:3]
            for candidate in candidates:
                key = (candidate, wildcard)
                if key not in page_counts:
                    page_counts[key] = _num_pages(
                        client,
                        candidate,
                        wildcard,
                        tries=2,
                    )
                if page_counts[key] > 0:
                    api = candidate
                    total = page_counts[key]
                    break
            if total <= 0:
                logger.info("CDX no pages %s", wildcard)
                continue
            if shard_count > 1:
                epoch = int(rotation if rotation is not None else time.time() // 1800)
                # The relatively prime stride gives each slot a different
                # slice while successive 30-minute runs move through the
                # available page space. Two pages per slot (opposite halves
                # of the index) double the yield of every successful slot.
                stride = max(1, total // 17)
                base = (slot_number + epoch * stride + query_number) % total
                tasks.append((api, wildcard, url_re, base, total))
                tasks.append((api, wildcard, url_re, (base + total // 2) % total, total))
                continue
            # Wide TLDs hold tens of thousands of pages; pull more slices there.
            picks = min(weight * (4 if total > 2000 else 2), total)
            for page in rng.sample(range(total), k=picks):
                tasks.append((api, wildcard, url_re, page, total))
        rng.shuffle(tasks)
        logger.info("CDX plan: %s page request(s) across %s query set(s)", len(tasks), len(queries))

        def _fetch(task: tuple[str, str, str, int, int]) -> tuple[str, int, int, list[str]]:
            api, wildcard, url_re, page, total = task
            if _left() <= 15:
                return wildcard, page, total, []
            params = _base_params(wildcard, url_re) + [
                ("limit", str(per_page)),
                ("page", str(page)),
            ]
            response = _get(client, api, params, timeout_s=45.0)
            if response is None or response.status_code >= 400:
                return wildcard, page, total, []
            return (
                wildcard,
                page,
                total,
                [ln for ln in response.text.splitlines() if ln.startswith("{")],
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch, task): task for task in tasks}
            for future in as_completed(futures):
                try:
                    wildcard, page, total, lines = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.info("CDX task failed: %s", exc)
                    continue
                if not lines:
                    continue
                added = _ingest_lines(lines, by_host)
                logger.info(
                    "CDX %s p%s/%s lines=%s kept=%s total=%s",
                    wildcard,
                    page,
                    total,
                    len(lines),
                    added,
                    len(by_host),
                )
                if _left() <= 10:
                    for pending in futures:
                        pending.cancel()
                    break
        rows = sorted(by_host.values(), key=lambda r: -int(r.get("easy_score") or 0))
        preflight_budget = max(8.0, min(_left(), 45.0 if shard_count > 1 else 90.0))
        if rows and preflight_budget >= 8:
            rows = form_preflight.filter_verified_rows(
                rows,
                client=client,
                deadline_s=preflight_budget,
                workers=8 if shard_count == 1 else 6,
                timeout=7.0 if shard_count > 1 else 8.0,
            )
        else:
            rows = []
        logger.info(
            "Harvest unique verified hosts=%s elapsed=%.0fs",
            len(rows),
            time.monotonic() - started,
        )
        return rows
    return []


def merge_feed(path: Path, rows: list[dict[str, str | int]], *, cap: int = 60000) -> dict:
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
    fresh = 0
    for row in rows:
        host = str(row.get("host") or "")
        url = str(row.get("url") or "")
        if not host or not _keep(url):
            continue
        prev = existing.get(host)
        if prev is None:
            fresh += 1
        elif int(prev.get("easy_score") or 0) > int(row.get("easy_score") or 0):
            continue
        existing[host] = row
    ranked = sorted(existing.values(), key=lambda r: -int(r.get("easy_score") or 0))[:cap]
    logger.info("Feed merge: +%s new host(s), total %s", fresh, len(ranked))
    return {
        "version": 1,
        "source": "commoncrawl-cdx",
        "updated_at": _utc(),
        "count": len(ranked),
        "new_hosts": fresh,
        "urls": ranked,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "feeds" / "ready_queue.json"))
    parser.add_argument("--limit", type=int, default=60000)
    parser.add_argument("--per-page", type=int, default=1200)
    parser.add_argument("--deadline", type=int, default=480)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--rotation", type=int, default=None)
    parser.add_argument(
        "--profile",
        choices=("all", "tr", "global", "eu", "platform", "longtail"),
        default="all",
    )
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str | int]] = []
    try:
        rows = harvest(
            per_page=args.per_page,
            deadline_s=float(args.deadline),
            seed=args.seed,
            workers=max(1, args.workers),
            profile=args.profile,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
            rotation=args.rotation,
        )
    except Exception:
        logger.exception("Harvest aborted — keeping prior feed")
    payload = merge_feed(out, rows, cap=max(500, args.limit))
    tmp = out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"Feed {payload['count']} URL(s) (+{payload.get('new_hosts', 0)} new) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
