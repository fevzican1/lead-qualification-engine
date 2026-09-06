"""Nirvana — 9-lane free-tier autonomous module architecture.

Heavy lanes (discovery, enrichment, audit, strategy, retention) run on GitHub
Actions; light listeners (onboarding, delivery, watchdog/quota) run on the
Oracle VM. Everything here is deterministic, zero-cost (no paid API) and gated
by the existing quota/knowledge caps in this repo.
"""
from __future__ import annotations

import config  # noqa: F401  (env/.env load happens once, at import)

from nirvana.registry import MODULES, module, load_registry  # noqa: F401

__all__ = ["MODULES", "module", "load_registry"]
