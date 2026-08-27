"""
Local Ollama client (default: deepseek-r1:14b). No cloud API key, no per-token bill.

Talks to http://127.0.0.1:11434 — start the Ollama app or `ollama serve`.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from typing import Any, Iterable, Optional

import httpx

import config
import knowledge

logger = logging.getLogger(__name__)


def _base() -> str:
    return config.OLLAMA_HOST.rstrip("/")


def ping(timeout: float = 2.0) -> bool:
    try:
        response = httpx.get(f"{_base()}/api/tags", timeout=timeout)
        return response.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def list_models() -> list[str]:
    try:
        response = httpx.get(f"{_base()}/api/tags", timeout=10.0)
        response.raise_for_status()
        models = response.json().get("models") or []
        names: list[str] = []
        for item in models:
            name = str(item.get("name") or item.get("model") or "")
            if name:
                names.append(name)
        return names
    except Exception:  # noqa: BLE001
        return []


def _start_daemon() -> None:
    binary = shutil.which("ollama")
    if not binary:
        raise RuntimeError(
            "Ollama is not installed. Install from https://ollama.com/download "
            "or `winget install Ollama.Ollama`, then run this again."
        )
    logger.info("Starting Ollama daemon (%s serve)", binary)
    subprocess.Popen(
        [binary, "serve"],
        cwd=str(config.ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_until_up(*, seconds: int = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if ping():
            return
        time.sleep(1.5)
    raise RuntimeError(f"Ollama did not become ready at {config.OLLAMA_HOST}")


def ensure_model(model: Optional[str] = None) -> None:
    """Make sure the daemon is up and `model` is pulled locally."""
    model = knowledge.enforce_model(model or config.OLLAMA_MODEL)
    if not ping():
        _start_daemon()
        wait_until_up()
    names = list_models()
    if any(model == name or name.startswith(model) or model in name for name in names):
        return
    locked = str(knowledge.oracle_lock().get("model") or "deepseek-r1:14b")
    if model != locked and not model.startswith(locked):
        logger.warning("Refusing to pull %s — Always Free lock is %s", model, locked)
        return
    logger.info("Pulling Ollama model %s (first time only)", model)
    binary = shutil.which("ollama")
    if binary:
        subprocess.run([binary, "pull", model], check=True)
        return
    response = httpx.post(
        f"{_base()}/api/pull",
        json={"name": model, "stream": False},
        timeout=600.0,
    )
    response.raise_for_status()


_THINK_RE = re.compile(r"<think>.*?</think>", re.I | re.S)


def _visible_text(raw: str) -> str:
    text = _THINK_RE.sub("", raw or "")
    text = re.sub(r"</?think>", "", text, flags=re.I)
    return text.strip()


def chat(
    messages: Iterable[dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: float = 0.6,
    max_tokens: int = 280,
    timeout: float = 180.0,
) -> str:
    ensure_model(model)
    payload: dict[str, Any] = {
        "model": model or config.OLLAMA_MODEL,
        "messages": list(messages),
        "stream": False,
        "think": False,
        "keep_alive": "45m",
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = httpx.post(f"{_base()}/api/chat", json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            content = ((data.get("message") or {}).get("content")) or data.get("response") or ""
            return _visible_text(str(content))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("Ollama chat attempt %s failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def generate(
    prompt: str,
    *,
    system: str = "",
    model: Optional[str] = None,
    temperature: float = 0.9,
    max_tokens: int = 120,
    timeout: float = 240.0,
) -> str:
    ensure_model(model)
    payload: dict[str, Any] = {
        "model": model or config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if system:
        payload["system"] = system
    response = httpx.post(f"{_base()}/api/generate", json=payload, timeout=timeout)
    response.raise_for_status()
    return _visible_text(str(response.json().get("response") or ""))
