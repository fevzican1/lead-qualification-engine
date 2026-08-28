"""
Processed-domain table + unprocessed lead queue (JSON, no extra DB).

Keeps the agent from re-hitting submitted/CAPTCHA/opt-out sites and gives
discovery a place to drop fresh URLs without touching Chromium.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import config
import optout

logger = logging.getLogger(__name__)

PROCESSED_PATH = config.ROOT / "processed_domains.json"
QUEUE_PATH = config.ROOT / "unprocessed_leads.json"
BUDGET_PATH = config.ROOT / "http_budget.json"

ENTERPRISE = {
    "salesforce.com",
    "hubspot.com",
    "stripe.com",
    "shopify.com",
    "woocommerce.com",
    "zendesk.com",
    "intercom.com",
    "twilio.com",
    "datadoghq.com",
    "snowflake.com",
    "databricks.com",
    "mongodb.com",
    "cloudflare.com",
    "atlassian.com",
    "adobe.com",
    "microsoft.com",
    "google.com",
    "amazon.com",
    "amazon.com.tr",
    "oracle.com",
    "apple.com",
    "meta.com",
    "facebook.com",
    "trendyol.com",
    "hepsiburada.com",
    "n11.com",
    "getir.com",
    "yemeksepeti.com",
    "sahibinden.com",
    "aliexpress.com",
    "ebay.com",
    "sap.com",
    "ibm.com",
    "workday.com",
    "servicenow.com",
    "klaviyo.com",
    "mailchimp.com",
    "sendgrid.com",
    "turkcell.com.tr",
    "vodafone.com.tr",
    "turktelekom.com.tr",
    "lcw.com",
    "lcwaikiki.com",
    "migros.com.tr",
    "carrefoursa.com",
    "mediamarkt.com.tr",
    "arcelik.com.tr",
    "vestel.com.tr",
    "beko.com.tr",
    "teknosa.com",
    "samsung.com",
    "samsung.com.tr",
    "defacto.com.tr",
    "mavi.com",
    "flo.com.tr",
    "gratis.com",
    "boyner.com.tr",
    "vakko.com",
    "beymen.com",
    "vatanbilgisayar.com",
    "sinsay.com.tr",
    "reserved.com.tr",
    "bershka.com.tr",
    "stradivarius.com.tr",
    "pullandbear.com.tr",
    "massimodutti.com.tr",
    "oysho.com.tr",
    "jackjones.com.tr",
    "sephora.com.tr",
    "watsons.com.tr",
    "rossmann.com.tr",
    "karaca.com",
    "englishhome.com",
    "madamecoco.com",
    "pttavm.com",
    "ciceksepeti.com",
    "pazarama.com",
    "kitapyurdu.com",
    "dr.com.tr",
}

NOISE = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "youtu.be",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
    "wikipedia.org",
    "duckduckgo.com",
    "bing.com",
    "google.com",
    "google.com.tr",
    "yahoo.com",
    "yandex.com",
    "yandex.com.tr",
    "baidu.com",
    "github.com",
    "gitlab.com",
    "medium.com",
    "quora.com",
    "googleapis.com",
    "gstatic.com",
    "googleusercontent.com",
    "schema.org",
    "w3.org",
    "jsdelivr.net",
    "unpkg.com",
    "cloudfront.net",
    "fontawesome.com",
    "cookiebot.com",
    "hotjar.com",
    "doubleclick.net",
    "googletagmanager.com",
    "t.me",
    "whatsapp.com",
    "apple.com",
    "1001demo.com.tr",
}

TERMINAL = {
    "submitted",
    "submitted_confirmed",
    "submitted_unconfirmed",
    "skipped_captcha",
    "skipped_no_form",
    "skipped_no_open_form",
    "skipped_unsubscribed",
    "skipped_enterprise",
    "skipped_submit_failed",
    "skipped_unreachable",
}

DEAD_QUEUE = {
    "skipped_captcha",
    "skipped_no_form",
    "skipped_no_open_form",
    "skipped_unreachable",
    "skipped_enterprise",
}
RETRYABLE_NO_SEND = frozenset(
    {"skipped_no_form", "skipped_no_open_form", "skipped_unreachable"}
)
RETRY_COOLDOWN = timedelta(hours=12)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_ts(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        stamp = datetime.fromisoformat(text)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    except ValueError:
        return None


def _queue_cap() -> int:
    return int(getattr(config, "QUEUE_MAX", 250) or 250)


def host_of(url: str) -> str:
    raw = (url or "").strip()
    if raw and "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower()
    return host.removeprefix("www.")


def origin_url(url: str) -> str:
    raw = (url or "").strip()
    if raw and not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def is_enterprise(url: str) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in ENTERPRISE)


def is_noise(url: str) -> bool:
    host = host_of(url)
    if not host or "." not in host:
        return True
    if "demo" in host.split(".")[0] or "1001demo" in host:
        return True
    if re.match(r"^[0-9a-f]{5,12}(?:-[0-9a-z]{1,6})?\.myshopify\.com$", host, re.I):
        return True
    return any(host == d or host.endswith("." + d) for d in NOISE) or host.endswith(".gov") or host.endswith(".gov.tr")


def _load_json(path, default):
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt %s — starting fresh", path.name)
        return default
    return data if isinstance(data, type(default)) else default


def _save_json(path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _processed() -> dict[str, Any]:
    data = _load_json(PROCESSED_PATH, {"domains": {}})
    if "domains" not in data or not isinstance(data["domains"], dict):
        data["domains"] = {}
    return data


def _queue() -> dict[str, Any]:
    data = _load_json(QUEUE_PATH, {"urls": []})
    if "urls" not in data or not isinstance(data["urls"], list):
        data["urls"] = []
    return data


def is_processed(url: str) -> bool:
    host = host_of(url)
    if not host:
        return True
    if is_enterprise(url) or is_noise(url) or optout.is_url_opted_out(url):
        return True
    row = (_processed().get("domains") or {}).get(host)
    return bool(row) and str(row.get("status") or "") != "retrying"


def requeue_if_retryable(url: str) -> bool:
    """Allow one cooldown retry only for hosts where no form was sent."""
    host = host_of(url)
    if not host or is_enterprise(url) or is_noise(url) or optout.is_url_opted_out(url):
        return False
    data = _processed()
    domains = data.get("domains") or {}
    row = domains.get(host)
    if not isinstance(row, dict):
        return False
    status = str(row.get("status") or "")
    if status not in RETRYABLE_NO_SEND or int(row.get("retry_count") or 0) >= 1:
        return False
    stamp = _parse_ts(str(row.get("at") or ""))
    if stamp is not None and datetime.now(timezone.utc) - stamp < RETRY_COOLDOWN:
        return False
    row["status"] = "retrying"
    row["retry_count"] = 1
    row["retry_at"] = utc_now()
    data["domains"] = domains
    data["updated_at"] = utc_now()
    _save_json(PROCESSED_PATH, data)
    return True


def is_deferred(url: str) -> bool:
    """Return whether a queue lease is still active for this host."""
    host = host_of(url)
    if not host:
        return True
    now = datetime.now(timezone.utc)
    for row in _queue().get("urls") or []:
        if not isinstance(row, dict) or host_of(str(row.get("url") or "")) != host:
            continue
        next_try = _parse_ts(str(row.get("next_try") or ""))
        if next_try is not None and next_try > now:
            return True
    return False


def mark(url: str, status: str, *, source: str = "pipeline") -> None:
    host = host_of(url)
    if not host:
        return
    data = _processed()
    data["domains"][host] = {
        "status": status,
        "source": source,
        "at": utc_now(),
        "url": origin_url(url),
    }
    data["updated_at"] = utc_now()
    _save_json(PROCESSED_PATH, data)
    dequeue(url)


def unmark(url: str) -> None:
    """Put a host back in play (used when a submit-map fail is retried with a better filler)."""
    host = host_of(url)
    if not host:
        return
    data = _processed()
    domains = data.get("domains") or {}
    if host not in domains:
        return
    domains.pop(host, None)
    data["domains"] = domains
    data["updated_at"] = utc_now()
    _save_json(PROCESSED_PATH, data)


def hydrate_from_leads(leads: list[dict[str, Any]] | None = None) -> int:
    if leads is None:
        path = config.LEADS_PATH
        if not path.exists():
            return 0
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return 0
        leads = payload if isinstance(payload, list) else []
    n = 0
    data = _processed()
    for item in leads:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        host = host_of(url)
        status = str(item.get("status") or "")
        if not host or status not in TERMINAL:
            continue
        rationale = str(item.get("fit_rationale") or item.get("error") or "").lower()
        if status == "skipped_no_form" and (
            "http probe failed" in rationale or "no chromium" in rationale
        ):
            continue
        if host not in data["domains"]:
            data["domains"][host] = {
                "status": status,
                "source": "leads.json",
                "at": utc_now(),
                "url": origin_url(url),
            }
            n += 1
    if n:
        data["updated_at"] = utc_now()
        _save_json(PROCESSED_PATH, data)
        logger.info("Hydrated %s processed domain(s) from leads.json", n)
    return n


def prune_dead_queue(leads: list[dict[str, Any]] | None = None) -> int:
    """Drop CAPTCHA / no-form hosts from the live queue so HTTP spends on form sites."""
    if leads is None:
        path = config.LEADS_PATH
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = []
            leads = payload if isinstance(payload, list) else []
        else:
            leads = []
    processed = _processed()
    domains = processed.setdefault("domains", {})
    marked = 0
    for item in leads:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        url = str(item.get("url") or "")
        host = host_of(url)
        if status not in DEAD_QUEUE or not host:
            continue
        if status == "skipped_no_form":
            why = str(item.get("fit_rationale") or item.get("error") or "").lower()
            if "http probe failed" in why or "no chromium" in why:
                continue
        existing = domains.get(host)
        if isinstance(existing, dict) and str(existing.get("status") or "") in TERMINAL:
            continue
        domains[host] = {
            "status": status,
            "source": "dead-prune",
            "at": utc_now(),
            "url": origin_url(url),
        }
        marked += 1
    if marked:
        processed["domains"] = domains
        processed["updated_at"] = utc_now()
        _save_json(PROCESSED_PATH, processed)
    data = _queue()
    kept: list[Any] = []
    extra = 0
    for row in data.get("urls") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        if is_processed(url):
            extra += 1
            continue
        kept.append(row)
    if extra:
        data["urls"] = kept
        data["updated_at"] = utc_now()
        _save_json(QUEUE_PATH, data)
    if marked or extra:
        logger.info(
            "Dead-lead prune: marked %s, dequeued %s, depth=%s",
            marked,
            extra,
            len(kept) if extra else queue_depth(),
        )
    return extra


def prune_enterprise_queue() -> int:
    """Drop retail giants that leaked into the catalog — they never buy a $200 bridge."""
    data = _queue()
    keep: list[dict[str, Any]] = []
    dropped = 0
    for row in data.get("urls") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        if is_enterprise(url) or is_noise(url):
            mark(url, "skipped_enterprise", source="catalog-giant")
            dropped += 1
            continue
        keep.append(row)
    if dropped:
        data["urls"] = keep
        data["updated_at"] = utc_now()
        _save_json(QUEUE_PATH, data)
        logger.info("Pruned %s enterprise/noise host(s) from queue", dropped)
    return dropped


def enqueue(url: str, *, source: str = "discovery", easy_score: int = 40) -> bool:
    url = origin_url(url)
    if not url or is_processed(url) or is_enterprise(url) or is_noise(url):
        return False
    if optout.is_url_opted_out(url):
        return False
    data = _queue()
    host = host_of(url)
    for item in data["urls"]:
        if not isinstance(item, dict):
            continue
        if host_of(str(item.get("url") or "")) != host:
            continue
        prev = int(item.get("easy_score") or 0)
        if easy_score > prev:
            item["easy_score"] = int(easy_score)
            data["updated_at"] = utc_now()
            _save_json(QUEUE_PATH, data)
        return False
    if len(data["urls"]) >= _queue_cap():
        if not _evict_low_score(data, below=int(getattr(config, "EASY_SCORE_MIN", 55) or 55)):
            return False
    data["urls"].append(
        {
            "url": url,
            "source": source,
            "authorized_contact": source == "targets.txt",
            "queued_at": utc_now(),
            "fails": 0,
            "easy_score": int(easy_score),
        }
    )
    data["updated_at"] = utc_now()
    _save_json(QUEUE_PATH, data)
    return True


def _evict_low_score(data: dict[str, Any], *, below: int) -> bool:
    worst_i = -1
    worst_score = below
    for index, item in enumerate(data.get("urls") or []):
        if not isinstance(item, dict):
            continue
        score = int(item.get("easy_score") or 0)
        if score < worst_score:
            worst_score = score
            worst_i = index
    if worst_i < 0:
        return False
    data["urls"].pop(worst_i)
    logger.info("Evicted low-score queue row (score=%s) to make room", worst_score)
    return True


def defer(
    url: str,
    *,
    hours: float | None = None,
    reason: str = "http_fail",
    count_fail: bool = True,
    easy_score: int | None = None,
) -> None:
    """Leave the host in queue for a later retry. After 3 counted fails, mark terminal."""
    if hours is None:
        minutes = max(5, int(getattr(config, "DEFER_MINUTES", 20) or 20))
        wait = timedelta(minutes=minutes)
    else:
        wait = timedelta(hours=hours)
    host = host_of(url)
    if not host:
        return
    data = _queue()
    item = None
    rest: list[dict[str, Any]] = []
    for row in data["urls"]:
        if not isinstance(row, dict):
            continue
        if host_of(str(row.get("url") or "")) == host:
            item = row
        else:
            rest.append(row)
    if item is None:
        item = {"url": origin_url(url), "source": "defer", "queued_at": utc_now()}
    fails = int(item.get("fails") or 0)
    if count_fail:
        fails += 1
        if fails >= 3:
            mark(url, "skipped_no_form", source="probe-retry-exhausted")
            return
    nxt = datetime.now(timezone.utc) + wait
    item["fails"] = fails
    item["defer_reason"] = reason
    item["next_try"] = nxt.replace(microsecond=0).isoformat()
    if easy_score is not None:
        item["easy_score"] = int(easy_score)
    data["urls"] = rest + [item]
    data["updated_at"] = utc_now()
    _save_json(QUEUE_PATH, data)


def clamp_long_defers() -> int:
    """Pull next_try back if it is longer than DEFER_MINUTES (old 8h window)."""
    minutes = max(5, int(getattr(config, "DEFER_MINUTES", 20) or 20))
    now = datetime.now(timezone.utc)
    cap = now + timedelta(minutes=minutes)
    data = _queue()
    changed = 0
    for row in data.get("urls") or []:
        if not isinstance(row, dict):
            continue
        nxt = _parse_ts(str(row.get("next_try") or ""))
        if nxt is None or nxt <= cap:
            continue
        row["next_try"] = now.replace(microsecond=0).isoformat()
        changed += 1
    if changed:
        data["updated_at"] = utc_now()
        _save_json(QUEUE_PATH, data)
        logger.info("Clamped %s long defer(s) to now (max %s min)", changed, minutes)
    return changed


def reclaim_false_kills() -> int:
    """HEAD/403 'discovery-dead' marks were not real no-form skips — put them back in play."""
    data = _processed()
    domains = data.get("domains") or {}
    keep: dict[str, Any] = {}
    removed = 0
    for host, row in domains.items():
        source = str((row or {}).get("source") or "") if isinstance(row, dict) else ""
        if source in {"discovery-dead", "probe-retry-exhausted"}:
            removed += 1
            continue
        keep[host] = row
    if removed:
        data["domains"] = keep
        data["updated_at"] = utc_now()
        _save_json(PROCESSED_PATH, data)
        logger.info("Reclaimed %s false-dead domain(s) for requeue", removed)
    return removed


def dequeue(url: str) -> None:
    host = host_of(url)
    data = _queue()
    kept = [
        item
        for item in data["urls"]
        if isinstance(item, dict) and host_of(str(item.get("url") or "")) != host
    ]
    if len(kept) != len(data["urls"]):
        data["urls"] = kept
        data["updated_at"] = utc_now()
        _save_json(QUEUE_PATH, data)


def pending_rows(*, limit: int = 40, min_easy: int = 0, max_easy: int | None = None) -> list[dict[str, Any]]:
    """Ready queue rows, highest easy_score first. Does not dequeue."""
    data = _queue()
    now = datetime.now(timezone.utc)
    seen: set[str] = set()
    ready: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    for item in data["urls"]:
        if not isinstance(item, dict):
            continue
        url = origin_url(str(item.get("url") or ""))
        host = host_of(url)
        if not url or host in seen or is_processed(url):
            continue
        seen.add(host)
        row = {
            "url": url,
            "source": item.get("source") or "queue",
            "queued_at": item.get("queued_at"),
            "fails": int(item.get("fails") or 0),
            "easy_score": int(item.get("easy_score") or 0),
            "authorized_contact": bool(item.get("authorized_contact")),
            "next_try": item.get("next_try"),
            "defer_reason": item.get("defer_reason"),
        }
        nxt = _parse_ts(str(item.get("next_try") or ""))
        if nxt is not None and nxt > now:
            waiting.append(row)
            continue
        ready.append(row)
    ready.sort(
        key=lambda row: (
            -int(row.get("easy_score") or 0),
            int(row.get("fails") or 0),
            str(row.get("queued_at") or ""),
        )
    )
    data["urls"] = ready + waiting
    data["updated_at"] = utc_now()
    _save_json(QUEUE_PATH, data)
    out: list[dict[str, Any]] = []
    for row in ready:
        score = int(row.get("easy_score") or 0)
        if score < int(min_easy or 0):
            continue
        if max_easy is not None and score >= int(max_easy):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def pending_urls(*, limit: int = 40) -> list[str]:
    """Return up to `limit` ready queue URLs without dropping them (pipeline still owns them)."""
    return [str(row["url"]) for row in pending_rows(limit=limit)]


def prune_noise_queue() -> int:
    """Drop demo / hash-Shopify / gov junk already sitting in the live queue."""
    data = _queue()
    keep: list[dict[str, Any]] = []
    dropped = 0
    for row in data.get("urls") or []:
        if not isinstance(row, dict):
            dropped += 1
            continue
        url = str(row.get("url") or "")
        if is_noise(url):
            dropped += 1
            continue
        keep.append(row)
    if dropped:
        data["urls"] = keep
        data["updated_at"] = utc_now()
        _save_json(QUEUE_PATH, data)
        logger.info("Pruned %s noise host(s) from queue; depth=%s", dropped, len(keep))
    return dropped


def evict_below(score: int) -> int:
    """Drop low-score junk so catalog/discovery can refill. No HTTP."""
    floor = max(0, int(score))
    data = _queue()
    keep: list[dict[str, Any]] = []
    dropped = 0
    for row in data.get("urls") or []:
        if not isinstance(row, dict):
            dropped += 1
            continue
        if int(row.get("easy_score") or 0) < floor:
            dropped += 1
            continue
        keep.append(row)
    if dropped:
        data["urls"] = keep
        data["updated_at"] = utc_now()
        _save_json(QUEUE_PATH, data)
        logger.info("Evicted %s queue row(s) below easy_score %s; depth=%s", dropped, floor, len(keep))
    return dropped


def chromium_fuel_count(*, min_easy: int | None = None) -> int:
    """Hosts Chromium can shoot this hour without a new HTTP probe."""
    if min_easy is None:
        min_easy = int(getattr(config, "CHROMIUM_DIRECT_MIN", 65) or 65)
    return len(pending_rows(limit=400, min_easy=int(min_easy)))


def queue_depth() -> int:
    return len(_queue().get("urls") or [])


def ready_pool_size(leads: list[dict[str, Any]] | None = None) -> int:
    """Form-ready or high-score queued hosts the hourly 20 can actually shoot."""
    min_easy = int(getattr(config, "EASY_SCORE_MIN", 55) or 55)
    if leads is None:
        path = config.LEADS_PATH
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = []
            leads = payload if isinstance(payload, list) else []
        else:
            leads = []
    seen: set[str] = set()
    n = 0
    for item in leads:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        url = str(item.get("url") or "")
        host = host_of(url)
        if not host or host in seen:
            continue
        if status.startswith("submitted") or status in TERMINAL:
            continue
        if item.get("captcha_detected"):
            continue
        form_ok = bool((item.get("contact_form") or {}).get("found"))
        score = int(item.get("easy_score") or (85 if form_ok else 0))
        if form_ok and score >= min_easy:
            seen.add(host)
            n += 1
    for row in _queue().get("urls") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        host = host_of(url)
        if not host or host in seen or is_processed(url):
            continue
        if int(row.get("easy_score") or 0) >= int(getattr(config, "CHROMIUM_DIRECT_MIN", 65) or 65):
            seen.add(host)
            n += 1
    return n


def _hour_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")


def _http_state() -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    hour = _hour_key()
    data = _load_json(BUDGET_PATH, {"date": today, "count": 0, "hour": hour, "hour_count": 0})
    if data.get("date") != today:
        data = {"date": today, "count": 0, "hour": hour, "hour_count": 0}
    elif data.get("hour") != hour:
        data["hour"] = hour
        data["hour_count"] = 0
    return data


def http_caps() -> tuple[int, int]:
    daily = int(getattr(config, "DAILY_HTTP_PROBE_LIMIT", 500) or 500)
    hourly = int(getattr(config, "HOURLY_HTTP_PROBE_LIMIT", 22) or 22)
    return daily, hourly


def http_budget_parts() -> tuple[int, int, int, int]:
    """(daily_left, daily_cap, hourly_left, hourly_cap)."""
    daily_cap, hourly_cap = http_caps()
    data = _http_state()
    day_left = max(0, daily_cap - int(data.get("count") or 0))
    hour_left = max(0, hourly_cap - int(data.get("hour_count") or 0))
    return day_left, daily_cap, hour_left, hourly_cap


def http_budget_remaining(*, role: str = "pipeline") -> int:
    day_left, _, hour_left, _ = http_budget_parts()
    left = min(day_left, hour_left)
    if role == "discovery":
        reserve = int(getattr(config, "HTTP_RESERVE_FOR_PIPELINE", 20) or 0)
        return min(max(0, day_left - reserve), hour_left)
    return left


def http_budget_label() -> str:
    day_left, daily_cap, hour_left, hourly_cap = http_budget_parts()
    return f"gün {day_left}/{daily_cap} saat {hour_left}/{hourly_cap}"


def seconds_until_next_utc_hour() -> int:
    now = datetime.now(timezone.utc)
    nxt = (now.replace(minute=0, second=0, microsecond=0)) + timedelta(hours=1)
    return max(90, int((nxt - now).total_seconds()))


def consume_http(n: int = 1, *, role: str = "pipeline") -> bool:
    if n < 1:
        return True
    if http_budget_remaining(role=role) < n:
        return False
    daily_cap, hourly_cap = http_caps()
    data = _http_state()
    used = int(data.get("count") or 0)
    hour_used = int(data.get("hour_count") or 0)
    if used + n > daily_cap or hour_used + n > hourly_cap:
        return False
    data["count"] = used + n
    data["hour_count"] = hour_used + n
    data["updated_at"] = utc_now()
    _save_json(BUDGET_PATH, data)
    return True
