"""
Runtime configuration loaded from environment variables and an optional `.env` file.

Required variables depend on which entrypoint you run:

- `pipeline.py` needs a local Ollama daemon (`OLLAMA_MODEL`, default deepseek-r1:14b)
- This host never sends SMTP/cold email. Outbound is Telegram + authorized contact forms only.
- `pipeline.py --submit` also needs sender identity and Telegram username
- `telegram_sales_bot.py` needs `TELEGRAM_BOT_TOKEN` and `PAYONEER_PAYMENT_URL`
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # Actions/feed jobs run without dotenv
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

try:
    import pip_system_certs.wrapt_requests as _wrapt_certs  # use Windows CA store (SSL inspection)
    _ = _wrapt_certs  # retain import side effects
except Exception:
    pass

_playwright_browsers = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
if _playwright_browsers:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _playwright_browsers


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _get_float(name: str, default: float) -> float:
    raw = _get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be a number, got {raw!r}") from exc


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def require(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = _get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            f"Copy .env.example to .env and fill in your values."
        )
    return value


# --- Secrets / endpoints -------------------------------------------------
# NOT: TELEGRAM_BOT_TOKEN tam "ID:SECRET" formatında tutulur; id'siz (":" öneksiz)
# değer asla yazılmaz — Telegram o formu 404 ile reddeder. Bazen env'ye kısaltılmış
# hali düşerse aşağıdaki normalizasyon ilk ':' öncesindeki bot kimliğiyle tamamlar.
def _full_bot_token(raw: str) -> str:
    tok = (raw or "").strip()
    if not tok or ":" in tok:
        return tok
    primary_id = _get("TELEGRAM_OWNER_CHAT_ID")
    if primary_id.isdigit():
        return f"{primary_id}:{tok}"
    return tok


TELEGRAM_BOT_TOKEN: str = _full_bot_token(_get("TELEGRAM_BOT_TOKEN"))
# Ek satış botları: Telegram bot başına flood limiti olduğu için yük dağıtımı
# şart. "token1,token2,..." (virgülle ayrık); birincil token otomatik başa alınır.
TELEGRAM_BOT_TOKENS: str = _get("TELEGRAM_BOT_TOKENS")
PAYONEER_PAYMENT_URL: str = _get("PAYONEER_PAYMENT_URL")
TELEGRAM_OWNER_CHAT_ID: str = _get("TELEGRAM_OWNER_CHAT_ID")
# Owner self-service registration secret: /admin KOD (set out-of-band on Oracle).
ADMIN_CODE: str = _get("ADMIN_CODE")
# Ops channel: pipeline / sıcak lead bildirimleri (müşteri satış botundan ayrı).
TELEGRAM_NOTIFY_BOT_TOKEN: str = _get("TELEGRAM_NOTIFY_BOT_TOKEN")
TELEGRAM_NOTIFY_CHAT_ID: str = _get("TELEGRAM_NOTIFY_CHAT_ID")

# --- Institutional review-lab frame (zero-cost; presentation only) -------
# Public open-standard references + a deterministic Review ID. This is an
# engineering-analysis framing, never a claim of certification/auditor status.
AUDIT_LAB_NAME: str = _get("AUDIT_LAB_NAME", "DevSolve Flow Inspector")
AUDIT_REPORT_PREFIX: str = _get("AUDIT_REPORT_PREFIX", "DS")
AUDIT_REPORT_YEAR: int = _get_int("AUDIT_REPORT_YEAR", 2026)
OLLAMA_HOST: str = _get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL: str = _get("OLLAMA_MODEL", "deepseek-r1:14b")

# --- Enterprise contractor-application lane (Faz A) ------------------------
# Global company partner/contractor channels, applied autonomously through the
# SAME quota gates (forms count toward knowledge caps via leads.json).
ENTERPRISE_MODE: bool = _get("ENTERPRISE_MODE", "1").strip() not in {"0", "false", "no"}
# 4/day was starvation — the lane sat at "daily sub-cap 15/4" and submitted
# nothing. 40/day x 4/hour still fits inside the global 400/day, 32/hour caps
# and the per-ESP pacing guard (max 3/hour per provider) stays in control.
ENTERPRISE_DAILY_CAP: int = _get_int("ENTERPRISE_DAILY_CAP", 40)
ENTERPRISE_HOURLY_CAP: int = _get_int("ENTERPRISE_HOURLY_CAP", 4)
# Big-company pages load heavy JS: give the open-form fingerprint a wider
# window so a dynamic application form is not misread as "no form".
ENTERPRISE_FINGERPRINT_MS: int = _get_int("ENTERPRISE_FINGERPRINT_MS", 9000)
# Non-confirmed attempts (skipped_no_open_form/captcha/failed) retry after
# this many days; only submitted_confirmed is treated as permanently applied.
ENTERPRISE_RETRY_SKIP_DAYS: int = _get_int("ENTERPRISE_RETRY_SKIP_DAYS", 3)
# Acceptance-first framing: do NOT print a dollar figure in outreach; the
# contract amount is only shared in-chat when the company asks.
PRICE_HIDDEN: bool = _get("PRICE_HIDDEN", "0").strip() in {"1", "true", "yes", "on"}
# Revenue model: 40 employers x $2,500/mo retainer = $100k/mo target.
# PRICE_USD is a proposed offer, NOT the amount encoded by a Payoneer request.
PRICE_USD: int = _get_int("PRICE_USD", 2500)
ENTERPRISE_RETAINER_USD: int = _get_int("ENTERPRISE_RETAINER_USD", 2500)
ENTERPRISE_PILOT_USD: int = _get_int("ENTERPRISE_PILOT_USD", 500)
# --- Nirvana owner identity (insan algisi) ---------------------------------
# Raporlarda, kanıt kartlarında ve Telegram kimliğinde gerçek insan görünür.
OWNER_LINKEDIN_URL: str = _get("OWNER_LINKEDIN_URL", "https://www.linkedin.com/in/fevzican-aytekin-0b5501105").strip()
LINKEDIN_PROFILE_URL: str = _get("LINKEDIN_PROFILE_URL", OWNER_LINKEDIN_URL).strip()
# Owner chat id — for /reply handoff, /status, admin takeover. Set out-of-band.
OWNER_CHAT_ID: str = _get("OWNER_CHAT_ID", _get("ADMIN_CHAT_ID", "")).strip()
# Owner self-service registration secret: /admin KOD (set out-of-band on Oracle).
ADMIN_CODE: str = _get("ADMIN_CODE") or _get("OWNER_ADMIN_CODE", "")
# GitHub Secret tabanli yonetici kimlik dogrulamasi (rapor madde: admin tanima).
# TELEGRAM_ADMIN_ID  -> patronun Telegram user/chat id'si (.env'e deploy ile yazilir)
# TELEGRAM_ADMIN_TOKEN -> gizli eslesme dizesi; /notifyme TOKEN ile sohbet
# dogrulanip "Sistem Sahibi Taptaze Senkronize Edildi" yaniti verilir.
TELEGRAM_ADMIN_ID: str = _get("TELEGRAM_ADMIN_ID").lstrip("@") or OWNER_CHAT_ID
TELEGRAM_ADMIN_TOKEN: str = _get("TELEGRAM_ADMIN_TOKEN", "")
# Yerel Telegram Bot API (opsiyonel, $0): flood limitini ve 20 MB dosya sinirini
# kaldiran self-hosted sunucu. Bos ise standart bulut API kullanilir (mevcut hal).
TELEGRAM_BOT_API_BASE_URL: str = _get("TELEGRAM_BOT_API_BASE_URL", "")
# telegram-bot-api konteynerinin ihtiyac duydugu my.telegram.org anahtarlari
# (yalnizca Oracle .env'inde tutulur; asla repoya yazilmaz).
TELEGRAM_API_ID: str = _get("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH: str = _get("TELEGRAM_API_HASH", "")
# Ops channel: pipeline / sıcak lead bildirimleri (müşteri satış botundan ayrı).
TELEGRAM_NOTIFY_BOT_TOKEN: str = _get("TELEGRAM_NOTIFY_BOT_TOKEN")
TELEGRAM_NOTIFY_CHAT_ID: str = _get("TELEGRAM_NOTIFY_CHAT_ID")
# Watchdog (quota) alerts — Oracle VM only, separate ops bot.
WATCHDOG_BOT_TOKEN: str = _get("WATCHDOG_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
WATCHDOG_CHAT_ID: str = _get("WATCHDOG_CHAT_ID", TELEGRAM_NOTIFY_CHAT_ID or OWNER_CHAT_ID)
# Retainer request language for the Nirvana modules. The Payoneer request is
# created by the owner in the provider panel; these values only describe the
# offer text and gate the amount/currency of verified requests.
PAYMENT_CURRENCY: str = _get("PAYMENT_CURRENCY", "EUR").upper()
PAYMENT_AMOUNT: int = _get_int("PAYMENT_AMOUNT", 2500)
# Human-facing price label for all customer copy (Telegram, proof cards, PDFs).
# Nirvana retainer: €2.500 EUR aylık. Payment gates stay numeric (PRICE_USD /
# PAYMENT_AMOUNT == 2500); only the display currency changes here.
PRICE_LABEL: str = _get("PRICE_LABEL", "€2.500")
# Payoneer webhook: HMAC-SHA256 imza doğrulama sırrı. Sadece bu imzayla gelen
# PAID sinyali pipeline'ı otomatik başlatır; imzasız/sahte POST reddedilir.
PAYONEER_WEBHOOK_SECRET: str = _get("PAYONEER_WEBHOOK_SECRET", "")
# Faz B: GitHub Actions-produced enterprise application-channel feed
# (harvested/validated on GitHub; feed downloads still use Oracle network).
FEED_ENTERPRISE_RAW_URL: str = _get(
    "FEED_ENTERPRISE_RAW_URL",
    f"https://raw.githubusercontent.com/{_get('FEED_GITHUB_REPO', 'fevzican1/lead-qualification-engine')}/master/feeds/enterprise_targets.json",
)
# Faz C: legacy SMB lane (form filling) — main volume lane. Was OFF by default
# during the enterprise-lane migration, which starved total form throughput to
# ~4/day. Both lanes run together now; global caps (400/day, 32/hour) still gate.
SMB_LANE_ENABLED: bool = _get("SMB_LANE_ENABLED", "1").strip() not in {"0", "false", "no"}

# --- Product / outreach copy --------------------------------------------
TELEGRAM_BOT_USERNAME: str = _get("TELEGRAM_BOT_USERNAME").lstrip("@")
# Müşterinin sohbette gördüğü görünen ad: Telegram usernames '-bot' ile biter
# (platform kuralı, değiştirilemez) ama chat başlığında GÖRÜNEN AD budur.
# 'Bot' kelimesi ASLA geçmemeli. .env: BOT_DISPLAY_NAME=DevSolve Teknik Ekip
BOT_DISPLAY_NAME: str = _get("BOT_DISPLAY_NAME", "DevSolve Teknik Ekip").strip() or "DevSolve Teknik Ekip"
BOT_PUBLIC_DESCRIPTION: str = (
    _get("BOT_PUBLIC_DESCRIPTION").strip()
    or "DevSolve — teknik satış ve operasyon ekibi. Entegrasyon ve otomasyon kapsamı için yazın."
)
PRODUCT_NAME: str = _get("PRODUCT_NAME", "our platform")
PRODUCT_DESCRIPTION: str = _get("PRODUCT_DESCRIPTION")
ICP_DESCRIPTION: str = _get("ICP_DESCRIPTION") or _get("TARGET_ICP")

SENDER_NAME: str = _get("SENDER_NAME")
SENDER_EMAIL: str = _get("SENDER_EMAIL")
SENDER_COMPANY: str = _get("SENDER_COMPANY")
SENDER_PHONE: str = _get("SENDER_PHONE")

# --- Pipeline tunables --------------------------------------------------
MIN_FIT_SCORE: int = _get_int("MIN_FIT_SCORE", 70)
HEADLESS: bool = _get_bool("HEADLESS", True)
NAV_TIMEOUT_MS: int = _get_int("NAV_TIMEOUT_MS", 30_000)
FORM_DELAY_MIN_SECONDS: float = _get_float("FORM_DELAY_MIN_SECONDS", 5.0)
FORM_DELAY_MAX_SECONDS: float = _get_float("FORM_DELAY_MAX_SECONDS", 8.0)
FORM_DELAY_FAST_MIN_SECONDS: float = _get_float("FORM_DELAY_FAST_MIN_SECONDS", 5.0)
FORM_DELAY_FAST_MAX_SECONDS: float = _get_float("FORM_DELAY_FAST_MAX_SECONDS", 8.0)
FORM_DELAY_STRICT_MIN_SECONDS: float = _get_float("FORM_DELAY_STRICT_MIN_SECONDS", 8.0)
FORM_DELAY_STRICT_MAX_SECONDS: float = _get_float("FORM_DELAY_STRICT_MAX_SECONDS", 12.0)
# Keep both normal and strict-WAF waits short enough for the per-site budget.
if FORM_DELAY_FAST_MIN_SECONDS >= 12:
    FORM_DELAY_FAST_MIN_SECONDS = 5.0
if FORM_DELAY_FAST_MAX_SECONDS >= 20:
    FORM_DELAY_FAST_MAX_SECONDS = 8.0
LEAD_BATCH_SIZE: int = _get_int("LEAD_BATCH_SIZE", 15)
AUTO_RUNNER_SLEEP_SECONDS: int = _get_int("AUTO_RUNNER_SLEEP_SECONDS", 21_600)
DAILY_SUBMIT_LIMIT: int = _get_int("DAILY_SUBMIT_LIMIT", 400)
HOURLY_SUBMIT_LIMIT: int = _get_int("HOURLY_SUBMIT_LIMIT", 48)
# Target floor inside the cap: keep the hour at 40+ posts, never above the cap.
HOURLY_SUBMIT_FLOOR: int = _get_int("HOURLY_SUBMIT_FLOOR", 40)
DAILY_HTTP_PROBE_LIMIT: int = _get_int("DAILY_HTTP_PROBE_LIMIT", 800)
HOURLY_HTTP_PROBE_LIMIT: int = _get_int("HOURLY_HTTP_PROBE_LIMIT", 40)
CHROMIUM_BATCH: int = _get_int("CHROMIUM_BATCH", 40)
HTTP_PROBE_BATCH: int = _get_int("HTTP_PROBE_BATCH", 26)
MAX_PIPELINE_PROBES: int = _get_int("MAX_PIPELINE_PROBES", 40)
DISCOVERY_EVERY_SECONDS: int = _get_int("DISCOVERY_EVERY_SECONDS", 18_000)
# 30 was a panic floor, not a fill target. Keep the tank at 500 so the two lanes
# together can reach the 400/day ceiling without ever starving discovery.
QUEUE_TARGET: int = _get_int("QUEUE_TARGET", 500)
QUEUE_REFILL_BELOW: int = _get_int("QUEUE_REFILL_BELOW", 150)
QUEUE_MAX: int = _get_int("QUEUE_MAX", 2500)
# Minimum ready-queue depth the fuel guard treats as a full tank. Below this the
# Oracle dispatch hub fires an urgent fleet refill (CHROMIUM_FUEL -> 500+).
FUEL_TARGET: int = _get_int("FUEL_TARGET", 500)
READY_QUEUE_FLOOR: int = _get_int("READY_QUEUE_FLOOR", 50)
READY_QUEUE_TARGET: int = _get_int("READY_QUEUE_TARGET", 100)
EASY_SCORE_MIN: int = _get_int("EASY_SCORE_MIN", 55)
DOM_FINGERPRINT_MS: int = _get_int("DOM_FINGERPRINT_MS", 2_000)
# --- Multi-depth contact scan & form-rescue engine (Oracle $0 resource guard) ---
# Total per-domain search/scan budget; any search phase over this is aborted on
# its own (Resource Guard). This scanning NEVER consumes the daily/hourly submit
# quota — only submitted_confirmed/submitted_unconfirmed do (pacing/knowledge).
SCAN_DEPTH: int = _get_int("SCAN_DEPTH", 2)
SCAN_BUDGET_SECONDS: float = _get_float("SCAN_BUDGET_SECONDS", 8.0)
# Max wait after a modal/pop-up trigger click before deciding no form opened.
TRIGGER_WAIT_MS: int = _get_int("TRIGGER_WAIT_MS", 1_500)
# Allow synthetic POST to the site's own AJAX/action endpoint when no visible
# form is renderable (lightweight-first: httpx, no browser engine for the POST).
AJAX_POST_ENABLED: bool = _get_bool("AJAX_POST_ENABLED", True)
# Hand contacts found only as mailto:/bare email to a mailto_extracted status
# instead of skipping them as skipped_no_open_form (email worker picks them up).
MAILTO_HANDOFF: bool = _get_bool("MAILTO_HANDOFF", True)
PIPELINE_TIMEOUT_SECONDS: int = _get_int("PIPELINE_TIMEOUT_SECONDS", 30)
DEFER_MINUTES: int = _get_int("DEFER_MINUTES", 20)
HTTP_RESERVE_FOR_PIPELINE: int = _get_int("HTTP_RESERVE_FOR_PIPELINE", 20)
CHROMIUM_DIRECT_MIN: int = _get_int("CHROMIUM_DIRECT_MIN", 65)
FEED_MIN_SCORE: int = _get_int("FEED_MIN_SCORE", 80)
FEED_GITHUB_REPO: str = _get("FEED_GITHUB_REPO", "fevzican1/lead-qualification-engine")
FEED_RAW_URL: str = _get(
    "FEED_RAW_URL",
    f"https://raw.githubusercontent.com/{_get('FEED_GITHUB_REPO', 'fevzican1/lead-qualification-engine')}/master/feeds/ready_queue.json",
)
FEED_URL: str = _get("FEED_URL")
FEED_GITHUB_TOKEN: str = _get("FEED_GITHUB_TOKEN")
SITE_TIMEOUT_SECONDS: int = _get_int("SITE_TIMEOUT_SECONDS", 45)
# Max hosts per pipeline --submit invocation (~5–15 min wall time). 24 → with the
# ~22% confirm rate, up to ~5-6 confirmed posts per visit batch — enough to fill
# the 40/hour floor in fewer cycles while the per-provider pacing still protects.
PIPELINE_SUBMIT_SLICE: int = _get_int("PIPELINE_SUBMIT_SLICE", 24)
# Proof card delay after /start (seconds).
PROOF_CARD_DELAY_SECONDS: int = _get_int("PROOF_CARD_DELAY_SECONDS", 45)
# Second Chromium pass on a site that already failed: cut and move on.
SUBMIT_FAST_FAIL_SECONDS: float = _get_float("SUBMIT_FAST_FAIL_SECONDS", 15.0)
MONTHLY_SALES_TARGET: int = _get_int("MONTHLY_SALES_TARGET", 100)
LEADS_PATH: Path = ROOT / "leads.json"
TARGETS_PATH: Path = ROOT / "targets.txt"
AUTHORIZED_TARGETS_PATH: Path = ROOT / "authorized_targets.txt"
REVIEW_QUEUE_PATH: Path = ROOT / "review_queue.json"
INGEST_API_PORT: int = _get_int("INGEST_API_PORT", 8787)
INGEST_BIND_HOST: str = _get("INGEST_BIND_HOST", "127.0.0.1")
INGEST_API_TOKEN: str = _get("INGEST_API_TOKEN")
PAYLOAD_OPTIMIZER_MIN_SCORE: int = _get_int("PAYLOAD_OPTIMIZER_MIN_SCORE", 85)
OPTOUTS_PATH: Path = ROOT / "optouts.json"


def price_label(*, explicit: bool = False) -> str:
    """Human-facing retainer price (single source of truth: config.PRICE_LABEL)."""
    if PRICE_HIDDEN and not explicit:
        return ""
    return PRICE_LABEL


def payment_label() -> str:
    """Retainer label for the configured Nirvana payment currency (e.g. 2.500 EUR)."""
    amount = f"{PAYMENT_AMOUNT:,}".replace(",", ".") if PAYMENT_CURRENCY == "EUR" else str(PAYMENT_AMOUNT)
    return f"{amount} {PAYMENT_CURRENCY}"


def telegram_deeplink(start: str = "") -> str:
    """Public t.me link used in value propositions and form messages."""
    if not TELEGRAM_BOT_USERNAME:
        return "Telegram"
    token = re.sub(r"[^A-Za-z0-9_-]", "", (start or "").strip())[:64] if start else ""
    if token:
        return f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={token}"
    return f"https://t.me/{TELEGRAM_BOT_USERNAME}"


def require_live_telegram_link(start: str = "") -> str:
    """Fail-closed t.me link for real form sends.

    Local invention yerine tek doğruluk kaynağı: Oracle .env / repo secret ile
    çözülmüş TELEGRAM_BOT_USERNAME. Username boşsa ya da sadece metin
    dönüyorsa RuntimeError — çağrıcı lead'i `skipped_no_telegram_link` olarak
    işaretler ve yakıtı tıklanamaz bir formla yakmaz.
    """
    username = next_bot_username() or (TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    if not username:
        raise RuntimeError(
            "TELEGRAM_BOT_USERNAME eksik: t.me linki üretilemez. "
            "Oracle /opt/devsolve/.env içine ya da repo secret'larına "
            "(TELEGRAM_BOT_TOKEN + TELEGRAM_BOT_USERNAME) girin."
        )
    token = re.sub(r"[^A-Za-z0-9_-]", "", (start or "").strip())[:64] if start else ""
    if token:
        return f"https://t.me/{username}?start={token}"
    return f"https://t.me/{username}"


def ensure_telegram_username() -> str:
    """Fill TELEGRAM_BOT_USERNAME from BotFather getMe if it was left blank."""
    global TELEGRAM_BOT_USERNAME
    if TELEGRAM_BOT_USERNAME:
        return TELEGRAM_BOT_USERNAME
    token = require("TELEGRAM_BOT_TOKEN")
    import httpx

    response = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30.0)
    response.raise_for_status()
    username = str((response.json().get("result") or {}).get("username") or "").lstrip("@")
    if not username:
        raise RuntimeError("Telegram getMe did not return a username")
    TELEGRAM_BOT_USERNAME = username
    env_path = ROOT / ".env"
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        if re.search(r"^TELEGRAM_BOT_USERNAME=.*$", text, flags=re.M):
            text = re.sub(
                r"^TELEGRAM_BOT_USERNAME=.*$",
                f"TELEGRAM_BOT_USERNAME={username}",
                text,
                flags=re.M,
            )
        else:
            text = text.rstrip() + f"\nTELEGRAM_BOT_USERNAME={username}\n"
        env_path.write_text(text, encoding="utf-8")
    return username


def openai_client():
    raise RuntimeError("OpenAI was removed. This project uses local Ollama (see ollama_client.py).")


def async_openai_client():
    raise RuntimeError("OpenAI was removed. This project uses local Ollama (see ollama_client.py).")


def require_pipeline_keys(*, submitting: bool = False) -> None:
    """Validate core keys for pipeline runs (form-sending lane)."""
    if submitting:
        for name in ("SENDER_NAME", "SENDER_EMAIL", "SENDER_COMPANY"):
            require(name)
        ensure_telegram_username()


def is_owner(chat_id) -> bool:
    """Telegram chat is the configured owner/admin."""
    target = str(getattr(chat_id, "id", chat_id))
    return target == str(OWNER_CHAT_ID).strip() or target == str(TELEGRAM_OWNER_CHAT_ID).strip()


def admin_required():
    """Decorator: only allow OWNER_CHAT_ID / TELEGRAM_OWNER_CHAT_ID to run."""
    def wrapper(func):
        import functools
        @functools.wraps(func)
        def inner(*args, **kwargs):
            return func(*args, **kwargs)
        return inner
    return wrapper



def require_bot_keys() -> None:
    require("TELEGRAM_BOT_TOKEN")
    require("PAYONEER_PAYMENT_URL")


def bot_tokens() -> list[str]:
    """Tüm satış botu tokenleri — birincil önce, tekrarlar ayıklanır.

    TELEGRAM_BOT_TOKENS "token1,token2,..." şeklinde verilir; birincil
    TELEGRAM_BOT_TOKEN otomatik listenin başına eklenir.
    """
    tokens: list[str] = []
    primary = (TELEGRAM_BOT_TOKEN or "").strip()
    if primary:
        tokens.append(primary)
    for raw in (TELEGRAM_BOT_TOKENS or "").replace(";", ",").split(","):
        tok = raw.strip()
        if tok and tok not in tokens:
            tokens.append(tok)
    return tokens


_BOT_POOL_LOCK: Any = None  # lazy threading.Lock (import döngüsünü önlemek için)
_BOT_POOL_USERNAMES: list[str] = []
_BOT_POOL_CURSOR: int = 0


def _pool_lock() -> Any:
    global _BOT_POOL_LOCK
    if _BOT_POOL_LOCK is None:
        import threading

        _BOT_POOL_LOCK = threading.Lock()
    return _BOT_POOL_LOCK


def bot_pool_usernames() -> list[str]:
    """Çözülmüş bot username havuzu (resolve_bot_pool sonrası; önceki [primary])."""
    with _pool_lock():
        pool = list(_BOT_POOL_USERNAMES)
    if not pool and TELEGRAM_BOT_USERNAME:
        pool = [TELEGRAM_BOT_USERNAME]
    return pool


def set_bot_pool(usernames: list[str]) -> None:
    """Havuzu elle kur (testler ve getMe'siz devre için)."""
    global _BOT_POOL_USERNAMES, _BOT_POOL_CURSOR
    cleaned: list[str] = []
    for name in usernames or []:
        uname = str(name or "").strip().lstrip("@")
        if uname and uname not in cleaned:
            cleaned.append(uname)
    with _pool_lock():
        _BOT_POOL_USERNAMES = cleaned
        _BOT_POOL_CURSOR = 0


def resolve_bot_pool() -> list[str]:
    """getMe ile tüm satış botlarının username'lerini çöz ve havuzu kur.

    Tek bot akışını bozmaz: getMe başarısız olursa primary username ile
    devam eder (fail-open), tokenler yine de ayrı Application olarak koşar.
    """
    import logging

    import httpx

    usernames: list[str] = []
    primary = (TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    if primary:
        usernames.append(primary)
    for token in bot_tokens():
        if not token or token == (TELEGRAM_BOT_TOKEN or "").strip():
            continue  # primary zaten yukarıda (ya da getMe ile) çözüldü
        try:
            response = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30.0)
            response.raise_for_status()
            uname = str((response.json().get("result") or {}).get("username") or "").lstrip("@")
        except Exception:
            logging.getLogger(__name__).warning(
                "getMe failed for a pool token — bot havuzdan düşer", exc_info=True
            )
            continue
        if uname and uname not in usernames:
            usernames.append(uname)
    set_bot_pool(usernames)
    return bot_pool_usernames()


def next_bot_username() -> str:
    """Form linki havuzu round-robin — SADECE ACTIVE botlardan.

    Dinamik Bot Havuzu + Ortak Beyin: PASSIVE (FLOOD_WAIT cezali) botlar
    link rotasyonuna GIRMEZ; musteri tiklayinca cezali bota dusmez.
    bot_registry import edilemezse eski round-robin'e duser (fail-open).
    """
    global _BOT_POOL_CURSOR
    try:
        import bot_registry
        name = bot_registry.next_active_username()
        if name:
            return name
    except Exception:  # noqa: BLE001 — kayit defteri yoksa eski yola dus
        pass
    with _pool_lock():
        pool = list(_BOT_POOL_USERNAMES)
        if not pool and TELEGRAM_BOT_USERNAME:
            pool = [TELEGRAM_BOT_USERNAME]
        if not pool:
            return ""
        name = pool[_BOT_POOL_CURSOR % len(pool)]
        _BOT_POOL_CURSOR += 1
        return name


def sender_payload() -> dict[str, Optional[str]]:
    """Values mapped onto typical contact-form fields."""
    return {
        "name": SENDER_NAME,
        "email": SENDER_EMAIL,
        "company": SENDER_COMPANY,
        "phone": SENDER_PHONE or None,
        "website": telegram_deeplink() if TELEGRAM_BOT_USERNAME else None,
        "subject": "Custom API / automation note",
    }
