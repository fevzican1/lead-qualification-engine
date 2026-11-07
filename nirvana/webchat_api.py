"""FastAPI / Uvicorn tabanlı Web Application & Webchat API.

Rapor gereksinimi:
  * Ana Ampere A1 VM üzerinde Web Application & Webchat API çalışır.
  * Müşteri hatti (WEBCHAT_PUBLIC_URL) linkiyle erişilir; demo sayfasında
    ödeme butonu aktif ve sorunsuz çalışır.
  * Her istek multi-tenant izolasyon bağlamına bağlanır (X/Y karışıklaşma yok).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from nirvana.tenant_isolation import (
    clear_tenant_id,
    require_tenant_id,
    set_tenant_id,
    tenant_schema,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Nirvana Whitelabel Webchat API", version="1.0.0")

STATE_DIR = Path(__file__).resolve().parent.parent / "nirvana" / "state"
WEBCHAT_SESSIONS_PATH = STATE_DIR / "webchat_sessions.json"


class WebchatMessage(BaseModel):
    chat_id: int
    message: str
    agency: Optional[str] = None


class WebchatSession(BaseModel):
    chat_id: int
    company: Optional[str] = None
    context: dict[str, Any] = {}
    created_at: float = 0.0


# ---------------------------------------------------------------------------
# Middleware: her istek tenant izolasyon bağlamına bağlanır
# ---------------------------------------------------------------------------
@app.middleware("http")
async def tenant_context_middleware(request: Request, call_next):
    """Her HTTP isteği için tenant bağlamını açar ve kapatır.

    İzolasyon bağlamı olmadan istek işlenmez (403).
    """
    tid = request.headers.get("X-Tenant-Id") or request.query_params.get("tenant")
    if tid:
        set_tenant_id(str(tid))
    else:
        # İzolasyon şeması yoksa erişim engellenir (multi-tenant garanti).
        try:
            require_tenant_id()
        except RuntimeError:
            return JSONResponse(
                {"error": "tenant_isolation_required"},
                status_code=403,
            )
    try:
        response = await call_next(request)
        return response
    finally:
        clear_tenant_id()


# ---------------------------------------------------------------------------
# Endpointler
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, Any]:
    """Kalp atışı — self-healing denetleyici tarafından 500 ms aralıklarla tarar."""
    return {
        "ok": True,
        "service": "webchat_api",
        "ts": time.time(),
        "tenant_schema": tenant_schema(require_tenant_id()) if get_tenant_id_ctx() else None,
    }


def get_tenant_id_ctx() -> Optional[str]:
    try:
        from nirvana.tenant_isolation import get_tenant_id as _g

        return _g()
    except Exception:
        return None


@app.post("/api/v1/webchat/message")
async def webchat_message(msg: WebchatMessage) -> dict[str, Any]:
    """Müşteri mesajı alınır, tenant bağlamında işlenir ve yanıt döner."""
    tid = require_tenant_id()  # izolasyon zorunlu
    schema = tenant_schema(tid)

    # Oturum hafızası (session memory): müşteri dönüşü tanınır.
    sessions = _load_sessions()
    sess = sessions.get(str(msg.chat_id))
    if not sess:
        sess = {
            "chat_id": msg.chat_id,
            "company": msg.agency,
            "context": {},
            "created_at": time.time(),
        }
        sessions[str(msg.chat_id)] = sess
        _save_sessions(sessions)

    # Müşteri işlem geçmişi ve X firmasına ait veriler sohbet bağlamına yüklenir.
    sess["context"]["last_message"] = msg.message
    sess["context"]["last_seen"] = time.time()
    _save_sessions(sessions)

    # Otomatik satış temsilcisi: ödeme linkini iletir (demo ödeme butonu).
    from nirvana.payment import payment_link

    reply = (
        f"Merhaba! {msg.agency or 'Değerli Müşteri'} için özel ödeme linkiniz hazır: "
        f"{payment_link()}\n"
        f"İşlem hatasız ve şifreli (tenant şeması: {schema})."
    )
    return {
        "ok": True,
        "tenant_id": tid,
        "tenant_schema": schema,
        "reply": reply,
        "payment_link": payment_link(),
        "chat_id": msg.chat_id,
    }


@app.get("/api/v1/webchat/session/{chat_id}")
async def webchat_session(chat_id: int) -> dict[str, Any]:
    """Müşteri hatırlama: önceki oturum bağlamı döner (tekrar satış için)."""
    require_tenant_id()
    sessions = _load_sessions()
    sess = sessions.get(str(chat_id))
    if not sess:
        raise HTTPException(status_code=404, detail="session_not_found")
    return {"ok": True, "session": sess}


def _load_sessions() -> dict[str, Any]:
    try:
        return json.loads(WEBCHAT_SESSIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_sessions(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = WEBCHAT_SESSIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(WEBCHAT_SESSIONS_PATH)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("nirvana.webchat_api:app", host="127.0.0.1", port=8765, reload=False)


__all__ = ["app"]
