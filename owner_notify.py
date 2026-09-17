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


def _parse_chat_id(raw: str) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _mask(text: Any) -> str:
    """Log/journal'a bot tokenı sızmasın.

    Telegram hata mesajları URL içinde tokenı taşır
    (https://api.telegram.org/bot<TOKEN>/sendMessage) — maskelenmezse token
    journald'ye ve CI loglarına düşer.
    """
    out = str(text)
    for raw in (config.TELEGRAM_NOTIFY_BOT_TOKEN, config.TELEGRAM_BOT_TOKEN):
        tok = (raw or "").strip()
        if tok:
            out = out.replace(tok, "***")
    return out


def _token_ok(token: str) -> bool:
    """Token gerçekten geçerli mi? (Telegram geçersiz/iptal token'a 404 döner.)"""
    tok = (token or "").strip()
    if not tok:
        return False
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        resp = httpx.post(f"https://api.telegram.org/bot{tok}/getMe", timeout=15)
        return bool(resp.json().get("ok"))
    except Exception:  # noqa: BLE001
        return False


def load_admin_chat_ids() -> set[int]:
    """Tüm geçerli admin chat ID'leri.

    Kaynaklar: TELEGRAM_OWNER_CHAT_ID / TELEGRAM_ADMIN_ID (.env, GitHub Secret ->
    Oracle .env) ve /admin KOD ya da /notifyme TOKEN ile owner.json'a kaydolan
    sohbet. Böylece .env'deki ID yeni hesapla uyuşmasa bile operatör kendini
    kaydedip /notifyme özetini çekebilir. Müşteri sohbetleri asla admin sayılmaz.
    """
    ids: set[int] = set()
    for raw in (config.TELEGRAM_OWNER_CHAT_ID,
                str(getattr(config, "TELEGRAM_ADMIN_ID", "") or "")):
        cid = _parse_chat_id(raw)
        if cid is not None:
            ids.add(cid)
    if not PATH.exists():
        return ids
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ids
    cid = _parse_chat_id(str(data.get("chat_id") or ""))
    if cid is not None:
        ids.add(cid)
    return ids


def admin_token_ok(token: str) -> bool:
    """Gizli eşleşme dizesi: TELEGRAM_ADMIN_TOKEN (GitHub Secret → Oracle .env).

    Rapor: yönetici kimliği hem ID hem TOKEN ile doğrulanır. Token tanımlı
    değilse hiçbir giriş kabul edilmez (fail-closed).
    """
    expected = str(getattr(config, "TELEGRAM_ADMIN_TOKEN", "") or "").strip()
    given = str(token or "").strip()
    if not expected or not given:
        return False
    import secrets as _secrets
    return bool(_secrets.compare_digest(given, expected))


def register_admin_chat(chat_id: int) -> int:
    """Doğrulanmış sohbeti yönetici olarak kaydet (owner.json)."""
    save_chat_id(int(chat_id))
    logger.info("Admin chat %s registered (TELEGRAM_ADMIN_TOKEN dogrulamasi)", chat_id)
    return int(chat_id)


def load_admin_chat_id() -> int | None:
    """Sales-bot admin (/reply, /status) — never a customer thread."""
    ids = load_admin_chat_ids()
    return next(iter(ids)) if ids else None


def load_registered_chat_id() -> int | None:
    """/notifyme (veya /admin) ile KAYITLI satış-botu operatör sohbeti.

    Env'deki ID eski/yeni hesap olabilir; butonlu bildirim kayıtlı sohbete gider
    ki inline callback'i polling yapan satış botu işleyebilsin.
    """
    if not PATH.exists():
        return None
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return _parse_chat_id(str(data.get("chat_id") or ""))


def load_notify_chat_id() -> int | None:
    """Ops notifications only — private admin chat/channel, not customer inboxes."""
    cid = _parse_chat_id(config.TELEGRAM_NOTIFY_CHAT_ID)
    if cid is not None:
        return cid
    cid = _parse_chat_id(config.TELEGRAM_OWNER_CHAT_ID)
    if cid is not None:
        return cid
    if not PATH.exists():
        return None
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return _parse_chat_id(str(data.get("notify_chat_id") or data.get("chat_id") or ""))


def load_chat_id() -> int | None:
    """Backward compat — notify destination."""
    return load_notify_chat_id()


def save_chat_id(chat_id: int) -> None:
    data: dict[str, Any] = {}
    if PATH.exists():
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    data["notify_chat_id"] = int(chat_id)
    data.setdefault("chat_id", int(chat_id))
    PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
    import knowledge
    import enterprise_apply
    import enterprise_targets
    import telegram_sessions

    submitted = sum(v for k, v in counts.items() if str(k) in knowledge.CONFIRMED_SUBMIT_STATUSES)
    sessions = [r for r in telegram_sessions._load().values() if isinstance(r, dict)]
    funnel = {key: sum(bool(r.get(key)) for r in sessions) for key in
              ("interest_reported", "contract_signed", "payment_reported", "payment_verified")}
    # DİKKAT: listedeki ardışık string'ler Python'un implicit concatenation'ı ile
    # TEK satıra birleşir. Bir bloğun ortasına yeni bir satır eklerken araya
    # boşluk bırakmak "Kuyruk: ..." ile "Model: ..."ı yapıştırır. Özet bu yüzden
    # tek bloktur; Funnel/Kuyruk satırları bir kez yazılır (sahibe giden özet
    # mesajında tekrar ve yapışma olmaz).
    lines = [
        "DevSolve motor özeti (Oracle, Always Free)",
        f"Model: {config.OLLAMA_MODEL}",
        f"Toplam lead: {len(data)}",
        f"Form gönderildi: {submitted}",
        f"Kurumsal uygun hedef: {len(enterprise_targets.load_all())}; "
        f"deneme gün/saat: {enterprise_apply.enterprise_counts()}",
        f"Funnel (başvuru ≠ kabul ≠ tahsilat): {funnel}",
        "Kabul/dönüş oranı yalnızca doğrulanmış (verified) sonuçlarla ölçülür; "
        "henüz yeterli veri birmediğinde kesin rakam paylaşılmaz.",
        f"Kuyruk: {domain_store.queue_depth()}/{getattr(config, 'QUEUE_TARGET', 150)} "
        f"(max {getattr(config, 'QUEUE_MAX', 250)}) | hazır {domain_store.ready_pool_size()} "
        f"| HTTP {domain_store.http_budget_label()}",
        f"Durumlar: {counts}",
        "",
        "Telegram sohbetin boşsa bu normal: satış botu müşteriye ilk mesajı ATMAZ.",
        "Müşteri formdan t.me linkine tıklayınca satış botunda sohbet başlar.",
        "Pipeline / sıcak lead → yalnızca ops chat (müşteri satış sohbetine gitmez).",
        "Satış devralma: satış botunda /reply CHATID metin",
        "Bu özet yalnızca sana gider. /stop müşteri çıkışıdır, bunu kapatmaz.",
    ]
    return "\n".join(lines)


def _post_message(target: int, body: str, *, silent: bool,
                  reply_markup: dict[str, Any] | None = None) -> bool:
    import flood_guard

    token = (config.TELEGRAM_NOTIFY_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN or "").strip()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    payload: dict[str, Any] = {
        "chat_id": target,
        "text": body[:3500],
        "disable_notification": silent,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    # httpx yolu da flood kapısından geçer: ceza aktifse Telegram'a vurmadan
    # çık (çağıran kuyruğa yazar), 429 RetryAfter gelirse ceza kaydedilir.
    if not flood_guard.sync_acquire(int(target)):
        raise flood_guard.FloodBlocked(int(target), flood_guard.remaining(int(target)))
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        ra = getattr(exc, "retry_after", None)
        if ra is None and exc.response is not None and exc.response.status_code == 429:
            try:
                ra = float(exc.response.headers.get("retry-after") or 0) or None
            except (TypeError, ValueError):
                ra = None
        if ra is not None:
            flood_guard.note_retry_after(float(ra), chat_id=int(target))
        raise
    return True


# Rapor: canlı müşteri talebi bildirimi altında tek kurgulanmış buton.
HANDOFF_BUTTON_TEXT = "💬 Sohbete Bağlan / Reply"


def handoff_keyboard(target_chat_id: int) -> dict[str, Any]:
    """Inline Keyboard: patron bastığı an sohbet ona devredilir."""
    return {"inline_keyboard": [[{
        "text": HANDOFF_BUTTON_TEXT,
        "callback_data": f"handoff:{int(target_chat_id)}",
    }]]}


def handoff_route() -> dict[str, Any]:
    """Butonlu bildirim rotası — callback'i POLLING yapan satış botuna düşmeli.

    1) /notifyme ile kayıtlı satış-botu operatör sohbeti varsa: hedef o sohbet,
       token satış botu tokeni (buton callback'ini aynı polling döngüsü alır).
    2) Kayıtlı sohbet yoksa: ops kanalına butonsuz düz bildirim (haber yine gider;
       /reply CHATID yedeği her zaman çalışır).
    """
    sales_token = (config.TELEGRAM_BOT_TOKEN or "").strip()
    notify_token = (config.TELEGRAM_NOTIFY_BOT_TOKEN or "").strip()
    registered = load_registered_chat_id()
    if registered is not None and sales_token:
        return {"chat_id": int(registered), "token": sales_token, "token_kind": "sales",
                "button": True,
                "reason": "kayitli satis-botu sohbeti (buton callback'i polling hattina duser)"}
    notify_chat = load_notify_chat_id()
    return {"chat_id": int(notify_chat) if notify_chat is not None else None,
            "token": notify_token or sales_token,
            "token_kind": "notify" if notify_token else "sales",
            "button": False,
            "reason": ("ops kanali — satis-botu sohbeti kayitli degil; operatör "
                       "/notifyme <TELEGRAM_ADMIN_TOKEN> ile kaydolunca buton aktiflesir")}


def send_handoff_alert(text: str, *, target_chat_id: int,
                       chat_id: int | None = None,
                       token: str | None = None,
                       high_priority: bool = True) -> bool:
    """🚨 Canlı müşteri talebi: bildirim + [💬 Sohbete Bağlan / Reply] butonu.

    Buton gönderilemezse (ağ hatası / buton reddi) butonsuz düz bildirim denenir;
    haber her koşulda patrona ulaşır. Rota handoff_route() ile seçilir.
    """
    import circuit_breaker

    route = handoff_route()
    destination = chat_id if chat_id is not None else route.get("chat_id")
    use_token = token or str(route.get("token") or "")
    if not destination or not use_token:
        logger.warning("Handoff alert skipped (ops hedefi/token yok — /notifyme ile kayit ol)")
        return False
    body = text if text.startswith("[DevSolve") else f"[DevSolve Ops]\n{text}"
    markup = handoff_keyboard(target_chat_id) if route.get("button") else None
    try:
        _post_message(int(destination), body, silent=not high_priority,
                      reply_markup=markup, token=use_token)
        circuit_breaker.record("telegram_notify", True)
        return True
    except Exception as exc:  # noqa: BLE001 — bildirim buton yüzünden kaybolmaz
        logger.warning("Handoff alert failed (%s) — plain fallback", _mask(exc))
        try:
            _post_message(int(destination), body, silent=not high_priority, token=use_token)
            circuit_breaker.record("telegram_notify", True)
            return True
        except Exception as exc2:  # noqa: BLE001
            circuit_breaker.record("telegram_notify", False)
            logger.error("Handoff alert failed: %s", _mask(exc2))
            return False


def deliver_queued_notify(task: dict[str, Any]) -> bool:
    """task_queue işçisi: kuyruktaki bildirimi tek denemede gönder (şalter kontrollü)."""
    import circuit_breaker

    payload = task.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {"text": str(payload)}
    if not circuit_breaker.allow("telegram_notify"):
        return False
    target = payload.get("chat_id") or load_notify_chat_id()
    if not target:
        return True  # hedef yok — görev anlamsız, ack'le
    try:
        _post_message(int(target), str(payload.get("text") or ""),
                      silent=not bool(payload.get("high_priority")))
        circuit_breaker.record("telegram_notify", True)
        return True
    except Exception as exc:  # noqa: BLE001
        circuit_breaker.record("telegram_notify", False)
        logger.warning("Queued notify #%s failed: %s", task.get("id"), exc)
        return False


def probe_delivery() -> dict[str, Any]:
    """Bildirim hattının GERÇEK sağlığı (mesaj göndermeden ölçer).

    Token'ın .env'de VAR olması yetmez: Telegram geçersiz/iptal token'a 404,
    bot hesabına hedeflenmiş sohbete 403 döner. Bu yüzden "kanal: True"
    yanılsaması oluşup lead bildirimleri sessizce kaybolabiliyordu.
    getMe + getChat ile gerçek durum ölçülür (hiç mesaj gönderilmez).
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    notify_token = (config.TELEGRAM_NOTIFY_BOT_TOKEN or "").strip()
    sales_token = (config.TELEGRAM_BOT_TOKEN or "").strip()
    target = load_notify_chat_id()
    notify_valid = _token_ok(notify_token)
    sales_valid = _token_ok(sales_token)
    token = notify_token if notify_valid else sales_token
    result: dict[str, Any] = {
        "notify_token_valid": notify_valid,
        "sales_token_valid": sales_valid,
        "token_used": "notify" if notify_valid else ("sales" if sales_valid else "yok"),
        "target_chat_id": target,
        "target_reachable": False,
        "target_detail": "",
        "can_deliver": False,
    }
    if not target or not token:
        result["target_detail"] = "hedef chat veya geçerli token yok"
        return result
    try:
        resp = httpx.post(f"https://api.telegram.org/bot{token}/getChat",
                          json={"chat_id": int(target)}, timeout=20)
        data = resp.json()
        result["target_reachable"] = bool(data.get("ok"))
        detail = str(data.get("description") or (data.get("result") or {}).get("type") or "")
        result["target_detail"] = _mask(detail)
    except Exception as exc:  # noqa: BLE001
        result["target_detail"] = _mask(exc)
    result["can_deliver"] = bool(result["target_reachable"] and (notify_valid or sales_valid))
    return result


def send(text: str, *, chat_id: int | None = None, high_priority: bool = False) -> bool:
    """Send owner notification. high_priority=True disables silent notification
    so the owner's phone alerts even in Do-Not-Disturb mode.

    Dayanıklılık: Telegram API şalteri açıksa veya tüm denemeler başarısızsa
    bildirim KAYBOLMAZ — task_queue'ya yazılır, hat dönüşünce otomatik iletilir.
    """
    import circuit_breaker
    import task_queue

    target = chat_id if chat_id is not None else load_notify_chat_id()
    token = (config.TELEGRAM_NOTIFY_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN or "").strip()
    if not target or not token:
        logger.info("Ops notify skipped (no TELEGRAM_NOTIFY_CHAT_ID / OWNER chat in .env)")
        return False
    body = f"[DevSolve Ops]\n{text}" if not text.startswith("[DevSolve") else text
    if not circuit_breaker.allow("telegram_notify"):
        task_queue.enqueue("telegram_notify",
                           {"chat_id": int(target), "text": body,
                            "high_priority": bool(high_priority)},
                           max_attempts=8, delay_s=45)
        logger.warning("Telegram notify circuit open — queued instead")
        return False
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            _post_message(int(target), body, silent=not high_priority)
            circuit_breaker.record("telegram_notify", True)
            return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("Owner Telegram notify attempt %s failed: %s", attempt, _mask(exc))
            time.sleep(2 * attempt)
    circuit_breaker.record("telegram_notify", False)
    task_queue.enqueue("telegram_notify",
                       {"chat_id": int(target), "text": body,
                        "high_priority": bool(high_priority)},
                       max_attempts=8, delay_s=60)
    logger.error(
        "Owner Telegram notify failed after retries — queued for relay. "
        "Kontrol: TELEGRAM_NOTIFY_BOT_TOKEN gecerliligi (getMe) ve hedef chat "
        "(bot hesabina isaret ediyorsa 403 doner). Son hata: %s",
        _mask(last_exc) if last_exc else "bilinmiyor",
    )
    return False


def notify_pipeline(
    counts: dict[str, Any],
    *,
    submitted: int,
    scoped: int,
    skipped: int = 0,
) -> None:
    del counts
    import knowledge

    today_n, hour_n = knowledge.submit_counts()
    hourly = knowledge.hourly_cap()
    if scoped <= 0:
        fuel = domain_store.chromium_fuel_count()
        queue = domain_store.queue_depth()
        logger.info(
            "Pipeline slice empty — fuel=%s queue=%s (notify only if fuel thin)",
            fuel,
            queue,
        )
        floor = min(int(getattr(config, "HOURLY_SUBMIT_FLOOR", 30) or 30), hourly)
        need = max(0, floor - hour_n)
        if fuel < max(need * 3, 1) and need > 0:
            send(
                "Pipeline turu: 0 form (yakıt/kuyruk ince).\n"
                f"Yakıt: {fuel} | Kuyruk: {queue}\n"
                f"Saat: {hour_n}/{hourly}\n"
                "GitHub feed yenilenince kuyruk dolar."
            )
        return

    floor = min(int(getattr(config, "HOURLY_SUBMIT_FLOOR", 30) or 30), hourly)
    send(
        "Pipeline turu bitti.\n"
        f"Bu tur onaylı form: {submitted}\n"
        f"Saatlik toplam onaylı form: {hour_n}/{hourly} (taban hedef {floor})\n"
        f"Atlanan (CAPTCHA / form yok / ulaşılamaz): {skipped}\n"
        f"Bu tur bakılan: {scoped}\n"
        f"Gün toplamı: {today_n}/{knowledge.daily_cap()}\n"
        f"Kuyruk: {domain_store.queue_depth()}/{getattr(config, 'QUEUE_TARGET', 150)} "
        f"| HTTP {domain_store.http_budget_label()}\n"
        "Müşteri yazarsa yalnızca satış botundaki kendi sohbetine düşer."
    )
