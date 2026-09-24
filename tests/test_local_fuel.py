"""Lane AN (local_fuel) + Lane AO (job_watchdog) — iç yakıt ve iş bekçisi testleri.

İç yakıt = GitHub/CDN dışı, hedef siteye dokunmayan liste-servisi beslemesi.
İş bekçisi = servis 'active' olsa bile iş durursa yakalayan onarım.
"""
from __future__ import annotations

import config
import domain_store
from nirvana import hot_fuel, job_watchdog, local_fuel
from nirvana.registry import MODULES, state_path


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(hot_fuel, "DB_PATH", tmp_path / "hot_fuel.db")
    monkeypatch.setattr(hot_fuel, "STATE_DIR", tmp_path)
    monkeypatch.setattr(domain_store, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(domain_store, "PROCESSED_PATH", tmp_path / "processed.json")
    monkeypatch.setattr(domain_store, "BUDGET_PATH", tmp_path / "budget.json")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nirvana.registry.STATE_DIR", state_dir)
    return tmp_path


def test_local_fuel_registry_and_runner():
    assert MODULES()["local_fuel"]["host"] == "oracle"
    assert MODULES()["job_watchdog"]["host"] == "oracle"
    from nirvana import runner as nirvana_runner

    assert nirvana_runner.RUNNERS["local_fuel"] == "nirvana.local_fuel"
    assert nirvana_runner.RUNNERS["job_watchdog"] == "nirvana.job_watchdog"


def test_local_fuel_contact_paths_cover_tr_and_global():
    assert local_fuel.contact_url("ornek.com.tr", profile="tr").endswith("/iletisim")
    assert local_fuel.contact_url("Example.COM ", profile="all").startswith("https://")
    seeds = local_fuel.parse_seeds(
        "  tohum.com.tr  \n# yorum\nbozuk-url!!\n", profile="tr",
    )
    assert seeds and seeds[0]["url"] == "https://tohum.com.tr/iletisim"
    glob = local_fuel.parse_seeds("tohum.example\n", profile="all")
    assert glob and glob[0]["url"] == "https://tohum.example/contact"


def test_local_fuel_dedupe_prefers_higher_score():
    rows = local_fuel.dedupe(
        [
            {"url": "https://a.example/iletisim", "easy_score": 60},
            {"url": "https://a.example/iletisim", "easy_score": 95},
            {"url": "https://b.example/contact", "easy_score": 70},
        ]
    )
    by_url = {row["url"]: int(row["easy_score"]) for row in rows}
    assert by_url["https://a.example/iletisim"] == 95
    assert len(rows) == 2


def test_local_fuel_run_batch_dry_run_writes_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    rows = [{"url": "https://dry.example/iletisim", "easy_score": 85, "source": "t"}]
    result = local_fuel.run_batch(dry_run=True, rows=rows)
    assert result["dry_run"] is True
    assert result["added"] == 0
    assert hot_fuel.status()["depth"] == 0


def test_local_fuel_run_batch_feeds_reservoir(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    rows = [
        {"url": "https://yerel-a.example/iletisim", "easy_score": 90, "source": "t"},
        {"url": "https://yerel-b.example/contact", "easy_score": 85, "source": "t"},
        {"url": "https://yerel-c.example/iletisim", "easy_score": 40, "source": "t"},
    ]
    result = local_fuel.run_batch(rows=rows, prime=2)
    # 40 skor hot_fuel FEED_MIN_SCORE (80) barajının altında -> elenir.
    assert result["added"] == 2
    assert result["primed"] == 2
    assert hot_fuel.status()["depth"] == 2
    # İkinci koşu: upsert aynı URL'leri günceller (rowcount>0) ama derinlik artmaz.
    again = local_fuel.run_batch(rows=rows)
    assert hot_fuel.status()["depth"] == 2


def test_local_fuel_full_reservoir_skips_network(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(hot_fuel, "DEFAULT_TARGET", 4)
    hot_fuel.push(
        [{"url": f"https://dolu-{i}.example/iletisim", "easy_score": 90} for i in range(5)]
    )

    def _boom(*args, **kwargs):  # pragma: no cover - çağrılmamalı
        raise AssertionError("dolu rezervuarda ağ çağrılmamalı")

    monkeypatch.setattr(local_fuel, "collect", _boom)
    result = local_fuel.run_batch()
    assert result.get("skipped") == "reservoir_full"


def test_local_fuel_self_test_ok(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    code = local_fuel.self_test()
    assert code == 0
    assert "LOCAL_FUEL SELF-TEST OK" in capsys.readouterr().out


def test_job_watchdog_red_when_work_stopped(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(domain_store, "queue_depth", lambda: 0)
    monkeypatch.setattr(job_watchdog, "WEBCHAT_PORT", 9)
    report = job_watchdog.run_batch(dry_run=True, notify=False)
    assert report["ok"] is False
    assert set(report["red"]) >= {"queue", "fuel"}
    assert report["restarts"] == []


def test_job_watchdog_restart_after_streak(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(domain_store, "queue_depth", lambda: 0)
    monkeypatch.setattr(job_watchdog, "WEBCHAT_PORT", 9)
    monkeypatch.setattr(job_watchdog, "STREAK", 1)
    restarted: list[str] = []
    job_watchdog.run_batch(
        dry_run=False,
        notify=False,
        restart_fn=lambda unit: restarted.append(unit) or {"unit": unit, "ok": True},
    )
    assert "nirvana-pipeline.service" in restarted


def test_job_watchdog_dry_run_never_restarts(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(domain_store, "queue_depth", lambda: 0)
    monkeypatch.setattr(job_watchdog, "WEBCHAT_PORT", 9)
    monkeypatch.setattr(job_watchdog, "STREAK", 1)

    def _boom(unit):  # pragma: no cover - çağrılmamalı
        raise AssertionError("dry-run restart YOK")

    report = job_watchdog.run_batch(dry_run=True, restart_fn=_boom)
    assert report["dry_run"] is True
    assert report["restarts"] == []


def test_job_watchdog_self_test_ok(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    code = job_watchdog.self_test()
    assert code == 0
    assert "JOB_WATCHDOG SELF-TEST OK" in capsys.readouterr().out
