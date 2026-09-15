"""Çoklu bot havuzu testleri: token listesi, round-robin link dağıtımı, sahiplik yönlendirme."""

import pytest

import config


@pytest.fixture(autouse=True)
def _reset_pool():
    """Havuz state'i testler arası sızmasın (rotation mevcut form testlerini bozar)."""
    config.set_bot_pool([])
    yield
    config.set_bot_pool([])


def test_bot_tokens_primary_once_and_dedup(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "tok-a")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKENS", "tok-b, tok-a ,tok-c,,tok-b")
    assert config.bot_tokens() == ["tok-a", "tok-b", "tok-c"]


def test_bot_tokens_single_token_path(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "tok-a")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKENS", "")
    assert config.bot_tokens() == ["tok-a"]


def test_require_live_link_rotates_pool(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "Main_Bot")
    config.set_bot_pool(["Main_Bot", "Two_Bot", "Three_Bot"])
    links = [config.require_live_telegram_link(f"dsT{i}") for i in range(3)]
    users = [link.split("t.me/")[1].split("?")[0] for link in links]
    assert users == ["Main_Bot", "Two_Bot", "Three_Bot"]


def test_next_bot_username_wraps_and_falls_back(monkeypatch):
    config.set_bot_pool(["A_Bot", "B_Bot"])
    seq = [config.next_bot_username() for _ in range(5)]
    assert seq == ["A_Bot", "B_Bot", "A_Bot", "B_Bot", "A_Bot"]
    # Boş havuz → birincil username'e düşer (tek bot davranışı bozulmaz).
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "Solo_Bot")
    config.set_bot_pool([])
    assert config.next_bot_username() == "Solo_Bot"


def test_resolve_bot_pool_drops_dead_tokens(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "Main_Bot")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "tok-a")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKENS", "tok-b,tok-bad")

    names = {"tok-b": "Two_Bot"}

    def fake_get(url, timeout=None):
        token = url.split("/bot")[1].split("/")[0]
        if token == "tok-bad":
            raise RuntimeError("network down")

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"result": {"username": names.get(token, "Main_Bot2")}}

        return _Resp()

    monkeypatch.setattr("httpx.get", fake_get)
    assert config.resolve_bot_pool() == ["Main_Bot", "Two_Bot"]


def test_app_for_chat_routes_to_owner_bot(monkeypatch, tmp_path):
    import telegram_sales_bot as bot
    import telegram_sessions

    monkeypatch.setattr(telegram_sessions, "PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(bot, "_APPS", {"Owner_Bot": "APP-OWNER"})
    telegram_sessions._put(55, bot_username="Owner_Bot")
    telegram_sessions._put(56, bot_username="Ghost_Bot")
    # Sahip bot bulundu:
    assert bot._app_for_chat(55, "APP-DEFAULT") == "APP-OWNER"
    # Havuzda olmayan / kayıtsız sohbet → varsayılan uygulama:
    assert bot._app_for_chat(56, "APP-DEFAULT") == "APP-DEFAULT"
    assert bot._app_for_chat(999, "APP-DEFAULT") == "APP-DEFAULT"
