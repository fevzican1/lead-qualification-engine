"""Dinamik Bot Havuzu + Ortak Beyin kayıt defteri testleri."""
import time

import bot_registry
import config


def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_registry, "STATE_PATH", tmp_path / "bot_registry.json")
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "Main_Bot")
    config.set_bot_pool([])


def test_pasif_bot_link_rotasyonuna_girmez(tmp_path, monkeypatch):
    _iso(monkeypatch, tmp_path)
    bot_registry.register_pool(["Main_Bot", "Two_Bot"])
    bot_registry.mark_passive("Main_Bot", 3600)
    assert bot_registry.active_usernames() == ["Two_Bot"]
    assert bot_registry.next_active_username() == "Two_Bot"
    assert config.next_bot_username() == "Two_Bot"


def test_cooldown_dolunca_otomatik_reaktive(tmp_path, monkeypatch):
    _iso(monkeypatch, tmp_path)
    bot_registry.register_pool(["Solo_Bot"])
    bot_registry.mark_passive("Solo_Bot", 0.05)
    assert bot_registry.is_active("Solo_Bot") is False
    time.sleep(0.08)
    # Hiçbir komut gerekmez: okuma anında sweep reaktive eder.
    assert bot_registry.is_active("Solo_Bot") is True
    assert bot_registry.pool_snapshot()["Solo_Bot"]["status"] == "active"
    assert bot_registry.active_usernames() == ["Solo_Bot"]


def test_tum_botlar_pasifse_fail_open(tmp_path, monkeypatch):
    _iso(monkeypatch, tmp_path)
    bot_registry.register_pool(["A_Bot", "B_Bot"])
    bot_registry.mark_passive("A_Bot", 3600)
    bot_registry.mark_passive("B_Bot", 3600)
    # Sistem durmaz: rotasyon tüm havuzdan devam eder.
    assert set(bot_registry.active_usernames()) == {"A_Bot", "B_Bot"}
    assert bot_registry.next_active_username() in ("A_Bot", "B_Bot")
