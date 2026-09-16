"""telegram_bot_api — yerel Bot API cozumleyici + fail-safe testleri."""

from __future__ import annotations

import telegram_bot_api as tba


def test_cloud_defaults():
    bot, file = tba.cloud_urls()
    assert bot == "https://api.telegram.org/bot"
    assert file == "https://api.telegram.org/file/bot"


def test_local_urls_normalize_eder():
    assert tba.local_urls("http://127.0.0.1:8081") == (
        "http://127.0.0.1:8081/bot", "http://127.0.0.1:8081/file/bot")
    # Sonda egik cizgi ve '/bot' tekrarini tolere eder (PTB 'value + token' yapar).
    assert tba.local_urls("http://127.0.0.1:8081/bot/") == (
        "http://127.0.0.1:8081/bot", "http://127.0.0.1:8081/file/bot")
    assert tba.local_urls("http://telegram-bot-api:8081") == (
        "http://telegram-bot-api:8081/bot", "http://telegram-bot-api:8081/file/bot")


def test_base_tanimli_degilse_bulut(monkeypatch):
    monkeypatch.setattr(tba.config, "TELEGRAM_BOT_API_BASE_URL", "")
    out = tba.resolve()
    assert out["mode"] == "cloud" and out["file_limit_mb"] == 20
    assert out["base_url"] == tba.CLOUD_BOT_URL


def test_yerel_sunucu_saglikliysa_yerel_mod(monkeypatch):
    monkeypatch.setattr(tba.config, "TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081")
    out = tba.resolve(token="123:abc", probe=lambda tok, url: True)
    assert out["mode"] == "local"
    assert out["base_url"] == "http://127.0.0.1:8081/bot"
    assert out["base_file_url"] == "http://127.0.0.1:8081/file/bot"
    assert out["file_limit_mb"] == tba.LOCAL_FILE_LIMIT_MB == 2000


def test_yerel_sunucu_oluysa_buluta_duser(monkeypatch):
    monkeypatch.setattr(tba.config, "TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081")
    out = tba.resolve(token="123:abc", probe=lambda tok, url: False)
    assert out["mode"] == "cloud" and out["file_limit_mb"] == 20
    assert "fail-safe" in out["reason"]


def test_token_yoksa_bulut(monkeypatch):
    monkeypatch.setattr(tba.config, "TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081")
    monkeypatch.setattr(tba.config, "TELEGRAM_BOT_TOKEN", "")
    assert tba.resolve(token="", probe=lambda tok, url: True)["mode"] == "cloud"


def test_apply_to_builder_yalniz_yerel_modda_dokunur(monkeypatch):
    calls: list[tuple] = []

    class FakeBuilder:
        def base_url(self, url):
            calls.append(("bot", url))
            return self

        def base_file_url(self, url):
            calls.append(("file", url))
            return self

    monkeypatch.setattr(tba.config, "TELEGRAM_BOT_API_BASE_URL", "")
    builder, chosen = tba.apply_to_builder(FakeBuilder())
    assert calls == [] and chosen["mode"] == "cloud"

    monkeypatch.setattr(tba.config, "TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081")
    builder, chosen = tba.apply_to_builder(FakeBuilder(), token="t",
                                           probe=lambda tok, url: True)
    assert chosen["mode"] == "local" and calls[0][0] == "bot" and calls[1][0] == "file"


def test_status_line_okunabilir(monkeypatch):
    monkeypatch.setattr(tba.config, "TELEGRAM_BOT_API_BASE_URL", "")
    line = tba.status_line()
    assert "BULUT" in line and "20 MB" in line