"""Lane AM — idle_guard [Oracle VM].

OCI Always-Free "idle reclamation": Always-Free compute kaynaklari uzun sure
%20 esiginin altinda CPU/bellek kullanimiyla calisirsa Oracle kaynagi geri
alabilir. Bu lane hafif sentetik yuk ureterek alt esigin ustunde kalir;
watchdog_quota_agent (%85 tavan, sogutma modu) ile birlikte calisir:
ne bosta kalir, ne limite dayanir.

Tasarim kurallari:
- Nice=19 + tek/birkac kisa burst: gercek is (satis botu, teslimat) asla ac kalmaz.
- Bellek dokunusu sabit ve kucuk (varsayilan 256 MB): swap baskisi yaratmaz.
- dry_run=True hicbir yuk uretmez (test/CI).
- Telegram bildirimi YOK: 10 dakikalik gurultu olurdu (status dosyasi yeter).
"""
from __future__ import annotations

import json
import multiprocessing
import os
import time
from typing import Any

from nirvana.registry import state_path

STATE_NAME = "idle_guard.json"
FLOOR_PCT = 0.20       # alt esik: bunun altinda "idle" sayilma riski
CEIL_PCT = 0.60        # ust esik: watchdog tavanina (0.85) yaklasmadan dur
BURST_SECONDS = 20     # tek burst suresi (systemd TimeoutStartSec=90 icinde)
BURST_WORKERS = 1      # 2 OCPU'da load ~0.5 hedefi: tek worker + kisa burst
MEM_TOUCH_MB = 256     # sabit bellek dokunusu


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cpu_snapshot() -> dict[str, Any]:
    """watchdog ile ayni olcum: /proc yoksa {'available': False} (Windows/CI)."""
    from nirvana import watchdog_quota_agent as watchdog
    return watchdog.system_snapshot()


def load_pct(snapshot: dict[str, Any] | None = None) -> float | None:
    snap = snapshot if snapshot is not None else cpu_snapshot()
    if not snap.get("available"):
        return None
    value = snap.get("load_pct")
    return float(value) if value is not None else None


def decide(pct: float | None, *, floor: float = FLOOR_PCT,
           ceil: float = CEIL_PCT) -> str:
    """'unknown' | 'idle' | 'ok' | 'high' — yuk uretilsin mi karari."""
    if pct is None:
        return "unknown"
    if pct < floor:
        return "idle"
    if pct >= ceil:
        return "high"
    return "ok"


def _burn(seconds: float) -> None:
    """Alt surec: kisa CPU burst (kendi basina import yok — hafif kalir)."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    acc = 0.0
    while time.monotonic() < deadline:
        acc += (acc + 1.0) ** 0.5 % 7.0
    _ = acc


def mem_touch(mb: int = MEM_TOUCH_MB) -> int:
    """Sabit miktar bellek ayir ve sayfalara dokun (RSS olcer)."""
    size = max(0, int(mb)) * 1024 * 1024
    if not size:
        return 0
    try:
        buf = bytearray(size)
        for offset in range(0, size, 4096):
            buf[offset] = 1
        touched = len(buf) // (1024 * 1024)
        del buf
        return touched
    except MemoryError:
        return 0


def synthetic_burst(*, seconds: float = BURST_SECONDS, workers: int = BURST_WORKERS,
                    mem_mb: int = MEM_TOUCH_MB) -> dict[str, Any]:
    """Hafif sentetik yuk: N kisa CPU burst + sabit bellek dokunusu."""
    procs: list[multiprocessing.Process] = []
    try:
        for _ in range(max(1, int(workers))):
            proc = multiprocessing.Process(target=_burn, args=(seconds,), daemon=True)
            proc.start()
            procs.append(proc)
        touched = mem_touch(mem_mb) if mem_mb else 0
        for proc in procs:
            proc.join(timeout=seconds + 10)
    finally:
        for proc in procs:
            if proc.is_alive():  # pragma: no cover - guvenlik subi
                proc.terminate()
    return {"seconds": seconds, "workers": len(procs) or 1, "mem_touched_mb": touched}


def run_batch(*, notify: bool = False, dry_run: bool = False,
              seconds: float = BURST_SECONDS) -> dict[str, Any]:
    """CLI/timer giris noktasi. notify parametresi bilinçli olarak yok sayilir."""
    before = cpu_snapshot()
    pct_before = load_pct(before)
    verdict = decide(pct_before)
    work: dict[str, Any] = {}
    if verdict == "idle" and not dry_run:
        work = synthetic_burst(seconds=seconds)
        time.sleep(1.0)  # load1 EMA'nin burst'i gormesi icin kisa nefes
    after = cpu_snapshot()
    status = {
        "at": _now_iso(),
        "verdict": verdict,
        "load_pct_before": pct_before,
        "load_pct_after": load_pct(after),
        "floor_pct": FLOOR_PCT,
        "ceil_pct": CEIL_PCT,
        "dry_run": bool(dry_run),
        "synthetic": work,
        "system": after,
        "note": ("OCI Always-Free idle geri alim korumasi: hafif sentetik yuk, "
                 "gercek ise dokunmaz (Nice=19)."),
        "out": str(state_path(STATE_NAME)),
    }
    path = state_path(STATE_NAME)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return status