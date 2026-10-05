"""HA WebSocket + chat readiness (target: api/chat_handler.py).

webchat_server/core uzerinde ince hazirlik katmani: auto-reconnect tasiyici
(sunucu: /health + oturum korumasi; istemci parcasi templates/chat.html'de
mevcut backoff'u tamamlar), health-check, session persist, timeout yonetimi.
"""
from __future__ import annotations
import logging, os, time
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
    import webchat_core as wc
    s = wc.get_session(sid)
    if s: return s
    row, _ = wc.ensure_session(sid, lang=str(kw.get("lang") or "en"))
    return row
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
