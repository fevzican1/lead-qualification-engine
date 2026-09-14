"""
İşletim Sistemi Bekçisi — heartbeat (self-healing).

systemd altında (Type=notify + WatchdogSec) her başarılı döngüde
`WATCHDOG=1` sinyali gönderir; sinyal kesilirse systemd süreci SIGABRT ile
öldürüp RestartSec içinde tertemiz bellekle yeniden başlatır (MTTR → ~1-2s).

systemd dışı ortamlar (Windows geliştirme, docker) için dosya tabanlı
heartbeat yedeği yazar: nirvana/state/heartbeat_<name>.json
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import config

logger = logging.getLogger(__name__)

STATE_DIR = config.ROOT / "nirvana" / "state"

try:  # saf-python, $0; yoksa dosya yedeği çalışır
    import sdnotify  # type: ignore

    _NOTIFIER = sdnotify.SystemdNotifier()
except Exception:  # noqa: BLE001
    _NOTIFIER = None


def _systemd_active() -> bool:
    return bool(os.environ.get("NOTIFY_SOCKET")) and _NOTIFIER is not None


def notify(status: str) -> bool:
    if _NOTIFIER is None:
        return False
    try:
        _NOTIFIER.notify(status)
        return True
    except Exception:  # noqa: BLE001
        return False


def pulse(name: str = "engine", extra: dict[str, Any] | None = None) -> None:
    """'Hayattayım' sinyali: systemd WATCHDOG=1 + dosya yedeği."""
    if os.environ.get("NOTIFY_SOCKET"):
        notify("WATCHDOG=1")
    now = time.time()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"heartbeat_{name}.json"
    payload: dict[str, Any] = {"ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
    if extra:
        payload.update(extra)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def age_seconds(name: str = "engine") -> float | None:
    path = STATE_DIR / f"heartbeat_{name}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return max(0.0, time.time() - float(data.get("ts") or 0))
    except Exception:  # noqa: BLE001
        return None


def healthy(name: str = "engine", *, max_age_s: float = 30.0) -> bool:
    age = age_seconds(name)
    return age is not None and age <= max_age_s


def ready() -> None:
    """Servis başlangıcını systemd'ye bildir (Type=notify için zorunlu)."""
    if os.environ.get("NOTIFY_SOCKET"):
        if notify("READY=1"):
            logger.info("systemd READY=1 sent")
