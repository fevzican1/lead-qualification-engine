"""Root pytest configuration for Nirvana assurance layers.

Registers custom markers: contract, trace, vla, e2e.
"""
from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers so `pytest -m contract` etc. work without warnings."""
    for marker in [
        "contract: Consumer-Driven Contract Test (Pact) — service interface verification",
        "trace: Trace-Based Test (OpenTelemetry + Tracetest) — live distributed trace verification",
        "vla: VLA AI model validation — Vision-Language-Action continuous verification",
        "e2e: End-to-End integration test — full staging environment",
    ]:
        config.addinivalue_line("markers", marker)


__all__ = []
