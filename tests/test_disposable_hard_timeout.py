"""Kullan-at tarayıcı + 30 sn hard timeout + karantina (kurallar 2-3).

- Kural 2: her form gönderim denemesinden sonra Chromium browser.close() +
  purge (pkill) ile imha edilir; zombi/ram artığı kalmaz.
- Kural 3: site 30 sn'yi aşarsa iptal + karantina + sıradaki taze lead;
  ana motor/systemd asla durmaz, sunucuya reboot YOK.
"""
from __future__ import annotations

import inspect
import threading
import time
from types import SimpleNamespace


def test_config_hard_timeout_defaults() -> None:
    import config

    assert float(config.SUBMIT_HARD_TIMEOUT_SECONDS) == 30.0
    assert float(config.SUBMIT_QUARANTINE_HOURS) > 0


def test_purge_chromium_fails_open(monkeypatch) -> None:
    """ps/kill altyapısı yoksa bile purge asla raise etmez (fail-open)."""
    import browser as browser_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("ps altyapisi yok")

    monkeypatch.setattr(browser_mod.subprocess, "run", boom)
    assert browser_mod.purge_chromium() == 0


def test_arm_hard_kill_fires_and_purges(monkeypatch) -> None:
    """Duvar-saati dolan an bekçi tarayıcıyı imha eder (armed → fired)."""
    import browser as browser_mod
    import pipeline

    purged = threading.Event()
    monkeypatch.setattr(browser_mod, "purge_chromium", lambda **_k: purged.set())
    arm = pipeline._arm_hard_kill(0.0, grace_s=0.05)
    assert purged.wait(3.0), "hard kill purge çağırmadı"
    arm["stop"].set()
    assert arm["fired"] is True


def test_arm_hard_kill_cancelled_when_attempt_finishes(monkeypatch) -> None:
    """Deneme zamanında biterse bekçi ateşlemez (yanlış imha yok)."""
    import browser as browser_mod
    import pipeline

    calls: list[int] = []
    monkeypatch.setattr(browser_mod, "purge_chromium", lambda **_k: calls.append(1))
    arm = pipeline._arm_hard_kill(0.0, grace_s=0.3)
    arm["stop"].set()
    time.sleep(0.6)
    assert arm["fired"] is False
    assert calls == []


def test_quarantine_hard_timeout_defers_host(monkeypatch) -> None:
    """Kilitlenen lead karantinaya alınır: 6 saat, fail saymadan, fail-open."""
    import config
    import pipeline

    seen: dict[str, object] = {}

    def _defer(url: str, **kwargs) -> None:
        seen.update({"url": url, **kwargs})

    monkeypatch.setattr(pipeline.domain_store, "defer", _defer)
    pipeline.quarantine_hard_timeout("https://stuck.example/contact")
    assert seen["url"] == "https://stuck.example/contact"
    assert seen["reason"] == "submit_hard_timeout_30s"
    assert seen["count_fail"] is False
    assert float(seen["hours"]) == float(config.SUBMIT_QUARANTINE_HOURS)


def test_submit_with_page_wall_guard_sets_hard_timeout(monkeypatch) -> None:
    """Yavaş site duvar-saatini aşarsa deneme iptal + hard_timeout bayrağı."""
    import form_submitter

    monkeypatch.setattr(form_submitter.config, "SUBMIT_HARD_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(
        form_submitter.config,
        "require_live_customer_link",
        lambda *_a, **_k: "https://chat.example/chat?sid=x",
    )
    monkeypatch.setattr(form_submitter.optout, "is_url_opted_out", lambda *_a, **_k: False)

    def slow_goto(_page, _url, _timeout_ms=None):
        time.sleep(0.6)  # 0.3 sn duvar-saatini aşar
        return None

    monkeypatch.setattr(form_submitter, "goto_page", slow_goto)
    page = SimpleNamespace(
        set_default_timeout=lambda *_a, **_k: None,
        set_default_navigation_timeout=lambda *_a, **_k: None,
    )
    lead = {
        "url": "https://stuck.example",
        "status": "qualified",
        "submit_attempts": 0,
        "contact_form": {"found": True, "page_url": "https://stuck.example/contact"},
        "value_proposition": "Test pitch STOP",
        "form_subject": "Hi",
    }
    out = form_submitter._submit_with_page(page, lead)
    assert out["status"] == "skipped_submit_failed"
    assert out.get("hard_timeout") is True
    assert "timeout" in str(out.get("error") or "")


def test_kullanat_wired_in_sources() -> None:
    """Kod kazıması: kullan-at + hard kill + karantina hatlara bağlı."""
    import form_submitter
    import pipeline

    assert "purge_chromium" in inspect.getsource(form_submitter.submit_lead)
    submit_src = inspect.getsource(pipeline._run_browser_pipeline)
    assert "_dispose_bundle" in submit_src
    assert "_arm_hard_kill" in submit_src
    assert "quarantine_hard_timeout" in submit_src


def test_watchdog_still_never_reboots() -> None:
    """Kural 1 kalıcı: bekçide reboot kodu yok (yalnızca servis restart)."""
    from pathlib import Path

    import nirvana.job_watchdog as job_watchdog

    src = Path(job_watchdog.__file__).read_text(encoding="utf-8")
    assert '"systemctl", "reboot"' not in src
    assert '"sudo", "reboot"' not in src
    assert "hard_reboot" not in src
