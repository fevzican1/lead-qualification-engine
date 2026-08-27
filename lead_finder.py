"""
Discover mid-market B2B sites that can actually receive a $200 partnership note.

Enterprise giants (Salesforce, Stripe, Shopify HQ) are excluded: they do not
buy scoped integration work from a contact form.

Catalog URLs are enqueued with zero HTTP. Alive-checks belong to the pipeline
prefilter so the daily probe budget is not burned filling the queue.
"""

from __future__ import annotations

import logging
import sys
from urllib.parse import urlparse

import config
import domain_store
import knowledge

logger = logging.getLogger(__name__)

# Agencies, mid-SaaS, e-commerce platforms, and ops tools with public contact pages.
CATALOG = (
    "https://www.thoughtbot.com",
    "https://www.tighten.com",
    "https://www.spatie.be",
    "https://www.testdouble.com",
    "https://www.dockyard.com",
    "https://www.hashrocket.com",
    "https://www.planetargon.com",
    "https://www.lullabot.com",
    "https://www.humanmade.com",
    "https://www.10up.com",
    "https://www.rtcamp.com",
    "https://www.xwp.co",
    "https://www.multidots.com",
    "https://www.happycog.com",
    "https://www.simplethread.com",
    "https://www.savaslabs.com",
    "https://ohdear.app",
    "https://www.honeybadger.io",
    "https://www.appsignal.com",
    "https://www.rollbar.com",
    "https://www.bugsnag.com",
    "https://www.scoutapm.com",
    "https://ploi.io",
    "https://gridpane.com",
    "https://spinupwp.com",
    "https://www.kinsta.com",
    "https://www.nexcess.net",
    "https://www.cloudways.com",
    "https://statamic.com",
    "https://filamentphp.com",
    "https://www.pixelunion.net",
    "https://www.fluorescent.co",
    "https://www.archetype-themes.com",
    "https://www.clean-canvas.com",
    "https://www.ideasoft.com.tr",
    "https://www.tsoft.com.tr",
    "https://www.ticimax.com",
    "https://ikas.com",
    "https://akinon.com",
    "https://www.iyzico.com",
    "https://www.kolayik.com",
    "https://www.param.com.tr",
    "https://www.wisersell.com",
    "https://www.insider.com",
    "https://www.chromatic.com",
    "https://www.raycast.com",
    "https://linear.app",
    "https://www.cron.com",
    "https://www.parasut.com",
    "https://www.logo.com.tr",
    "https://www.paytr.com",
    "https://www.craftgate.io",
    "https://www.shopier.com",
    "https://n8n.io",
    "https://www.softr.io",
)


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def find_leads(batch: int | None = None) -> list[str]:
    """Enqueue catalog domains until QUEUE_MAX. No HTTP — that is the point."""
    cap = int(getattr(config, "QUEUE_MAX", 250) or 250)
    depth = domain_store.queue_depth()
    need = max(0, cap - depth)
    if batch is not None:
        need = min(need, max(0, int(batch)))
    if need <= 0:
        logger.info("Queue already at %s/%s — catalog skip", depth, cap)
        return []

    catalog = list(dict.fromkeys([*CATALOG, *knowledge.catalog_urls()]))
    catalog.sort(key=lambda url: -knowledge.catalog_priority(url))
    added: list[str] = []
    for raw in catalog:
        if len(added) >= need:
            break
        url = _normalize_url(raw)
        host = _domain(url)
        if not host:
            continue
        if domain_store.enqueue(url, source="catalog", easy_score=68):
            added.append(url)

    if added:
        existing = ""
        if config.TARGETS_PATH.exists():
            existing = config.TARGETS_PATH.read_text(encoding="utf-8")
            if existing and not existing.endswith("\n"):
                existing += "\n"
        known = {_domain(line) for line in existing.splitlines() if line.strip() and not line.startswith("#")}
        with config.TARGETS_PATH.open("a", encoding="utf-8") as handle:
            if not existing:
                handle.write("# Mid-market B2B targets with reachable contact pages\n")
            for url in added:
                if _domain(url) not in known:
                    handle.write(url + "\n")
        logger.info("Catalog enqueued %s URL(s); queue=%s/%s", len(added), domain_store.queue_depth(), cap)
    else:
        logger.info("No new catalog targets this round (all known or processed)")
    return added


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import os

    os.chdir(config.ROOT)
    knowledge.reload_overlays()
    domain_store.hydrate_from_leads()
    domain_store.reclaim_false_kills()
    added = find_leads()
    print(f"Added {len(added)} URL(s) to queue. Depth={domain_store.queue_depth()}")
    for url in added[:25]:
        print(f"  {url}")
    if len(added) > 25:
        print(f"  ... {len(added) - 25} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
