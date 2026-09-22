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
import threading
from collections import defaultdict
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import bounded_agents
import knowledge
import ollama_client
import heartbeat
import task_queue
import optout
import owner_notify
import proof_card
import telegram_bot_api
import telegram_handoff
import telegram_sessions
import payment_safety
import flood_guard

try:
    from nirvana.self_serve_close import TERMS_RE as _TERMS_RE
except Exception:  # nirvana her koşulda botu bloklamaz
    _TERMS_RE = re.compile(r"(sözleşme|sla|nda|şartlar|terms\s+accepted)", re.I)

logger = logging.getLogger(__name__)

MAX_HISTORY = 16

_histories: dict[int, list[dict[str, str]]] = defaultdict(list)
_briefs: dict[int, dict[str, Any]] = {}
_payment_sent: set[int] = set()
_proof_tasks: dict[int, asyncio.Task[Any]] = {}

# Çoklu bot havuzu: username -> Application. Form linkleri config.next_bot_username()
# ile round-robin dağıtılır; her sohbet SADECE kendi botuyla konuşur (sahiplik
# sessions'da `bot_username` ile kayıtlı). Telegram bot başına flood limiti
# olduğu için 3 bot = 3x kapasite, tek süreç = tek state (RAM düşük kalır).
_APPS: dict[str, Application] = {}


def _app_for_chat(chat_id: int, default: Application | None = None) -> Application | None:
    """Sohbetin sahibi botu — oturum /start'ta kaydedilir, followup ve /reply oradan gider."""
    username = str(telegram_sessions._row(chat_id).get("bot_username") or "")
    app = _APPS.get(username)
    if app is not None:
        return app
    return default

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


def _currency_symbol(currency: str) -> str:
    """Return the display symbol for a currency code (EUR/USD/GBP)."""
    return {"EUR": "€", "USD": "$", "GBP": "£"}.get((currency or "").upper(), "")


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

# DeepSeek Handoff: mužteri patron/yetkili/insan/yönetici isterse
# Türkçe karakterler önce normalize edilir (_tr_norm), böylece ö/ü/ş/ğ/ç
# varyantlarının tamamı ASCII regex ile yakalanir.
_tr_ascii = str.maketrans("öüıışğçÖÜİŞĞÇ", "ouuisgcOUISGC")


def _tr_norm(text: str) -> str:
    return (text or "").translate(_tr_ascii)


_HANDOFF_RE = re.compile(
    r"(?:patron|sahip|kurucu|yonetici|imza sahibi|imza sahibiyle)\w*\s*(?:ile\s*)?"
    r"(?:gorusmek|konusmak|gorusebilir|konusabilir|gorus|konus)"
    r"\s*(?:istiyorum|istiyoruz|mumkun mu|muyum|miyim|mu|mi)?|"
    r"\b(?:owner|founder|boss|ceo|cmo|cto|manager|human)\b|"
    r"(?:seninle|bana)\s*(?:gorusmek|konusmak|gorusebilir|konusabilir|gorus|konus)"
    r"\s*(?:istiyorum|istiyoruz|mumkun mu|muyum|miyim|mu|mi)?|"
    r"yetkili\s*(?:biri|kisi)?\s*(?:var\s*mi|ile\s*(?:gorusmek|konusmak|gorusebilir|konusabilir|gorus|konus))|"
    r"(?:insanla|insan ile|insan temsilci|insan asistan)\s*(?:gorus|konus|gorusebilir|konusabilir)|"
    r"talk\s+to\s+(?:a\s+)?(?:human|person|someone|the\s+(?:owner|founder|boss|ceo|manager))|"
    r"speak\s+(?:to|with)\s+(?:a\s+)?(?:human|person|someone|the\s+(?:owner|founder|boss|ceo|manager))|"
    r"real\s+(?:human|person)|"
    r"\bhuman\s*(?:agent|representative|person)?\b",
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

# Teslimat işçisi (Lane AF): rapor hatırlatma + hizmet niyeti.
_REPORT_RE = re.compile(r"\b(rapor|raporlar|report|reports|sonuç|sonuc|result|results)\b", re.I)
_SERVICE_CONTACT_RE = re.compile(r"\b(iletişim|iletisim|contact|form)\b", re.I)
_SERVICE_MONITOR_RE = re.compile(r"\b(izleme|monitor|renewal|yenileme)\b", re.I)


def _service_intent(text: str) -> str | None:
    """Müşteri metninden istenen hizmet (karışmaz: hizmet kataloğuyla sınırlı)."""
    if _SERVICE_CONTACT_RE.search(text or ""):
        return "contact-audit"
    if _SERVICE_MONITOR_RE.search(text or ""):
        return "renewal-monitor"
    if re.search(r"\b(sağlık|saglik|tur|sweep|altyapı|altyapi|infra)\b", text or "", re.I):
        return "infra-sweep"
    return None


def _returning_customer_greeting(mem: dict[str, Any], *, turkish: bool) -> str:
    """Geri dönen teslimat müşterisi: bot geçmişi hatırlar (rapor numarası + sorunlar)."""
    lines = ["Tekrar hoş geldiniz — sizi ve geçmiş teslimatlarınızı hatırlıyoruz."
             if turkish else "Welcome back — we remember you and your past deliveries."]
    last = mem.get("last_report")
    if last:
        lines.append(f"Geçmiş teslimat raporunuz: {last} "
                     f"(toplam {mem.get('report_count')} rapor)."
                     if turkish else
                     f"Your last delivery report: {last} "
                     f"({mem.get('report_count')} reports in total).")
    issues = mem.get("past_issues") or []
    if issues:
        listed = ", ".join(f"{i.get('report_id')} ({i.get('status')})" for i in issues[:3])
        lines.append(f"Geçmişte tespit ettiğimiz sorunlar: {listed}."
                     if turkish else f"Issues we found earlier: {listed}.")
    active = mem.get("active_jobs") or []
    if active:
        lines.append(f"Aktif teslimat işiniz: {', '.join(map(str, active))} — "
                     "raporu numarasıyla bu sohbete düşecek."
                     if turkish else
                     f"Your active delivery jobs: {', '.join(map(str, active))} — "
                     "the report will land here with its number.")
    else:
        lines.append("Şu an istediğiniz hizmeti yazın (örn. iletişim denetimi, izleme turu) — "
                     "ödeme teyidiniz doğrulanmışsa hemen teslimat kuyruğuna alalım."
                     if turkish else
                     "Tell me the service you need now (e.g. contact audit, monitoring sweep) — "
                     "with your verified payment we queue it right away.")
    return "\n".join(lines)


def _report_recall_text(reports: list[dict[str, Any]], *, turkish: bool) -> str:
    lines = [("Teslimat kayıtlarınız (rapor numarasıyla):"
              if turkish else "Your delivery records (by report number):")]
    for r in reports[-5:]:
        badge = {"ok": "✅", "degraded": "⚠️", "down": "🔴", "error": "❌"}.get(str(r.get("status")), "•")
        lines.append(f"{badge} {r.get('report_id')} — {r.get('service')} — {r.get('domain')} "
                     f"({r.get('status')}, {r.get('at')})")
    lines.append("Yeni hizmet için: ödeme teyidiniz doğrulanmışsa istediğiniz hizmeti yazın."
                 if turkish else
                 "For a new service: state it and we queue it once payment is verified.")
    return "\n".join(lines)



def _remember(chat_id: int, role: str, content: str) -> None:
    history = _histories[chat_id]
    history.append({"role": role, "content": content})
    overflow = len(history) - MAX_HISTORY
    if overflow > 0:
        del history[:overflow]


def _is_owner(chat_id: int) -> bool:
    return int(chat_id) in owner_notify.load_admin_chat_ids()


def _customer_lang(update: Update) -> bool:
    code = ""
    if update.effective_user and update.effective_user.language_code:
        code = str(update.effective_user.language_code).lower()
    return code.startswith("tr")


_TR_TEXT = re.compile(
    r"[çğıöşüÇĞİÖŞÜ]|"
    r"\b(merhaba|selam|fiyat|ödeme|ne kadar|ücret|ucret|kabul|onayl|başla|basla|evet|"
    r"hayır|hayir|alıyorum|aliyorum|istiyorum|sözleşme|sozlesme|şart)\b",
    re.I,
)


def _conv_lang(user_text: str, chat_id: int) -> bool:
    """Konuşmanın gerçek dili: önce mesajın dili, sonra handoff, sonra profil.

    Bu sayede müşteri TR siteye İngilizce yazsa bile yanıt İngilizce olur ve
    tam tersi; dil karışıklığı yaşanmaz.
    """
    if _TR_TEXT.search(user_text or ""):
        return True
    row = _briefs.get(chat_id) or {}
    if row.get("turkish") is not None:
        return bool(row["turkish"])
    return False


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


def _identity_prompt_line() -> str:
    """Gerçek insan kimliği — 'robot mu insan mı' algısını kırmak için sistem prompt'una eklenir."""
    url = str(getattr(config, "OWNER_LINKEDIN_URL", "") or "").strip()
    return (f"\nBehind this system is a real, verifiable human engineer: {url}. "
            "You may share this when trust matters; never claim to be a human typing live — "
            "you are an assistant backed by that engineer. "
            "When asked who you are, never say bot/AI/yapay zeka/otomatik: "
            "you are the founder's technical sales and operations assistant "
            "(kurucunun teknik satış ve operasyon asistanı)."
            if url else "")


def _complete(messages: list[dict[str, str]]) -> str:
    # Nirvana semantik önbellek: aynı system+user çiftine Ollama'yı tekrar yorma
    # (Oracle CPU kotası dostu). Hata olursa sessizce normal yola düşer.
    try:
        from nirvana import semantic_cache
        cached = semantic_cache.get(messages)
        if cached:
            return cached
    except Exception:
        pass
    reply = ollama_client.chat(
        messages,
        temperature=0.4,
        max_tokens=380,
        timeout=75.0,
    )
    try:
        from nirvana import semantic_cache
        if len(messages) <= 3:  # yalnız kısa bağlamlarda cache'le
            semantic_cache.put(messages, reply)
    except Exception:
        pass
    return reply


def _owner_intro() -> str:
    return (
        "Operatör paneli — müşteri bunu görmez.\n"
        f"Custom API / otomasyon, {config.price_label(explicit=True)} teklif (tahsilat değil).\n"
        "Motor özeti: /notifyme   durum: /status\n"
        "Sıcak aday: bu sohbete ping düşer.\n"
        "Canlı müşteri talebi: bildirimdeki [ Sohbete Bağlan / Reply] butonuna bas; "
        "sonra buraya yazdığın her mesaj doğrudan müşteriye gider.\n"
        "Sohbete gir: /reply CHATID metin\n"
        "Botu geri ver: /release CHATID   |   Devri kapat: /disarm\n"
        "Unsubscribe test: /stop"
    )


def _webchat_point_text(*, turkish: bool, link: str = "") -> str:
    """Müşteriyi web sohbete yönlendiren tek satır (satış akışı BAŞLATMAZ)."""
    try:
        url = (link or config.webchat_link()).strip()
    except Exception:  # noqa: BLE001
        url = ""
    if not url:
        return ""
    if turkish:
        return (
            f"Sohbet hattımız web'e taşındı: {url}\n"
            "Tarayıcıda açılır (uygulama/indirme yok, giriş istemez) — oradan devam edelim."
        )
    return (
        f"Our chat moved to the web: {url}\n"
        "Opens in your browser (no app, no download, no login) — let's continue there."
    )


async def _redirect_customer_to_webchat(update: Update, chat_id: int) -> bool:
    """Telegram musteri girisini web sohbete yonlendir (FLOOD/ban riski = 0).

    WEBCHAT_PUBLIC_URL tanimli oldugunda (Oracle canli) True doner: musteri satis
    akisi Telegram'da BASLAMAZ, tek satir web sohbet adresi verilir. Adres yoksa
    False doner ve eski davranis korunur (gecis donemi uyumlulugu). Bu mesaj da
    flood_guard.install ile sarilan bot._post uzerinden gider (Telegram 429 yok).
    """
    try:
        if not config.webchat_customer_only():
            return False
    except Exception:  # noqa: BLE001
        return False
    text = _webchat_point_text(turkish=_customer_lang(update))
    if not text:
        return False
    try:
        await update.message.reply_text(text)
    except Exception:  # noqa: BLE001
        logger.warning("webchat yonlendirme mesaji gonderilemedi (chat %s)", chat_id)
    return True


def _not_owner_hint() -> str:
    """Clear dead-end instead of a silent sales intro when a command is admin-only."""
    return (
        "Bu komut yalnızca operatör içindir.\n"
        "Operatör isen: /admin KOD ile bu sohbeti operatör sohbeti olarak kaydet. "
        "(KOD sana ayrı kanaldan iletilen gizli dizidir; .env ADMIN_CODE.)"
    )


def _cold_intro(*, turkish: bool) -> str:
    if config.ENTERPRISE_MODE:
        return ("DevSolve teknik ekip asistanıyım. Henüz sisteminizi incelemedik. "
                "Hangi entegrasyon veya otomasyon işi için destek arıyorsunuz?" if turkish else
                "DevSolve technical team assistant. We have not inspected your system. "
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


def _audited(text: str, *, turkish: bool) -> str:
    """Gönderim öncesi son kapı: bot sızıntısı + ücretsiz/indirim teklifi + imla.

    Açılış/intro metinleri de bu kapıdan geçer; model çıktısı dışındaki sabit
    metinlerde bir hata olursa (ör. 'ücretsiz' kelimesi) müşteriye ASLA gitmez.
    """
    try:
        from nirvana.language_auditor import audit as _audit

        cleaned, issues = _audit(text, turkish=turkish, user_text="")
        if issues:
            logger.info("Greeting language audit: %s", issues)
        return cleaned or text
    except Exception:  # noqa: BLE001 — denetçi hatası iletişimi bloklamaz
        logger.exception("greeting language audit failed")
        return text


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


def _track_link_click(chat_id: int, row: dict[str, Any] | None) -> None:
    """Form → Telegram tıklama sinyali (madde 48): sahibi ANINDA haberdar olur.

    Sinyal /start token'ıyla gelir; kayıt chat↔domain bağlantısıyla tutulur.
    Bildirim gönderimi hata verse bile selamlama akışı asla bozulmaz.
    """
    brief = row or {}
    domain = str(brief.get("host") or brief.get("target_domain") or brief.get("company") or "")
    try:
        from nirvana import interaction_tracker

        interaction_tracker.track(chat_id, domain, "telegram_start")
    except Exception:  # noqa: BLE001 — izleme hatası akışı bozmaz
        logger.debug("interaction track failed for chat %s", chat_id, exc_info=True)


async def _greet_from_token(update: Update, bot: Any, chat_id: int, row: dict[str, Any]) -> None:
    """No empty channel: type for a beat, then the named greeting, then the card."""
    turkish = bool(row.get("turkish", True))
    _track_link_click(chat_id, row)
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(1.2)
    except Exception:  # noqa: BLE001
        logger.debug("greeting typing failed for %s", chat_id, exc_info=True)
    text = _audited(telegram_handoff.opener(row), turkish=turkish)
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
    ok = await asyncio.to_thread(owner_notify.send_handoff_alert, ping,
                                 target_chat_id=chat_id)
    if ok:
        telegram_sessions.mark_warm(chat_id)
        logger.info("Warm conversion ping sent for chat %s (%s)", chat_id, who)


async def _send_payment_link_critical(chat_id: int, text: str, update: Update,
                                      context: ContextTypes.DEFAULT_TYPE,
                                      who: str | None = None) -> None:
    """Ödeme linkini engel yemeden gönder (flood harici kritik hat).

    Sıra: (1) sohbetin sahibi bot reply_text dener; 429/FloodBlocked gelirse
    (2) havuzdaki DİĞER bot doğrudan send_message dener (Telegram cezası
    bot+chat bazlıdır — diğer bot aynı sohbete yazabilir); hepsi patlarsa
    (3) reply_text'e düş (PTB retry'sine bırak). Link metni asla kaybolmaz:
    en kötü halde normal yoldan gider.
    """
    try:
        await update.message.reply_text(text)
        return
    except Exception as first_exc:  # noqa: BLE001 — bypass hattına geç
        logger.warning("Odeme linki birincil bottan gidemedi (%s) — havuz bypass",
                       type(first_exc).__name__)
        ra = getattr(first_exc, "retry_after", None)
        if ra is not None:
            try:
                owner = str(getattr(context.bot, "username", "") or "")
                flood_guard.note_retry_after(float(ra), chat_id=chat_id,
                                             bot_username=owner)
            except (TypeError, ValueError):
                pass
    for uname, app in list(_APPS.items()):
        if app is context.application:
            continue
        # ZERO-TOUCH: FLOOD_WAIT karantinasındaki bota tek istek dahi yok.
        try:
            import bot_registry
            if not bot_registry.is_active(str(uname)):
                continue
        except Exception:  # noqa: BLE001 — kayıt yoksa fail-open
            pass
        try:
            if not await flood_guard.acquire(chat_id):
                continue
            await app.bot.send_message(chat_id=chat_id, text=text)
            logger.info("Odeme linki havuz bypass ile gonderildi (@%s -> %s)", uname, chat_id)
            try:
                telegram_sessions._put(
                    chat_id, bot_username=str(getattr(app.bot, "username", "") or uname))
            except Exception:  # noqa: BLE001
                pass
            return
        except Exception as exc:  # noqa: BLE001
            ra = getattr(exc, "retry_after", None)
            if ra is not None:
                try:
                    flood_guard.note_retry_after(float(ra), chat_id=chat_id,
                                                 bot_username=str(uname))
                except (TypeError, ValueError):
                    pass
            continue
    await update.message.reply_text(text)


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
    # KRİTİK HAT (sıcak temas): flood harici — cezalı olsa bile gider.
    # Butonlu handoff alert aynı hatta yedeklenir (tek çağrı, çift şans).
    ok = await asyncio.to_thread(owner_notify.send_handoff_alert, ping,
                                 target_chat_id=chat_id)
    if ok:
        telegram_sessions.mark_hot(chat_id)
        logger.info("Hot-lead ping sent for chat %s (%s)", chat_id, who)
    else:
        logger.warning("Hot-lead ping FAILED for chat %s — mark edilmedi, tekrar dener", chat_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    if _is_owner(chat_id):
        # Operatör sohbeti opt-out'tan ÖNCE gelir: /stop testi sırasında
        # optouts.json'a düşen patron bir daha "you previously unsubscribed"
        # duvarına çarpmaz (canlı arıza, 2026-09).
        await update.message.reply_text(_owner_intro())
        return
    if optout.is_chat_opted_out(chat_id):
        await update.message.reply_text(
            "You previously unsubscribed. Send /resume if you want to talk again."
        )
        return

    # Musteri girisi web sohbete tasindi (WEBCHAT_PUBLIC_URL dolu -> akis orada).
    if await _redirect_customer_to_webchat(update, chat_id):
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
    # Bot sahipliği: bu sohbeti hangi bot aldıysa, followup + /reply o bottan gider.
    telegram_sessions._put(
        chat_id, bot_username=str(getattr(context.bot, "username", "") or "")
    )
    # Teslimat işçisi (Lane AF): geri dönen müşteri hafızayla karşılanır —
    # geçmiş rapor numaraları, önceki sorunlar, ödeme teyidi hatırlanır.
    try:
        from nirvana import delivery_worker as _dworker
        mem = _dworker.customer_memory(chat_id)
        if mem.get("returning"):
            text = _audited(_returning_customer_greeting(mem, turkish=turkish), turkish=turkish)
            _remember(chat_id, "assistant", text)
            await update.message.reply_text(_display_text(text))
            return
    except Exception:
        logger.exception("delivery memory greeting failed for chat %s", chat_id)
    if row:
        await _warm_ping(chat_id, update, row)
        await _greet_from_token(update, context.bot, chat_id, row)
        return
    text = _audited(_cold_intro(turkish=turkish), turkish=turkish)
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
    if not secrets.compare_digest(code, secret):
        await update.message.reply_text(
            "Kod hatalı. /admin KOD  →  KOD, operatöre ayrı kanaldan iletilen gizli dizi."
        )
        return
    owner_notify.save_chat_id(chat_id)
    logger.info("Owner chat %s registered via /admin code", chat_id)
    await update.message.reply_text(
        "✅ Bu sohbet artık operatör. Özet + form verileri: /notifyme   durum: /status\n"
        "Sıcak lead ping bildirim botunun ops chatine düşer; /reply CHATID metin ile "
        "müşteri sohbetine girersin."
    )


def _form_data_digest() -> str:
    """Sıcak temaslar + form verileri — /notifyme eki (push yok, sahibi kendisi çeker).

    Bildirim davranışı DEĞİŞMEZ: ping'ler eskisi gibi bildirim botuna gider.
    Bu blok yalnızca operatör /notifyme yazdığında form bilgilerini görür:
    form URL, platform, skor, rapor, son müşteri mesajı ve funnel durumu.
    """
    try:
        sessions = telegram_sessions._load()
    except Exception:
        logger.exception("notifyme form digest: sessions okunamadi")
        return ""
    entries = [(str(cid), row) for cid, row in sessions.items()
               if isinstance(row, dict) and row.get("started_at") and not row.get("declined")]
    if not entries:
        return ""
    entries.sort(key=lambda e: str(e[1].get("last_at") or ""), reverse=True)
    lines = ["", "SICAK TEMASLAR — form verileri (yalnızca sana, push değil):"]
    shown = 0
    for cid, row in entries:
        if shown >= 8:
            lines.append(f"… +{len(entries) - shown} sohbet daha — /reply CHATID ile bakabilirsin")
            break
        token = str(row.get("session_token") or "")
        hand = (telegram_handoff.lookup(token) if token else None) or {}
        lead = hand.get("lead_info") if isinstance(hand.get("lead_info"), dict) else {}
        stack = hand.get("detected_stack") if isinstance(hand.get("detected_stack"), dict) else {}
        company = str(row.get("company") or hand.get("company") or hand.get("host") or "—")
        form_url = str(lead.get("form_page_url") or hand.get("url") or "—")
        platform = str(stack.get("platform") or hand.get("platform") or "—")
        score = str(lead.get("lead_score") or "—")
        report = str(hand.get("report_id") or row.get("report_id") or "—")
        last_user = " ".join(str(row.get("last_user") or "").split())
        flags: list[str] = []
        if row.get("payment_verified"):
            flags.append("ödeme DOĞRULANDI ✅")
        elif row.get("payment_reported"):
            flags.append("ödeme bildirildi (teyit bekliyor)")
        elif row.get("payment_sent"):
            flags.append("ödeme linki gönderildi")
        if row.get("contract_signed"):
            flags.append("sözleşme imzalı")
        if row.get("takeover"):
            flags.append("insan devraldı")
        if not flags:
            flags.append("sıcak ping gönderildi" if row.get("hot_pinged")
                         else ("warm ping gönderildi" if row.get("warm_pinged") else "yeni sohbet"))
        badge = "🔥" if (row.get("hot_pinged") or row.get("warm_pinged")
                         or flags[0].startswith(("ödeme", "sözleşme"))) else "•"
        lines.append(
            f"\n{badge} #{shown + 1} {company} | chat {cid} | @{row.get('username') or 'yok'}\n"
            f"   Platform: {platform} | Skor: {score} | Rapor: {report}\n"
            f"   Form: {form_url}\n"
            + (f"   Son mesaj: {last_user[:120]}\n" if last_user else "")
            + f"   Durum: {', '.join(flags)}\n"
            f"   Sohbete gir: /reply {cid} merhaba, ben DevSolve tarafıyım…"
        )
        shown += 1
    return "\n".join(lines)


async def cmd_notifyme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = int(update.effective_chat.id)
    if not _is_owner(chat_id):
        # GitHub Secret'tan gelen gizli TOKEN ile ilk kurulum:
        #   /notifyme <TELEGRAM_ADMIN_TOKEN>  ->  sohbet yönetici olarak kaydedilir.
        args = context.args or []
        given = str(args[0]).strip() if args else ""
        if given and owner_notify.admin_token_ok(given):
            owner_notify.register_admin_chat(chat_id)
            logger.info("Owner chat %s registered via /notifyme token", chat_id)
            await update.message.reply_text(
                "✅ Sistem Sahibi Taptaze Senkronize Edildi.\n"
                f"Bu sohbet (chat_id={chat_id}) operatör olarak kaydedildi.\n"
                "Şimdi /notifyme ile motor özetini ve sıcak form verilerini görebilirsin.\n"
                "Canlı müşteri talebinde bildirim altındaki butonla sohbete gireceksin.\n"
                "Tüm operatör komutları için: /admin 0"
            )
            return
        await update.message.reply_text(_not_owner_hint())
        return
    try:
        text = (
            "Özet aşağıda. *Pipeline / sıcak lead bildirimleri* müşteri sohbetlerine "
            "gitmez — yalnızca .env'deki ops chat ID'sine (bildirim botun) gider.\n\n"
            + owner_notify.lead_digest()
            + _form_data_digest()
        )
    except Exception as exc:
        logger.exception("notifyme ozeti olusturulamadi")
        await update.message.reply_text(f"⚠️ Özet oluşturulamadı: {exc}"[:400])
        return
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
        return
    except BadRequest:
        # Unbalanced * / _ in a hostname would kill the whole status report.
        try:
            await update.message.reply_text(text)
            return
        except Exception:
            pass
    except Exception:
        logger.warning("notifyme ozeti satis botundan gidemedi — yedek hatta dusuluyor",
                       exc_info=True)
    # Yedek hat: httpx + TEK flood kapısı (notify tokeni önce). Satış botu
    # Telegram'da cezalıysa (canlı arıza 2026-09-19: 73420 sn FLOOD_WAIT) özet
    # buradan ulaşır; kimse temiz değilse task_queue'ya yazılır — sessiz kayıp yok.
    ok = await asyncio.to_thread(
        owner_notify.send, text.replace("*", ""), chat_id=chat_id, high_priority=True)
    if not ok:
        logger.warning("notifyme ozeti yedek hattan da gidemedi — task_queue kuyrugunda")


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
            "/payready CHATID 2500 EUR ALICI_ETIKETI TALEP_REFERANSI. Bu komut ödeme oluşturmaz.")
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
            "/verifypayment CHATID 2500 EUR ISLEM_REFERANSI. Talep tutarı eşleşmeli; referans tek kullanımlık.")
        return
    await update.message.reply_text("Sahip doğrulaması kaydedildi. Sözleşme/erişim onayı olmadan iş başlamaz.")
    # DeepSeek Success Alert: ödeme doğrulandığında owner'a bildir
    row = telegram_sessions._row(int(chat))
    who = str(row.get("company") or row.get("host") or "—")
    await asyncio.to_thread(
        owner_notify.send,
        f"🎉 SATIŞ KAPANDI!\n"
        f"💰 Tutar: {amount} {currency.upper()}\n"
        f"🌐 Müşteri: {who}\n"
        f"⚙️ Durum: Ödeme onaylandı, otomatik işlem başlatıldı."
    )
    # Teslimat işçisi (Lane AF): ödeme teyitli → o anki hizmet kusursuz kuyruğa girer.
    try:
        from nirvana import delivery_worker as _dworker
        brief = _briefs.get(int(chat)) or {}
        domain = str(brief.get("host") or brief.get("target_domain") or who)
        job = _dworker.start_job(int(chat), domain, "infra-sweep")
        if job.get("ok"):
            j = job["job"]
            await update.message.reply_text(
                f"Teslimat kuyruğa alındı: {j['job_id']} — {j['service']} "
                f"({j['domain']}, chat {j['chat_id']}). "
                "İşçi turunda teslim eder; rapor numarası (RPT-…) müşteriye ve size düşer.")
        else:
            await update.message.reply_text(
                f"Teslimat kuyruğu kurulamadı: {job.get('reason')} — "
                "domain'i /deliver CHATID HIZMET DOMAIN ile elle açabilirsiniz.")
    except Exception:
        logger.exception("delivery queue after payment verification failed")


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


async def cmd_deliver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Teslimat işçisi: elle iş aç. /deliver CHATID HIZMET [DOMAIN]"""
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_not_owner_hint())
        return
    from nirvana import delivery_worker as dworker
    args = context.args or []
    if len(args) < 2 or not str(args[0]).lstrip("-").isdigit():
        await update.message.reply_text(
            "Kullanım: /deliver CHATID HIZMET [DOMAIN]\n"
            "Hizmetler: " + ", ".join(sorted(dworker.SERVICES)) + "\n"
            "Domain verilmezse sohbetin bağlı olduğu şirketin alan adı kullanılır. "
            "Ödeme doğrulanmamışsa iş awaiting_payment'te bekler.")
        return
    target = int(args[0])
    service = args[1].strip().lower()
    domain = args[2].strip() if len(args) > 2 else ""
    if not domain:
        brief = _briefs.get(target) or {}
        domain = str(brief.get("host") or brief.get("target_domain")
                     or telegram_sessions._row(target).get("company") or "")
    res = dworker.start_job(target, domain, service)
    if res.get("ok"):
        j = res["job"]
        await update.message.reply_text(
            f"✅ İş açıldı: {j['job_id']} — {j['service']} ({j['domain']}) "
            f"chat {j['chat_id']}, durum {j['status']}.")
    else:
        await update.message.reply_text(f"❌ İş açılamadı: {res.get('reason')} ({res})")


async def cmd_deliveries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Teslimat işçisi durumu: aktif işler + son rapor numaraları."""
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_not_owner_hint())
        return
    from nirvana import delivery_worker as dworker
    s = dworker.status_summary()
    lines = [
        "TESLİMAT İŞÇİSİ — durum",
        f"Aktif: {s['active']}/{s['max_concurrent']} | ödeme bekleyen: {s['awaiting_payment']} | "
        f"kuyrukta: {s['queued']} | teslim edilen (toplam iş): {s['delivered_total']}",
    ]
    for j in s["jobs"]:
        lines.append(f"• {j['job_id']} | chat {j['chat_id']} | {j['service']} | "
                     f"{j['domain']} | {j['status']}")
    reports = dworker.load_reports()[-8:]
    if reports:
        lines.append("Son raporlar:")
        for r in reports:
            lines.append(f"  {r['report_id']} | chat {r['chat_id']} | {r['service']} | "
                         f"{r['domain']} | {r['status']} | {r['at']}")
    await update.message.reply_text("\n".join(lines) or "Kayıt yok.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_not_owner_hint())
        return
    try:
        digest = owner_notify.lead_digest()
    except Exception as exc:
        logger.exception("status ozeti olusturulamadi")
        await update.message.reply_text(f"⚠️ Durum özeti oluşturulamadı: {exc}"[:400])
        return
    text = (
        f"Operatör sohbeti (chat_id={update.effective_chat.id}) tanınıyor.\n"
        f"{telegram_bot_api.status_line()}\n\n"
        + digest
    )
    try:
        await update.message.reply_text(text)
        return
    except Exception:
        # Satış botu Telegram'da cezalıysa yedek hattan (tek flood kapısı)
        # düşür; kimse temiz değilse task_queue'ya yazılır.
        await asyncio.to_thread(
            owner_notify.send, text.replace("*", ""),
            chat_id=int(update.effective_chat.id), high_priority=True)


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
        # Sahiplik: müşteriyi alan bot üzerinden gönder — çapraz bot 403 verir.
        owner_app = _app_for_chat(target, context.application)
        bot = owner_app.bot if owner_app is not None else context.bot
        if not await flood_guard.acquire(target):
            raise flood_guard.FloodBlocked(target, flood_guard.remaining())
        await bot.send_message(chat_id=target, text=body)
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


# --- İnsan devri (Human-in-the-Loop) -----------------------------------------
# Rapor: bildirimin altındaki [ Sohbete Bağlan / Reply] butonuna basan patron
# otonom yanıtlayıcıyı duraklatır ve yazdığı mesaj DOĞRUDAN müşteriye gider.

HANDOFF_CALLBACK_PREFIX = "handoff:"


def _handoff_target_from_callback(data: str) -> int | None:
    """'handoff:123456' -> 123456. Bozuk veri sessizce yok sayılır."""
    text = str(data or "").strip()
    if not text.startswith(HANDOFF_CALLBACK_PREFIX):
        return None
    raw = text[len(HANDOFF_CALLBACK_PREFIX):].strip()
    if not raw.lstrip("-").isdigit():
        return None
    return int(raw)


async def on_handoff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline buton: yalnızca operatör basabilir; basınca sohbet ona bağlanır."""
    query = update.callback_query
    if query is None:
        return
    owner_chat = update.effective_chat.id if update.effective_chat else None
    target = _handoff_target_from_callback(str(query.data or ""))
    if owner_chat is None or target is None:
        await query.answer()
        return
    if not _is_owner(owner_chat):
        # Müşteri ya da yabancı biri bastı: hiçbir yetki açılmaz.
        await query.answer("Bu buton operatör içindir.", show_alert=False)
        return
    telegram_sessions.arm_reply(owner_chat, target)
    await query.answer("Sohbet sana bağlandı")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logger.debug("handoff butonu kaldirilamadi", exc_info=True)
    await context.bot.send_message(
        chat_id=owner_chat,
        text=(f"✅ Bağlandı: müşteri {target}. Otonom yanıtlayıcı DURDU.\n"
              "Şimdi bu sohbete yazdığın her mesaj doğrudan müşteriye gider.\n"
              "Bitirmek için: /disarm"),
    )


async def _relay_owner_reply(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             owner_chat_id: int, text: str) -> bool:
    """Butonla bağlı patronun mesajını müşteriye iletir.

    Dönüş: True => mesaj devir hattına alındı (oto-yanıtlayıcı çalışmaz).
    """
    target = telegram_sessions.armed_target(owner_chat_id)
    if target is None:
        return False
    body = " ".join(str(text or "").split())
    if not body or not update.message:
        return True
    if body.lower() in {"/disarm", "disarm"}:
        return True
    try:
        # Sahiplik: müşteriyi alan bot üzerinden gönder — çapraz bot 403 verir.
        owner_app = _app_for_chat(target, context.application)
        bot = owner_app.bot if owner_app is not None else context.bot
        if not await flood_guard.acquire(target):
            raise flood_guard.FloodBlocked(target, flood_guard.remaining())
        await bot.send_message(chat_id=target, text=body)
    except Exception as exc:
        await update.message.reply_text(f"Gönderilemedi: {exc}".strip()[:300])
        return True
    telegram_sessions.set_takeover(target, True)
    _remember(target, "assistant", body)
    task = _proof_tasks.pop(target, None)
    if task:
        task.cancel()
    logger.info("Human handoff relay owner=%s -> customer=%s", owner_chat_id, target)
    return True


async def cmd_disarm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Devri kapat: otonom yanıtlayıcı tekrar çalışır."""
    if not update.effective_chat or not update.message:
        return
    if not _is_owner(update.effective_chat.id):
        await update.message.reply_text(_not_owner_hint())
        return
    target = telegram_sessions.clear_armed(update.effective_chat.id)
    if target is None:
        await update.message.reply_text(
            "Aktif devir yok. Sohbete girmek için bildirimdeki butona bas "
            "ya da /reply CHATID metin kullan."
        )
        return
    await update.message.reply_text(
        f"Devir kapandı. Otonom asistan {target} sohbetinde yeniden yanıtlıyor."
    )


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

    if _is_owner(chat_id):
        # Buton ile bağlanan patronun mesajı doğrudan müşteriye gider (oto-yanıt DURUR).
        # Opt-out'tan ÖNCE: patronun "stop" yazısı kendini optout'a düşürmesin.
        if await _relay_owner_reply(update, context, chat_id, user_text):
            return
        # Devir yoksa operatör panel ipucu ver (sessiz kalma).
        await update.message.reply_text(_owner_intro())
        return

    if optout.is_chat_opted_out(chat_id) or optout.OPT_OUT_RE.search(user_text):
        await _confirm_stop(update)
        return

    # MUSTERI HATTI WEBCHAT'E TASINDI (WEBCHAT_PUBLIC_URL dolu): Telegram'da
    # musteri satis akisi BASLATILMAZ — FLOOD_WAIT/ban/hiz limitleri musteri
    # mimarisinden tamamen cikar; tek satir web adresi verilir.
    if await _redirect_customer_to_webchat(update, chat_id):
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

    # (operatör sohbeti yukarıda, opt-out'tan önce ele alındı)

    # Teslimat işçisi (Lane AF): müşteri rapor numarasıyla sorar → kendi raporu.
    # Yalnızca ödemesi doğrulanmış chate; satış akışını hiç etkilemez.
    if _REPORT_RE.search(user_text) and not _wants_to_buy(user_text) and not _PRICE_RE.search(user_text):
        try:
            from nirvana import delivery_worker as _dworker
            own = _dworker.reports_for_chat(chat_id)
            if own:
                reply = _report_recall_text(own, turkish=_conv_lang(user_text, chat_id))
                _remember(chat_id, "assistant", reply)
                await update.message.reply_text(_display_text(reply))
                return
        except Exception:
            logger.exception("report recall failed for chat %s", chat_id)

    # Ödeme teyitli müşteri o anki hizmetini yazınca doğrudan teslimat kuyruğu:
    if telegram_sessions.fulfillment_ready(chat_id):
        wanted = _service_intent(user_text)
        if wanted:
            from nirvana import delivery_worker as _dworker
            brief = _briefs.get(chat_id) or {}
            domain = str(brief.get("host") or brief.get("target_domain")
                         or telegram_sessions._row(chat_id).get("company") or "")
            job = _dworker.start_job(chat_id, domain, wanted)
            if job.get("ok"):
                reply = (
                    f"Hizmetiniz kuyruğa alındı: {job['job']['job_id']} — "
                    f"{wanted} ({job['job']['domain']}). Teslimat raporu numarasıyla "
                    "bu sohbete düşecek."
                    if _conv_lang(user_text, chat_id) else
                    f"Your service is queued: {job['job']['job_id']} — "
                    f"{wanted} ({job['job']['domain']}). The delivery report will land "
                    "here with its report number.")
                _remember(chat_id, "assistant", reply)
                await update.message.reply_text(_display_text(reply))
                await asyncio.to_thread(
                    owner_notify.send,
                    f"TESLİMAT KUYRUĞU — chat {chat_id}: {wanted} ({job['job']['domain']}), "
                    f"iş {job['job']['job_id']}. Ödeme doğrulanmış; teslimat işçisi turda işler.")
                return
            if job.get("reason") == "domain_bound_to_other_chat":
                await update.message.reply_text(
                    "Bu oturum yalnızca kendi alan adınızla işlem yürütür; "
                    "karışıklığı önlemek için başka bir alana teslimat açılmadı."
                    if _conv_lang(user_text, chat_id) else
                    "This session can only operate on your own domain; "
                    "no delivery was opened for another domain.")
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

    # DeepSeek Handoff: müşteri patron/yetkili isterse bot durur, owner'a bildir
    if _HANDOFF_RE.search(_tr_norm(user_text)):
        who = str((_briefs.get(chat_id) or {}).get("company") or (_briefs.get(chat_id) or {}).get("host") or "—")
        user = _username(update) or "yok"
        handle = f"@{user}" if user != "yok" else "yok"
        # İnsan devri: patronun bildirimi BUTONLU gider (tek tıkla sohbete girer).
        await asyncio.to_thread(
            owner_notify.send_handoff_alert,
            f"🚨 CANLI MÜŞTERİ TALEBİ: {who} yetkilisi kurucu/uzman ile görüşmek istiyor.\n"
            f"👤 Hesap: {handle}  |  💬 Sohbet: {chat_id}\n"
            f'💬 Son Mesajı: "{user_text[:300]}"\n'
            "Sohbete katılmak için aşağıdaki butona tıklayın "
            f"(ya da /reply {chat_id} metin).",
            target_chat_id=chat_id,
        )
        telegram_sessions.set_takeover(chat_id, True)
        turkish = _conv_lang(user_text, chat_id)
        ack = ("Talep alındı. Sorumlu mühendis arkadaşım bu sohbete dönüş yapacak; "
               "bu arada kapsam ya da rapor detayı için yazmaya devam edebilirsiniz."
               if turkish else
               "Request received. Our responsible engineer will reply in this chat; "
               "meanwhile feel free to keep asking about scope or the report findings.")
        await update.message.reply_text(ack)
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
        # Yüksek otoriteli kapanış: direkt rakam satışı değil, kota + kabul protokolü.
        try:
            from nirvana import slot_gate
            reply = slot_gate.price_response(turkish=_conv_lang(user_text, chat_id),
                                             row=_briefs.get(chat_id), chat_id=chat_id)
        except Exception:
            logger.exception("slot_gate price fallback")
            reply = (
                f"Önerilen aylık retainer {config.price_label(explicit=True)}; "
                "nihai kapsam ve sözleşme onayına bağlıdır."
                if _conv_lang(user_text, chat_id) else
                f"The proposed monthly retainer is {config.price_label(explicit=True)}, "
                "subject to agreed scope and contract.")
        await update.message.reply_text(reply)
        return

    terms_match = _TERMS_RE.search(user_text)
    if terms_match and not _wants_to_buy(user_text):
        try:
            from nirvana import self_serve_close as ssc
            if not ssc.terms_acknowledged(user_text):
                who = str((_briefs.get(chat_id) or {}).get("company") or "")
                await update.message.reply_text(
                    ssc.terms_presentation(company=who, turkish=_customer_lang(update)))
                return
            # Şart onayı = satın alma niyeti; aşağıdaki SSC akışına düşer.
        except Exception:
            logger.exception("nirvana terms presentation failed")
            return

    if _wants_to_buy(user_text) or terms_match:
        telegram_sessions._put(chat_id, interest_reported=True, followup_sent=True)
        # Lane N/SSC: owner /reply olmadan profesyonel kapanış denemesi.
        # Kapılar: açık niyet + şart onayı + kanıt artefaktı + opt-out temiz.
        try:
            from nirvana import self_serve_close as ssc
            allowed = not optout.is_chat_opted_out(chat_id)
            ssc_res = ssc.evaluate(chat_id, user_text, brief=_briefs.get(chat_id),
                                   row=telegram_sessions._row(chat_id), allowed=allowed)
        except Exception:
            logger.exception("nirvana self-serve close failed")
            ssc_res = None
        if ssc_res and ssc_res.get("ok"):
            # ÖDEME LİNKİ — KRİTİK GÖNDERİM (engel yemez): önce link metni
            # spam-ateşleyici olabilecek kuyruk/proof işlerinden ÖNCE gider.
            # Havuzdaki botla gönderilir; bu bot 429 yerse sıradaki botla
            # bypass denenir (flood harici, retry'li).
            try:
                await _send_payment_link_critical(
                    chat_id, ssc_res["message"], update, context, who=None)
            except Exception:
                logger.exception("kritik odeme linki gonderilemedi chat %s", chat_id)
                await update.message.reply_text(ssc_res["message"])
            telegram_sessions._put(chat_id, terms_acknowledged=True, self_serve_link_sent=True)
            telegram_sessions.mark_payment(chat_id)
            who = str((_briefs.get(chat_id) or {}).get("company") or "—")
            # KRİTİK HAT (ödeme bildirimi): flood harici — cezalı olsa bile gider.
            await asyncio.to_thread(
                owner_notify.send_handoff_alert,
                f"💰 SELF-SERVE SATIŞ — doğrulanmış ödeme linki gönderildi "
                f"(chat {chat_id}, {who}). "
                "Yerleşince insan doğrulaması yapılacak; teslimat o onaydan sonra.",
                target_chat_id=chat_id)
            return
        if ssc_res and ssc_res.get("reason") == "terms":
            who = str((_briefs.get(chat_id) or {}).get("company") or "")
            await update.message.reply_text(
                ssc.terms_presentation(company=who, turkish=_customer_lang(update)))
            return
        if ssc_res and ssc_res.get("reason") == "confusion":
            # Anti-karışıklık bekçisi devreye girdi: başka şirkete link yok.
            await update.message.reply_text(
                "Bu oturumda yalnızca formda geçen şirketle işlem yürüyebilir. "
                "Karışıklık yaşandıysa tekrar iletin."
                if _customer_lang(update) else
                "This session is bound to the company from the form. "
                "If there is a mix-up, please restate your company.")
            await asyncio.to_thread(
                owner_notify.send,
                f"🛡️ KARIŞIKLIK BEKÇİSİ — chat {chat_id}: farklı şirket bağlanmaya çalıştı. Link gönderilmedi.")
            return
        request = payment_safety.ready_request(chat_id)
        contract = telegram_sessions._row(chat_id)
        if (request is None or not contract.get("contract_signed")
                or contract.get("contract_amount") != request["amount"]):
            try:
                from nirvana import slot_gate
                gate = slot_gate.intent_package(turkish=_conv_lang(user_text, chat_id),
                                                row=_briefs.get(chat_id), chat_id=chat_id)
            except Exception:
                logger.exception("slot_gate intent fallback")
                gate = None
            if gate:
                tail = (
                    "\n\nŞartları görmek isterseniz 'kabul ediyorum' yazın — şart metni "
                    "bu sohbete düşer; onayınızla Payoneer talebi ve SLA üretilir."
                    if _conv_lang(user_text, chat_id) else
                    "\n\nTo review the terms simply reply 'I accept' — the terms land here; "
                    "on your approval the Payoneer request and SLA are produced.")
                await update.message.reply_text(gate + tail)
            else:
                await update.message.reply_text(
                    "Interest noted, not yet a signed engagement. Before payment we must agree scope, "
                    "contract and access, and verify the Payoneer request's recipient and amount. "
                    f"Proposed retainer: {config.price_label(explicit=True)}/month.")
            await asyncio.to_thread(owner_notify.send, f"Satın alma ilgisi (kabul/ödeme değil), chat {chat_id}. "
                                    "Kapsam/sözleşme ve Payoneer talep doğrulaması gerekiyor.")
            return
        # Ödeme linki (sözleşmeli hat) — KRİTİK GÖNDERİM, engel yemez.
        try:
            await _send_payment_link_critical(
                chat_id,
                f"Agreed request: {_currency_symbol(request['currency'])}{request['amount']} {request['currency']}. "
                "Check the recipient and amount on Payoneer before paying.\n" + config.PAYONEER_PAYMENT_URL,
                update, context)
        except Exception:
            logger.exception("kritik odeme linki (sozlesmeli) gonderilemedi chat %s", chat_id)
            await update.message.reply_text(
                f"Agreed request: {_currency_symbol(request['currency'])}{request['amount']} {request['currency']}. "
                "Check the recipient and amount on Payoneer before paying.\n" + config.PAYONEER_PAYMENT_URL)
        telegram_sessions._put(chat_id, payment_request=request)
        telegram_sessions.mark_payment(chat_id)
        return

    _remember(chat_id, "user", user_text)
    brief = _closer_brief(_briefs.get(chat_id))
    try:
        system_prompt = knowledge.telegram_system_prompt(brief=brief) + _identity_prompt_line()
    except Exception:
        system_prompt = knowledge.telegram_system_prompt(brief=brief)
    try:
        from nirvana.conversion_maximizer import close_block
        system_prompt += close_block(user_text=user_text, brief=brief, chat_id=chat_id)
    except Exception:
        logger.exception("conversion close_block failed")
    messages = [
        {"role": "system", "content": system_prompt},
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

    # Dil Bekçisi: bot sızıntısı + ücretsiz/indirim + imla — gönderim öncesi son kapı.
    try:
        from nirvana.language_auditor import audit
        reply, audit_issues = audit(reply, turkish=_conv_lang(user_text, chat_id), user_text=user_text)
        if audit_issues:
            logger.info("Language audit chat %s: %s", chat_id, audit_issues)
    except Exception:
        logger.exception("language audit failed")

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
    # Nirvana lane E: deterministic objection handling before any model fallback.
    try:
        from nirvana.objection_handler_agent import handle as _objection
        auto = _objection(user_text, turkish=turkish)
        if auto:
            return auto, False
    except Exception:
        logger.exception("nirvana objection handler failed")
    # Canlı bilgi tabanı: model kapalıyken bile bilgi soruları boş kalmaz.
    try:
        hits = knowledge.assistant_context()
        if hits and re.search(
            r"ne\s*biliyorsun|ne\s*yap[ıi]yorsunuz|hakk[ıi]nda|neler\s*yap[ıi]yorsunuz|"
            r"what\s+do\s+you\s+(?:know|do)|tell\s+me\s+about|your\s+services|entegrasyon",
            _tr_norm(user_text), re.I,
        ):
            first = ("Bilgi tabanımız güncel ve canlı besleniyor. Uzmanlık alanlarımız:\n"
                     if turkish else "Our knowledge base is live-fed. Our expertise map:\n")
            tail = ("\nSizin altyapınız hangisi, en çok nerede takılıyorsunuz?"
                    if turkish else "\nWhich stack are you on, and where are you stuck?")
            return (first + "\n".join(hits.splitlines()[:14]) + tail), False
    except Exception:
        logger.exception("assistant_context fallback failed")
    buy = _wants_to_buy(user_text)
    try:
        from nirvana import slot_gate
    except Exception:
        slot_gate = None  # type: ignore[assignment]
    # Yüksek otoriteli slot kapanışı (model kapalıyken de aynı dil):
    if slot_gate and _PRICE_RE.search(user_text) and not buy:
        return slot_gate.price_response(turkish=turkish, row=row), False
    if slot_gate and buy:
        return slot_gate.intent_package(turkish=turkish, row=row), False
    if turkish and buy:
        return (
            "Önce değer: ölçtüğümüz darboğaz her ay ciro kaybettiriyor ve kapanışı bu kaybı keser. "
            "Oracle izleme slotlarımız sınırlı; 24 saatlik rezervasyonla ilerliyoruz. "
            f"Sabit retainer: {config.price_label(explicit=True)}. Kapsam ve ödeme talebi doğrulanmalı.",
            False,
        )
    if buy:
        return (
            "Value first: the bottleneck we measured leaks revenue every month and closing it stops the loss. "
            "Our Oracle monitoring slots are limited and we proceed on a 24-hour reservation. "
            f"Fixed retainer: {config.price_label(explicit=True)}. Scope and payment request need verification.",
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
    # Flood cezası aktifken kullanıcıya hata mesajı DENEME — döngüyü büyütür.
    # Sohbet-bazlı kontrol: operatör/müşteri sohbetine yazılan ceza yalnızca
    # O sohbeti bağlar; temiz bir sohbete hata bildirimi engellenmez.
    affected = None
    try:
        upd = update
        if isinstance(upd, Update) and upd.effective_chat is not None:
            affected = int(upd.effective_chat.id)
    except Exception:
        affected = None
    if flood_guard.remaining(affected) > 5:
        return
    # Sessiz ölüm olmasın: handler içinde patlarsa kullanıcıya da söyle.
    chat_id = None
    try:
        upd = update
        if isinstance(upd, Update) and upd.effective_chat is not None:
            chat_id = upd.effective_chat.id
    except Exception:
        chat_id = None
    if chat_id is None:
        return
    detail = str(context.error or "bilinmeyen hata")[:350]
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Komut işlenirken hata oluştu, loglandı:\n{detail}",
        )
    except Exception:
        logger.exception("on_error: hata mesaji kullaniciya ulasirken patladi")


async def _followup_loop(application: Application) -> None:
    await asyncio.sleep(45)
    while True:
        try:
            if flood_guard.remaining() > 60:
                # Flood cezası uzun: Telegram'a spam atma, sessiz bekle.
                await asyncio.sleep(60)
                continue
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
                    # Sahiplik: sohbeti alan bot gönderir; 3 bot havuzunda
                    # her bot kendi limit havuzunu kullanır.
                    owner_app = _app_for_chat(chat_id, application)
                    owner_bot = owner_app.bot if owner_app is not None else application.bot
                    if not await flood_guard.acquire(chat_id):
                        raise flood_guard.FloodBlocked(chat_id, flood_guard.remaining())
                    await owner_bot.send_message(chat_id=chat_id, text=_display_text(text))
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


async def _heartbeat_loop(application: Application) -> None:
    """Self-healing: systemd WATCHDOG=1 + kalıcı kuyruk aktarıcısı (10 sn ritim).

    KUYRUĞU ASLA EVENT LOOP'TA SENKRON İŞLEME: run_due -> deliver_queued_notify
    -> httpx post, cezalı/yavaş Telegram hedefinde dakikalarca bloklarsa
    WATCHDOG=1 kesilir ve systemd servisi SIGABRT ile çökme-restart döngüsüne
    sokar (canlı arıza, 2026-09: her ~4.7 dk'da bir 'watchdog' ölümü). Bu
    yüzden iş, timeout'lu thread'e alınır; loop daima 10 sn'de bir nabız vurur.
    """
    heartbeat.ready()
    while True:
        heartbeat.pulse("salesbot")
        try:
            # ZERO-TOUCH sweep: PASSIVE botun updater'ı durdurulur, cooldown'u
            # biten bot otomatik (manuel komut/restart olmadan) aktive edilir.
            await _sync_pool_polling()
        except Exception:
            logger.exception("pool polling sweep failed")
        try:
            relay = await asyncio.wait_for(
                asyncio.to_thread(
                    task_queue.run_due,
                    "telegram_notify", owner_notify.deliver_queued_notify, limit=10,
                ),
                timeout=25.0,
            )
            if relay.get("done"):
                logger.info("Queued notify relay: %s", relay)
        except asyncio.TimeoutError:
            logger.warning("Queued notify relay timed out (>25s) — sonraki tura bırak")
        except Exception:
            logger.exception("Queued notify relay failed")
        await asyncio.sleep(10)


_POLLING_ON: set[str] = set()


async def _start_polling(app: Application, uname: str) -> None:
    """ACTIVE botun polling'ini başlat + kamu kimliğini uygula.

    Kimlik çağrıları (set_my_name vb.) da Telegram'a giden İSTEKTÜR:
    sadece polling başlarken, aktif botta, bir kez yapılır (cezalı bota
    health/config istekleri gitmez)."""
    if uname in _POLLING_ON:
        return
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        timeout=30,
        bootstrap_retries=8,
    )
    _POLLING_ON.add(uname)
    await _apply_public_identity(app)
    logger.info("Polling AKTİF: @%s", uname)


async def _stop_polling(app: Application, uname: str) -> None:
    """PASSIVE botun polling'ini durdur — ZERO-TOUCH PASSIVE.

    Updater çalışırken PTB getUpdates'i kendisi tekrarlar; exception loop'u
    bile Telegram'a istek demektir. Tek doğru: updater'ı DURDURMAK."""
    if uname not in _POLLING_ON:
        return
    try:
        await app.updater.stop()
    except Exception:
        logger.exception("updater stop failed for @%s", uname)
    _POLLING_ON.discard(uname)
    logger.info("Polling DURDURULDU: @%s — ZERO-TOUCH PASSIVE (cooldown bitene dek tek istek yok)", uname)


async def _sync_pool_polling() -> None:
    """Havuz polling'ini bot_registry durumuyla senkronla.

    Süre Sonu Otomatik Aktivasyon: bot_registry.is_active() cooldown bitmiş
    pasifleri okuma anında ACTIVE'a çeker; sweep polling'i yeniden başlatır.
    Hiçbir manuel komut/restart gerekmez."""
    import bot_registry

    for uname, app in list(_APPS.items()):
        try:
            live = bot_registry.is_active(uname)
        except Exception:  # noqa: BLE001 — kayıt defteri yoksa fail-open
            live = True
        if live:
            try:
                await _start_polling(app, uname)
            except Exception:
                logger.exception("polling start failed for @%s", uname)
        else:
            await _stop_polling(app, uname)


async def _apply_public_identity(application: Application) -> None:
    """Bot olduğunu gizle: görünen ad + açıklamalarda 'bot' kelimesi ASLA görünmesin.

    Telegram, kullanıcı adının '-bot' ile bitmesini zorunlu tutar (değiştirilemez);
    ancak müşterinin sohbet başlığında ve profilde gördüğü görünen ad (set_my_name)
    ve açıklamalar (set_my_description / set_my_short_description) buradan profesyonel
    isme zorlanır — BotFather'da eski isim kalsa bile her açılışta düzeltilir.
    """
    bot = application.bot
    display = config.BOT_DISPLAY_NAME
    try:
        await bot.set_my_name(display)
        await bot.set_my_description(config.BOT_PUBLIC_DESCRIPTION)
        await bot.set_my_short_description(config.BOT_PUBLIC_DESCRIPTION[:120])
        logger.info("Public identity applied: name=%r (no bot reveal)", display)
    except Exception:
        logger.exception("Public identity apply failed — bot may still show old name")


async def _post_init(application: Application, *, primary: bool = True) -> None:
    # Telegram flood cezası bir daha yaşanmasın: TÜM botlar korumalı —
    # her botun giden çağrıları kapıdan geçer (bot başına limit havuzu).
    # username post_init anında belli olmayabilir; _serve'de initialize
    # sonrası gerçek username ile tekrar kurulur.
    flood_guard.install(application.bot,
                        bot_username=str(getattr(application.bot, "username", "") or ""))
    # Bot kimliğini diske yaz: TELEGRAM_BOT_TOKEN id'siz girilmişse normalizasyon
    # doğru bot id'sini buradan okur; ayrıca botun kendi id'si admin listesinden
    # çıkarılır (madde: "telegram botu beni patronu olarak biliyor mu").
    try:
        config.save_bot_identity(
            getattr(application.bot, "id", "") or "",
            str(getattr(application.bot, "username", "") or ""),
            primary=bool(primary),
        )
    except Exception:  # noqa: BLE001 — kimlik kaydı botu bloklamaz
        logger.debug("bot kimlik kaydi basarisiz", exc_info=True)
    if primary:
        # Arka plan döngüleri yalnızca birincil uygulamada: 3 bot = 3 kopya
        # followup/heartbeat olmasın, routing zaten _app_for_chat ile yapılır.
        application.bot_data["followup_task"] = asyncio.create_task(
            _followup_loop(application), name="tg-followup"
        )
        application.bot_data["heartbeat_task"] = asyncio.create_task(
            _heartbeat_loop(application), name="tg-heartbeat"
        )
    # NOT: _apply_public_identity BURADA çağrılmaz — set_my_name vb. de
    # Telegram'a giden istektür ve ZERO-TOUCH PASSIVE bot için yasaktır.
    # Kimlik, yalnızca polling başlatıldığında (_start_polling) uygulanır.


def _build_application(token: str, *, primary: bool) -> Application:
    async def _post(app: Application) -> None:
        await _post_init(app, primary=primary)

    builder = (
        Application.builder()
        .token(token)
        .connect_timeout(5.0)
        .read_timeout(10.0)
        .write_timeout(10.0)
        .pool_timeout(5.0)
        .get_updates_connect_timeout(5.0)
        .get_updates_read_timeout(40.0)
        .get_updates_pool_timeout(20.0)
        .post_init(_post)
    )
    # Yerel (Local) Bot API varsa oraya bağlan: dakikada-30-mesaj ve 20 MB dosya
    # sınırı kalkar. Sunucu sağlıksızsa otomatik olarak bulut API'ye düşülür.
    builder, api_choice = telegram_bot_api.apply_to_builder(builder, token=token)
    logger.info("Telegram API: %s (%s)", api_choice["mode"], api_choice["reason"])
    application = builder.build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(CommandHandler("payready", cmd_payready))
    application.add_handler(CommandHandler("verifypayment", cmd_verifypayment))
    application.add_handler(CommandHandler("approvecontract", cmd_approvecontract))
    application.add_handler(CommandHandler("deliver", cmd_deliver))
    application.add_handler(CommandHandler("deliveries", cmd_deliveries))
    application.add_handler(CommandHandler("notifyme", cmd_notifyme))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("reply", cmd_reply))
    application.add_handler(CommandHandler("release", cmd_release))
    application.add_handler(CommandHandler("disarm", cmd_disarm))
    application.add_handler(CommandHandler("stop", cmd_stop))
    application.add_handler(CommandHandler("unsubscribe", cmd_stop))
    application.add_handler(CommandHandler("resume", cmd_resume))
    # Canlı müşteri bildirimindeki [ Sohbete Bağlan / Reply] butonu.
    application.add_handler(
        CallbackQueryHandler(on_handoff_callback, pattern=r"^handoff:-?\d+$")
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)
    return application


async def _serve(apps: list[Application]) -> None:
    """Çoklu botu tek asyncio döngüsünde koştur: her bot kendi polling akışı.

    PTB'nin run_polling'i tek uygulama içindir; burada initialize → post_init →
    start → updater.start_polling adımları her uygulama için elle sıralanır.
    SIGTERM (systemd) tüm poller'ları temiz kapatır.
    """
    import signal as _signal

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    for sig in (_signal.SIGTERM, _signal.SIGINT, getattr(_signal, "SIGBREAK", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # Windows: add_signal_handler yok
            try:
                _signal.signal(sig, _request_stop)
            except (ValueError, OSError):
                pass

    healthy: list[Application] = []
    for app in apps:
        try:
            await app.initialize()
        except Exception:
            # Geçersiz/iptal token tüm süreci çökertmesin: o bot havuzdan düşer,
            # kalan botlar çalışmaya devam eder.
            logger.exception("bot initialize failed — havuzdan düşürüldü")
            continue
        uname = str(getattr(app.bot, "username", "") or "")
        if uname:
            _APPS[uname] = app
            # Gerçek username belli: flood_guard bu botu ismiyle tanır
            # (FLOOD_WAIT yerse havuzda PASSIVE'a çekilir).
            try:
                app.bot._flood_guard_bot = uname
            except Exception:  # noqa: BLE001
                pass
        healthy.append(app)
    apps = healthy
    # Ortak Beyin kayıt defteri: havuz üyeleri ACTIVE doğar; pasiflerin
    # cooldown'u dolmuşsa okuma anında otomatik reaktive olur. token_hint
    # parmak izleri de yazılır — 429 cezası sonradan getMe ÇAĞIRMADAN
    # (zero-touch) doğru bota eşlenir.
    try:
        import bot_registry as _registry
        _registry.register_pool(list(_APPS.keys()))
        import hashlib as _hashlib
        for _uname, _app in _APPS.items():
            _token = str(getattr(_app.bot, "_token", "") or "")
            if _token:
                _registry.set_token_hint(
                    _uname, _hashlib.sha256(_token.encode()).hexdigest()[:12])
    except Exception:  # noqa: BLE001
        logger.exception("bot_registry kaydi basarisiz — rotasyon config havuzundan")
    for index, app in enumerate(apps):
        await _post_init(app, primary=(index == 0))
    # ZERO-TOUCH + SÜRE SONU OTOMATİK AKTİVASYON: yalnızca ACTIVE botlar
    # polling başlatır. FLOOD_WAIT'te (PASSIVE) botun updater'ı HİÇ
    # başlatılmaz — getUpdates dâhil tokenine tek istek gitmez. Cooldown
    # bitince heartbeat sweep'i (aşağıda) botu otomatik aktive eder.
    for app in apps:
        await app.start()
    await _sync_pool_polling()
    logger.info("Sales bot pool live: %d bot(s) — %s", len(apps), ", @".join(_APPS))
    try:
        await stop.wait()
    finally:
        for app in apps:
            try:
                await app.updater.stop()
            except Exception:
                logger.exception("updater stop failed")
            try:
                await app.stop()
                await app.shutdown()
            except Exception:
                logger.exception("app stop/shutdown failed")


def _ensure_model_background() -> None:
    """Ollama modeli ARKAPLANDA hazırla — main()'i bloklamaz.

    Neden: Type=notify serviste READY=1 (heartbeat.ready) anahtar verilmeden
    WatchdogSec (30s) dolarsa systemd SIGABRT ile öldürür. ensure_model (ping
    yoksa 90 sn bekleme + ilk pull dakikalar) main()'de çağrılırsa bot çökme-
    restart döngüsüne girer (canlı arıza, 2026-09). Model eksikse ollama_client
    her chat çağrısında zaten yeniden dener.
    """

    def _run() -> None:
        try:
            ollama_client.ensure_model()
        except Exception:  # noqa: BLE001 — model yoksa bot yine konuşur
            logger.warning("Ollama model hazırlığı arkaplanda başarısız oldu", exc_info=True)

    threading.Thread(target=_run, name="ollama-ensure", daemon=True).start()


def main() -> None:
    os.chdir(config.ROOT)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _ensure_model_background()
    config.require_bot_keys()
    config.ensure_telegram_username()
    pool = config.resolve_bot_pool()
    tokens = config.bot_tokens()
    if not pool:
        pool = [config.TELEGRAM_BOT_USERNAME or "bot"]
    apps = [
        _build_application(token, primary=(index == 0))
        for index, token in enumerate(tokens)
    ]
    logger.info(
        "Telegram sales bot pool: %d application (%s) — birincil @%s",
        len(apps), ", @".join(pool), pool[0],
    )
    # systemd sends SIGTERM; sinyal yönetimi _serve içinde (PTB stop_signals=None).
    asyncio.run(_serve(apps))


if __name__ == "__main__":
    main()
