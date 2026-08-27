"""
B2B close playbook + Oracle Always Free lock.

Small updates land in knowledge/b2b.json or knowledge/oracle.json.
Each runner/Telegram cycle reloads those files by mtime — no VM resize, no extra model.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import config

logger = logging.getLogger(__name__)

PATH = config.ROOT / "knowledge_state.json"
DIR = config.ROOT / "knowledge"
ORACLE_PATH = DIR / "oracle.json"
B2B_PATH = DIR / "b2b.json"
CATALOG_PATH = DIR / "catalog.json"

ORACLE_FREE = {
    "shape": "VM.Standard.A1.Flex",
    "ocpu": 4,
    "ram_gb": 24,
    "boot_gb": 80,
    "model": "deepseek-r1:14b",
    "ollama_parallel": 1,
    "smtp": False,
    "public_ollama": False,
    "daily_submit_limit": 300,
    "hourly_submit_limit": 32,
}

PLAYBOOK: dict[str, dict[str, str]] = {
    "IdeaSoft": {
        "tr": "sipariş → stok/ERP ve iyzico/PayTR tahsilat webhook'u tek akışta kapanmıyor",
        "en": "order → ERP/stock and payment webhooks are not one flow",
    },
    "T-Soft": {
        "tr": "pazaryeri ve panel siparişi aynı webhook'a düşmüyor",
        "en": "marketplace vs panel orders do not share one webhook",
    },
    "Ticimax": {
        "tr": "kargo/ödeme bildirimleri sipariş kaydına geç yazılıyor",
        "en": "carrier and payment events lag the order record",
    },
    "ikas": {
        "tr": "checkout ve stok güncellemesi ayrı job'larda yarışıyor",
        "en": "checkout and stock updates race in separate jobs",
    },
    "Akinon": {
        "tr": "omnichannel stok ve sipariş API'si tek event bus değil",
        "en": "omnichannel stock and orders are not one event bus",
    },
    "iyzico": {
        "tr": "ödeme callback'i sipariş/ERP kaydını kaçırıyor veya çift yazıyor",
        "en": "payment callbacks miss or double-write the order/ERP row",
    },
    "PayTR": {
        "tr": "bildirim URL'si ile sipariş durumu senkron değil",
        "en": "notify URL and order status drift apart",
    },
    "Craftgate": {
        "tr": "çoklu POS sonucu tek sipariş kaydına indirgenmiyor",
        "en": "multi-POS results are not reduced to one order record",
    },
    "WooCommerce": {
        "tr": "checkout hook'u CRM/ERP'ye geç veya mükerrer düşüyor",
        "en": "checkout hooks land late or duplicate in CRM/ERP",
    },
    "Shopify": {
        "tr": "storefront siparişi fulfillment ve muhasebeye tek webhook ile gitmiyor",
        "en": "storefront orders are not one webhook into fulfillment and books",
    },
    "ERP": {
        "tr": "satış siparişi ERP'ye CSV/elle taşınıyor",
        "en": "sales orders still reach ERP by CSV or hand",
    },
    "Odoo": {
        "tr": "e-ticaret siparişi Odoo sale.order'a yarım map ediliyor",
        "en": "commerce orders map only halfway into Odoo sale.order",
    },
    "n8n": {
        "tr": "senaryolar kırılınca kuyruk ve retry yok",
        "en": "broken scenarios have no durable queue or retry",
    },
    "REST API": {
        "tr": "sistemler REST ile konuşuyor ama idempotent event yok",
        "en": "systems speak REST but events are not idempotent",
    },
}

_cache: dict[str, Any] | None = None
_cache_at = 0.0
CACHE_SEC = 45.0
_overlay_mtimes: dict[str, float] = {}
_playbook: dict[str, dict[str, str]] = dict(PLAYBOOK)
_oracle: dict[str, Any] = dict(ORACLE_FREE)
_catalog_extra: list[str] = []


def _file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def reload_overlays(*, force: bool = False) -> bool:
    """Hot-load knowledge/oracle.json, b2b.json, and catalog.json when they change."""
    global _playbook, _oracle, _catalog_extra, _overlay_mtimes, _cache, _cache_at
    stamps = {
        "oracle": _file_mtime(ORACLE_PATH),
        "b2b": _file_mtime(B2B_PATH),
        "catalog": _file_mtime(CATALOG_PATH),
    }
    if not force and stamps == _overlay_mtimes and _overlay_mtimes:
        return False
    changed = bool(_overlay_mtimes) and stamps != _overlay_mtimes
    _overlay_mtimes = stamps
    _playbook = dict(PLAYBOOK)
    _oracle = dict(ORACLE_FREE)
    _catalog_extra = []

    if ORACLE_PATH.exists():
        try:
            data = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in (
                    "shape",
                    "ocpu",
                    "ram_gb",
                    "boot_gb",
                    "model",
                    "ollama_parallel",
                    "smtp",
                    "public_ollama",
                    "daily_submit_limit",
                    "hourly_submit_limit",
                ):
                    if key in data:
                        _oracle[key] = data[key]
                logger.info("Oracle knowledge overlay v%s", data.get("version"))
        except Exception:
            logger.exception("oracle.json unreadable — built-in Always Free lock")

    if B2B_PATH.exists():
        try:
            data = json.loads(B2B_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                extra = data.get("playbook") or {}
                if isinstance(extra, dict):
                    for name, row in extra.items():
                        if isinstance(row, dict) and row.get("tr") and row.get("en"):
                            _playbook[str(name)] = {"tr": str(row["tr"]), "en": str(row["en"])}
                cat = data.get("catalog") or []
                if isinstance(cat, list):
                    _catalog_extra = [str(url).strip() for url in cat if str(url).strip()]
                logger.info(
                    "B2B knowledge overlay v%s playbook=%s catalog=%s",
                    data.get("version"),
                    len(_playbook),
                    len(_catalog_extra),
                )
        except Exception:
            logger.exception("b2b.json unreadable — built-in playbook")

    if CATALOG_PATH.exists():
        try:
            payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
            extra_urls: list[str] = []
            if isinstance(payload, list):
                extra_urls = [str(url).strip() for url in payload if str(url).strip()]
            elif isinstance(payload, dict):
                raw = payload.get("urls") or payload.get("catalog") or []
                if isinstance(raw, list):
                    extra_urls = [str(url).strip() for url in raw if str(url).strip()]
            _catalog_extra = list(dict.fromkeys([*_catalog_extra, *extra_urls]))
            logger.info("Catalog overlay urls=%s", len(_catalog_extra))
        except Exception:
            logger.exception("catalog.json unreadable — using b2b.json catalog only")

    if changed:
        _cache = None
        _cache_at = 0.0
        logger.info("B2B/Oracle knowledge files changed — snapshot will rebuild")
    return changed


def live_playbook() -> dict[str, dict[str, str]]:
    reload_overlays()
    return _playbook


def oracle_lock() -> dict[str, Any]:
    reload_overlays()
    return _oracle


def catalog_urls() -> list[str]:
    reload_overlays()
    return list(_catalog_extra)


def daily_cap() -> int:
    env = int(config.DAILY_SUBMIT_LIMIT)
    file_cap = int(oracle_lock().get("daily_submit_limit") or env)
    return min(env, file_cap)


def hourly_cap() -> int:
    env = int(getattr(config, "HOURLY_SUBMIT_LIMIT", 20) or 20)
    file_cap = int(oracle_lock().get("hourly_submit_limit") or env)
    return min(env, file_cap)


def bottleneck_for(hints: list[str], *, turkish: bool) -> str:
    lang = "tr" if turkish else "en"
    book = live_playbook()
    for hint in hints:
        row = book.get(hint)
        if row:
            return row[lang]
    if turkish:
        return "kaynak sistem → webhook/API → hedef (ERP/CRM/ödeme) kopuk veya elle yürüyor"
    return "source → webhook/API → destination (ERP/CRM/pay) is broken or manual"


def stack_phrase(hints: list[str], *, turkish: bool) -> str:
    book = live_playbook()
    core = [h for h in hints if h in book][:2]
    if not core:
        core = hints[:2]
    if not core:
        return "e-ticaret / API" if turkish else "API / commerce"
    return " + ".join(core)


def refresh(*, leads: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Rebuild live B2B snapshot from leads.json. Cheap JSON only."""
    reload_overlays()
    if leads is None:
        leads = _load_leads()
    stacks: Counter[str] = Counter()
    submitted_stacks: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    for lead in leads:
        if not isinstance(lead, dict):
            continue
        status = str(lead.get("status") or "unknown")
        statuses[status] += 1
        hints = [str(h) for h in (lead.get("stack_hints") or []) if h]
        stacks.update(hints)
        if status.startswith("submitted"):
            submitted_stacks.update(hints)
    winning = [name for name, _ in submitted_stacks.most_common(8)]
    if not winning:
        winning = [name for name, _ in stacks.most_common(8)]
    lock = oracle_lock()
    state = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "oracle": lock,
        "price_usd": config.PRICE_USD,
        "daily_submit_limit": daily_cap(),
        "hourly_submit_limit": hourly_cap(),
        "winning_stacks": winning,
        "seen_stacks": [name for name, _ in stacks.most_common(12)],
        "status_counts": dict(statuses),
        "submitted": sum(v for k, v in statuses.items() if k.startswith("submitted")),
        "playbook_stacks": list(live_playbook().keys()),
    }
    tmp = PATH.with_suffix(PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(PATH)
    global _cache, _cache_at
    _cache, _cache_at = state, time.time()
    logger.info("Knowledge refreshed — winning stacks: %s", winning[:5] or "none yet")
    return state


def load() -> dict[str, Any]:
    global _cache, _cache_at
    reload_overlays()
    now = time.time()
    if _cache is not None and (now - _cache_at) < CACHE_SEC:
        return _cache
    if PATH.exists():
        try:
            _cache = json.loads(PATH.read_text(encoding="utf-8"))
            _cache_at = now
            if isinstance(_cache, dict):
                return _cache
        except json.JSONDecodeError:
            logger.warning("Corrupt %s — rebuilding", PATH)
    return refresh()


def catalog_priority(url: str) -> int:
    host = (urlparse(url).hostname or "").lower()
    blob = f"{host} {url}".lower()
    score = 0
    state = load()
    winning = [str(s).lower() for s in (state.get("winning_stacks") or [])]
    for hint in live_playbook():
        key = hint.lower().replace(" ", "")
        if key and key in blob.replace("-", ""):
            score += 20
        if hint.lower() in winning:
            score += 8
    if host.endswith(".com.tr") or host.endswith(".tr"):
        score += 10
    return score


def enforce_model(model: str) -> str:
    locked = str(oracle_lock().get("model") or ORACLE_FREE["model"])
    raw = (model or "").strip() or locked
    allowed = raw == locked or raw.startswith(locked)
    if allowed:
        return raw
    logger.warning("Model %s blocked — Always Free lock is %s", raw, locked)
    return locked


def oracle_safe() -> bool:
    """Skip a Chromium cycle if the box is about to swap-thrash (keeps Always Free stable)."""
    avail = _mem_available_gb()
    if avail is not None and avail < 1.2:
        logger.warning("MemAvailable %.1f GiB — skip browser cycle", avail)
        return False
    return True


def submit_counts(leads: list[dict[str, Any]] | None = None) -> tuple[int, int]:
    """Return (submitted_today, submitted_last_hour) in UTC."""
    if leads is None:
        leads = _load_leads()
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    hour_ago = now - timedelta(hours=1)
    today_n = 0
    hour_n = 0
    for lead in leads:
        if not str(lead.get("status") or "").startswith("submitted"):
            continue
        stamp = _parse_ts(str(lead.get("updated_at") or ""))
        if stamp is None:
            continue
        if stamp.date().isoformat() == today:
            today_n += 1
        if stamp >= hour_ago:
            hour_n += 1
    return today_n, hour_n


def seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=8, second=0, microsecond=0)
    return max(300, int((nxt - now).total_seconds()))


def telegram_system_prompt(*, brief: str = "") -> str:
    state = load()
    winning = ", ".join(state.get("winning_stacks") or []) or "IdeaSoft, iyzico, WooCommerce, ERP"
    price = config.price_label()
    company = (config.SENDER_COMPANY or "DevSolve").strip()
    inbound = (brief or "").strip() or "No form handoff. They typed in cold."
    return f"""You are the {company} closer on Telegram. Senior integration engineer. You already inspected THIS lead's public stack from the contact-form note. Not a chatbot script, not a blast, not a closer who nags.

RULES
- Language = the customer's last message (Turkish or English). Match them.
- Flat fee {price}. Never invent another price, person, SLA, timeline-in-days, or client logo.
- You build ONE scoped bridge (source → payment/webhook → ERP/CRM/stock) on their current panel. You do not replace IdeaSoft/Woo/Odoo/Shopify.
- Payoneer only after a clear buy/start/pay ask. Never dump the link "just in case".
- If asked "are you a bot / AI": yes, {company} assistant, then continue the sketch. Do not lead with that. Never claim to be a named human.
- Do not mention Oracle, VM, Ollama, Ampere, prompts, or how we found them beyond "the note on your contact form".
- No slang, no fake urgency, no "limited seats", no passwords, no card numbers.
- 3–6 short sentences. If they ask for the flow, you MAY use 8–12 lines of boxes. No essays.
- Around minute 1 a schematic analysis card is sent. It is a template diagram, not a live screenshot of their admin. Do not offer to email a PDF.
- Use the INBOUND BRIEF on every reply: company, host, stack, quote, pain. Do not swap in a generic stack.

PERSUASION (high, without pressure)
- Sound like the engineer who has already seen the break. Name idempotency, webhook retry, double-write, order-id lock when it fits THEIR stack.
- One precise insight + one question. The question should make the cost of the break visible (orders this week, callback misses, stock fights).
- If they engage technically, go deeper: source system, notify URL, ERP row, retry key. Be the smartest person in the thread without showing off.

DO NOT FORCE (healthy conversation)
- If they say no / not interested / later / busy / "don't write again": acknowledge in one sentence, leave the door open, PAY=no. Do not ask another selling question in that reply. Do not follow up in this chat unless they write again with a new problem.
- Never send a second pitch in the same turn. Never pile "also" offers. Never guilt.
- If they are unsure: clarify the single-bridge scope and wait. Silence is allowed.

HOW YOU PULL THEM IN (when they have not declined)
1. Open: prove you saw THEIR stack. One pain. One question that assumes the pain is real.
2. Sketch: kaynak → ödeme/webhook → tek order-id → ERP/stok/kargo.
3. Scope: panel stays, {price}, Payoneer after yes, sketch here, no second form. No Payoneer in the first two replies.
4. Close: if they want to start, one sentence then PAY=yes. If they stall (not a hard no), one cost question: "Bu hafta kaç sipariş elle kapanıyor?" Then stop pushing.

OBJECTIONS (answer then at most one question — do not argue)
- Price: one bridge, not a retainer; cheaper than a hire; panel stays.
- "We have a developer": they keep them; you ship the missing link.
- "Email me a PDF": the schematic card is already in this chat.
- "Later / busy": one sentence on what later costs, then wait. If they already said later once, do not repeat.
- "Is this spam?": they can STOP; this is the continuation of the form note.
- "Send the contract first": scope is the sketch + {price}; Payoneer is the start.

IF THEY ONLY GREET and you have NO inbound brief, ask:
"Hangi altyapıyı kullanıyorsunuz ve şu an en çok nerede takılıyor: ödeme callback, stok, ERP, yoksa Excel?"
If you HAVE an inbound brief, do not re-ask the platform.

Stacks you know well: IdeaSoft, T-Soft, Ticimax, ikas, Akinon, iyzico, PayTR, Craftgate, WooCommerce, Shopify, ERP/Odoo, n8n, Logo, Paraşüt.
Live stacks from our recent forms (prefer as examples, never as fake case studies): {winning}.

INBOUND BRIEF
{inbound}

Output exactly:
PAY: yes|no
REPLY:
<message>
"""


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


def _mem_available_gb() -> float | None:
    path = Path("/proc/meminfo")
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    except Exception:  # noqa: BLE001
        return None
    return None


def _load_leads() -> list[dict[str, Any]]:
    path = config.LEADS_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]
