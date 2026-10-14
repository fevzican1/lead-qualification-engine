"""TMUX Otopilot testleri — sentinel + quality_audit 7/24 aktif tmux modu.

Kullanıcı kararı (2026-10-14): sentinel ve kalite denetim motoru PASIF
timer değil, tmux oturumlarinda AKTIF ve otonom calisir; supervisor +
systemd + singleton lock ile kesintisizlik üç katman garanti altindadir.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT = ROOT / "oracle" / "tmux_autopilot.sh"
UNIT = ROOT / "oracle" / "nirvana-autopilot.service"
INSTALL = ROOT / "oracle" / "nirvana_oracle_install.sh"


def test_autopilot_script_exists_and_wires_both_lanes():
    src = AUTOPILOT.read_text(encoding="utf-8")
    # iki aktif oturum
    assert "nirvana-sentinel" in src
    assert "nirvana-quality" in src
    # surekli dongu, taze kod cagrisi (her tur yeni import)
    assert "while true" in src
    assert "-m nirvana.runner" in src
    assert "sentinel" in src and "quality_audit" in src
    # idempotent ayakta tutma + 60sn supervisor denetimi
    assert "has-session" in src
    assert "supervise" in src
    # .envsurulur (systemd EnvironmentFile'inin tmux karsiligi)
    assert ".env" in src and "set -a" in src
    # AKTIF mod: --no-notify (dry_run) KULLANILMAZ — onarim/bildirim acik
    assert "--no-notify" not in src


def test_autopilot_unit_self_heals_forever():
    unit = UNIT.read_text(encoding="utf-8")
    assert "tmux_autopilot.sh supervise" in unit
    assert "Restart=always" in unit, "supervisor kendini yeniden baslamali (7/24)"
    assert "WantedBy=multi-user.target" in unit, "boot'ta otomatik baslamali"


def test_install_script_wires_autopilot_and_tmux():
    install = INSTALL.read_text(encoding="utf-8")
    assert "nirvana-autopilot.service" in install, "unit kopyalanmali"
    assert "tmux_autopilot.sh" in install, "oturumlar kurulumda acilmali"
    assert "command -v tmux" in install, "tmux yoksa kurulumla saglanmali"
    # mevcut timer'lar emniyet agi olarak KORUNUR (singleton lock cakismayi onler)
    assert "nirvana-sentinel.timer" in install
    assert "nirvana-quality.timer" in install


def test_lanes_stay_registered_for_both_systemd_and_tmux():
    from nirvana.runner import RUNNERS

    assert RUNNERS["sentinel"] == "nirvana.sentinel"
    assert RUNNERS["quality_audit"] == "nirvana.quality_audit"
