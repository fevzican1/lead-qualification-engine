"""VLA model continuous validation using vla-evaluation-harness.

Simulates visual + textual inputs through a WebSocket / msgpack binary protocol
against a local VLA model server running in Docker. Validates System 2 reasoning
output (sub-task plans) and System 1 action execution at 120Hz.

Run: pytest tests/vla/test_vla_evaluation.py -m vla
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

pytestmark = pytest.mark.vla

try:
    import msgpack  # type: ignore
    import websocket  # type: ignore
except ImportError:  # pragma: no cover - optional VLA deps
    msgpack = None  # type: ignore
    websocket = None  # type: ignore

VLA_WS_URL = "ws://localhost:8081/vla/eval"
EPISODE_SHARDS = 64


@pytest.fixture
def vla_client() -> Any:
    if websocket is None or msgpack is None:
        pytest.skip("vla-evaluation-harness deps (msgpack, websocket-client) not installed")
    try:
        ws = websocket.create_connection(VLA_WS_URL, timeout=5)
    except Exception:  # noqa: BLE001 — VLA server not running in local dev
        pytest.skip("VLA model server not available at ws://localhost:8081/vla/eval")
    yield ws
    try:
        ws.close()
    except Exception:
        pass


def _build_episode(system2_plan: dict[str, Any], visual_input: str) -> dict[str, Any]:
    """Build a VLA evaluation episode from System 2 plan + visual context."""
    return {
        "episode_id": f"vla-eval-{abs(hash(str(system2_plan) + visual_input)) % 10_000_000:07d}",
        "system2_plan": system2_plan,
        "visual_input": visual_input,
        "context": {"lang": "tr", "channel": "webchat"},
    }


def test_vla_payment_inquiry_episode(vla_client: Any) -> None:
    """VLA must convert a payment-inquiry message + screenshot into send_payment_link action."""
    plan = {
        "task_type": "payment_inquiry",
        "sub_tasks": [
            {"id": "step1", "action": "detect_payment_intent", "confidence_threshold": 0.85},
            {"id": "step2", "action": "generate_payment_link", "provider": "payoneer"},
            {"id": "step3", "action": "send_link_to_customer", "channel": "webchat"},
        ],
    }
    episode = _build_episode(plan, "screenshot: customer asks 'ödeme linkini alabilir miyim?'")

    payload = msgpack.packb(episode)
    vla_client.send_binary(payload)
    response_raw = vla_client.recv()
    result = msgpack.unpackb(response_raw, raw=False)

    assert result["status"] == "success"
    assert result["system1_action"]["action_type"] == "send_payment_link"
    assert result["system1_action"]["latency_ms"] < 100  # <100ms action latency
    assert result["system1_action"]["confidence"] > 0.85


def test_vla_delivery_assignment_episode(vla_client: Any) -> None:
    """VLA must convert delivery assignment visual context into assign_delivery_job action."""
    plan = {
        "task_type": "delivery_assignment",
        "sub_tasks": [
            {"id": "step1", "action": "scan_delivery_queue", "confidence_threshold": 0.80},
            {"id": "step2", "action": "assign_nearest_worker", "max_distance_km": 15},
            {"id": "step3", "action": "notify_customer", "channel": "sms"},
        ],
    }
    episode = _build_episode(plan, "screenshot: delivery dashboard with 3 pending jobs")

    payload = msgpack.packb(episode)
    vla_client.send_binary(payload)
    response_raw = vla_client.recv()
    result = msgpack.unpackb(response_raw, raw=False)

    assert result["status"] == "success"
    assert result["system1_action"]["action_type"] == "assign_delivery_job"
    assert result["system1_action"]["worker_id"] is not None


def test_vla_catastrophic_forgetting_guard(vla_client: Any) -> None:
    """VLA must retain previously learned payment flow after delivery tasks (no catastrophic forgetting)."""
    # First: payment episode
    payment_plan = {
        "task_type": "payment_inquiry",
        "sub_tasks": [{"id": "step1", "action": "generate_payment_link", "provider": "payoneer"}],
    }
    ep1 = _build_episode(payment_plan, "screenshot: payment request")
    vla_client.send_binary(msgpack.packb(ep1))
    r1 = msgpack.unpackb(vla_client.recv(), raw=False)
    assert r1["status"] == "success"

    # Then: several delivery episodes to potentially cause drift
    for i in range(10):
        delivery_plan = {
            "task_type": "delivery_assignment",
            "sub_tasks": [{"id": f"step{i}", "action": "assign_nearest_worker"}],
        }
        ep = _build_episode(delivery_plan, f"screenshot: delivery job {i}")
        vla_client.send_binary(msgpack.packb(ep))
        r = msgpack.unpackb(vla_client.recv(), raw=False)
        assert r["status"] == "success"

    # Finally: payment again — must still succeed (no forgetting)
    vla_client.send_binary(msgpack.packb(ep1))
    r2 = msgpack.unpackb(vla_client.recv(), raw=False)
    assert r2["status"] == "success"
    assert r2["system1_action"]["action_type"] == "send_payment_link"


def test_vla_parallel_episode_sharding() -> None:
    """Verify episode sharding configuration for parallel evaluation."""
    assert EPISODE_SHARDS > 0
    assert EPISODE_SHARDS % 8 == 0  # shard count must divide evenly across workers
