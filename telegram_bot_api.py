"""Yerel Telegram Bot API (Local Bot API Server) cozumleyici — opsiyonel, $0.

Neden: standart bulut API dakikada ~30 mesaj ve 20 MB dosya siniri koyar; yuksek
musteri yogunlugunda flood/gecici ban riski dogar. Ayni makinede calisan
telegram-bot-api konteyneri bu sinirlari kaldirir (2000 MB dosya, sunucu hizi,
RetryAfter'i kendi akilli kuyruguyla yonetir).

Fail-safe kural (kesintisiz sistem):
- TELEGRAM_BOT_API_BASE_URL tanimli degilse -> bulut API (varsayilan, mevcut hal).
- Tanimli ama yerel sunucu getMe'ye saglikli yanit vermiyorsa -> bulut API'ye
  otomatik dusulur, gerekce raporlanir. Satis hatti hicbir kosulda durmaz.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import config

logger = logging.getLogger(__name__)

CLOUD_BOT_URL = "https://api.telegram.org/bot"
CLOUD_FILE_URL = "https://api.telegram.org/file/bot"
LOCAL_FILE_LIMIT_MB = 2000
CLOUD_FILE_LIMIT_MB = 20


def cloud_urls() -> tuple[str, str]:
    return CLOUD_BOT_URL, CLOUD_FILE_URL


def local_urls(base: str) -> tuple[str, str]:
    """'http://127.0.0.1:8081' veya '.../bot' girdisinden (bot, file) URL'leri.

    PTB base_url'e tokeni kendisi ekler (value + token); bu yuzden URL '/bot' ile
    bitmeli ve sonda egik cizgi OLMAMALI.
    """
    root = (base or "").strip().rstrip("/")
    if not root:
        raise ValueError("bos TELEGRAM_BOT_API_BASE_URL")
    if root.endswith("/bot"):
        root = root[:-4]
    root = root.rstrip("/")
    return f"{root}/bot", f"{root}/file/bot"


def configured_base() -> str:
    return str(getattr(config, "TELEGRAM_BOT_API_BASE_URL", "") or "").strip()


def _probe(token: str, bot_url: str, *, timeout: float = 5.0) -> bool:
    """Yerel sunucu gercekten yanit veriyor mu? (Telegram'a mesaj GITMEZ.)"""
    import httpx

    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        resp = httpx.post(f"{bot_url}{token}/getMe", timeout=timeout)
        return bool(resp.json().get("ok"))
    except Exception:  # noqa: BLE001 — ag hatasi = yerel mod yok
        return False


def resolve(*, token: str | None = None,
            probe: Callable[[str, str], bool] | None = None) -> dict[str, Any]:
    """Kullanilacak API modunu belirle: {'mode': 'local'|'cloud', ...}."""
    base = configured_base()
    bot_url, file_url = cloud_urls()
    result: dict[str, Any] = {
        "mode": "cloud",
        "base_url": bot_url,
        "base_file_url": file_url,
        "file_limit_mb": CLOUD_FILE_LIMIT_MB,
        "configured_base": base,
        "reason": "TELEGRAM_BOT_API_BASE_URL tanimli degil (standart bulut API)",
        "probed": False,
    }
    if not base:
        return result
    try:
        local_bot, local_file = local_urls(base)
    except ValueError as exc:
        result["reason"] = str(exc)
        return result
    result.update({"base_url": local_bot, "base_file_url": local_file,
                   "file_limit_mb": LOCAL_FILE_LIMIT_MB})
    probe_token = (token or getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not probe_token:
        result["reason"] = "token yok — yerel mod dogrulanamadi, bulut API kullaniliyor"
        result.update({"base_url": bot_url, "base_file_url": file_url,
                       "file_limit_mb": CLOUD_FILE_LIMIT_MB})
        return result
    check = probe or _probe
    if check(probe_token, local_bot):
        result["mode"] = "local"
        result["probed"] = True
        result["reason"] = "yerel telegram-bot-api saglikli (flood limiti yok, 2 GB dosya)"
        return result
    result.update({"base_url": bot_url, "base_file_url": file_url,
                   "file_limit_mb": CLOUD_FILE_LIMIT_MB,
                   "reason": "yerel sunucu getMe vermedi — bulut API'ye dusuldu (fail-safe)"})
    logger.warning("Local Bot API unreachable — falling back to cloud API (%s)", base)
    return result


def apply_to_builder(builder: Any, *, token: str | None = None,
                     probe: Callable[[str, str], bool] | None = None) -> dict[str, Any]:
    """PTB ApplicationBuilder'i yerel moda gore ayarla (yerel degilse dokunma)."""
    chosen = resolve(token=token, probe=probe)
    if chosen["mode"] == "local":
        builder = builder.base_url(chosen["base_url"]).base_file_url(chosen["base_file_url"])
    return builder, chosen


def status_line() -> str:
    """Operator ozeti (/status, /notifyme ve deploy cikitisi icin)."""
    chosen = resolve()
    label = "YEREL (Local Bot API)" if chosen["mode"] == "local" else "BULUT (api.telegram.org)"
    return (f"Telegram API modu: {label} | dosya limiti: {chosen['file_limit_mb']} MB | "
            f"limit korumasi: flood_guard + {chosen['reason']}")