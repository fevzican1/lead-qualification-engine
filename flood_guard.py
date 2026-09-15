"""Telegram flood koruması — tek kapıdan tüm giden API çağrıları.

Neden: Telegram, satış botuna RetryAfter (flood control) cezası verdiğinde
bot çakılıp restart döngüsüne giriyor ve ceza daha da büyüyordu. Bu modül:

1. Hız sınırları (ceza hiç yaşamamak için):
   - sohbet başına min 1.5 sn aralık (Telegram private limiti 1/sn + pay)
   - global min 0.10 sn aralık (30/sn limit + burst koruması)
2. RetryAfter yakalanırsa ceza süresi not edilir; süre boyunca giden
   mesaj çağrıları (a) kısaysa bekleyip gönderir, (b) uzunsa mesajı
   güvenle düşürür (FloodBlocked) — Telegram'a spam atmadan.
3. install(bot): PTB Bot._post'u sarar — send_message, reply_text,
   send_photo, send_chat_action... hepsi tek noktadan korunur.

Kullanım:
    flood_guard.install(application.bot)   # _post_init'te bir kez
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class FloodBlocked(RuntimeError):
    """Flood cezası uzun — bu mesaj gönderilmedi (sessizce düşürüldü)."""

    def __init__(self, chat_id: Any, remaining: float) -> None:
        super().__init__(f"flood gate aktif ({remaining:.0f}s) — chat {chat_id} mesaji duşuruldu")
        self.chat_id = chat_id
        self.remaining = remaining


# Telegram limitleri, güvenli pay ile:
CHAT_MIN_INTERVAL = 1.5   # private chat: 1 msg/sn → 1.5s
GLOBAL_MIN_INTERVAL = 0.10  # global: 30 msg/sn → burst koruması
# Ceza bu kadar kısaysa göndermeden önce bekle; uzunsa mesajı düşür.
SHORT_WAIT_MAX = 20.0
# Rezonable üst sınır: Telegram'ın verebileceği en büyük makul ceza (2 gün).
MAX_RETRY_AFTER = 2 * 86400.0

_until = 0.0              # flood cezası bitişi (monotonic)
_last_global = 0.0
_last_chat: dict[int, float] = {}
_lock = asyncio.Lock()


def note_retry_after(seconds: float) -> float:
    """Telegram 429 cezası geldi — kapıyı kapat."""
    global _until
    try:
        seconds = min(max(float(seconds), 1.0), MAX_RETRY_AFTER)
    except (TypeError, ValueError):
        seconds = 60.0
    until = time.monotonic() + seconds
    if until > _until:
        _until = until
        logger.warning(
            "FLOOD GATE: %.0f sn Telegram cezasi — giden mesajlar duraklatildi", seconds
        )
    return seconds


def remaining() -> float:
    """Kapalı kalan süre (sn); 0.0 = açık."""
    return max(0.0, _until - time.monotonic())


def reset() -> None:
    """Testler için: tüm durumu sıfırla."""
    global _until, _last_global
    _until = 0.0
    _last_global = 0.0
    _last_chat.clear()


async def acquire(chat_id: int | None = None, *, max_wait: float = SHORT_WAIT_MAX) -> bool:
    """Göndermeden önce çağrılır.

    True  → gönder (hız sınırı beklemeleri dahil edildi).
    False → ceza uzun, mesajı düşür (FloodBlocked raise etmek çağıranın işi).
    """
    global _last_global
    rem = remaining()
    if rem > 0:
        if rem > max_wait:
            return False
        await asyncio.sleep(rem)
    async with _lock:
        now = time.monotonic()
        earliest = max(now, _last_global + GLOBAL_MIN_INTERVAL)
        if chat_id is not None:
            earliest = max(earliest, _last_chat.get(int(chat_id), 0.0) + CHAT_MIN_INTERVAL)
        # Slotu rezerve et (uyku lock altında değil).
        _last_global = max(_last_global, earliest)
        if chat_id is not None:
            _last_chat[int(chat_id)] = earliest
        wait = earliest - now
    if wait > 0:
        await asyncio.sleep(wait)
    return True


def install(bot: Any) -> None:
    """PTB Bot örneğinin TÜM API çağrılarını flood kapısından geçir.

    Bot._post sarılır; reply_text/send_message/send_photo dahil her uç
    otomatik korunur. RetryAfter geldiğinde ceza süresi not edilir.
    """
    if getattr(bot, "_flood_guard_installed", False):
        return
    original = bot._post

    async def guarded(endpoint: str, data: dict[str, Any], *args: Any, **kwargs: Any):
        chat_id = data.get("chat_id") if isinstance(data, dict) else None
        cid: int | None = None
        if chat_id is not None:
            text = str(chat_id).lstrip("-")
            if text.isdigit():
                cid = int(chat_id)
            if not await acquire(cid):
                raise FloodBlocked(cid, remaining())
        try:
            return await original(endpoint, data, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — RetryAfter fark et, tekrar fırlat
            ra = getattr(exc, "retry_after", None)
            if ra is not None:
                try:
                    note_retry_after(float(ra))
                except (TypeError, ValueError):
                    note_retry_after(60.0)
            raise

    bot._post = guarded
    bot._flood_guard_installed = True
    logger.info("flood_guard kurulu: chat %.1fs / global %.2fs hiz siniri + RetryAfter kapisi",
                CHAT_MIN_INTERVAL, GLOBAL_MIN_INTERVAL)
