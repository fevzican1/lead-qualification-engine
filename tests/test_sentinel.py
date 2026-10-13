# PART1
"""Lane AP (sentinel) — Master Sentinel testleri: puan, izolasyon, cooldown.

Kural: runner/yaml/timer canli kurulum + /sentinel gozlem ucu mevcut olmali;
onarimlar her zaman enjekte edilir (gercek systemctl asla cagrilmaz).
"""
from __future__ import annotations

import config
from nirvana import runner as nirvana_runner
from nirvana import sentinel
from nirvana.registry import MODULES


def _isolate(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nirvana.registry.STATE_DIR", state_dir)
    return tmp_path


def _healthy_injections(now: float = 1000.0) -> dict:
    return {
        "state": {},
        "now_fn": lambda: now,
        "health_fn": lambda: True,
        "age_fn": lambda: 10.0,
        "dbs_fn": lambda: [],
        "locked_fn": lambda _p: False,
        "stats_fn": lambda: {"done": 3, "dead": 0},
        "status_fn": lambda: {},
        "notify_probe_fn": lambda: {"can_deliver": True},
        "restart_fn": lambda unit: {"ok": True, "unit": unit},
        "wal_fn": lambda path: {"ok": True, "path": str(path)},
    }


def test_sentinel_registry_runner_and_units():
    meta = MODULES()["sentinel"]
    assert meta["host"] == "oracle" and meta["letter"] == "AP"
    assert nirvana_runner.RUNNERS["sentinel"] == "nirvana.sentinel"
    root = config.ROOT
    service = (root / "oracle" / "nirvana-sentinel.service").read_text(encoding="utf-8")
    timer = (root / "oracle" / "nirvana-sentinel.timer").read_text(encoding="utf-8")
    assert "nirvana.runner sentinel" in service and "TimeoutStartSec=" in service
    assert "OnCalendar=" in timer and "nirvana-sentinel.service" in timer


def test_sentinel_all_green_scores_100_and_touches_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    res = sentinel.run_batch(**_healthy_injections())
    assert res["lane"] == "sentinel" and res["ok"] is True
    assert res["score"] == 100 and res["red"] == [] and res["repairs"] == []


def test_sentinel_streak_two_before_pipeline_repair(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    inj = _healthy_injections()
    inj["age_fn"] = lambda: 9999.0  # pipeline kalp atisi gecikmeli
    # run_batch state'i dosyada tasir: birinci tur streak yazar, ikinci tur okur.
    first = sentinel.run_batch(**dict(inj, state=None))
    assert first["score"] == 70  # pipeline(30) kirmizi, digerleri yesil
    assert first["repairs"] == []  # streak 1 -> bekleme (kacak restart yok)
    second = sentinel.run_batch(**dict(inj, state=None))
    assert [r["component"] for r in second["repairs"]] == ["pipeline"]
    assert second["repairs"][0]["ok"] is True
    # Cooldown: ayni durum ucuncu turda tekrar onarilmaz.
    state = {"streaks": {"pipeline": 3}, "cooldowns": {"pipeline": 1000.0}}
    probes = {name: {"ok": True, "detail": ""} for name in sentinel.COMPONENTS}
    probes["pipeline"] = {"ok": False, "detail": "hala kirmizi"}
    assert sentinel.decide(probes, state, now=1010.0) == []


def test_sentinel_failed_repair_writes_no_cooldown():
    res = sentinel.repair("webchat", state={}, now=1000.0,
                          cmd_fn=lambda cmd: {"ok": False},
                          restart_fn=lambda unit: {"ok": False, "unit": unit})
    assert res["ok"] is False  # cooldown yazilmaz -> sonraki tur tekrar dener


def test_sentinel_probe_failure_is_fail_open(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    inj = _healthy_injections()
    inj["health_fn"] = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    inj["age_fn"] = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    res = sentinel.run_batch(**inj)
    assert res["ok"] is True and res["score"] == 100  # kor restart yok


def test_sentinel_endpoint_is_read_only():
    import webchat_server as ws

    app = ws._lazy_app()
    paths = sorted({r.path for r in app.routes})
    assert "/sentinel" in paths and "/health" in paths
    # Route yalnizca GET ve salt-okunur (onarim tetiklemez): kaynakta POST yok.
    route = next(r for r in app.routes if getattr(r, "path", "") == "/sentinel")
    methods = {m for m in (getattr(route, "methods", set()) or set())}
    assert methods <= {"GET"}


