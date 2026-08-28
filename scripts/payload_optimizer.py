"""GitHub Actions payload optimizer — fetch/analyze on runner, push to Oracle ingest.

All HTTP fetches and HTML parsing happen here, not on Oracle.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import domain_store  # noqa: E402
import easy_score  # noqa: E402
import payload_builder  # noqa: E402

logger = logging.getLogger(__name__)

FEED_PATH = ROOT / "feeds" / "ready_queue.json"
SHARD_DIR = ROOT / "feeds" / "shards"


def _load_candidates(*, min_score: int) -> list[dict]:
    merged: dict[str, dict] = {}
    sources: list[list[dict]] = []
    if FEED_PATH.exists():
        try:
            payload = json.loads(FEED_PATH.read_text(encoding="utf-8"))
            rows = payload.get("urls") if isinstance(payload, dict) else payload
            sources.append([row for row in (rows or []) if isinstance(row, dict)])
        except json.JSONDecodeError:
            logger.warning("Corrupt ready_queue.json")
    if SHARD_DIR.exists():
        for path in sorted(SHARD_DIR.glob("commoncrawl-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            rows = payload.get("urls") if isinstance(payload, dict) else payload
            sources.append([row for row in (rows or []) if isinstance(row, dict)])

    for rows in sources:
        for row in rows:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            score = int(row.get("easy_score") or easy_score.from_contact_url(url)[0])
            if score < min_score:
                continue
            host = domain_store.host_of(url)
            if not host or domain_store.is_enterprise(url) or domain_store.is_noise(url):
                continue
            prev = merged.get(host)
            item = {
                "url": domain_store.origin_url(url),
                "easy_score": score,
                "source": str(row.get("source") or "public-discovery")[:80],
                "profile": str(row.get("profile") or "")[:40],
            }
            if not prev or int(prev["easy_score"]) < score:
                merged[host] = item
    ranked = sorted(merged.values(), key=lambda row: -int(row["easy_score"]))
    return ranked


def _fetch_one(url: str, *, timeout_s: float) -> tuple[str, str, dict[str, str]] | None:
    headers = {
        "User-Agent": "devsolve-payload-optimizer/1.0 (+contact-form research)",
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
            response = client.get(url)
            if response.status_code >= 400:
                return None
            return url, response.text[:250_000], {k: v for k, v in response.headers.items()}
    except Exception as exc:  # noqa: BLE001
        logger.info("Fetch skip %s (%s)", url, exc)
        return None


def optimize_batch(
    candidates: list[dict],
    *,
    workers: int,
    timeout_s: float,
    limit: int,
) -> list[dict]:
    out: list[dict] = []
    todo = candidates[:limit]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_fetch_one, str(row["url"]), timeout_s=timeout_s): row for row in todo
        }
        for future in as_completed(futures):
            row = futures[future]
            fetched = future.result()
            if not fetched:
                continue
            url, html, hdrs = fetched
            built = payload_builder.build_target(
                url=url,
                html=html,
                headers=hdrs,
                easy_score=int(row["easy_score"]),
                source=str(row.get("source") or "payload-optimizer"),
                profile=str(row.get("profile") or ""),
            )
            if built:
                out.append(built)
    out.sort(key=lambda item: -int(item.get("easy_score") or 0))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=int, default=85)
    parser.add_argument("--limit", type=int, default=96)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--out", default="optimized_batch.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    candidates = _load_candidates(min_score=max(80, int(args.min_score)))
    logger.info("Optimizer candidates score>=%s: %s", args.min_score, len(candidates))
    targets = optimize_batch(
        candidates,
        workers=max(1, min(int(args.workers), 12)),
        timeout_s=float(args.timeout),
        limit=max(1, int(args.limit)),
    )
    payload = {"version": 1, "targets": targets}
    out = Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Optimized {len(targets)} target(s) -> {out}")
    return 0 if targets else 0


if __name__ == "__main__":
    raise SystemExit(main())
