"""queue_fuel_guard — acil yakıt ikmali (kritik eşik <100 -> +500) testleri."""

from __future__ import annotations

import json

import pytest

from nirvana import queue_fuel_guard as qfg
from nirvana.registry import state_path


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Kuyruk/state dosyalari GERCEK depo state'ine yazilmaz (izolasyon)."""
    monkeypatch.setattr("nirvana.registry.STATE_DIR", tmp_path / "state")
    return tmp_path


def _dump(name: str, rows: list[dict]) -> None:
    state_path(name).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def _clear_queues() -> None:
    for name in ("verified_queue.json", "tactic_matrix_pending.json",
                 "tactic_matrix.json", "enterprise_targets.json"):
        state_path(name).write_text("[]", encoding="utf-8")


def test_kritik_esik_ve_sabitler():
    assert qfg.CRITICAL_WATERMARK == 100
    assert qfg.LOW_WATERMARK == 500
    assert qfg.EMERGENCY_ADD >= 500, "acil ikmal en az 500 hedef eklemeli"


def test_kuyruk_bos_kritik_ve_500_ister():
    _clear_queues()
    check = qfg.check_refill_needed()
    assert check["severity"] == "critical" and check["needed"] is True
    assert check["critical"] is True
    assert check["requested_add"] == qfg.EMERGENCY_ADD == 500
    assert check["flag"] == "EMERGENCY_REFILL_REQUIRED"
    flag = json.loads(state_path("refill_required.json").read_text(encoding="utf-8"))
    assert flag["critical"] is True and flag["requested_add"] == 500
    assert flag["critical_watermark"] == 100


def test_300_hedef_dusuk_seviye_eksik_kadar_ister():
    _clear_queues()
    _dump("verified_queue.json", [{"url": f"https://a{i}.example"} for i in range(300)])
    check = qfg.check_refill_needed()
    assert check["severity"] == "low" and check["critical"] is False
    assert check["requested_add"] == 200  # 500 - 300


def test_600_hedef_saglikli_ve_bayrak_silinir():
    _clear_queues()
    _dump("verified_queue.json", [{"url": f"https://b{i}.example"} for i in range(600)])
    state_path("refill_required.json").write_text("{}", encoding="utf-8")
    check = qfg.check_refill_needed()
    assert check["severity"] == "healthy" and check["needed"] is False
    assert check["requested_add"] == 0 and check["flag"] is None
    assert not state_path("refill_required.json").exists()


def test_run_batch_kritikte_emergency_blok_dondurur(monkeypatch):
    _clear_queues()
    monkeypatch.setattr(qfg, "request_refill_via_github",
                        lambda **kw: {"dispatched": False, "reason": "queue_healthy"})
    out = qfg.run_batch()
    assert out["severity"] == "critical"
    assert out["emergency"]["triggered"] is True
    assert out["emergency"]["add_target"] == 500
    assert out["refill"]["requested_add"] == 500


def test_run_batch_saglikli_kuyrukta_emergency_yok(monkeypatch):
    _clear_queues()
    _dump("verified_queue.json", [{"url": f"https://c{i}.example"} for i in range(700)])
    monkeypatch.setattr(qfg, "request_refill_via_github",
                        lambda **kw: {"dispatched": False, "reason": "queue_healthy"})
    out = qfg.run_batch()
    assert out["severity"] == "healthy" and "emergency" not in out


def test_dispatch_istegi_500_hedef_ve_severity_tasir(monkeypatch):
    _clear_queues()
    seen: dict = {}

    def fake_dispatch(workflow, inputs, **kwargs):
        seen["workflow"] = workflow
        seen["inputs"] = inputs
        return {"ok": True, "status": 204}

    monkeypatch.setattr("nirvana.github_orchestrator.dispatch_workflow", fake_dispatch)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_OWNER", "fevzican1")
    monkeypatch.setenv("GITHUB_REPO", "lead-qualification-engine")
    out = qfg.request_refill_via_github(reason="critical_watermark")
    assert seen["workflow"] == "enterprise-feed.yml"
    assert seen["inputs"]["refill_batch"] == "500"
    assert seen["inputs"]["severity"] == "critical"
    assert out["dispatched"] is True and out["trigger_reason"] == "critical_watermark"


def test_token_yoksa_dispatch_etmez(monkeypatch):
    _clear_queues()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    out = qfg.request_refill_via_github()
    assert out["dispatched"] is False and out["reason"] == "no_token_or_repo_env"


def test_kuyruk_saglikliyken_dispatch_edilmez(monkeypatch):
    _clear_queues()
    _dump("verified_queue.json", [{"url": f"https://d{i}.example"} for i in range(900)])
    out = qfg.request_refill_via_github()
    assert out["dispatched"] is False and out["reason"] == "queue_healthy"