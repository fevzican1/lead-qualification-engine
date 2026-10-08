"""Async Telegram bildirim hatti (httpx tabanli, spam korumali)."""
from __future__ import annotations
import hashlib
import logging
import os
import time
from typing import Any
logger = logging.getLogger(__name__)
_NOTIFIED: dict[str, float] = {}
_NOTIFY_TTL_S = 86400.0
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()
def resolve_token() -> str:
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_NOTIFY_BOT_TOKEN"):
        tok = _env(key)
        if tok:
            return tok
    return ""
def resolve_chat_id() -> str:
    for key in ("TELEGRAM_CHAT_ID", "TELEGRAM_NOTIFY_CHAT_ID", "TELEGRAM_OWNER_CHAT_ID", "TELEGRAM_ADMIN_ID"):
        cid = _env(key)
        if cid:
            return cid
    return ""
def partner_message(agency: str, contact: str, link: str, model: str = "Whitelabel Retainer (5.000 EUR/ay)", profit_eur: Any = "") -> str:
    a = (agency or "-").strip() or "-"
    c = (contact or "-").strip() or "-"
    li = (link or "-").strip() or "-"
    msg = ("YENI B2B IS ORTAGI KAZANILDI!\n-----------------------------------\nAjans: %s\nIletisim: %s\nModel: %s\nOdeme Baglantisi: %s" % (a, c, model, li))
    pe = str(profit_eur or "").strip()
    if pe:
        msg += "\nHesaplanan aylik net ek gelir: %s EUR/ay (kar recetesi)" % pe
    return msg
def _dedupe_key(agency: str, contact: str) -> str:
    raw = ("%s|%s" % ((agency or "").strip().lower(), (contact or "").strip().lower())).encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest()[:24]
def already_notified(agency: str, contact: str) -> bool:
    ts = _NOTIFIED.get(_dedupe_key(agency, contact), 0.0)
    return bool(ts and (time.time() - ts) < _NOTIFY_TTL_S)
def mark_notified(agency: str, contact: str) -> None:
    _NOTIFIED[_dedupe_key(agency, contact)] = time.time()
class TelegramNotifier:
    def __init__(self, token: str = "", chat_id: str = "", timeout: float = 15.0) -> None:
        self.token = (token or resolve_token()).strip()
        self.chat_id = (chat_id or resolve_chat_id()).strip()
        self.timeout = float(timeout or 15.0)
    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)
    def _endpoint(self) -> str:
        base = _env("TELEGRAM_BOT_API_BASE_URL").rstrip("/")
        if base:
            return "%s/bot%s/sendMessage" % (base, self.token)
        return "https://api.telegram.org/bot%s/sendMessage" % self.token
    def _chat_int(self):
        try:
            t = self.chat_id.strip()
            if t.lstrip("-").isdigit():
                return int(t)
        except Exception:
            pass
        return None
    def _queue(self, body: str, high_priority: bool) -> None:
        try:
            import task_queue as _tq
            cid: Any = self.chat_id
            try:
                cid = int(str(self.chat_id).strip())
            except Exception:
                pass
            _tq.enqueue("telegram_notify", {"chat_id": cid, "text": body, "high_priority": bool(high_priority), "critical": True}, max_attempts=8, delay_s=45)
        except Exception:
            logger.debug("notify queue fallback", exc_info=True)
    async def send(self, text: str, high_priority: bool = True) -> bool:
        body = (text or "").strip()
        if not body or not self.configured:
            return False
        try:
            import flood_guard as _fg
            ok = await _fg.acquire(self._chat_int())
            if not ok:
                raise RuntimeError("flood-gate closed")
        except RuntimeError:
            self._queue(body, high_priority)
            return False
        except Exception:
            pass
        try:
            import circuit_breaker as _cb
            if not _cb.allow("telegram_notify"):
                self._queue(body, high_priority)
                return False
        except Exception:
            pass
        try:
            import httpx as _hx
            async with _hx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self._endpoint(), json={"chat_id": self.chat_id, "text": body, "disable_web_page_preview": True})
                if resp.status_code == 429:
                    retry = 60.0
                    try:
                        retry = float((resp.json().get("parameters") or {}).get("retry_after", 60.0))
                    except Exception:
                        retry = 60.0
                    try:
                        import flood_guard as _fg2
                        _fg2.note_retry_after(retry, chat_id=self._chat_int())
                    except Exception:
                        pass
                    self._queue(body, high_priority)
                    return False
                resp.raise_for_status()
                try:
                    import circuit_breaker as _cb2
                    _cb2.record("telegram_notify", True)
                except Exception:
                    pass
                return True
        except Exception as exc:
            logger.warning("telegram send fail: %s", str(exc)[:160])
            try:
                import circuit_breaker as _cb3
                _cb3.record("telegram_notify", False)
            except Exception:
                pass
            self._queue(body, high_priority)
            return False
    async def notify_partner_onboarded(self, agency_name: str, contact_info: str, payment_checkout_link: str, profit_eur: str = "") -> bool:
        if already_notified(agency_name, contact_info):
            return True
        ok = await self.send(partner_message(agency_name, contact_info, payment_checkout_link, profit_eur=profit_eur), high_priority=True)
        if ok:
            mark_notified(agency_name, contact_info)
        return ok
async def notify_partner_onboarded(agency: str, contact: str, link: str, notifier=None, profit_eur: str = "") -> bool:
    nb = notifier or TelegramNotifier()
    try:
        return await nb.notify_partner_onboarded(agency, contact, link, profit_eur=profit_eur)
    except Exception:
        logger.debug("notify_partner izole hata", exc_info=True)
        return False

