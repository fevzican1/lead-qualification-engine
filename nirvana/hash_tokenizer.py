"""Lane MOD-09 — hash_tokenizer [GitHub Actions + Oracle, light].

Kriptografik SHA-256 token üretimi: form sohbet eşleşmesi için chat↔domain
bağlantı token'ı üretir. forget_guard veri izolasyon zincirini besler.
Saf Python hashlib — maliyet sıfır, Oracle kotasına dokunmaz.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import time
from typing import Any

import config
from nirvana.registry import state_path
import json


def bind_token(chat_id: int, domain: str) -> str:
    seed = str(getattr(config, "INGEST_API_TOKEN", "") or "devsolve").strip()
    msg = f"{chat_id}:{domain.strip().lower()}".encode("utf-8")
    return hmac_mod.new(seed.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def verify_token(chat_id: int, domain: str, token: str) -> bool:
    return hmac_mod.compare_digest(bind_token(chat_id, domain), (token or "").strip())


def run_batch(**kwargs: Any) -> dict[str, Any]:
    out = {"ok": True, "algo": "HMAC-SHA256-32",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = state_path("hash_tokenizer.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {**out, "out": str(path)}
