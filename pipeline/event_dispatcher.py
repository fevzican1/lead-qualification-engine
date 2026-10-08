"""Otonom B2B olay dagitici: hata-izole async event bus.

Her handler kendi try/except zirhinda kosar; tek handler patlasa bile
digerleri calisir. Oracle Ampere limitsiz mod: semafor tavani
EVENT_DISPATCH_MAX_CONCURRENCY (0/negatif = limitsiz) ile kontrol edilir.
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Any, Awaitable, Callable
logger = logging.getLogger(__name__)
def _max_concurrency() -> int:
    try:
        val = int((os.getenv("EVENT_DISPATCH_MAX_CONCURRENCY", "0") or "0").strip())
    except (TypeError, ValueError):
        return 0
    return max(0, val)
Handler = Callable[[dict[str, Any]], Awaitable[Any] | Any]
class EventDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
    def on(self, event: str, fn: Handler) -> Handler:
        key = str(event or "").strip() or "*"
        self._handlers.setdefault(key, []).append(fn)
        return fn
    def handlers_for(self, event: str) -> list:
        out: list = []
        out.extend(self._handlers.get("*", []))
        if event != "*":
            out.extend(self._handlers.get(str(event), []))
        return list(out)
    async def _run_one(self, fn, payload: dict) -> dict:
        try:
            res = fn(payload)
            if asyncio.iscoroutine(res):
                res = await res
            return {"ok": True, "result": res}
        except Exception as exc:
            logger.warning("dispatcher handler izole hata: %s", str(exc)[:160])
            return {"ok": False, "error": str(exc)[:200]}
    async def emit(self, event: str, payload: dict | None = None) -> dict:
        data = dict(payload or {})
        data.setdefault("event", event)
        handlers = self.handlers_for(event)
        if not handlers:
            return {"event": event, "handled": 0, "results": []}
        cap = _max_concurrency()
        sem = asyncio.Semaphore(cap) if cap > 0 else None
        async def _guarded(fn):
            if sem is None:
                return await self._run_one(fn, data)
            async with sem:
                return await self._run_one(fn, data)
        results = await asyncio.gather(*[_guarded(fn) for fn in handlers])
        ok = sum(1 for r in results if r.get("ok"))
        return {"event": event, "handled": len(handlers), "ok": ok, "failed": len(handlers) - ok, "results": list(results)}
    def emit_sync(self, event: str, payload: dict | None = None) -> dict:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            return asyncio.run(self.emit(event, payload))
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(1) as ex:
            return ex.submit(lambda: asyncio.run(self.emit(event, payload))).result()
bus = EventDispatcher()
def on(event: str, fn: Handler) -> Handler:
    return bus.on(event, fn)
async def emit(event: str, payload: dict | None = None) -> dict:
    return await bus.emit(event, payload)

