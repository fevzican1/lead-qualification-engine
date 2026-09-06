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
import secrets
from collections import defaultdict
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
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
import payment_safety

logger = logging.getLogger(__name__)

MAX_HISTORY = 16

_histories: dict[int, list[dict[str, str]]] = defaultdict(list)
_briefs: dict[int, dict[str, Any]] = {}
_payment_sent: set[int] = set()
_proof_tasks: dict[int, asyncio.Task[Any]] = {}

_BUY_RE = re.compile(
    r"nasıl\s+satın\s*al|nasil\s+satin\s*al|satın\s*al[ıi]r[ıi]m|satın\s*almak\s+ist|"
    r"baslayabilir|başlayabilir|haydi\s+başla|hadi\s+başla|anlaştık|anlastik|"
    r"nasıl\s+başla|ödeme\s*link|odeme\s*link|"
    r"how\s+(do\s+i|can\s+i|to)\s+(buy|pay|start|purchase)|"
    r"ready\s+to\s+(buy|start|pay)|i\s+want\s+to\s+(buy|start|proceed)|"
    r"proceed\s+to\s+pay|send\s+(the\s+)?(invoice|payment|link)|fatura\s*kes|"
    r"we\s+accept(ed)?\s+(the\s+offer|your\s+terms)|approve\s+(the\s+)?(pilot|retainer|scope|proposal)|"
    r"let[’']?s\s+proceed|proceed\s+with\s+(the\s+)?(pilot|retainer|work|offer)|"
    r"hire\s+you\b|start\s+the\s+(pilot|retainer)|go\s+ahead\s+with\s+(the\s+)?(pilot|retainer)|"
    r"sounds\s+good,\s*let|let[’']?s\s+start\s+(the\s+)?(pilot|work|retainer)|"
    r"kabul\s+ediyoruz|onayl[ıi]yoruz|onayl[ıi]yorum|kiralamak\s+istiyoruz|"
    r"çalışmaya\s+başlayalım|başlayalım\s*o\s*halde|pilot\s*(a|'a)?\s*başlayalım",
    re.I,
)

_NEGATIVE_BUY_RE = re.compile(
    r"\b(not|no|never|don't|do not|can't|cannot|won't|if|whether|haven't)\b|"
    r"hayır|hayir|istemiyorum|değil|degil|onaylam|kabul\s+etm|henüz|henuz|\?", re.I)
_PRICE_RE = re.compile(r"fiyat|ne kadar|ücret|ucret|kaç\s*dolar|price|how much|cost|salary|retainer.*(?:amount|fee)", re.I)


def _wants_to_buy(text: str) -> bool:
    # Questions about a provider, negatives and conditional interest are not authorization.
    clean = (text or "").strip()
    question = clean.endswith("?")
    if question and re.match(r"(?:how (?:do i|can i|to) (?:buy|pay|purchase)|nasıl satın al)", clean, re.I):
        clean = clean[:-1]
    return bool(_BUY_RE.search(clean) and not _NEGATIVE_BUY_RE.search(clean))

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

_PAID_RE = re.compile(
    r"ödeme(?:yi)?\s+(?:yapt[ıi]m|tamamlad[ıi]m|tamamland[ıi]|yap[ıi]ld[ıi]|gönderdim|gonderdim)|"
    r"ödend[ıi]|odendi|odeme\s+(?:yaptim|tamamladim|tamamlandi|yapildi|gonderdim)|"
    r"payment\s+(?:done|sent|made|completed)|"
    r"(?:made|completed|sent)\s+(?:the\s+)?(?:payment|transfer)|"
    r"ödeme\s*yapıld[ıi]?",
    re.I,
)


def _remember(chat_id: int, role: str, content: str) -> None:
    history = _histories[chat_id]
    history.append({"role": role, "content": content})
    overflow = len(history) - MAX_HISTORY
    if overflow > 0:
        del history[:overflow]


def _is_owner(chat_id: int) -> bool:
    known = owner_notify.load_admin_chat_id()
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
    prior = str(telegram_sessions._row(chat_id).get("session_token") or "")
    if prior and prior != token:
        return None  # never attach another company's brief to an existing payment/contract
    row = telegram_handoff.lookup(token)
    if row:
        _briefs[chat_id] = row
        telegram_sessions._put(chat_id, session_token=token, audience=row.get("audience", ""),
                               variant=row.get("variant", ""), report_id=row.get("report_id", ""))
    return row


def _closer_brief(row: dict[str, Any] | None) -> str:
    """Give the model the same evidence gate used by form qualification."""
    if not row:
        if config.ENTERPRISE_MODE:
            return telegram_handoff.brief_block({"audience": "enterprise", "variant": "X"})
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
    # The model may not introduce payment URLs or invent readiness.
    reply = re.sub(r"https?://\S+", "", reply, flags=re.I).strip()
    pay = _wants_to_buy(user_text)
    return reply, pay


def _display_text(text: str | None) -> str:
    """Clean plain rendering: drop markdown emphasis markers, keep URLs intact."""
    if not text:
        return ""
    return "".join(
        seg if seg.startswith("http") else re.sub(r"[*_`]", "", seg)
        for seg in re.split(r"(https?://[^\s]+)", text)
    )


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
        f"Custom API / otomasyon, {config.price_label(explicit=True)} teklif (tahsilat değil).\n"
        "Motor özeti: /notifyme   durum: /status\n"
        "Sıcak aday: bu sohbete ping düşer.\n"
        "Sohbete gir: /reply CHATID metin\n"
        "Botu geri ver: /release CHATID\n"
        "Unsubscribe test: /stop"
    )


def _not_owner_hint() -> str:
    """Clear dead-end instead of a silent sales intro when a command is admin-only."""
    return (
        "Bu komut yalnızca operatör içindir.\n"
        "Operatör isen: /admin KOD ile bu sohbeti operatör sohbeti olarak kaydet. "
        "(KOD sana ayrı kanaldan iletilen gizli dizidir; .env ADMIN_CODE.)"
    )


def _cold_intro(*, turkish: bool) -> str:
    if config.ENTERPRISE_MODE:
        return ("DevSolve AI destekli kontratlı hizmet asistanı. Henüz sisteminizi incelemedik. "
                "Hangi entegrasyon veya otomasyon işi için destek arıyorsunuz?" if turkish else
                "DevSolve AI-assisted contractor intake assistant. We have not inspected your system. "
                "Which integration or automation deliverable are you looking for?")
    if turkish:
        return (
            "DevSolve Flow Inspector — otomatik teknik inceleme servisi.\n"
            "Sitenizin halka açık form/iletim akışını; W3C form yönergeleri, OWASP "
            "veri aktarım prensipleri ve Google Lighthouse kıstaslarıyla bugün ön "
            "incelemeye aldık. 60 saniyelik özet kart hazır — göstereyim mi?"
        )
    return (
        "DevSolve Flow Inspector — automated technical review service.\n"
        "Today we ran a pre-review of your site's public form/transmission flow "
        "against W3C form guidance, OWASP data-handling principles, and Google "
        "Lighthouse criteria. A 60-second summary card is ready — shall I show it?"
    )



def _is_hot(text: str) -> bool:
    return bool(_HOT_RE.search(text or "")) or _wants_to_buy(text)


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
    stopped_brief = _briefs.pop(chat.id, None)
    if stopped_brief and stopped_brief.get("url"):
        optout.harvest_from_text(str(stopped_brief["url"]), reason="telegram_stop")
    _payment_sent.discard(chat.id)
    task = _proof_tasks.pop(chat.id, None)
    if task:
        task.cancel()
    telegram_sessions.mark_declined(chat.id)  # retain audit/payment history
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
    if not row:
        return  # no source-bound brief, no fabricated proof card
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
        await update.message.reply_text(_display_text(text))
    else:
        await bot.send_message(chat_id=chat_id, text=_display_text(text))
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
    if not row and config.ENTERPRISE_MODE:
        telegram_sessions._put(chat_id, audience="enterprise", variant="X")
    telegram_sessions.touch_start(
        chat_id, company=company, turkish=turkish, username=_username(update)
    )
    if row:
        await _warm_ping(chat_id, update, row)
        await _greet_from_token(update, context.bot, chat_id, row)
        return
    text = _cold_intro(turkish=turkish)
    _remember(chat_id, "assistant", text)
    await update.message.reply_text(_display_text(text))
    _schedule_proof(chat_id, context.bot, turkish=turkish)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Self-service owner registration: /admin KOD (secret set in .env on Oracle)."""
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    if _is_owner(chat_id):
        await update.message.reply_text(
            "Bu sohbet operatör olarak zaten tanınıyor. /status ile durumu gör."
        )
        return
    code = (context.args[0] if context.args else "") or ""
    secret = str(getattr(config, "ADMIN_CODE", "") or "").strip()
    if not secret:
        await update.message.reply_text(
            "ADMIN_CODE .env'de tanımlı değil — sistem yöneticisine başvur."
        )
        return
    if owner_notify.load_admin_chat_id() is not None:
        await update.message.reply_text("Operatör zaten kayıtlı; yeniden kayıt kapalı.")
        return
    if not secrets.compare_digest(code, secret):
        await update.message.reply_text(
            "Kod hatalı. /admin KOD  →  KOD, operatöre ayrı kanaldan iletilen gizli dizi."
        )
        return
    owner_notify.save_chat_id(chat_id)
    logger.info("Owner chat %s registered via /admin code", chat_id)
    await update.message.reply_text(
        "✅ Bu sohbet artık operatör. Özet: /notifyme   durum: /status\n"
        "Sıcak lead ping buraya düşer; /reply CHATID metin ile müşteri sohbetine girersin."
    )


async def cmd_notifyme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_not_owner_hint())
        return
    text = (
        "Özet aşağıda. *Pipeline / sıcak lead bildirimleri* müşteri sohbetlerine "
        "gitmez — yalnızca .env'deki ops chat ID'sine gider.\n\n"
        + owner_notify.lead_digest()
    )
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except BadRequest:
        # Unbalanced * / _ in a hostname would kill the whole status report.
        await update.message.reply_text(text)


def _financial_owner(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_user and update.message
                and update.effective_chat.type == "private"
                and update.effective_user.id == update.effective_chat.id
                and _is_owner(update.effective_chat.id))


async def cmd_payready(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _financial_owner(update):
        return
    try:
        chat, amount, currency, recipient, reference = context.args
        if not telegram_sessions._row(int(chat)).get("started_at"):
            raise ValueError("Existing customer chat required")
        payment_safety.approve_link(chat_id=int(chat), amount=int(amount), currency=currency.upper(), recipient=recipient,
                                    reference=reference, owner_id=update.effective_user.id)
    except (ValueError, TypeError):
        await update.message.reply_text(
            "Payoneer panelinde gerçek tutar, alıcı ve hesap uygunluğunu kontrol ettikten sonra: "
            "/payready CHATID 2500 USD ALICI_ETIKETI TALEP_REFERANSI. Bu komut ödeme oluşturmaz.")
        return
    await update.message.reply_text("Talep sahibi tarafından kontrol edildi olarak kaydedildi. Tahsilat değildir.")


async def cmd_verifypayment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _financial_owner(update):
        return
    try:
        chat, amount, currency, reference = context.args
        telegram_sessions.verify_payment(int(chat), amount=int(amount), currency=currency.upper(),
                                         reference=reference, owner_id=update.effective_user.id)
    except (ValueError, TypeError):
        await update.message.reply_text(
            "Payoneer panelinde yerleşmiş ödemeyi kontrol ettikten sonra: "
            "/verifypayment CHATID 2500 USD ISLEM_REFERANSI. Talep tutarı eşleşmeli; referans tek kullanımlık.")
        return
    await update.message.reply_text("Sahip doğrulaması kaydedildi. Sözleşme/erişim onayı olmadan iş başlamaz.")


async def cmd_approvecontract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _financial_owner(update):
        return
    try:
        chat, contract, scope, access = context.args
        telegram_sessions.approve_contract(int(chat), contract_ref=contract, scope_ref=scope,
                                           access_ref=access, owner_id=update.effective_user.id)
    except (ValueError, TypeError):
        await update.message.reply_text("/approvecontract CHATID IMZALI_SOZLESME_REF KAPSAM_REF ERISIM_IZNI_REF")
        return
    await update.message.reply_text("Sözleşme ve izin referansları kaydedildi. Otomatik üretim erişimi açılmadı.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_not_owner_hint())
        return
    await update.message.reply_text(
        f"Operatör sohbeti (chat_id={update.effective_chat.id}) tanınıyor.\n\n"
        + owner_notify.lead_digest()
    )


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_not_owner_hint())
        return
    args = context.args or []
    if len(args) < 2 or not str(args[0]).lstrip("-").isdigit():
        await update.message.reply_text(
            "Kullanım: /reply CHATID metin\n"
            "CHATID, sıcak lead pingindeki id'dir (örn. /reply 123456789 merhaba).\n"
            "Hedef müşteri henüz bota /start yapmamışsa gönderilemez."
        )
        return
    target = int(args[0])
    body = " ".join(args[1:]).strip()
    if not body:
        await update.message.reply_text("Kullanım: /reply CHATID metin")
        return
    try:
        await context.bot.send_message(chat_id=target, text=body)
    except Exception as exc:
        await update.message.reply_text(
            f"Gönderilemedi: {exc}".strip()[:400]
        )
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
        await update.message.reply_text(_not_owner_hint())
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
    if chat_id not in _briefs:
        token = str(telegram_sessions._row(chat_id).get("session_token") or "")
        if token:
            row = telegram_handoff.lookup(token)
            if row:
                _briefs[chat_id] = row

    if _DECLINE_RE.search(user_text):
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
        # Customer already got the Payoneer link; a payment-confirm message
        # must reach the owner so the delivered service can start manually.
        if _PAID_RE.search(user_text):
            telegram_sessions.mark_payment_reported(chat_id)
            row = _briefs.get(chat_id) or {}
            who = str(row.get("company") or row.get("host") or "—")
            await asyncio.to_thread(
                owner_notify.send,
                f"ÖDEME BİLDİRİMİ (DOĞRULANMADI) — {who} (chat {chat_id}).\n"
                "Payoneer panelinde alıcı, tutar, para birimi ve yerleşmiş işlem referansını kontrol et. "
                "Müşteri mesajı tahsilat kanıtı değildir; teslimatı başlatma.",
            )
            await update.message.reply_text(
                "Payment reported, not yet verified. We must confirm settlement, scope and access before starting.\n"
                "Ödeme bildiriminiz alındı; tahsilat henüz doğrulanmadı."
            )
            logger.info("Payment reported, unverified, chat %s", chat_id)
            return
        logger.info("Payment already sent; closing automated sales loop for chat %s", chat_id)
        return

    if _PRICE_RE.search(user_text) and not _wants_to_buy(user_text):
        amount = config.price_label(explicit=True)
        await update.message.reply_text(
            f"Önerilen aylık hizmet bedeli {amount}; nihai kapsam ve sözleşme onayına bağlıdır. "
            "$5000 ancak ayrı kapsam ve tutarı doğrulanmış ödeme talebiyle değerlendirilir."
            if _customer_lang(update) else
            f"The proposed monthly service retainer is {amount}, subject to agreed scope and contract. "
            "$5000 requires separately agreed scope and a matching verified payment request.")
        return

    if _wants_to_buy(user_text):
        telegram_sessions._put(chat_id, interest_reported=True, followup_sent=True)
        request = payment_safety.ready_request(chat_id)
        contract = telegram_sessions._row(chat_id)
        if (request is None or not contract.get("contract_signed")
                or contract.get("contract_amount") != request["amount"]):
            await update.message.reply_text(
                "Interest noted, not yet a signed engagement. Before payment we must agree scope, "
                "contract and access, and verify the Payoneer request's recipient and amount. "
                f"Proposed retainer: {config.price_label(explicit=True)}/month.")
            await asyncio.to_thread(owner_notify.send, f"Satın alma ilgisi (kabul/ödeme değil), chat {chat_id}. "
                                    "Kapsam/sözleşme ve Payoneer talep doğrulaması gerekiyor.")
            return
        await update.message.reply_text(
            f"Agreed request: ${request['amount']} {request['currency']}. "
            "Check the recipient and amount on Payoneer before paying.\n" + config.PAYONEER_PAYMENT_URL)
        telegram_sessions._put(chat_id, payment_request=request)
        telegram_sessions.mark_payment(chat_id)
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

    # Payment is exclusively handled by the deterministic owner-verified path above.

    _remember(chat_id, "assistant", reply)
    try:
        await update.message.reply_text(_display_text(reply))
    except Exception:
        logger.exception("Telegram send failed for chat %s", chat_id)


def _offline_reply(user_text: str, row: dict[str, Any] | None) -> tuple[str, bool]:
    turkish = bool(re.search(r"[çğıöşüÇĞİÖŞÜ]", user_text or "")) or bool(
        re.search(r"\b(merhaba|selam|ödeme|fiyat|entegrasyon)\b", user_text or "", re.I)
    )
    buy = _wants_to_buy(user_text)
    if turkish and buy:
        return (
            f"Önerilen bedel {config.price_label(explicit=True)}. Kapsam ve ödeme talebi doğrulanmalı.",
            False,
        )
    if buy:
        return (
            f"Proposed fee {config.price_label(explicit=True)}. Scope and payment request need verification.",
            False,
        )
    if row:
        return telegram_handoff.opener(row), False
    if config.ENTERPRISE_MODE:
        return _cold_intro(turkish=turkish), False
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
            owner_id = owner_notify.load_admin_chat_id()
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
                    await application.bot.send_message(chat_id=chat_id, text=_display_text(text))
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
    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(CommandHandler("payready", cmd_payready))
    application.add_handler(CommandHandler("verifypayment", cmd_verifypayment))
    application.add_handler(CommandHandler("approvecontract", cmd_approvecontract))
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
