"""
Self-feeding discovery: HTTP only, no Playwright, no Google.

Finds customer domains from platform sitemaps (HEAD then light XML GET),
then HEAD-filters each origin. 403 does not kill a host. Dedups via
domain_store. Stays inside DAILY_HTTP_PROBE_LIMIT.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import httpx

import config
import domain_store
import easy_score
import knowledge
import prefilter

logger = logging.getLogger(__name__)

STATE_PATH = config.ROOT / "discovery_state.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}
HREF_RE = re.compile(r"""href=["']([^"'#]+)""", re.I)
DDG_UDDG_RE = re.compile(r"[?&]uddg=([^&]+)", re.I)
BING_CITE_RE = re.compile(r"<cite[^>]*>([^<]+)</cite>", re.I)
BARE_HOST_RE = re.compile(
    r"(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9-]{1,50}\.(?:com\.tr|com|net|io|co|org|app|be|de|nl))\b",
    re.I,
)
SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
CONTACT_HEAD_PATHS = ("/contact", "/iletisim", "/contact-us", "/bize-ulasin")
SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml")

DEFAULT_QUERIES = (
    "iyzico odeme e-ticaret magaza",
    "IdeaSoft altyapili magaza",
    "T-Soft e-ticaret",
    "Ticimax referans magaza",
    "ikas e-ticaret",
    "B2B tedarikci yazilim entegrasyon",
    "WooCommerce ajans Turkiye",
    "PayTR sanal pos e-ticaret",
    "custom API automation agency",
    "Shopify plus agency mid market",
    "n8n otomasyon ajans Turkiye",
    "Odoo entegrasyon ajans",
    "Laravel ajans Istanbul",
    "e-ticaret yazilim ajansi",
)

DEFAULT_SEEDS = (
    "https://www.ideasoft.com.tr/referanslar",
    "https://www.ideasoft.com.tr/musterilerimiz",
    "https://www.ideasoft.com.tr/basari-hikayeleri",
    "https://www.ticimax.com/referanslar",
    "https://www.ticimax.com/musterilerimiz",
    "https://www.ticimax.com/basari-hikayeleri",
    "https://www.tsoft.com.tr/referanslar",
    "https://ikas.com/tr/musteriler",
    "https://akinon.com",
    "https://www.iyzico.com/is-ortaklari",
    "https://www.paytr.com/musterilerimiz",
    "https://www.shopier.com",
    "https://www.craftgate.io",
    "https://www.logo.com.tr",
    "https://www.parasut.com",
)


def _state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"query_index": 0, "seed_index": 0, "last_run": None}


def _save_state(data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def _b2b_blob() -> dict[str, Any]:
    path = config.ROOT / "knowledge" / "b2b.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _queries() -> list[str]:
    extra = [str(q).strip() for q in (_b2b_blob().get("search_queries") or []) if str(q).strip()]
    return list(dict.fromkeys([*DEFAULT_QUERIES, *extra]))


def _seeds() -> list[str]:
    extra = [str(u).strip() for u in (_b2b_blob().get("seed_pages") or []) if str(u).strip()]
    return list(dict.fromkeys([*DEFAULT_SEEDS, *extra]))


def _queue_target() -> int:
    return int(getattr(config, "QUEUE_TARGET", 150) or 150)


def _queue_max() -> int:
    return int(getattr(config, "QUEUE_MAX", 250) or 250)


def _refill_below() -> int:
    return int(getattr(config, "QUEUE_REFILL_BELOW", 80) or 80)


def _head(client: httpx.Client, url: str) -> int:
    """Return status code. 0 = miss. 403/429 are not treated as dead."""
    if not domain_store.consume_http(1, role="discovery"):
        return -1
    try:
        response = client.head(
            url,
            headers={**HEADERS, "Accept": "*/*"},
            follow_redirects=True,
            timeout=8.0,
        )
        return int(response.status_code or 0)
    except Exception as exc:  # noqa: BLE001
        logger.info("Discovery HEAD failed %s: %s", url, exc)
        return 0


def _head_contact(client: httpx.Client, url: str) -> bool:
    """One GET of /iletisim or /contact. Body must look like a form, not a WAF wall."""
    origin = domain_store.origin_url(url)
    if not origin:
        return False
    host = domain_store.host_of(origin)
    path = "/iletisim" if host.endswith(".tr") else "/contact"
    html = _get(client, origin + path)
    if not html:
        return False
    if prefilter.CAPTCHA_RE.search(html):
        return False
    return bool(prefilter.FORM_HINT_RE.search(html))


def _head_alive(client: httpx.Client, url: str) -> bool:
    """Keep 403 (old HEAD-wipe bug). Drop only clear 404/410."""
    origin = domain_store.origin_url(url)
    if not origin:
        return False
    code = _head(client, origin)
    if code == -1:
        return False
    if code in {404, 410}:
        logger.info("HEAD skip %s HTTP %s", origin, code)
        return False
    return True


def _get(client: httpx.Client, url: str) -> str:
    if not domain_store.consume_http(1, role="discovery"):
        logger.info("HTTP budget exhausted — discovery stops")
        return ""
    import risk_guard

    def _fetch():
        return client.get(url, headers=HEADERS, follow_redirects=True, timeout=15.0)

    try:
        response = risk_guard.call_once_retry(_fetch)
        if response.status_code >= 400:
            return ""
        return (response.text or "")[:250_000]
    except Exception as exc:  # noqa: BLE001
        logger.info("Discovery GET failed %s: %s", url, exc)
        return ""


def _clean_href(href: str, base: str = "") -> str:
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "tel:")):
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if base and not href.lower().startswith("http"):
        href = urljoin(base, href)
    match = DDG_UDDG_RE.search(href)
    if match:
        href = unquote(match.group(1))
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return domain_store.origin_url(href)


def _extract_links(html: str, base: str = "") -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in HREF_RE.findall(html or ""):
        url = _clean_href(raw, base)
        host = domain_store.host_of(url)
        if not url or not host or host in seen:
            continue
        seen.add(host)
        found.append(url)
    for match in BARE_HOST_RE.findall(html or ""):
        url = domain_store.origin_url("https://" + match)
        host = domain_store.host_of(url)
        if not url or not host or host in seen:
            continue
        if domain_store.is_noise(url) or domain_store.is_enterprise(url):
            continue
        seen.add(host)
        found.append(url)
    return found


def harvest_sitemap(client: httpx.Client, page_url: str) -> list[str]:
    origin = domain_store.origin_url(page_url)
    if not origin:
        return []
    xml = ""
    for path in SITEMAP_PATHS:
        sm = origin + path
        code = _head(client, sm)
        if code == -1:
            return []
        if code not in {200, 301, 302, 303, 405}:
            continue
        xml = _get(client, sm)
        if xml:
            break
    if not xml:
        return []
    locs = [ _clean_href(raw, origin) for raw in SITEMAP_LOC_RE.findall(xml) ]
    if "<sitemapindex" in xml[:2000].lower():
        extra: list[str] = []
        for child in [u for u in locs if u][:2]:
            body = _get(client, child)
            extra.extend(_clean_href(raw, child) for raw in SITEMAP_LOC_RE.findall(body or ""))
        locs.extend(extra)
    base_host = domain_store.host_of(origin)
    out: list[str] = []
    seen: set[str] = set()
    for url in locs:
        host = domain_store.host_of(url)
        if not url or not host or host == base_host or host in seen:
            continue
        if domain_store.is_noise(url) or domain_store.is_enterprise(url):
            continue
        seen.add(host)
        out.append(url)
        if len(out) >= 40:
            break
    logger.info("Sitemap %s yielded %s external domain(s)", origin, len(out))
    return out


def harvest_seed(client: httpx.Client, page_url: str, *, light: bool = True) -> list[str]:
    """Listing pages (referanslar) beat sitemaps: one GET, many customer hosts, no extra HEAD."""
    if not light:
        mapped = harvest_sitemap(client, page_url)
        if mapped:
            return mapped
    html = _get(client, page_url)
    if not html:
        return []
    base_host = domain_store.host_of(page_url)
    out: list[str] = []
    for url in _extract_links(html, page_url):
        host = domain_store.host_of(url)
        if host == base_host or domain_store.is_noise(url) or domain_store.is_enterprise(url):
            continue
        out.append(url)
    logger.info("Seed %s yielded %s external domain(s)", page_url, len(out))
    return out


def search_bing(client: httpx.Client, query: str) -> list[str]:
    from urllib.parse import quote_plus

    html = _get(client, f"https://www.bing.com/search?q={quote_plus(query)}")
    if not html:
        return []
    links = _extract_links(html, "https://www.bing.com/search")
    for cite in BING_CITE_RE.findall(html):
        text = cite.strip()
        cleaned = _clean_href(text if "://" in text else "https://" + text)
        if cleaned:
            links.append(cleaned)
    logger.info("Bing %r -> %s link(s)", query, len(links))
    return links


def search_duckduckgo(client: httpx.Client, query: str) -> list[str]:
    url = "https://html.duckduckgo.com/html/"
    if not domain_store.consume_http(1, role="discovery"):
        return []
    try:
        response = client.post(
            url,
            data={"q": query},
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=True,
            timeout=15.0,
        )
        html = (response.text or "")[:250_000] if response.status_code < 400 else ""
    except Exception as exc:  # noqa: BLE001
        logger.info("DDG failed for %r: %s", query, exc)
        html = ""
    links = _extract_links(html, url) if html else []
    if links:
        logger.info("DDG %r -> %s link(s)", query, len(links))
        return links
    return search_bing(client, query)


def run_discovery(
    *,
    max_new: int | None = None,
    queries_this_run: int = 4,
    seeds_this_run: int = 8,
    require_contact: bool = False,
) -> int:
    domain_store.hydrate_from_leads()
    room = max(0, _queue_max() - domain_store.queue_depth())
    if max_new is None:
        max_new = room or 20
    elif room <= 0:
        max_new = min(max_new, 20)
    else:
        max_new = min(max_new, room)
    if max_new <= 0:
        max_new = 12
    if domain_store.http_budget_remaining(role="discovery") < 2:
        logger.info("HTTP budget too low for discovery")
        return 0

    state = _state()
    queries = _queries()
    seeds = _seeds()
    q_index = int(state.get("query_index") or 0)
    s_index = int(state.get("seed_index") or 0)
    added = 0
    candidates: list[str] = []

    with httpx.Client() as client:
        for _ in range(min(seeds_this_run, max(len(seeds), 1))):
            if not seeds or domain_store.http_budget_remaining(role="discovery") < 2:
                break
            if added + (len(candidates) // 2) >= max_new:
                break
            page = seeds[s_index % len(seeds)]
            s_index += 1
            candidates.extend(harvest_seed(client, page, light=True))
            time.sleep(0.8)

        for _ in range(min(queries_this_run, max(len(queries), 1))):
            if require_contact:
                break
            if not queries or domain_store.http_budget_remaining(role="discovery") < 6:
                break
            if added + (len(candidates) // 2) >= max_new:
                break
            query = queries[q_index % len(queries)]
            q_index += 1
            candidates.extend(search_duckduckgo(client, query))
            time.sleep(2.4)

        seen: set[str] = set()
        for url in candidates:
            if added >= max_new:
                break
            host = domain_store.host_of(url)
            if not host or host in seen or domain_store.is_processed(url):
                continue
            seen.add(host)
            if require_contact:
                if not _head_contact(client, url):
                    continue
                score = 88
            else:
                if not _head_alive(client, url):
                    continue
                score = easy_score.from_head(
                    contact_ok=False, origin_ok=True, sitemap_ok=False
                )
            if domain_store.enqueue(url, source="discovery", easy_score=score):
                added += 1
                logger.info("Queued %s score=%s", url, score)

    state["query_index"] = q_index
    state["seed_index"] = s_index
    state["last_run"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    state["last_added"] = added
    _save_state(state)
    logger.info(
        "Discovery added %s URL(s); queue=%s target=%s http_budget_left=%s",
        added,
        domain_store.queue_depth(),
        _queue_target(),
        domain_store.http_budget_remaining(role="discovery"),
    )
    return added


def seconds_since_last_run() -> float:
    raw = _state().get("last_run")
    if not raw:
        return 10**9
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
    except ValueError:
        return 10**9


def should_run() -> bool:
    """Hunt when the easy/ready pool is thin. Depth of junk does not block a heal."""
    ready = domain_store.ready_pool_size()
    floor = int(getattr(config, "READY_QUEUE_FLOOR", 50) or 50)
    target = int(getattr(config, "READY_QUEUE_TARGET", 100) or 100)
    if ready >= target:
        return False
    if ready < floor and domain_store.http_budget_remaining(role="discovery") >= 2:
        return True
    elapsed = seconds_since_last_run()
    depth = domain_store.queue_depth()
    if depth >= _queue_target() and ready >= floor:
        return False
    if depth < _refill_below() and elapsed >= 90:
        return True
    if depth < _queue_target() and elapsed >= 1_800:
        return True
    return False


def heal_queue() -> int:
    """Cheap refill: listing-page GET + one contact HEAD. No Bing, no 4-path HEAD."""
    fuel = domain_store.chromium_fuel_count()
    target = domain_store.chromium_fuel_target()
    if fuel >= target:
        return 0
    need = max(12, target - fuel + 8)
    logger.info("Self-heal: chromium_fuel %s < %s — seed HTML + 1x contact HEAD", fuel, target)
    return run_discovery(
        max_new=need,
        queries_this_run=0,
        seeds_this_run=10,
        require_contact=True,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import os

    os.chdir(config.ROOT)
    knowledge.reload_overlays()
    added = run_discovery()
    print(f"Queued {added} new domain(s). Depth={domain_store.queue_depth()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
