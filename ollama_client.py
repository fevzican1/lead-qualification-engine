"""
Local Ollama client (default: deepseek-r1:14b). No cloud API key, no per-token bill.

Talks to http://127.0.0.1:11434 — start the Ollama app or `ollama serve`.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any, Iterable, Optional

import httpx

import config
import knowledge

class ModelNotFound(RuntimeError):
    """Ollama'da istenen model diskte yok (404). Zincir aninda digerine gecer."""


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
    # Model diskte YOK. Indirmeye calismak yerine zincirin diger
    # adimina gec (fail-fast): 404 x3 x 180sn bekleme = donma.
    raise ModelNotFound(f"ollama model diskte yok: {model}")


_THINK_RE = re.compile(r"<think>.*?</think>", re.I | re.S)


def _normalize_keep_alive(value: str) -> str:
    """Ollama sürüm uyumu: `keep_alive` daima süre birimi ister (0.33+).

    Eski/sert sürümler saf sayısal `-1`/`600`'ü `time: missing unit in duration`
    hatasıyla reddeder. Saf sayı tespit edilirse `s` (saniye) birimi eklenir;
    `-1` -> `-1s` (kalıcı yükleme) — Ollama bunu kabul eder.
    """
    text = (value or "").strip()
    if not text:
        return text
    # Zaten birimli (son karakter harf) veya süresiz (0) ise olduğu gibi bırak.
    if text[-1].isalpha():
        return text
    try:
        float(text.replace(",", "."))
    except (TypeError, ValueError):
        return text
    return text + "s"


def keep_alive() -> str:
    """Model bellekte kalma süresi: OLLAMA_KEEP_ALIVE > oracle.json > 24h (rapor)."""
    env = (os.getenv("OLLAMA_KEEP_ALIVE") or "").strip()
    if env:
        return _normalize_keep_alive(env)
    try:
        locked = str(knowledge.oracle_lock().get("ollama_keep_alive") or "").strip()
        if locked:
            return _normalize_keep_alive(locked)
    except Exception:  # noqa: BLE001
        pass
    return "24h"


def model_present(model: str) -> bool:
    """Model diskte hazır mı (canlı VM'de indirme sürprizi olmasın)."""
    target = (model or "").strip()
    if not target:
        return False
    return any(target == name or name.startswith(target) or target in name
               for name in list_models())


def _present_small_model() -> str:
    """Diskte hazir olan KUCUK modeli sec (1b/3b/4b). DONMA YOK kurali:

    Ampere A1 (4 vCPU) ve dar bellekli dev makinelerinde 7b/14b model ya
    cok yavas ya da 6.4GB buffer ile OOM olur -> webchat DONAR. Bu yuzden
    once diskteki herhangi bir kucuk modeli tara; varsa onu kullan. Yoksa
    ana modele dus.
    """
    try:
        names = list_models()
    except Exception:  # noqa: BLE001
        return ""
    for name in names:
        if re.search(r":(1b|3b|4b)\b", name, re.I):
            return name
    return ""


def webchat_model() -> str:
    """WebChat icin hafif model; diskte yoksa ana modele duster (indirme zorlamaz).

    Once kilitli hizli modeli (fast) dener; o diskte YOKSA diskteki herhangi bir
    kucuk modeli (1b/3b/4b) bulur; hicbiri yoksa ana modele duser.
    """
    try:
        fast = knowledge.enforce_model(knowledge.model_fast())
    except Exception:  # noqa: BLE001
        fast = ""
    if fast and fast != config.OLLAMA_MODEL and model_present(fast):
        return fast
    small = _present_small_model()
    if small and small != config.OLLAMA_MODEL:
        return small
    return config.OLLAMA_MODEL


def chat_webchat(
    messages: Iterable[dict[str, str]],
    *,
    temperature: float = 0.6,
    max_tokens: int = 240,
    timeout: float = 90.0,
) -> tuple[str, str]:
    """(yanıt, kullanılan model). Ampere A1'de 3B Q4 modeli kısa yanıtı hızlandırır."""
    model = webchat_model()
    try:
        return chat(messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, timeout=timeout), model
    except Exception as exc:  # noqa: BLE001 — hızlı model düşerse ana model devreye girer
        if model == config.OLLAMA_MODEL:
            raise
        logger.warning("Hızlı model (%s) başarısız: %s — ana modele dönülüyor", model, exc)
        return chat(messages, model=config.OLLAMA_MODEL, temperature=temperature,
                    max_tokens=max_tokens, timeout=timeout), config.OLLAMA_MODEL


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
        "keep_alive": keep_alive(),
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            # Dar baglam + 4 CPU parcasi -> on isleme milisaniye (Ampere A1)
            "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "1024") or 1024),
            "num_thread": int(os.getenv("OLLAMA_NUM_THREAD", "4") or 4),
        },
    }
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = httpx.post(f"{_base()}/api/chat", json=payload, timeout=timeout)
            if response.status_code == 404:
                # Model diskte YOK (404). Tekrar denemek anlamsiz ve 3x180sn
                # bekletip donma yaratir -> HEMEN fail-fast (yoksa zincir duger).
                raise ModelNotFound(
                    f"ollama model yok (404): {payload.get('model')}")
            response.raise_for_status()
            data = response.json()
            content = ((data.get("message") or {}).get("content")) or data.get("response") or ""
            return _visible_text(str(content))
        except ModelNotFound:
            raise
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
            "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "1024") or 1024),
            "num_thread": int(os.getenv("OLLAMA_NUM_THREAD", "4") or 4),
        },
    }
    if system:
        payload["system"] = system
    response = httpx.post(f"{_base()}/api/generate", json=payload, timeout=timeout)
    response.raise_for_status()
    return _visible_text(str(response.json().get("response") or ""))
