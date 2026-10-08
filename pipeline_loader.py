"""Pipeline olay dagitici yukleyici (namespace-safe).

Neden dosya-yolu yukleme: kokte `pipeline.py` DOSYASI vardir; ayni anda
`pipeline/` klasoru paket olursa `import pipeline` dosyayi secer ve
`pipeline.event_dispatcher` cozulemiyordu. Bu yukleyici dosya yolundan
dogrudan yukler; mevcut `pipeline.py` import zinciri bozulmaz.
"""
from __future__ import annotations
import importlib.util as _ilu
from pathlib import Path as _Path
from types import ModuleType as _ModuleType
_ROOT = _Path(__file__).resolve().parent
_FILE = _ROOT / "pipeline" / "event_dispatcher.py"
_cache: _ModuleType | None = None
def load() -> _ModuleType:
    global _cache
    if _cache is not None:
        return _cache
    target = _FILE if _FILE.is_file() else (_Path.cwd() / "pipeline" / "event_dispatcher.py")
    spec = _ilu.spec_from_file_location("pipeline_event_dispatcher", str(target))
    if spec is None or spec.loader is None:
        raise ImportError("pipeline/event_dispatcher.py yuklenemedi")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _cache = mod
    return mod
def emit_sync(event: str, payload: dict | None = None) -> dict:
    return load().bus.emit_sync(event, payload)
async def emit(event: str, payload: dict | None = None) -> dict:
    return await load().bus.emit(event, payload)
def on(event: str, fn) -> object:
    return load().bus.on(event, fn)
def bus():
    return load().bus
