"""B2B olgunluk kilitleri: gunluk 100 gonderim + 100 ortak hedefi + donma korumasi.

- B2B_DAILY_TARGET=100 (gunluk gonderim hedefi; kota tavanlari asla asilmaz)
- PARTNER_TARGET=100 (toplam ortak hedefi; sayac nirvana/state'de)
- Donma korumasi: WS/LLM/pipeline duvar-saatleri tek dosyada, fail-open.
"""
from __future__ import annotations
import os
from typing import Any
def _int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name, "") or "").strip() or default)
    except (TypeError, ValueError):
        return default
def _float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name, "") or "").strip() or default)
    except (TypeError, ValueError):
        return default
B2B_DAILY_TARGET = max(1, _int("B2B_DAILY_TARGET", 100))
PARTNER_TARGET = max(1, _int("PARTNER_TARGET", 100))
WS_REPLY_TIMEOUT_S = max(20.0, _float("WS_REPLY_TIMEOUT_S", 150.0))
LLM_CALL_TIMEOUT_S = max(10.0, _float("LLM_CALL_TIMEOUT_S", 95.0))
SUBMIT_HARD_TIMEOUT_S = max(10.0, _float("SUBMIT_HARD_TIMEOUT_SECONDS", 30.0))
WS_QUEUE_MAX = max(1, _int("WS_QUEUE_MAX", 200))
def snapshot() -> dict[str, Any]:
    return {"b2b_daily_target": B2B_DAILY_TARGET, "partner_target": PARTNER_TARGET, "ws_reply_timeout_s": WS_REPLY_TIMEOUT_S, "llm_call_timeout_s": LLM_CALL_TIMEOUT_S, "submit_hard_timeout_s": SUBMIT_HARD_TIMEOUT_S, "ws_queue_max": WS_QUEUE_MAX}
