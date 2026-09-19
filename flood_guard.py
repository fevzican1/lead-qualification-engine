"""Telegram flood koruması — tek kapıdan tüm giden API çağrıları.

Neden: Telegram, satış botuna RetryAfter (flood control) cezası verdiğinde
bot çakılıp restart döngüsüne giriyor ve ceza daha da büyüyordu. Bu modül:

1. Hız sınırları (ceza hiç yaşamamak için):
   - sohbet başına min 1.5 sn aralık (Telegram private limiti 1/sn + pay)
   - sohbet başına min 5.0 sn "typing" aralığı (chat action da flood sayılır)
   - global min 0.10 sn aralık (30/sn limit + burst koruması)
2. RetryAfter yakalanırsa ceza CEZAYI ALAN SOHBETE yazılır; süre boyunca
   o sohbete giden mesajlar (a) kısaysa bekleyip gönderilir, (b) uzunsa
   güvenle düşürülür (FloodBlocked) — Telegram'a spam atmadan.
3. install(bot): PTB Bot._post'u sarar — send_message, reply_text,
   send_photo, send_chat_action... hepsi tek noktadan korunur.

KRİTİK TASARIM KARARLARI (canlı arıza sonrası, 2026-09):
   (a) Ceza kapısı YALNIZCA mesaj gönderen uçlara uygulanır. getUpdates gibi
       okuma uçları asla FloodBlocked ile düşürülmez; çünkü PTB'nin getUpdates
       çağrısı da Bot._post'tan geçer ve kapı kapalıyken polling'i öldürüyordu:
       bot süreci systemd tarafından 1 sn'de yeniden başlatılıyor, her açılışta
       setMyName/getUpdates atıyor, Telegram cezası 2.4 saatten 15.8 saate
       büyüyordu (gözlemlenen: RetryAfter 8608s → 56955s).
   (b) Ceza SOHBET BAŞINA tutulur: tek bir sohbetin cezası Telegram'daki TÜM
       sohbetlere giden mesajları düşürmez. GLOBAL_MAX_RETRY_AFTER yalnızca
       KENDİ hız soyutlamamız olan global kapı içindir; Telegram'ın sohbet
       cezası asla kırpılmaz (STRICT COOLDOWN).
   (c) Ceza süresi DİSKE yazılır (nirvana/state/flood_gate.json): süreç yeniden
       başlasa bile kapı açılmaz, böylece restart döngüsü Telegram cezasını
       büyütemez (cezayı "unutan" bot aynı sohbete tekrar yazarak cezayı
       ikiye katlıyordu).
   (d) STRICT COOLDOWN (2026-09-19 canlı arıza: 73420 sn FLOOD_WAIT):
       Telegram ne kadar süre verdiyse diskte/bellekte O SÜRE yazılır —
       yapay tavanla kırpma, kapıyı erken tıklatma YOK.
   (e) %100 EGRESS KORUMASI: arka plan ping'leri, durum denetimleri
       (health-check/getMe) ve müşteri mesajları istisnasız aynı kapıdan
       geçer. Cezalı (PASSIVE) botun tokenine süre bitene kadar tek istek
       bile atılmaz — Telegram'ın "kısıtlamayı delme" algoritması
       tetiklenmez (owner_notify._pool_tokens + probe_delivery + polling
       sweep; süre sonu otomatik aktivasyon: bot_registry sweep).

Kullanım:
    flood_guard.install(application.bot)      # _post_init'te bir kez
    flood_guard.sync_acquire(chat_id)         # httpx ile gönderen yollar için
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

import config

logger = logging.getLogger(__name__)


class FloodBlocked(RuntimeError):
    """Flood cezası uzun — bu mesaj gönderilmedi (sessizce düşürüldü)."""

    def __init__(self, chat_id: Any, remaining: float) -> None:
        super().__init__(f"flood gate aktif ({remaining:.0f}s) — chat {chat_id} mesaji duşuruldu")
        self.chat_id = chat_id
        self.remaining = remaining


# Telegram limitleri, güvenli pay ile:
CHAT_MIN_INTERVAL = 1.5   # private chat: 1 msg/sn → 1.5s
CHAT_ACTION_MIN_INTERVAL = 5.0  # typing/upload action: sohbet başına 5s
GLOBAL_MIN_INTERVAL = 0.10  # global: 30 msg/sn → burst koruması
# Ceza bu kadar kısaysa göndermeden önce bekle; uzunsa mesajı düşür.
SHORT_WAIT_MAX = 20.0
# Global kapı üst sınırı: tek sohbetin cezası tüm filoyu susturamaz.
GLOBAL_MAX_RETRY_AFTER = 15 * 60.0
# Sohbet başına makul üst sınır: Telegram'ın verebileceği en büyük makul ceza (2 gün).
MAX_RETRY_AFTER = 2 * 86400.0

# Gönderim YAPMAYAN uçlar: ceza kapısından muaf (polling'i ve sağlık
# probunu asla öldürmemeli).
BYPASS_ENDPOINTS = frozenset({
    "getupdates", "getme", "getchat", "getchatmember", "getchatmembercount",
    "getchatadministrators", "getfile", "getwebhookinfo", "getuserprofilephotos",
    "answercallbackquery", "setwebhook", "deletewebhook", "setmyname",
    "setmydescription", "setmyshortdescription", "setmycommands",
    "getmycommands", "deletemycommands", "logout", "close",
})
_until = 0.0              # global cooldown bitişi (monotonic)
_chat_until: dict[int, float] = {}
_last_global = 0.0
_last_chat: dict[int, float] = {}
_last_action: dict[int, float] = {}
_lock = asyncio.Lock()

# httpx (thread) yolu için ayrı pacing durumu — async lock thread'den kullanılmaz.
_sync_lock = threading.Lock()
_sync_last_global = 0.0
_sync_last_chat: dict[int, float] = {}

_STATE_PATH = config.ROOT / "nirvana" / "state" / "flood_gate.json"
_loaded = False


def _load_state() -> None:
    """Ceza süreleri diskte kalıcı: restart cezayı 'unutup' büyütemez."""
    global _until, _loaded
    if _loaded:
        return
    _loaded = True
    if not _STATE_PATH.exists():
        return
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    now = time.time()
    monotonic_now = time.monotonic()
    try:
        global_left = float(data.get("global_until_epoch") or 0.0) - now
        if global_left > 0:
            _until = monotonic_now + min(global_left, GLOBAL_MAX_RETRY_AFTER)
        for key, value in (data.get("chats") or {}).items():
            left = float(value) - now
            if left > 0:
                try:
                    cid = int(key)
                except (TypeError, ValueError):
                    continue
                # STRICT COOLDOWN: diskten gelen sohbet cezası kim olduğuna
                # bakılmaksızın AYNEN geri yüklenir (operatör istisnası yok —
                # erken tıklatma Telegram cezasını uzatır).
                _chat_until[cid] = monotonic_now + left
    except (TypeError, ValueError):
        return


def _save_state() -> None:
    now = time.time()
    monotonic_now = time.monotonic()
    payload = {
        "global_until_epoch": now + max(0.0, _until - monotonic_now),
        "chats": {
            str(chat_id): now + max(0.0, until - monotonic_now)
            for chat_id, until in _chat_until.items()
            if until > monotonic_now
        },
    }
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except Exception:  # noqa: BLE001
        logger.debug("flood_gate state yazilamadi", exc_info=True)


def note_retry_after(seconds: float, chat_id: int | None = None,
                     bot_username: str = "") -> float:
    """Telegram 429 cezası geldi — kapıyı kapat + botu havuzda PASSIVE'a çek.

    Dinamik Bot Havuzu: cezayi yiyen bot cooldown_until ile PASSIVE moda
    alinir — form linki rotasyonundan cikar; sure dolunca otomatik ACTIVE.
    Kritik bildirimler (sicak temas / odeme) bu kapidan ETKILENMEZ:
    onlar flood harici hatta gonderilir (owner_notify.CRITICAL).
    """
    global _until
    _load_state()
    try:
        seconds = min(max(float(seconds), 1.0), MAX_RETRY_AFTER)
    except (TypeError, ValueError):
        seconds = 60.0
    now = time.monotonic()
    global_until = now + min(seconds, GLOBAL_MAX_RETRY_AFTER)
    if global_until > _until:
        _until = global_until
    if chat_id is not None:
        try:
            cid: int | None = int(chat_id)
        except (TypeError, ValueError):
            cid = None
        # STRICT COOLDOWN: sohbet cezası kimliğe bakılmadan AYNEN yazılır
        # (kırpma yok). Kapı, Telegram'ın verdiği sürede o sohbeti bağlar;
        # süre dolmadan tek istek gitmez.
        if cid is not None and now + seconds > _chat_until.get(cid, 0.0):
            _chat_until[cid] = now + seconds
            logger.warning(
                "FLOOD GATE: chat %s icin %.0f sn Telegram cezasi — o sohbet duraklatildi",
                cid, seconds,
            )
    else:
        logger.warning(
            "FLOOD GATE: %.0f sn Telegram cezasi — giden mesajlar duraklatildi", seconds
        )
    _save_state()
    try:
        import bot_registry
        bot_registry.mark_passive(bot_username, seconds)
    except Exception:  # noqa: BLE001 — kayit defteri yoksa kapi yine calisir
        pass
    return seconds


def _penalty_remaining(chat_id: int | None = None) -> float:
    _load_state()
    now = time.monotonic()
    # STRICT COOLDOWN: Telegram ne kadar süre verdiyse kapı o kadar kapalıdır.
    # Tavan kırpma / erken tıklatma YOK — cezalı sürede Telegram'a tek istek
    # gitmez (erken deneme cezayı uzatır; canlı arıza 2026-09: 73420 sn).
    remaining_s = max(0.0, _until - now)
    if chat_id is not None:
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            return remaining_s
        return max(remaining_s, max(0.0, _chat_until.get(cid, 0.0) - now))
    for until in _chat_until.values():
        remaining_s = max(remaining_s, max(0.0, until - now))
    return remaining_s
    return remaining_s


def remaining(chat_id: int | None = None) -> float:
    """Kapalı kalan süre (sn); 0.0 = açık.

    chat_id verilirse o sohbetin (global cooldown dahil) bekleme süresi döner.
    """
    return _penalty_remaining(chat_id)


def token_ok(token: str) -> bool:
    """Tokenin botu gönderim için güvenli mi? (ZERO-TOUCH PASSIVE kapısı)

    - Bot karantinada (PASSIVE / FLOOD_WAIT) ise False: o tokenle TEK istek
      bile atılmaz (getMe/ping/health-check dâhil).
    - Bot adı çözülemiyorsa True (fail-open): adı bilinmeyen tokenin
      riski sohbet-bazlı ceza kapısıyla zaten sınırlıdır.
    Çözüm tamamen diskten yapılır (bot_registry token_hint / bot_ids.json
    önbelleği) — Telegram'a HTTP çağrısı atılmaz.
    """
    tok = (token or "").strip()
    if not tok:
        return False
    try:
        import hashlib

        import bot_registry
        uname = bot_registry.owner_for_hint(
            hashlib.sha256(tok.encode()).hexdigest()[:12])
        if not uname:
            uname = config.bot_username_for_token(tok)
        if not uname:
            return True  # fail-open: adı bilinmeyen token engellenmez
        return bot_registry.is_active(uname)
    except Exception:  # noqa: BLE001 — kayıt defteri yoksa kapı yine çalışır
        return True


def status() -> dict[str, Any]:
    """Teşhis: hangi sohbet ne kadar bekliyor (probe / rapor için)."""
    _load_state()
    now = time.monotonic()
    chats = {cid: round(until - now, 1) for cid, until in _chat_until.items() if until > now}
    return {"global_remaining": round(max(0.0, _until - now), 1), "chats": chats}


def reset() -> None:
    """Testler için: tüm durumu (diskteki kopya dahil) sıfırla."""
    global _until, _last_global, _sync_last_global, _loaded
    _until = 0.0
    _last_global = 0.0
    _sync_last_global = 0.0
    _chat_until.clear()
    _last_chat.clear()
    _last_action.clear()
    _sync_last_chat.clear()
    _loaded = False
    try:
        _STATE_PATH.unlink()
    except OSError:
        pass


async def acquire(chat_id: int | None = None, *, max_wait: float = SHORT_WAIT_MAX) -> bool:
    """Göndermeden önce çağrılır.

    True  → gönder (hız sınırı beklemeleri dahil edildi).
    False → ceza uzun, mesajı düşür (FloodBlocked raise etmek çağıranın işi).
    """
    global _last_global
    rem = _penalty_remaining(chat_id)
    if rem > 0:
        if rem > max_wait:
            return False
        await asyncio.sleep(rem)
    async with _lock:
        now = time.monotonic()
        earliest = max(now, _last_global + GLOBAL_MIN_INTERVAL)
        if chat_id is not None:
            cid = int(chat_id)
            earliest = max(earliest, _last_chat.get(cid, 0.0) + CHAT_MIN_INTERVAL)
        # Slotu rezerve et (uyku lock altında değil).
        _last_global = max(_last_global, earliest)
        if chat_id is not None:
            _last_chat[int(chat_id)] = earliest
        wait = earliest - now
    if wait > 0:
        await asyncio.sleep(wait)
    return True


async def acquire_action(chat_id: int, *, max_wait: float = 0.0) -> bool:
    """sendChatAction (typing/upload) kapısı — mesaj değil, ama flood sayılır.

    Sohbet başına 5 sn pacing uygular; ceza aktifse False döner (çağıran
    typing döngüsünü DURDURMALI — döngü ceza sırasında her 4 sn'de tekrar
    denerse Telegram cezasını büyütür).
    """
    if _penalty_remaining(chat_id) > max_wait:
        return False
    async with _lock:
        now = time.monotonic()
        cid = int(chat_id)
        earliest = max(now, _last_action.get(cid, 0.0) + CHAT_ACTION_MIN_INTERVAL)
        _last_action[cid] = earliest
        wait = earliest - now
    if wait > 0:
        await asyncio.sleep(wait)
    return True


def sync_acquire(chat_id: int | None = None, *, max_wait: float = 0.0) -> bool:
    """httpx gibi thread'den gönderen yollar için hız sınırı + ceza kapısı.

    True → gönder. False → ceza aktif / bekleme uzun: mesajı kuyruğa bırak
    (Telegram'a tekrar vurup cezayı büyütme).
    """
    global _sync_last_global
    rem = _penalty_remaining(chat_id)
    if rem > 0:
        if rem > max_wait:
            return False
        time.sleep(min(rem, max_wait))
    with _sync_lock:
        now = time.monotonic()
        earliest = max(now, _sync_last_global + GLOBAL_MIN_INTERVAL)
        if chat_id is not None:
            cid = int(chat_id)
            earliest = max(earliest, _sync_last_chat.get(cid, 0.0) + CHAT_MIN_INTERVAL)
        _sync_last_global = max(_sync_last_global, earliest)
        if chat_id is not None:
            _sync_last_chat[int(chat_id)] = earliest
        wait = earliest - now
    if wait > 0:
        time.sleep(wait)
    return True


def install(bot: Any, bot_username: str = "") -> None:
    """PTB Bot örneğinin TÜM API çağrılarını flood kapısından geçir.

    Bot._post sarılır; reply_text/send_message/send_photo dahil her gönderim
    otomatik korunur. RetryAfter geldiğinde ceza süresi CEZAYI ALAN SOHBETE
    yazılır (tek sohbet tüm filoyu susturamaz) + bot havuzda PASSIVE'a çekilir
    (form linki rotasyonundan çıkar; cooldown dolunca otomatik ACTIVE).
    bot_username verilirse hangi botun pasife çekileceği bilinir; verilmezse
    bot.username'dan çözülür.
    """
    if getattr(bot, "_flood_guard_installed", False):
        return
    original = bot._post
    _uname = str(bot_username or getattr(bot, "username", "") or "")
    bot._flood_guard_bot = _uname

    async def guarded(endpoint: str, data: dict[str, Any], *args: Any, **kwargs: Any):
        name = str(endpoint or "").lower()
        chat_id = data.get("chat_id") if isinstance(data, dict) else None
        cid: int | None = None
        if chat_id is not None:
            text = str(chat_id).lstrip("-")
            if text.isdigit():
                cid = int(chat_id)
        try:
            if name in BYPASS_ENDPOINTS or cid is None:
                # Okuma/sağlık uçları yalnızca global cooldown'a tabidir.
                rem = _penalty_remaining()
                if rem > 0 and name not in BYPASS_ENDPOINTS:
                    await asyncio.sleep(min(rem, SHORT_WAIT_MAX))
                return await original(endpoint, data, *args, **kwargs)
            if name == "sendchataction":
                if not await acquire_action(cid):
                    raise FloodBlocked(cid, remaining(cid))
            elif not await acquire(cid):
                raise FloodBlocked(cid, remaining(cid))
            return await original(endpoint, data, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — RetryAfter fark et, tekrar fırlat
            ra = getattr(exc, "retry_after", None)
            if ra is not None:
                try:
                    note_retry_after(float(ra), chat_id=cid,
                                     bot_username=getattr(bot, "_flood_guard_bot", "") or _uname)
                except (TypeError, ValueError):
                    note_retry_after(60.0, chat_id=cid,
                                     bot_username=getattr(bot, "_flood_guard_bot", "") or _uname)
            raise

    bot._post = guarded
    bot._flood_guard_installed = True
    logger.info(
        "flood_guard kurulu: chat %.1fs / typing %.1fs / global %.2fs hiz siniri "
        "+ sohbet-basina RetryAfter kapisi",
        CHAT_MIN_INTERVAL, CHAT_ACTION_MIN_INTERVAL, GLOBAL_MIN_INTERVAL,
    )
