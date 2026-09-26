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
    assert out == ["auto_runner_stray:556"]


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


def _signals_red(**overrides):
    """Deterministik kırmızı sinyal sözlüğü; override ile kalemler yeşile çekilir."""
    sig = {
        "queue_depth": 0,
        "fuel_ready": 500,
        "fuel_target": 2000,
        "forms_today": 0,
        "forms_hour": 0,
        "forms_cap": 400,
        "forms_expected": 10,
        "webchat_health": True,
        "webchat": {"sessions_today": 0, "sessions_total": 0, "last_seen_s": None},
    }
    sig.update(overrides)
    return sig


def _green_verdict():
    checks = {
        "queue": {"ok": True, "detail": "ok"},
        "fuel": {"ok": True, "detail": "ok"},
        "forms": {"ok": True, "detail": "ok"},
        "webchat": {"ok": True, "detail": "ok"},
    }
    return {"checks": checks, "red": [], "ok": True,
            "webchat_sessions_today": 0, "webchat_last_seen_s": None}


def _red_verdict():
    checks = {
        "queue": {"ok": True, "detail": "ok"},
        "fuel": {"ok": True, "detail": "ok"},
        "forms": {"ok": True, "detail": "ok"},
        "webchat": {"ok": False, "detail": "yanıtsız"},
    }
    return {"checks": checks, "red": ["webchat"], "ok": False,
            "webchat_sessions_today": 0, "webchat_last_seen_s": None}


def test_job_watchdog_autonomous_success_single_line_message(tmp_path, monkeypatch):
    """İş 5 dk içinde otonom çözülürse SADECE tek satır 'Kök Neden' bildirimi gider."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(job_watchdog, "STREAK", 99)  # hızlı streak restart devre dışı
    messages: list[str] = []
    report = job_watchdog.run_batch(
        dry_run=False, notify=True, checks=_signals_red(), auto_loop=True,
        sweep_fn=lambda **kwargs: [],
        sleep_fn=lambda seconds: None,
        probe_fn=lambda: (_signals_red(queue_depth=500), _green_verdict()),
        restart_fn=lambda unit: {"unit": unit, "ok": True},
        cmd_fn=lambda cmd: {"ok": True},
        db_fn=lambda path: False,
        kill_fn=lambda **kwargs: {"ok": True},
        wal_fn=lambda path: {"ok": True},
        notify_fn=lambda msg: messages.append(msg) or True,
    )
    recovery = report["recovery"]
    assert recovery["resolved"] is True and recovery["rebooted"] is False
    assert len(messages) == 1, "başarıda tek mesaj"
    line = messages[0]
    assert "\n" not in line, "tek satır olmalı"
    assert "Kök Neden:" in line and "-> 5 dk içinde Otonom Onarıldı" in line


def test_job_watchdog_chromium_lock_kills_and_restarts(tmp_path, monkeypatch):
    """5 dk form yok + iş kırmızı → pkill -9 -f chromium + pipeline restart."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(job_watchdog, "STREAK", 99)
    state = {"forms_last_count": 0, "forms_last_ts": time.time() - 400}
    state_path("job_watchdog.json").write_text(json.dumps(state), encoding="utf-8")
    killed: list[dict] = []
    restarted: list[str] = []
    report = job_watchdog.run_batch(
        dry_run=False, notify=True, checks=_signals_red(), auto_loop=True,
        sweep_fn=lambda **kwargs: [],
        sleep_fn=lambda seconds: None,
        probe_fn=lambda: (_signals_red(queue_depth=500), _green_verdict()),
        restart_fn=lambda unit: restarted.append(unit) or {"unit": unit, "ok": True},
        kill_fn=lambda **kwargs: killed.append(kwargs) or {"ok": True},
        db_fn=lambda path: False,
        cmd_fn=lambda cmd: {"ok": True},
        notify_fn=lambda msg: True,
    )
    assert killed, "5 dk form yok → Chromium süreçleri indirilmeliydi"
    assert "nirvana-pipeline.service" in restarted
    assert report["recovery"]["root_cause"] == job_watchdog.RC_CHROMIUM


def test_job_watchdog_port_lock_clears_port_and_resets_unit(tmp_path, monkeypatch):
    """WebChat yanıtsız: fuser -k -9 <port>/tcp + reset-failed + restart."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(job_watchdog, "STREAK", 99)
    monkeypatch.setattr(job_watchdog, "WEBCHAT_PORT", 9)
    cmds: list[list[str]] = []
    restarted: list[str] = []
    killed: list[dict] = []
    report = job_watchdog.run_batch(
        dry_run=False, notify=True, auto_loop=True,
        checks=_signals_red(queue_depth=500, forms_expected=0, webchat_health=False),
        sweep_fn=lambda **kwargs: [],
        sleep_fn=lambda seconds: None,
        probe_fn=lambda: (_signals_red(queue_depth=500, forms_expected=0,
                                       webchat_health=False), _green_verdict()),
        restart_fn=lambda unit: restarted.append(unit) or {"unit": unit, "ok": True},
        cmd_fn=lambda cmd: cmds.append(cmd) or {"ok": True},
        kill_fn=lambda **kwargs: killed.append(kwargs) or {"ok": True},
        db_fn=lambda path: False,
        notify_fn=lambda msg: True,
    )
    assert ["fuser", "-k", "-9", "9/tcp"] in cmds
    assert ["systemctl", "reset-failed", "nirvana-webchat.service"] in cmds
    assert "nirvana-webchat.service" in restarted
    assert not killed, "Port kilidinde chromium indirilmez"
    assert report["recovery"]["root_cause"].startswith("Port Kilidi")


def test_job_watchdog_wal_reset_on_sqlite_lock(tmp_path, monkeypatch):
    """SQLite kilitliyse WAL sıfırlanır; kök neden etiketi DB kilidi olur."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(job_watchdog, "STREAK", 99)
    reset_paths: list = []
    report = job_watchdog.run_batch(
        dry_run=False, notify=True, auto_loop=True,
        checks=_signals_red(queue_depth=500, forms_expected=0),
        sweep_fn=lambda **kwargs: [],
        sleep_fn=lambda seconds: None,
        probe_fn=lambda: (_signals_red(queue_depth=500, forms_expected=0),
                          _green_verdict()),
        restart_fn=lambda unit: {"unit": unit, "ok": True},
        db_fn=lambda path: True,
        wal_fn=lambda path: reset_paths.append(path) or {"ok": True},
        notify_fn=lambda msg: True,
    )
    assert reset_paths, "kilitli DB'de WAL sıfırlama çağrılmalı"
    assert report["recovery"]["root_cause"] == job_watchdog.RC_DB


def test_job_watchdog_hard_cap_reboots_without_notify(tmp_path, monkeypatch):
    """5 dk içinde çözülmezse: izin/bildirim OLMADAN reboot; Telegram YOK."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(job_watchdog, "STREAK", 99)
    messages: list[str] = []
    reboots: list[dict] = []
    report = job_watchdog.run_batch(
        dry_run=False, notify=True, auto_loop=True, budget_s=0.0,
        checks=_signals_red(queue_depth=500, forms_expected=0, webchat_health=False),
        sweep_fn=lambda **kwargs: [],
        sleep_fn=lambda seconds: None,
        probe_fn=lambda: (_signals_red(queue_depth=500, forms_expected=0,
                                       webchat_health=False), _red_verdict()),
        restart_fn=lambda unit: {"unit": unit, "ok": True},
        cmd_fn=lambda cmd: {"ok": True},
        kill_fn=lambda **kwargs: {"ok": True},
        db_fn=lambda path: False,
        reboot_fn=lambda **kwargs: reboots.append(kwargs) or {"ok": True,
                                                              "cmd": "sudo reboot"},
        notify_fn=lambda msg: messages.append(msg) or True,
    )
    assert reboots, "sert tavan dolunca reboot çalışmalı"
    assert messages == [], "reboot durumunda Telegram mesajı YOK"
    assert report["recovery"]["resolved"] is False
    assert report["recovery"]["rebooted"] is True
    assert report["notified"] == []


def test_job_watchdog_source_has_no_manual_intervention_text():
    """Kod kazıması: 'elle kontrol/manuel' kod ve mesajları kaynakta YOK."""
    import inspect

    src = inspect.getsource(job_watchdog).lower()
    for banned in ("elle kontrol", "elle müdahale", "manuel", "manual"):
        assert banned not in src, f"yasaklı ifade kaynakta: {banned}"


def test_job_watchdog_timer_scans_every_minute():
    """Tarama 1 dk; servis 5 dk otonom onarım tavanına göre zaman aşımı taşır."""
    timer = (config.ROOT / "oracle" / "nirvana-jobwatch.timer").read_text(encoding="utf-8")
    service = (config.ROOT / "oracle" / "nirvana-jobwatch.service").read_text(encoding="utf-8")
    assert "OnCalendar=*:0/1" in timer
    assert "TimeoutStartSec=420" in service
    assert "sudo reboot" in service
