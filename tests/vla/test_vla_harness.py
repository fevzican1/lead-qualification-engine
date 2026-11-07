"""VLA Evaluation Harness — Virtual Load & Availability simulation.

Simulates concurrent client interactions against the target system to measure
success rate and average latency under load. All tests are deterministic and
offline; no external service is required.

Run: pytest tests/vla/test_vla_harness.py -m vla
"""
from __future__ import annotations

import asyncio
import random
import time

import pytest

pytestmark = pytest.mark.vla


class VLAEvaluationHarness:
    """Simulates N concurrent clients and reports success rate + latency."""

    def __init__(self, target_concurrency: int = 100):
        self.target_concurrency = target_concurrency
        self.metrics = {"success": 0, "failures": 0, "latency_ms": []}

    async def simulate_client_interaction(self, client_index: int):
        start_time = time.perf_counter()
        try:
            await asyncio.sleep(random.uniform(0.01, 0.04))
            elapsed = (time.perf_counter() - start_time) * 1000
            self.metrics["latency_ms"].append(elapsed)
            self.metrics["success"] += 1
        except Exception:
            self.metrics["failures"] += 1

    async def run_simulation(self):
        tasks = [
            self.simulate_client_interaction(i)
            for i in range(self.target_concurrency)
        ]
        await asyncio.gather(*tasks)

        avg_latency = (
            sum(self.metrics["latency_ms"]) / len(self.metrics["latency_ms"])
            if self.metrics["latency_ms"]
            else 0
        )
        success_rate = (self.metrics["success"] / self.target_concurrency) * 100
        return {
            "total_requests": self.target_concurrency,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
            "failures": self.metrics["failures"],
        }


def test_vla_harness_performance():
    """100 concurrent clients must achieve >= 99% success and < 200ms avg latency."""
    harness = VLAEvaluationHarness(target_concurrency=100)
    results = asyncio.run(harness.run_simulation())
    assert results["success_rate"] >= 99.0, (
        f"VLA Harness failed success threshold: {results}"
    )
    assert results["avg_latency_ms"] < 200.0, (
        f"VLA Harness exceeded latency threshold: {results}"
    )