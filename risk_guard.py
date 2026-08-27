"""
Transient-fault helper. Does not raise form/HTTP caps or the Always Free shape.

Network blips retry once. Permanent skips (CAPTCHA) stay skipped.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_TRANSIENT = (
    "name resolution",
    "temporary failure",
    "temporarily unavailable",
    "connection reset",
    "connection aborted",
    "connect timeout",
    "timed out",
    "timeout",
    "network is unreachable",
    "connection refused",
    "server disconnected",
    "eof occurred",
    "ssl",
    "broken pipe",
)


def is_transient(exc: BaseException | str) -> bool:
    blob = str(exc or "").lower()
    return any(tok in blob for tok in _TRANSIENT)


def call_once_retry(fn: Callable[[], T], *, delay: float = 1.6) -> T:
    """Run fn; if it raises a transient network error, wait once and retry."""
    try:
        return fn()
    except Exception as exc:
        if not is_transient(exc):
            raise
        logger.info("Transient fault, one retry: %s", exc)
        time.sleep(delay)
        return fn()
