"""Lane AQ (quality_audit) — Akilli Kalite ve Denetim Motoru testleri.

Kural: runner/yaml/timer canli kurulum + /quality gozlem ucu mevcut olmali;
onarimlar her zaman enjekte edilir (gercek systemctl asla cagrilmaz).
En kritik 3 satir (form kanit, demo odeme, teslimat infaz) basta dogrulanir.
"""
from __future__ import annotations

import config
from nirvana import runner as nirvana_runner
from nirvana import quality_audit
from nirvana.registry import MODULES


def _isolate(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nirvana.registry.STATE_DIR", state_dir)
    return tmp_path


def _healthy_probes() -> dict:
    return {name: {"ok": True, "detail": ""} for name in quality_audit.CHECKS}


def test_quality_audit_has_13_checks_with_critical_first():
    assert len(quality_audit.CHECKS) == 13
    assert quality_audit.CHECKS[:3] == (
        "form_proof_roi", "demo_simulation_pay", "delivery_execution_audit")
    assert set(quality_audit.PROBES) == set(quality_audit.CHECKS)


def test_quality_audit_registry_runner_and_units():
    meta = MODULES()["quality_audit"]
    assert meta["host"] == "oracle" and meta["letter"] == "AQ"
    assert nirvana_runner.RUNNERS["quality_audit"] == "nirvana.quality_audit"
    root = config.ROOT
    service = (root / "oracle" / "nirvana-quality.service").read_text(encoding="utf-8")
    timer = (root / "oracle" / "nirvana-quality.timer").read_text(encoding="utf-8")
    assert "nirvana.runner quality_audit" in service and "TimeoutStartSec=" in service
    assert "OnCalendar=" in timer and "nirvana-quality.service" in timer
    install = (root / "oracle" / "nirvana_oracle_install.sh").read_text(encoding="utf-8")
    assert "nirvana-quality.timer" in install
    wf = (root / ".github" / "workflows" / "nirvana-oracle-deploy.yml").read_text(encoding="utf-8")
    assert "nirvana/quality_audit.py" in wf


def test_quality_audit_all_green_scores_100(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    res = quality_audit.run_batch(
        state={}, now_fn=lambda: 1000.0,
        health_fn=lambda: True, dry_run=True)
    assert res["lane"] == "quality_audit" and res["dry_run"] is True
    assert 0 <= res["score"] <= 100
    assert set(res["probes"]) == set(quality_audit.CHECKS)
    assert res["repairs"] == []  # dry-run: mudahale yok


def test_quality_audit_streak_two_before_fuel_repair(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state: dict = {}
    red = _healthy_probes()
    red["fuel_feeds"] = {"ok": False, "detail": "kuyruk kritik"}
    assert quality_audit.decide(red, state, now=1000.0) == []
    acts2 = quality_audit.decide(red, state, now=1010.0)
    assert [a["check"] for a in acts2] == ["fuel_feeds"]
    res = quality_audit.repair("fuel_feeds", state=state, now=1010.0,
                               refill_fn=lambda: {"dispatched": True})
    assert res["ok"] is True
    assert quality_audit.decide(red, state, now=1020.0) == []


def test_quality_audit_delivery_execution_is_repairable():
    state: dict = {}
    red = _healthy_probes()
    red["delivery_execution_audit"] = {"ok": False, "detail": "onay iletilmedi"}
    quality_audit.decide(red, state, now=1000.0)
    acts = quality_audit.decide(red, state, now=1010.0)
    assert [a["check"] for a in acts] == ["delivery_execution_audit"]


def test_quality_audit_report_only_never_repairs():
    state: dict = {}
    red = _healthy_probes()
    red["payoneer_webhook"] = {"ok": False, "detail": "link yer tutucu"}
    quality_audit.decide(red, state, now=1000.0)
    assert quality_audit.decide(red, state, now=1010.0) == []


def test_quality_audit_failed_repair_writes_no_cooldown():
    res = quality_audit.repair("uptime_zero_error", state={}, now=1000.0,
                               restart_fn=lambda unit: {"ok": False, "unit": unit})
    assert res["ok"] is False


def test_quality_audit_probe_failure_is_fail_open(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    probes = quality_audit.probe_all(health_fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert probes["uptime_zero_error"]["ok"] is True  # kor restart yok
    assert quality_audit.score(probes) >= 0


def test_quality_endpoint_is_read_only():
    import webchat_server as ws
    app = ws._lazy_app()
    assert "/quality" in sorted({r.path for r in app.routes})
    route = next(r for r in app.routes if getattr(r, "path", "") == "/quality")
    methods = {m for m in (getattr(route, "methods", set()) or set())}
    assert methods <= {"GET"}
