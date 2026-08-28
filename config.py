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

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

try:
    import pip_system_certs.wrapt_requests  # noqa: F401 — use Windows CA store (SSL inspection)
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
TELEGRAM_BOT_TOKEN: str = _get("TELEGRAM_BOT_TOKEN")
PAYONEER_PAYMENT_URL: str = _get("PAYONEER_PAYMENT_URL")
TELEGRAM_OWNER_CHAT_ID: str = _get("TELEGRAM_OWNER_CHAT_ID")
OLLAMA_HOST: str = _get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL: str = _get("OLLAMA_MODEL", "deepseek-r1:14b")
PRICE_USD: int = _get_int("PRICE_USD", 200)

# --- Product / outreach copy --------------------------------------------
TELEGRAM_BOT_USERNAME: str = _get("TELEGRAM_BOT_USERNAME").lstrip("@")
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
HOURLY_SUBMIT_LIMIT: int = _get_int("HOURLY_SUBMIT_LIMIT", 32)
# Target floor inside the cap: keep the hour at 30+ posts, never above the cap.
HOURLY_SUBMIT_FLOOR: int = _get_int("HOURLY_SUBMIT_FLOOR", 30)
DAILY_HTTP_PROBE_LIMIT: int = _get_int("DAILY_HTTP_PROBE_LIMIT", 500)
HOURLY_HTTP_PROBE_LIMIT: int = _get_int("HOURLY_HTTP_PROBE_LIMIT", 22)
CHROMIUM_BATCH: int = _get_int("CHROMIUM_BATCH", 32)
HTTP_PROBE_BATCH: int = _get_int("HTTP_PROBE_BATCH", 20)
MAX_PIPELINE_PROBES: int = _get_int("MAX_PIPELINE_PROBES", 22)
DISCOVERY_EVERY_SECONDS: int = _get_int("DISCOVERY_EVERY_SECONDS", 18_000)
# 30 was a panic floor, not a fill target. Keep a real buffer so Chromium never starves.
QUEUE_TARGET: int = _get_int("QUEUE_TARGET", 400)
QUEUE_REFILL_BELOW: int = _get_int("QUEUE_REFILL_BELOW", 80)
QUEUE_MAX: int = _get_int("QUEUE_MAX", 1500)
READY_QUEUE_FLOOR: int = _get_int("READY_QUEUE_FLOOR", 50)
READY_QUEUE_TARGET: int = _get_int("READY_QUEUE_TARGET", 100)
EASY_SCORE_MIN: int = _get_int("EASY_SCORE_MIN", 55)
DOM_FINGERPRINT_MS: int = _get_int("DOM_FINGERPRINT_MS", 2_000)
PIPELINE_TIMEOUT_SECONDS: int = _get_int("PIPELINE_TIMEOUT_SECONDS", 30)
DEFER_MINUTES: int = _get_int("DEFER_MINUTES", 20)
HTTP_RESERVE_FOR_PIPELINE: int = _get_int("HTTP_RESERVE_FOR_PIPELINE", 20)
CHROMIUM_DIRECT_MIN: int = _get_int("CHROMIUM_DIRECT_MIN", 65)
FEED_MIN_SCORE: int = _get_int("FEED_MIN_SCORE", 80)
FEED_URL: str = _get("FEED_URL")
FEED_GITHUB_TOKEN: str = _get("FEED_GITHUB_TOKEN")
SITE_TIMEOUT_SECONDS: int = _get_int("SITE_TIMEOUT_SECONDS", 20)
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


def price_label() -> str:
    return f"${PRICE_USD} USD"


def telegram_deeplink(start: str = "") -> str:
    """Public t.me link used in value propositions and form messages."""
    if not TELEGRAM_BOT_USERNAME:
        return "Telegram"
    token = re.sub(r"[^A-Za-z0-9_-]", "", (start or "").strip())[:64] if start else ""
    if token:
        return f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={token}"
    return f"https://t.me/{TELEGRAM_BOT_USERNAME}"


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
    if submitting:
        for name in ("SENDER_NAME", "SENDER_EMAIL", "SENDER_COMPANY"):
            require(name)
        ensure_telegram_username()


def require_bot_keys() -> None:
    require("TELEGRAM_BOT_TOKEN")
    require("PAYONEER_PAYMENT_URL")


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
