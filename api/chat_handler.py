"""HA WebSocket + chat readiness (target: api/chat_handler.py).

webchat_server/core uzerinde ince hazirlik katmani: auto-reconnect tasiyici
(sunucu: /health + oturum korumasi; istemci parcasi templates/chat.html'de
mevcut backoff'u tamamlar), health-check, session persist, timeout yonetimi.
"""
from __future__ import annotations
import logging, os, re, time
from typing import Any
logger = logging.getLogger(__name__)
RECONNECT_MAX = int(os.getenv("CHAT_RECONNECT_MAX", "12") or 12)
BASE_BACKOFF_S = float(os.getenv("CHAT_BACKOFF_S", "1.0") or 1.0)
SESSION_TIMEOUT_S = float(os.getenv("CHAT_SESSION_TIMEOUT_S", "1800") or 1800)
HEALTH_PATH = "/health"
def backoff_schedule(n: int = RECONNECT_MAX) -> list:
    return [min(8.0, BASE_BACKOFF_S * (2 ** i)) for i in range(max(0, n))]
def session_alive(sid: str) -> bool:
    try:
        import webchat_core as wc
        s = wc.get_session(sid)
        if not s: return False
        return (time.time() - float(s.get("last_at") or 0)) < SESSION_TIMEOUT_S
    except Exception: return False
def ensure_session_state(sid: str, **kw: Any) -> dict:
    """Oturumu acar/gunceller ve SATIR dondurur (ensure_session tek deger verir)."""
    import webchat_core as wc
    s = wc.get_session(sid)
    if s: return s
    return wc.ensure_session(sid, lang=str(kw.get("lang") or "en"))
def health_snapshot() -> dict:
    snap: dict[str, Any] = {"health_path": HEALTH_PATH, "reconnect_max": RECONNECT_MAX,
                            "session_timeout_s": SESSION_TIMEOUT_S, "ok": True}
    try:
        import webchat_server as ws
        app = ws._lazy_app()
        snap["routes"] = sorted({r.path for r in app.routes})
        snap["ws_route"] = "/ws/{sid}" in snap["routes"]
        snap["ok"] = snap["ws_route"] and "/health" in snap["routes"]
    except Exception as e: snap.update(ok=False, error=str(e)[:150])
    return snap
def run_check() -> dict:
    return {"health": health_snapshot(), "backoff": backoff_schedule(5)}

# --- Akis/SSE zirhi (target: api/chat_handler.py) ---------------------------
# API SSE akisinda JSON formatinda hata govdesi ya da HTTP 404/500 dondugunde
# bunun chata METIN olarak basilmasi engellenir. '[object Object]' ve raw JSON
# hata metinleri suzulur. ZERO-NOTICE: musteriye "aksama oldu", "yeniden
# baglaniliyor" ya da herhangi bir teknik/hata mesaji GOSTERILMEZ — bozuk
# yanit sessizce atlanir, akis yerel Ollama'dan pürüzsüz devam eder.
# (Bu sabit artik BOS: hicbir kosulda musteriye yazilmaz; yalnizca gecmis
#  uyumlulugu icin tutulur.)
DISRUPT_NOTICE = ""
DEGRADED_KEY = "__degraded"

def _looks_like_error_payload(s: str) -> bool:
    """'[object Object]' / raw JSON hata / HTTP hata metni mi?"""
    t = (s or "").strip()
    if not t:
        return False
    if "[object " in t:
        return True
    if t[0] in "{[" and t[-1] in "}]":
        return True
    low = t.lower()
    if low.startswith(("http ", "error", "{\"error", "{\"detail", "404 ", "500 ", "502 ")):
        return True
    # tek parca HTTP kodu/soz dizimi (404 Not Found, 500 Internal...)
    if len(t) <= 60 and re.search(r"\b(404|429|500|502|503|504)\b", t) and \
            re.search(r"(not\s*found|error|bad\s*request|internal|server)", t, re.I):
        return True
    return False

def guard_reply(text: Any) -> dict:
    """Yaniti chat-guvenli paketler (ZERO-NOTICE: musteriye hicbir hata yazisi).

    Dondurur: {"text": str, "degraded": bool}
    - temiz metin   -> {"text": <metin>, "degraded": False}
    - hata govdesi  -> {"text": "",      "degraded": True}
      boylece cagiran bu mesaji SESSIZCE atlar; musteri aksama mesaji gormez.
    """
    from core.llm_router import sanitize_reply as _san
    if isinstance(text, (dict, list)):
        return {"text": "", "degraded": True}
    safe, ok = _san(text)
    if ok:
        return {"text": safe, "degraded": False}
    return {"text": "", "degraded": True}

def degrade_notice(text: Any) -> str:
    """Yalnizca guvenli metin (bozuksa bos — zero-notice)."""
    return guard_reply(text)["text"]
