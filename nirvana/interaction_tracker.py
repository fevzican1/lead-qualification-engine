"""Lane MOD-19 — interaction_tracker [Oracle VM, light].

Etkileşim izleyici: Payoneer link tıklama/açma sinyallerini chat↔domain
bağlantısıyla kaydeder, owner'a bildirim üretir. Saf JSON — maliyet sıfır,
forget_guard izolasyonuna uyar (domain-dışı kayıt yok).
"""
from __future__ import annotations

import json
import time
from typing import Any

from nirvana.registry import state_path

LOG = "interaction_log.json"


def _load() -> dict[str, Any]:
    path = state_path(LOG)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"events": []}


def track(chat_id: int, domain: str, event: str) -> dict[str, Any]:
    data = _load()
    data["events"].append({"chat_id": int(chat_id), "domain": str(domain),
                           "event": str(event), "ts": time.time()})
    data["events"] = data["events"][-500:]
    path = state_path(LOG)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        import owner_notify
        owner_notify.send(
            f"🔗 LİNK ETKİLEŞİMİ — chat {chat_id} ({domain}): {event}. "
            f"Ödeme henüz doğrulanmadı; pipeline kapalı.")
    except Exception:
        pass
    return {"ok": True, "event": event}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    data = _load()
    return {"events": len(data.get("events", [])), "out": str(state_path(LOG))}
