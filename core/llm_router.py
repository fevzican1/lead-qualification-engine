"""Low-latency inference pipeline (target: core/llm_router.py).

LiteLLM-proxy uyumlu ince router: yerelde Ollama (llama3.2:3b hizli model,
qwen2.5:7b/deepseek ana hat) + semantic_cache + concurrency guard. <1sn webchat
hedefi icin once cache, sonra hizli model, en son ana model denenir.
"""
from __future__ import annotations
import asyncio, logging, os, time
from typing import Any
logger = logging.getLogger(__name__)
FAST_TIMEOUT_S = float(os.getenv("LLM_FAST_TIMEOUT_S", "0.9") or 0.9)
TOTAL_TIMEOUT_S = float(os.getenv("LLM_TOTAL_TIMEOUT_S", "6.0") or 6.0)
MAX_CONCURRENCY = max(1, min(8, int(os.getenv("LLM_MAX_CONCURRENCY", "4") or 4)))
_sem = asyncio.Semaphore(MAX_CONCURRENCY) if False else None
def _sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        try: _sem = asyncio.Semaphore(MAX_CONCURRENCY)
        except RuntimeError: _sem = asyncio.Semaphore(MAX_CONCURRENCY)
    return _sem
def fast_model() -> str:
    try:
        import knowledge
        return knowledge.model_fast() or "llama3.2:3b"
    except Exception: return os.getenv("OLLAMA_FAST_MODEL", "llama3.2:3b") or "llama3.2:3b"
def main_model() -> str:
    try:
        import config; return config.OLLAMA_MODEL
    except Exception: return os.getenv("OLLAMA_MODEL", "deepseek-r1:14b")
def litellm_proxy_config() -> dict:
    """LiteLLM proxy katmani yapilandirmasi (webchat <1sn, zero-latency cache)."""
    return {"proxy": os.getenv("LITELLM_PROXY_URL", "http://127.0.0.1:4000"),
            "enabled": os.getenv("LITELLM_PROXY_ENABLED", "0") == "1",
            "primary": fast_model(), "fallback": main_model(),
            "cache_ttl_s": 86400, "fast_timeout_s": FAST_TIMEOUT_S,
            "total_timeout_s": TOTAL_TIMEOUT_S, "max_concurrency": MAX_CONCURRENCY}
async def aroute(messages: list, *, temperature=0.6, max_tokens=240, lang="en") -> dict:
    """Cache -> fast Ollama -> main Ollama -> deterministic fallback."""
    t0 = time.monotonic()
    try:
        from nirvana import semantic_cache as sc
        hit = sc.get(messages)
        if hit: return {"text": hit, "model": "cache", "latency_s": round(time.monotonic()-t0, 3)}
    except Exception: pass
    try: loop = asyncio.get_running_loop()
    except RuntimeError: loop = None  # type: ignore
    async def _call(model: str, timeout: float) -> str:
        import ollama_client
        kw = dict(messages=messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if loop is not None:
            async with _sem():
                return await loop.run_in_executor(None, lambda: ollama_client.chat(messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout))
        return ollama_client.chat(messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
    last = ""
    for model, to in ((fast_model(), FAST_TIMEOUT_S), (main_model(), TOTAL_TIMEOUT_S)):
        try:
            if loop is not None:
                txt = await asyncio.wait_for(_call(model, to), timeout=to + 2.0)
            else: txt = __import__("ollama_client").chat(messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=to)
            try:
                from nirvana import semantic_cache as sc; sc.put(messages, txt)
            except Exception: pass
            return {"text": txt, "model": model, "latency_s": round(time.monotonic()-t0, 3)}
        except Exception as e: last = str(e)[:120]; logger.debug("llm %s fail: %s", model, last)
    try:
        import webchat_core as wc
        txt = wc.fallback_reply(lang=lang if lang in ("tr","en") else "en", tone="consult")
    except Exception: txt = "Teknik ekibimiz kisa surede donus yapacak."
    return {"text": txt, "model": "fallback", "latency_s": round(time.monotonic()-t0, 3), "error": last}
def route_sync(messages: list, **kw: Any) -> dict:
    try: asyncio.get_running_loop()
    except RuntimeError: return asyncio.run(aroute(messages, **kw))
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(1) as ex: return ex.submit(lambda: asyncio.run(aroute(messages, **kw))).result()
