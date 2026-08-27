"""
Owner-only Telegram status (not customer outreach).

The sales bot is inbound: customers must write first. This module lets the
operator see pipeline progress in Telegram after they send /notifyme.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

import config
import domain_store

logger = logging.getLogger(__name__)

PATH = config.ROOT / "owner.json"


def load_chat_id() -> int | None:
    raw = (config.TELEGRAM_OWNER_CHAT_ID or "").strip()
    if raw.isdigit():
        return int(raw)
    if not PATH.exists():
        return None
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    chat_id = data.get("chat_id")
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return None


def save_chat_id(chat_id: int) -> None:
    PATH.write_text(
        json.dumps({"chat_id": int(chat_id)}, indent=2) + "\n",
        encoding="utf-8",
    )


def lead_digest() -> str:
    path = config.LEADS_PATH
    if not path.exists():
        return "Henüz leads.json yok — pipeline ilk turu bitirmemiş olabilir."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "leads.json okunamadı."
    if not isinstance(data, list):
        return "leads.json beklenen formatta değil."
    counts: dict[str, int] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    submitted = sum(v for k, v in counts.items() if str(k).startswith("submitted"))
    lines = [
        "DevSolve motor özeti (Oracle, Always Free)",
        f"Model: {config.OLLAMA_MODEL}",
        f"Toplam lead: {len(data)}",
        f"Form gönderildi: {submitted}",
        f"Kuyruk: {domain_store.queue_depth()}/{getattr(config, 'QUEUE_TARGET', 150)} "
        f"(max {getattr(config, 'QUEUE_MAX', 250)}) | hazır {domain_store.ready_pool_size()} "
        f"| HTTP {domain_store.http_budget_label()}",
        f"Durumlar: {counts}",
        "",
        "Telegram sohbetin boşsa bu normal: bot müşteriye ilk mesajı ATMAZ.",
        "Müşteri formdan t.me linkine tıklayınca sohbet başlar; ~2 dk sonra analiz kartı gider.",
        "Sıcak sinyal (fiyat / başlayalım / telefon) bu sohbete ping düşer: /reply CHATID metin",
        "Bu özet yalnızca sana gider. /stop müşteri çıkışıdır, bunu kapatmaz.",
    ]
    return "\n".join(lines)


def send(text: str, *, chat_id: int | None = None) -> bool:
    target = chat_id if chat_id is not None else load_chat_id()
    token = config.TELEGRAM_BOT_TOKEN
    if not target or not token:
        logger.info("Owner notify skipped (no chat id yet — send /notifyme on the bot)")
        return False
    last_exc: Exception | None = None
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    for attempt in range(1, 4):
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": target, "text": text[:3500]},
                timeout=30.0,
            )
            response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("Owner Telegram notify attempt %s failed: %s", attempt, exc)
            time.sleep(2 * attempt)
            logger.error("Owner Telegram notify failed after retries: %s", last_exc)
    return False


def notify_pipeline(
    counts: dict[str, Any],
    *,
    submitted: int,
    scoped: int,
    skipped: int = 0,
) -> None:
    del counts
    send(
        "Pipeline turu bitti.\n"
        f"Form gönderim: {submitted}\n"
        f"Atlanan (CAPTCHA / form yok / ulaşılamaz): {skipped}\n"
        f"Bu tur bakılan: {scoped}\n"
        f"Kuyruk: {domain_store.queue_depth()}/{getattr(config, 'QUEUE_TARGET', 150)} "
        f"| HTTP {domain_store.http_budget_label()}\n"
        "Müşteri yazarsa satış sohbeti bu bota düşer."
    )
