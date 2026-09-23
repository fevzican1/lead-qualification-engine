"""Üçlü Zırh (Triple-Shield) — dayanıklılık çekirdeği.

Rapor (Triple-Shield Resilience):
1. Sıkı zaman aşımı: her dış çağrı (LLM, HTTP, kuyruk) `asyncio.wait_for` ile sarılır;
   yanıt vermeyen soket/C-uzantısı olay döngüsünü donduramaz.
2. Periyodik çöp toplama: her batch sonrası `gc_tick()`; RSS 1 GB eşiğini aşarsa
   işçi süreci yumuşak şekilde yenilenir (auto-recycle -> systemd Restart=always).
3. Kalp atışı: 2 sn'de bir `WATCHDOG=1`; olay döngüsü kilitlenirse systemd
   WatchdogSec dolar, süreç temiz bellekle yeniden başlar.

Not: auto-recycle yalnızca systemd altında (NOTIFY_SOCKET/INVOCATION_ID) ve
RESILIENCE_AUTO_RECYCLE=1 iken tetiklenir; testlerde/geliştirmede asla çıkış yapmaz.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

MEMORY_LIMIT_MB = int(os.getenv("RESILIENCE_MEMORY_LIMIT_MB", "1024") or 1024)
WATCHDOG_INTERVAL_S = float(os.getenv("RESILIENCE_WATCHDOG_INTERVAL_S", "2.0") or 2.0)
PULSE_EVERY_S = float(os.getenv("RESILIENCE_PULSE_EVERY_S", "15.0") or 15.0)
LAG_WARN_S = float(os.getenv("RESILIENCE_LAG_WARN_S", "5.0") or 5.0)
DEFAULT_TIMEOUT = float(os.getenv("RESILIENCE_CALL_TIMEOUT_S", "60.0") or 60.0)


def auto_recycle_enabled() -> bool:
    raw = (os.getenv("RESILIENCE_AUTO_RECYCLE", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def under_systemd() -> bool:
    return bool(os.getenv("NOTIFY_SOCKET") or os.getenv("INVOCATION_ID"))


def rss_mb() -> float:
    """Süreç RSS'i (MB) — psutil yok; Linux /proc, macOS resource, Windows ctypes."""
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as handle:
            pages = int(handle.read().split()[1])
        return round(pages * (os.sysconf("SC_PAGE_SIZE") or 4096) / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        import resource  # type: ignore

        peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return round(peak / 1024.0, 1) if peak else 0.0  # Linux: KB -> MB
    except Exception:  # noqa: BLE001
        pass
    try:  # Windows geliştirme: yalnızca gözlem amaçlı
        import ctypes
        import ctypes.wintypes as wt

        class _Counters(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
            handle, ctypes.byref(counters), counters.cb)
        if ok:
            return round(counters.WorkingSetSize / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def over_limit(limit_mb: int | None = None) -> bool:
    return rss_mb() >= float(MEMORY_LIMIT_MB if limit_mb is None else limit_mb)



@dataclass
class MemoryGuard:
    """RAM eşiği bekçisi: aşımda yumuşak yenileme (systemd restart) kararı verir."""

    limit_mb: int = MEMORY_LIMIT_MB
    hits: int = 0
    last_rss_mb: float = 0.0
    history: list[float] = field(default_factory=list)

    def check(self) -> bool:
        self.last_rss_mb = rss_mb()
        self.history.append(self.last_rss_mb)
        del self.history[:-20]
        if self.last_rss_mb >= float(self.limit_mb):
            self.hits += 1
            logger.warning("Bellek eşiği aşıldı: %.1f MB >= %s MB", self.last_rss_mb, self.limit_mb)
            return True
        return False

    def recycle(self, reason: str = "memory_limit") -> bool:
        """Soft recycle: systemd yoksa/kapalıysa yalnızca loglar (testte çıkış yok)."""
        if not (auto_recycle_enabled() and under_systemd()):
            logger.warning("Auto-recycle atlandı (%s) — systemd/izin yok", reason)
            return False
        logger.error("Auto-recycle: %s (rss=%.1f MB) — süreç temiz bellekle yenileniyor",
                     reason, self.last_rss_mb or rss_mb())
        try:
            import heartbeat  # type: ignore

            heartbeat.notify("STOPPING=1")
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)
        return True


def gc_tick(*, force: bool = False) -> int:
    """Batch sonrası çöp toplama; toplanan nesne sayısını döner."""
    try:
        return int(gc.collect())
    except Exception:  # noqa: BLE001
        return 0


async def shielded(awaitable: Awaitable[T], *, timeout: float = DEFAULT_TIMEOUT,
                   fallback: T | None = None, label: str = "") -> T | None:
    """Sıkı zaman aşımı zırhı: dış çağrı asla olay döngüsünü süresiz tutamaz."""
    try:
        return await asyncio.wait_for(awaitable, timeout=float(timeout))
    except asyncio.TimeoutError:
        logger.warning("Shield timeout (%.1fs) %s", timeout, label or "call")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Shield hata %s: %s", label or "call", exc)
    gc_tick()
    return fallback


@dataclass
class LoopLag:
    ticks: int = 0
    max_lag_s: float = 0.0
    last_lag_s: float = 0.0
    late_ticks: int = 0

    def observe(self, lag_s: float) -> bool:
        self.ticks += 1
        self.last_lag_s = round(lag_s, 3)
        self.max_lag_s = max(self.max_lag_s, self.last_lag_s)
        if lag_s > LAG_WARN_S:
            self.late_ticks += 1
            return True
        return False


async def watchdog_loop(name: str = "engine", *, interval: float | None = None,
                        pulse_every: float | None = None,
                        on_late: Callable[[float], None] | None = None,
                        guard: MemoryGuard | None = None,
                        max_iterations: int | None = None) -> LoopLag:
    """2 sn ritimli kalp atışı + gecikme/bellek denetimi (systemd WatchdogSec ile)."""
    tick = float(interval or WATCHDOG_INTERVAL_S)
    full_pulse = float(pulse_every or PULSE_EVERY_S)
    lag = LoopLag()
    guard = guard or MemoryGuard()
    iterations = 0
    next_pulse = 0.0
    expected = time.monotonic() + tick
    while True:
        iterations += 1
        now = time.monotonic()
        is_late = lag.observe(max(0.0, now - expected))
        if is_late and on_late is not None:
            try:
                on_late(lag.last_lag_s)
            except Exception:  # noqa: BLE001
                pass
        try:
            import heartbeat  # type: ignore

            if now >= next_pulse:
                heartbeat.pulse(name, {"lag_s": lag.last_lag_s, "rss_mb": guard.last_rss_mb})
                next_pulse = now + full_pulse
            else:
                heartbeat.notify("WATCHDOG=1")
        except Exception:  # noqa: BLE001
            pass
        if guard.check():
            guard.recycle("memory_limit")
        if max_iterations is not None and iterations >= int(max_iterations):
            return lag
        expected = now + tick
        await asyncio.sleep(tick)


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Lane çıktısı: zırh durumu (RSS, eşik, systemd, auto-recycle)."""
    return {
        "rss_mb": rss_mb(),
        "memory_limit_mb": MEMORY_LIMIT_MB,
        "over_limit": over_limit(),
        "auto_recycle": auto_recycle_enabled(),
        "under_systemd": under_systemd(),
        "watchdog_interval_s": WATCHDOG_INTERVAL_S,
        "lag_warn_s": LAG_WARN_S,
    }
