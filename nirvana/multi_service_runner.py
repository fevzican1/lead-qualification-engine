"""Lane U — multi_service_runner [Oracle VM, event].

Coklu hizmet yurutme:
- Ayni anda birden fazla musteriye hizmet verir
- Her musteri izole edilir (ayri state dosyalari)
- Musteri memnun kalirsa tekrar gelir, bot tanir
- Telegram link'ine tiklar, odeme yapar, hizmet baslar

Oracle kotasini asmadan: max 5 eszamanli hizmet, her biri haftalik tur.
"""
from __future__ import annotations

import json
import time
from typing import Any

from nirvana.registry import state_path

ACTIVE_SERVICES = "active_services.json"
MAX_CONCURRENT = 5


def load_active() -> list[dict[str, Any]]:
    try:
        return json.loads(state_path(ACTIVE_SERVICES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_active(services: list[dict[str, Any]]) -> None:
    path = state_path(ACTIVE_SERVICES)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(services, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def start_service(chat_id: int, company: str, host: str, *, scope: str = "weekly-monitor") -> dict[str, Any]:
    """Yeni hizmet baslat (kuyruk kontrolu ile)."""
    services = load_active()
    
    # Mevcut kontrol
    if any(s["chat_id"] == chat_id for s in services):
        return {"ok": False, "reason": "already_active"}
    
    # Kota kontrolu
    if len(services) >= MAX_CONCURRENT:
        return {"ok": False, "reason": "quota_full", "active": len(services)}
    
    service = {
        "chat_id": chat_id,
        "company": company,
        "host": host,
        "scope": scope,
        "started_at": time.time(),
        "last_run": 0,
        "status": "active",
        "visit_count": 1,
    }
    services.append(service)
    save_active(services)
    return {"ok": True, "service": service, "active_count": len(services)}


def record_return_visit(chat_id: int) -> dict[str, Any]:
    """Musteri tekrar geldi (Telegram link'ine tikladi)."""
    services = load_active()
    for s in services:
        if s["chat_id"] == chat_id:
            s["visit_count"] = s.get("visit_count", 1) + 1
            s["last_visit"] = time.time()
            save_active(services)
            return {"ok": True, "visit_count": s["visit_count"], "returning": True}
    return {"ok": False, "returning": False}


def complete_service(chat_id: int, *, reason: str = "completed") -> dict[str, Any]:
    """Hizmet tamamlandi veya iptal."""
    services = load_active()
    services = [s for s in services if s["chat_id"] != chat_id]
    save_active(services)
    return {"ok": True, "reason": reason, "active_count": len(services)}


def get_service(chat_id: int) -> dict[str, Any] | None:
    for s in load_active():
        if s["chat_id"] == chat_id:
            return s
    return None


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Oracle timer: aktif servisleri listeler, durum raporu."""
    services = load_active()
    return {
        "active_services": len(services),
        "max_concurrent": MAX_CONCURRENT,
        "services": [{"chat_id": s["chat_id"], "company": s["company"], "status": s["status"]} for s in services],
        "ts": time.time(),
    }
