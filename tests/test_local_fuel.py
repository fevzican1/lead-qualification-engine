"""Lane AN (local_fuel) + Lane AO (job_watchdog) — iç yakıt ve iş bekçisi testleri.

İç yakıt = GitHub/CDN dışı, hedef siteye dokunmayan liste-servisi beslemesi.
İş bekçisi = servis 'active' olsa bile iş durursa yakalayan onarım.
"""
from __future__ import annotations

import json
import time

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


def test_job_watchdog_sweep_kills_stuck_pipeline_only():
    """Yaş tavanını aşan pipeline kaçağı ölür; genç süreç ve bekçi dokunulmaz."""
    rows = [
        (111, 3000, "/opt/devsolve/.venv/bin/python pipeline.py --targets targets.txt --submit"),
        (222, 120, "/opt/devsolve/.venv/bin/python pipeline.py --targets targets.txt --submit"),
        (333, 9000, "/usr/bin/python -m nirvana.runner job_watchdog"),
    ]
    killed: list[int] = []
    out = job_watchdog.sweep_stuck_processes(
        max_age_s=2700, rows=rows, kill_fn=lambda pid: killed.append(pid) or True,
    )
    assert killed == [111]
    assert out == ["pipeline:111"]


def test_job_watchdog_sweep_catches_combined_pipe_wrapper():
    """Canlı arıza birebir: `bash -c ... pipeline.py ... | tail -n 50` asılı süreç.

    Süreç satırı pipeline kuralına da uyar; yaş tavanı (2700s) dolmamış olsa
    bile boru sarmalayıcısı 10 dk eşiğinde yakalanmalı (elif zinciri hatası:
    canlı arızada tail kuralı hiç değerlendirilmiyordu).
    """
    cmd = ("bash -c cd /opt/devsolve && /opt/devsolve/.venv/bin/python "
           "pipeline.py --targets targets.txt --submit 2>&1 | tail -n 50")
    killed: list[int] = []
    out = job_watchdog.sweep_stuck_processes(
        max_age_s=2700, rows=[(872396, 700, cmd)],
        kill_fn=lambda pid: killed.append(pid) or True,
    )
    assert killed == [872396]
    assert out == ["tail:872396"]


def test_job_watchdog_sweep_spares_service_and_self(monkeypatch):
    """systemd servis süreci ve bekçinin kendisi asla öldürülmez."""
    import os

    rows = [
        (555, 9999, "/opt/devsolve/.venv/bin/python /opt/devsolve/auto_runner.py"),
        (556, 9999, "bash -c /opt/devsolve/.venv/bin/python /opt/devsolve/auto_runner.py"),
        (os.getpid(), 99999, "python pipeline.py --targets x --submit"),
    ]
    killed: list[int] = []
    out = job_watchdog.sweep_stuck_processes(
        rows=rows, kill_fn=lambda pid: killed.append(pid) or True,
    )
    assert 555 not in killed  # servis süreci korunur
    assert os.getpid() not in killed  # kendini vurma yok
    assert 556 in killed  # elle başlatılmış kopya (bash -c) temizlenir
    assert out == ["auto_runner_manual:556"]


def test_job_watchdog_sweep_runs_every_cycle(tmp_path, monkeypatch):
    """Süpürge systemd'den bağımsız HER turda çalışır ve rapora yazılır."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(domain_store, "queue_depth", lambda: 0)
    monkeypatch.setattr(job_watchdog, "WEBCHAT_PORT", 9)
    seen: list[float | None] = []

    def fake_sweep(*, max_age_s=None):
        seen.append(max_age_s)
        return ["pipeline:4242"]

    report = job_watchdog.run_batch(
        dry_run=False, notify=False, sweep_fn=fake_sweep,
        restart_fn=lambda unit: {"unit": unit, "ok": True},
    )
    assert seen, "süpürge her turda çağrılmalı"
    assert report["swept"] == ["pipeline:4242"]


def test_job_watchdog_dry_run_no_sweep(tmp_path, monkeypatch):
    """dry_run (kurulum doğrulaması) hiçbir süreç öldürmez."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(domain_store, "queue_depth", lambda: 0)
    monkeypatch.setattr(job_watchdog, "WEBCHAT_PORT", 9)

    def _boom(**kwargs):  # pragma: no cover - çağrılmamalı
        raise AssertionError("dry-run süpürge YOK")

    report = job_watchdog.run_batch(dry_run=True, notify=False, sweep_fn=_boom)
    assert report["swept"] == []


def test_job_watchdog_cooldown_message_is_autonomous(tmp_path, monkeypatch):
    """Soğuma penceresindeki mesaj 'elle müdahale gerekli' değil, otonom olmalı."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(domain_store, "queue_depth", lambda: 0)
    monkeypatch.setattr(job_watchdog, "WEBCHAT_PORT", 9)
    monkeypatch.setattr(job_watchdog, "STREAK", 1)
    monkeypatch.setattr(job_watchdog, "systemd_available", lambda: True)
    state = {"signals": {"queue": {"streak": 1, "last_restart": time.time() - 60}}}
    state_path("job_watchdog.json").write_text(json.dumps(state), encoding="utf-8")
    messages: list[str] = []
    report = job_watchdog.run_batch(
        dry_run=False, notify=True,
        sweep_fn=lambda **kwargs: [],
        restart_fn=lambda unit: {"unit": unit, "ok": True},
        notify_fn=lambda msg: messages.append(msg) or True,
    )
    # Soğuma penceresi: kuyruk sinyali için yeni restart yok (diğer sinyaller bağımsız).
    assert all(row.get("signal") != "queue" for row in report["restarts"])
    joined = "\n".join(messages)
    assert "otomatik" in joined and "elle müdahale gerekmez" in joined
