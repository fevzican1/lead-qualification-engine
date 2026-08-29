"""
Inbound Telegram sales assistant powered by local Ollama.

Form links carry a /start token so the first reply already names their
stack. DeepSeek-R1:14B handles the rest of the close. Identifies as AI
only if asked. Honors STOP. Sends PAYONEER_PAYMENT_URL only after clear
purchase intent. Never sends email from this host.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import defaultdict
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import bounded_agents
import knowledge
import ollama_client
import optout
import owner_notify
import proof_card
import telegram_handoff
import telegram_sessions

logger = logging.getLogger(__name__)

MAX_HISTORY = 16

_histories: dict[int, list[dict[str, str]]] = defaultdict(list)
_briefs: dict[int, dict[str, Any]] = {}
_payment_sent: set[int] = set()
_proof_tasks: dict[int, asyncio.Task[Any]] = {}

_BUY_RE = re.compile(
    r"nasıl\s+satın\s*al|nasil\s+satin\s*al|satın\s*al[ıi]r[ıi]m|satın\s*almak\s+ist|"
    r"baslayabilir|başlayabilir|haydi\s+başla|hadi\s+başla|anlaştık|anlastik|"
    r"nasıl\s+başla|ödeme\s*link|odeme\s*link|payoneer|"
    r"how\s+(do\s+i|can\s+i|to)\s+(buy|pay|start|purchase)|"
    r"ready\s+to\s+(buy|start|pay)|i\s+want\s+to\s+(buy|start|proceed)|"
    r"proceed\s+to\s+pay|send\s+(the\s+)?(invoice|payment|link)|fatura\s*kes",
    re.I,
)

_DECLINE_RE = re.compile(
    r"ilgilenmiyorum|istemiyorum|gerek\s*yok|hayır\s*teşekkür|hayir\s*tesekkur|"
    r"rahatsız\s*etme|bir\s*daha\s*yazma|not interested|no thanks|"
    r"don't contact|do not contact|stop writing|şimdilik\s*olmaz|simdilik\s*olmaz",
    re.I,
)

_HOT_RE = re.compile(
    r"fiyat|ne kadar|ücret|ucret|kaç\s*dolar|kac\s*dolar|price|how much|cost|"
    r"ne zaman başla|ne zaman basla|when (can|do) we start|kaç günde|kac gunde|"
    r"telefon|aram[ae]|görüşelim|goruselim|call me|meeting|zoom|meet\b|"
    r"teklif|proposal|quote|demo|"
    r"yapalım|yapalim|devam edelim",
    re.I,
)


def _remember(chat_id: int, role: str, content: str) -> None:
    history = _histories[chat_id]
    history.append({"role": role, "content": content})
    overflow = len(history) - MAX_HISTORY
    if overflow > 0:
        del history[:overflow]


def _is_owner(chat_id: int) -> bool:
    known = owner_notify.load_chat_id()
    return known is not None and int(known) == int(chat_id)


def _customer_lang(update: Update) -> bool:
    code = ""
    if update.effective_user and update.effective_user.language_code:
        code = str(update.effective_user.language_code).lower()
    return code.startswith("tr")


def _username(update: Update) -> str:
    user = update.effective_user
    return (user.username or "").lstrip("@") if user else ""


def _bind_token(chat_id: int, token: str) -> dict[str, Any] | None:
    row = telegram_handoff.lookup(token)
    if row:
        _briefs[chat_id] = row
    return row


def _closer_brief(row: dict[str, Any] | None) -> str:
    """Give the model the same evidence gate used by form qualification."""
    if not row:
        return ""
    context = bounded_agents.closer_context(row)
    safe_row = dict(row)
    safe_row["platform"] = context["platform"]
    safe_row["stack"] = context["platform"] or ""
    safe_row["platform_confirmed"] = context["platform_confirmed"]
    safe_row["platform_confidence"] = context["platform_confidence"]
    safe_row["platform_evidence"] = context["platform_evidence"]
    return telegram_handoff.brief_block(safe_row)


def _parse_model_output(raw: str, user_text: str) -> tuple[str, bool]:
    reply = (raw or "").strip()
    if re.search(r"^PAY:\s*(yes|no|true|false)", reply, re.I | re.M):
        reply = re.sub(r"^PAY:\s*\S+\s*", "", reply, count=1, flags=re.I | re.M)
    reply = re.sub(r"^REPLY:\s*", "", reply, flags=re.I | re.M).strip()
    reply = re.sub(r"</?think>", "", reply, flags=re.I).strip()
    if not reply:
        reply = raw.strip()
    if len(reply) > 1400:
        reply = reply[:1390].rsplit(" ", 1)[0] + "…"
    pay = bool(_BUY_RE.search(user_text or ""))
    return reply, pay


def _complete(messages: list[dict[str, str]]) -> str:
    return ollama_client.chat(
        messages,
        temperature=0.4,
        max_tokens=380,
        timeout=180.0,
    )


def _owner_intro() -> str:
    return (
        "Operatör paneli — müşteri bunu görmez.\n"
        f"Custom API / otomasyon, {config.price_label()} (Payoneer).\n"
        "Motor özeti: /notifyme   durum: /status\n"
        "Sıcak aday: bu sohbete ping düşer.\n"
        "Sohbete gir: /reply CHATID metin\n"
        "Botu geri ver: /release CHATID\n"
        "Unsubscribe test: /stop"
    )


def _cold_intro(*, turkish: bool) -> str:
    price = config.price_label()
    if turkish:
        return (
            f"DevSolve — mevcut panelinize özel bir köprü, {price} flat.\n"
            "Formdan geldiyseniz sitenizi zaten gördük. Hangi altyapı "
            "(IdeaSoft, Woo, iyzico…) ve şu an en çok nerede takılıyor: "
            "ödeme callback, stok, ERP, yoksa Excel?"
        )
    return (
        f"DevSolve — one scoped bridge on your current panel, {price} flat.\n"
        "If you came from the contact form, we already looked at the public stack. "
        "Which platform (IdeaSoft, Woo, iyzico…) and what is burning: "
        "payment callback, stock, ERP, or Excel?"
    )


def _is_hot(text: str) -> bool:
    return bool(_HOT_RE.search(text or "")) or bool(_BUY_RE.search(text or ""))


async def _confirm_stop(update: Update) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        return
    optout.add_chat(chat.id, reason="telegram_stop")
    if message.text:
        optout.harvest_from_text(message.text, reason="telegram_stop")
    for turn in _histories.get(chat.id, []):
        if turn.get("role") == "user":
            optout.harvest_from_text(turn.get("content") or "", reason="telegram_stop")
    _histories.pop(chat.id, None)
    _briefs.pop(chat.id, None)
    _payment_sent.discard(chat.id)
    task = _proof_tasks.pop(chat.id, None)
    if task:
        task.cancel()
    telegram_sessions.clear(chat.id)
    await message.reply_text(
        "You are unsubscribed. We will not message you again from this assistant.\n"
        "Listeden çıktınız. Tekrar yazmamız için /resume yazın.\n"
        f"Email opt-out: {config.SENDER_EMAIL or 'hello@devsolvev2.com'} — subject Unsubscribe."
    )


async def _send_proof(chat_id: int, bot: Any, *, turkish: bool) -> None:
    if optout.is_chat_opted_out(chat_id) or _is_owner(chat_id):
        return
    if not telegram_sessions.should_send_proof(chat_id):
        return
    row = _briefs.get(chat_id)
    path = await asyncio.to_thread(proof_card.render, row, turkish=turkish)
    if path is None or not path.exists():
        logger.warning("Proof card skipped for chat %s (Pillow missing or render failed)", chat_id)
        return
    caption = proof_card.caption(row, turkish=turkish)[:1024]
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
        with path.open("rb") as handle:
            await bot.send_photo(chat_id=chat_id, photo=handle, caption=caption)
    except Exception:
        logger.exception("Proof photo failed for chat %s", chat_id)
        return
    telegram_sessions.mark_proof(chat_id)
    _remember(chat_id, "assistant", caption)
    logger.info("Proof card sent to chat %s", chat_id)


def _schedule_proof(chat_id: int, bot: Any, *, turkish: bool) -> None:
    if chat_id in _proof_tasks or _is_owner(chat_id):
        return
    if not telegram_sessions.should_send_proof(chat_id):
        return

    async def _run() -> None:
        try:
            delay = telegram_sessions.seconds_until_proof(chat_id)
            if delay > 0:
                await asyncio.sleep(delay)
            await _send_proof(chat_id, bot, turkish=turkish)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Proof schedule failed for chat %s", chat_id)
        finally:
            _proof_tasks.pop(chat_id, None)

    _proof_tasks[chat_id] = asyncio.create_task(_run(), name=f"proof-{chat_id}")


async def _greet_from_token(update: Update, bot: Any, chat_id: int, row: dict[str, Any]) -> None:
    """No empty channel: type for a beat, then the named greeting, then the card."""
    turkish = bool(row.get("turkish", True))
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(1.2)
    except Exception:  # noqa: BLE001
        logger.debug("greeting typing failed for %s", chat_id, exc_info=True)
    text = telegram_handoff.opener(row)
    _remember(chat_id, "assistant", text)
    if update.message:
        await update.message.reply_text(text)
    else:
        await bot.send_message(chat_id=chat_id, text=text)
    _schedule_proof(chat_id, bot, turkish=turkish)


async def _warm_ping(chat_id: int, update: Update, row: dict[str, Any]) -> None:
    """Notify owner when a warm-scored lead converts form → Telegram /start."""
    if _is_owner(chat_id) or telegram_sessions.is_declined(chat_id):
        return
    if not telegram_sessions.should_warm_ping(chat_id):
        return
    lead_info = row.get("lead_info") if isinstance(row.get("lead_info"), dict) else {}
    score = str(lead_info.get("lead_score") or "").lower()
    if score != "warm":
        return
    who = str(row.get("company") or row.get("host") or row.get("target_domain") or "—")
    user = _username(update) or "yok"
    handle = f"@{user}" if user != "yok" else "yok"
    platform = str((row.get("detected_stack") or {}).get("platform") or row.get("platform") or "—")
    ping = (
        "FORM→TELEGRAM (warm) — dönüşüm\n"
        f"Şirket: {who}\n"
        f"Platform: {platform}\n"
        f"Chat id: {chat_id}\n"
        f"Username: {handle}\n"
        f"Sohbete gir: /reply {chat_id} merhaba, ben DevSolve tarafıyım…\n"
        f"Botu geri ver: /release {chat_id}"
    )
    ok = await asyncio.to_thread(owner_notify.send, ping)
    if ok:
        telegram_sessions.mark_warm(chat_id)
        logger.info("Warm conversion ping sent for chat %s (%s)", chat_id, who)


async def _hot_ping(chat_id: int, update: Update, text: str) -> None:
    if _is_owner(chat_id) or telegram_sessions.is_declined(chat_id):
        return
    if not telegram_sessions.should_hot_ping(chat_id):
        return
    if not _is_hot(text):
        return
    row = _briefs.get(chat_id) or {}
    who = str(row.get("company") or row.get("host") or "—")
    user = _username(update) or "yok"
    handle = f"@{user}" if user != "yok" else "yok"
    snippet = " ".join((text or "").split())[:240]
    ping = (
        "SICAK ADAY — insan devir\n"
        f"Şirket: {who}\n"
        f"Chat id: {chat_id}\n"
        f"Username: {handle}\n"
        f"Sinyal: {snippet}\n"
        f"Sohbete gir: /reply {chat_id} merhaba, ben DevSolve tarafıyım…\n"
        f"Botu geri ver: /release {chat_id}\n"
        "Telegram özel sohbete üçüncü kişi eklenemez; metin bot üzerinden gider."
    )
    ok = await asyncio.to_thread(owner_notify.send, ping)
    if ok:
        telegram_sessions.mark_hot(chat_id)
        logger.info("Hot-lead ping sent for chat %s (%s)", chat_id, who)
    else:
        logger.warning("Hot-lead ping skipped (no owner chat — /notifyme)")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    if optout.is_chat_opted_out(chat_id):
        await update.message.reply_text(
            "You previously unsubscribed. Send /resume if you want to talk again."
        )
        return
    if _is_owner(chat_id):
        await update.message.reply_text(_owner_intro())
        return

    token = (context.args[0] if context.args else "") or ""
    row = _bind_token(chat_id, token) if token else _briefs.get(chat_id)
    turkish = bool(row.get("turkish")) if row else _customer_lang(update)
    company = str((row or {}).get("company") or (row or {}).get("host") or "")
    telegram_sessions.touch_start(
        chat_id, company=company, turkish=turkish, username=_username(update)
    )
    if row:
        await _warm_ping(chat_id, update, row)
        await _greet_from_token(update, context.bot, chat_id, row)
        return
    text = _cold_intro(turkish=turkish)
    _remember(chat_id, "assistant", text)
    await update.message.reply_text(text)
    _schedule_proof(chat_id, context.bot, turkish=turkish)


async def cmd_notifyme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    known = owner_notify.load_chat_id()
    if known is not None and int(chat_id) != int(known):
        await update.message.reply_text(_cold_intro(turkish=_customer_lang(update)))
        return
    owner_notify.save_chat_id(chat_id)
    await update.message.reply_text(
        "Bu sohbet motor bildirimleri ve sıcak-aday ping'i için kaydedildi.\n\n"
        + owner_notify.lead_digest()
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_cold_intro(turkish=_customer_lang(update)))
        return
    await update.message.reply_text(owner_notify.lead_digest())


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_cold_intro(turkish=_customer_lang(update)))
        return
    args = context.args or []
    if len(args) < 2 or not str(args[0]).lstrip("-").isdigit():
        await update.message.reply_text("Kullanım: /reply CHATID metin")
        return
    target = int(args[0])
    body = " ".join(args[1:]).strip()
    if not body:
        await update.message.reply_text("Kullanım: /reply CHATID metin")
        return
    try:
        await context.bot.send_message(chat_id=target, text=body)
    except Exception as exc:
        await update.message.reply_text(f"Gönderilemedi: {exc}")
        return
    telegram_sessions.set_takeover(target, True)
    _remember(target, "assistant", body)
    task = _proof_tasks.pop(target, None)
    if task:
        task.cancel()
    await update.message.reply_text(
        f"Gönderildi. DeepSeek bu sohbette durdu. Geri vermek için /release {target}"
    )


async def cmd_release(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_cold_intro(turkish=_customer_lang(update)))
        return
    args = context.args or []
    if not args or not str(args[0]).lstrip("-").isdigit():
        await update.message.reply_text("Kullanım: /release CHATID")
        return
    target = int(args[0])
    telegram_sessions.set_takeover(target, False)
    await update.message.reply_text(f"Bot tekrar yanıtlıyor: {target}")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _confirm_stop(update)


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    optout.remove_chat(update.effective_chat.id)
    await update.message.reply_text(
        f"Welcome back — DevSolve. Flat fee {config.price_label()}. "
        "Hangi altyapı ve şu an en çok nerede takılıyor?"
    )


async def _pulse_typing(chat_id: int, bot: Any, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            logger.debug("typing pulse failed for %s", chat_id, exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            continue


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()
    if not user_text:
        return

    if optout.RESUME_RE.search(user_text) and optout.is_chat_opted_out(chat_id):
        optout.remove_chat(chat_id)
        await update.message.reply_text(
            f"You are subscribed again. DevSolve — flat fee {config.price_label()}. "
            "Hangi altyapı ve kopuk neresi?"
        )
        return

    if optout.is_chat_opted_out(chat_id) or optout.OPT_OUT_RE.search(user_text):
        await _confirm_stop(update)
        return

    start_m = re.match(r"^/start(?:@\w+)?(?:\s+(\S+))?", user_text, re.I)
    if start_m and start_m.group(1):
        row = _bind_token(chat_id, start_m.group(1))
        if row:
            turkish = bool(row.get("turkish"))
            telegram_sessions.touch_start(
                chat_id,
                company=str(row.get("company") or row.get("host") or ""),
                turkish=turkish,
                username=_username(update),
            )
            await _greet_from_token(update, context.bot, chat_id, row)
            return

    if _is_owner(chat_id):
        return

    telegram_sessions.touch_user(chat_id, user_text, username=_username(update))

    if _DECLINE_RE.search(user_text) and not _BUY_RE.search(user_text):
        telegram_sessions.mark_declined(chat_id)
        task = _proof_tasks.pop(chat_id, None)
        if task:
            task.cancel()
        turkish = bool((_briefs.get(chat_id) or {}).get("turkish", True))
        if turkish:
            text = (
                "Anladım, zorlamam. Bu sohbet açık kalır; kopuk tekrar yanarsa yazmanız yeterli. "
                "Listeden çıkmak için STOP."
            )
        else:
            text = (
                "Understood — I will not push. This chat stays open if the break comes back. "
                "STOP removes you from the list."
            )
        _remember(chat_id, "user", user_text)
        _remember(chat_id, "assistant", text)
        await update.message.reply_text(text)
        return

    await _hot_ping(chat_id, update, user_text)

    if telegram_sessions.is_takeover(chat_id):
        snippet = " ".join(user_text.split())[:500]
        await asyncio.to_thread(
            owner_notify.send,
            f"Aday {chat_id} (senin sohbetin):\n{snippet}\n\n/reply {chat_id} …",
        )
        return

    if telegram_sessions.is_payment_sent(chat_id):
        logger.info("Payment already sent; closing automated sales loop for chat %s", chat_id)
        return

    _remember(chat_id, "user", user_text)
    brief = _closer_brief(_briefs.get(chat_id))
    messages = [
        {"role": "system", "content": knowledge.telegram_system_prompt(brief=brief)},
        *_histories[chat_id],
    ]

    stop = asyncio.Event()
    pulse = asyncio.create_task(_pulse_typing(chat_id, context.bot, stop))
    try:
        raw = await asyncio.to_thread(_complete, messages)
        reply, send_link = _parse_model_output(raw, user_text)
    except Exception:
        logger.exception("Ollama failed for chat %s", chat_id)
        reply, send_link = _offline_reply(user_text, _briefs.get(chat_id))
    finally:
        stop.set()
        pulse.cancel()

    if send_link and chat_id in _payment_sent and not _BUY_RE.search(user_text):
        send_link = False
    if send_link:
        url = config.PAYONEER_PAYMENT_URL
        if url and url not in reply:
            reply = f"{reply}\n\nPayoneer:\n{url}"
        _payment_sent.add(chat_id)
        telegram_sessions.mark_payment(chat_id)
        logger.info("Dispatched payment URL to chat %s", chat_id)

    _remember(chat_id, "assistant", reply)
    try:
        await update.message.reply_text(reply)
    except Exception:
        logger.exception("Telegram send failed for chat %s", chat_id)


def _offline_reply(user_text: str, row: dict[str, Any] | None) -> tuple[str, bool]:
    turkish = bool(re.search(r"[çğıöşüÇĞİÖŞÜ]", user_text or "")) or bool(
        re.search(r"\b(merhaba|selam|ödeme|fiyat|entegrasyon)\b", user_text or "", re.I)
    )
    buy = bool(_BUY_RE.search(user_text or ""))
    if turkish and buy:
        return (
            f"Kapsam sabit {config.price_label()}. Payoneer linkini paylaşıyorum.",
            True,
        )
    if buy:
        return (
            f"The scope is a flat {config.price_label()}. I will share the Payoneer link.",
            True,
        )
    if row:
        return telegram_handoff.opener(row), False
    if turkish:
        return (
            "Hangi altyapıyı kullanıyorsunuz ve şu an en çok nerede takılıyor: "
            "ödeme callback, stok, ERP, yoksa Excel?",
            False,
        )
    return (
        "Which stack are you on, and what is burning: payment callback, stock, ERP, or Excel?",
        False,
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Telegram error: %s", context.error, exc_info=context.error)


async def _followup_loop(application: Application) -> None:
    await asyncio.sleep(45)
    while True:
        try:
            owner_id = owner_notify.load_chat_id()
            for row in telegram_sessions.due_followups():
                try:
                    chat_id = int(row.get("chat_id"))
                except (TypeError, ValueError):
                    continue
                if owner_id is not None and int(chat_id) == int(owner_id):
                    continue
                if optout.is_chat_opted_out(chat_id):
                    telegram_sessions.mark_followup(chat_id)
                    continue
                text = telegram_sessions.followup_text(row)
                try:
                    await application.bot.send_message(chat_id=chat_id, text=text)
                except Exception:
                    logger.warning("Follow-up failed for chat %s", chat_id, exc_info=True)
                    telegram_sessions.mark_followup(chat_id)
                    continue
                telegram_sessions.mark_followup(chat_id)
                _remember(chat_id, "assistant", text)
                logger.info("24h follow-up sent to chat %s", chat_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Follow-up loop error")
        await asyncio.sleep(300)


async def _post_init(application: Application) -> None:
    application.bot_data["followup_task"] = asyncio.create_task(
        _followup_loop(application), name="tg-followup"
    )


def main() -> None:
    os.chdir(config.ROOT)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ollama_client.ensure_model()
    config.require_bot_keys()
    config.ensure_telegram_username()
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .connect_timeout(20.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(20.0)
        .get_updates_connect_timeout(20.0)
        .get_updates_read_timeout(40.0)
        .get_updates_pool_timeout(20.0)
        .post_init(_post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("notifyme", cmd_notifyme))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("reply", cmd_reply))
    application.add_handler(CommandHandler("release", cmd_release))
    application.add_handler(CommandHandler("stop", cmd_stop))
    application.add_handler(CommandHandler("unsubscribe", cmd_stop))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)
    logger.info("Telegram sales bot polling as @%s", config.TELEGRAM_BOT_USERNAME or "bot")
    # systemd sends SIGTERM; PTB signal handlers break the loop on restart.
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        stop_signals=None,
        timeout=30,
        bootstrap_retries=8,
    )


if __name__ == "__main__":
    main()
