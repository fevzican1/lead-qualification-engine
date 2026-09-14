"""
Şalter Mekanizması (Circuit Breaker) — dosya tabanlı, çok süreçli.

Bir sağlayıcı (Telegram API, hedef site, form backend) ardışık `threshold`
kere hata verirse o hattı `cooldown_s` boyunca dondurur: bekleyip kilitlenme
yok, diğer hatlar devam eder. Süre bitince yarı-açık (half-open) tek deneme;
başarılıysa kapanır, başarısızsa şalter yeniden açılır.

State: nirvana/state/circuit_breakers.json — tüm süreçler aynı şalteri görür.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import config

logger = logging.getLogger(__name__)

STATE_PATH = config.ROOT / "nirvana" / "state" / "circuit_breakers.json"
_LOCK = threading.Lock()

DEFAULT_THRESHOLD = 3
DEFAULT_COOLDOWN_S = 30.0


def _load() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def allow(name: str, *, threshold: int = DEFAULT_THRESHOLD,
          cooldown_s: float = DEFAULT_COOLDOWN_S) -> bool:
    """Şalter açıksa False döner (istek ATILMAZ); half-open tek denemeye izin verir."""
    now = time.time()
    with _LOCK:
        state = _load()
        row = state.get(name)
        if not isinstance(row, dict):
            return True
        opened_at = float(row.get("opened_at") or 0)
        if now - opened_at < cooldown_s:
            return False
        if now - opened_at < cooldown_s * 2 and row.get("half_open_tried"):
            # Yarı-açık deneme yapıldı ve hâlâ kayıt duruyor → yeni şalter döngüsü
            return False
        if now - opened_at >= cooldown_s:
            # Yarı-açık: tek prob izni
            row["half_open_tried"] = True
            row["half_open_at"] = now
            state[name] = row
            _save(state)
        return True


def record(name: str, ok: bool, *, threshold: int = DEFAULT_THRESHOLD,
           cooldown_s: float = DEFAULT_COOLDOWN_S) -> None:
    """Sonucu işle. Başarı şalteri kapatır; ardışık `threshold` hata açar."""
    with _LOCK:
        state = _load()
        row = state.get(name) if isinstance(state.get(name), dict) else {}
        if ok:
            state.pop(name, None)
            _save(state)
            return
        fails = int(row.get("consecutive_fails") or 0) + 1
        now = time.time()
        opened_at = float(row.get("opened_at") or 0)
        if fails >= max(1, int(threshold)) and now - opened_at >= cooldown_s:
            state[name] = {"consecutive_fails": fails, "opened_at": now,
                           "half_open_tried": False}
            logger.warning("Circuit OPEN: %s (%s ardışık hata, %ss soğuma)", name, fails, cooldown_s)
        else:
            row["consecutive_fails"] = fails
            state[name] = row
        _save(state)


def status() -> dict[str, Any]:
    """Şalter panosu — /status veya operatör özeti için."""
    now = time.time()
    out: dict[str, Any] = {}
    for name, row in _load().items():
        if not isinstance(row, dict):
            continue
        opened_at = float(row.get("opened_at") or 0)
        out[name] = {
            "open": now - opened_at < DEFAULT_COOLDOWN_S,
            "consecutive_fails": int(row.get("consecutive_fails") or 0),
            "opened_at": opened_at,
        }
    return out


def reset(name: str) -> None:
    with _LOCK:
        state = _load()
        state.pop(name, None)
        _save(state)
