"""STRICT COOLDOWN + ZERO-TOUCH PASSIVE testleri (canlı arıza 2026-09-19).

Telegram 73.420 sn FLOOD_WAIT verdiğinde:
  - süre yapay tavanla KIRPILMAZ (disk/bellek aynen taşınır),
  - cezalı bota getMe/ping/health-check dâhil TEK istek atılmaz,
  - cooldown bitince bot manuel komut/restart olmadan aktive edilir,
  - /notifyme özeti satış botu cezalıyken yedek hattan ulaşır.
"""
import asyncio
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot_registry
import config
import flood_guard
import owner_notify
import pytest
import telegram_sales_bot as bot


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(flood_guard, "_STATE_PATH", tmp_path / "flood_gate.json")
    flood_guard.reset()
    monkeypatch.setattr(bot_registry, "STATE_PATH", tmp_path / "bot_registry.json")
    bot_registry.register_pool(["B1_Bot", "B2_Bot"])
    monkeypatch.setattr(owner_notify, "PATH", tmp_path / "owner.json")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_CHAT_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_OWNER_CHAT_ID", "111")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKENS", "")
    yield
    flood_guard.reset()


def test_telegram_cezasi_tavansiz_aynen_yazilir():
    """73420 sn FLOOD_WAIT → kapı 73420 sn kapalı (kırpma YOK)."""
    flood_guard.note_retry_after(73420.0, chat_id=42, bot_username="B1_Bot")
    rem = flood_guard.remaining(42)
    assert 73400 < rem <= 73420.0, rem
    data = json.loads(flood_guard._STATE_PATH.read_text(encoding="utf-8"))
    assert float(data["chats"]["42"]) > time.time() + 73000


def test_cezali_bot_registryde_aynen_pasif():
    bot_registry.mark_passive("B1_Bot", 73420.0)
    entry = json.loads(bot_registry.STATE_PATH.read_text(encoding="utf-8"))["bots"]["B1_Bot"]
    assert entry["status"] == "passive"
    assert float(entry["cooldown_until"]) > time.time() + 73000
    assert bot_registry.is_active("B1_Bot") is False


def test_cezali_botun_tokeni_havuzdan_cikarilir():
    tok = "1111111111:AAA"
    bot_registry.set_token_hint("B1_Bot", hashlib.sha256(tok.encode()).hexdigest()[:12])
    monkey_tok = tok
    import config as _config
    _config.TELEGRAM_BOT_TOKEN = monkey_tok  # doğrudan (fixture zaten boşalttı)
    try:
        bot_registry.mark_passive("B1_Bot", 73420.0)
        assert tok not in owner_notify._pool_tokens()
        assert owner_notify._pool_tokens() == []
    finally:
        _config.TELEGRAM_BOT_TOKEN = ""


def test_healthcheck_cezali_bota_gitmez(monkeypatch):
    tok = "2222222222:BBB"
    bot_registry.set_token_hint("B2_Bot", hashlib.sha256(tok.encode()).hexdigest()[:12])
    bot_registry.mark_passive("B2_Bot", 73420.0)
    hits: list[tuple] = []

    def _yasak(*a, **k):
        hits.append(a)
        raise AssertionError("cezali bota health-check istegi gitti")

    monkeypatch.setattr(owner_notify.httpx, "post", _yasak)
    monkeypatch.setattr(owner_notify.httpx, "get", _yasak)
    assert owner_notify._token_ok(tok) is False
    assert hits == []
    # flood_guard.token_ok da aynı kararı verir (HTTP yok):
    assert flood_guard.token_ok(tok) is False


def test_sure_sonu_otomatik_aktivasyon():
    bot_registry.mark_passive("B2_Bot", 0.05)
    assert bot_registry.is_active("B2_Bot") is False
    time.sleep(0.08)
    assert bot_registry.is_active("B2_Bot") is True  # manuel komut/restart YOK


def test_token_owner_getmesiz_cozulur(tmp_path, monkeypatch):
    bot_ids = tmp_path / "bot_ids.json"
    bot_ids.write_text(json.dumps({"bots": {"7777777777": "Cache_Bot"}}), encoding="utf-8")
    monkeypatch.setattr(config, "BOT_ID_STATE_PATH", bot_ids)
    assert config.bot_username_for_token("7777777777:SEC") == "Cache_Bot"
    assert owner_notify._token_owner("7777777777:SEC") == "Cache_Bot"
    assert flood_guard.token_ok("7777777777:SEC") is True


def test_send_via_pool_cezali_bota_vurmaz(monkeypatch):
    tok = "3333333333:CCC"
    bot_registry.set_token_hint("B1_Bot", hashlib.sha256(tok.encode()).hexdigest()[:12])
    bot_registry.mark_passive("B1_Bot", 73420.0)
    hits: list[tuple] = []

    def _yasak(*a, **k):
        hits.append(a)
        raise AssertionError("cezali bota istek gitti")

    monkeypatch.setattr(owner_notify.httpx, "post", _yasak)
    assert owner_notify._send_via_pool(999, {"chat_id": 999}, [tok]) is False
    assert hits == []


def test_notifyme_ozeti_satis_botu_cezaliyken_yedek_hattan_gider(monkeypatch):
    class _RetryAfter(Exception):
        retry_after = 73420.0

    async def boom(*a, **k):
        raise _RetryAfter()

    upd = SimpleNamespace(
        effective_chat=SimpleNamespace(id=111, type="private"),
        effective_user=SimpleNamespace(id=111, language_code="tr", username="patron"),
        message=SimpleNamespace(text="/notifyme", reply_text=boom),
        callback_query=None,
    )
    ctx = SimpleNamespace(args=[], bot=SimpleNamespace(send_message=AsyncMock()))
    captured: dict = {}

    def fake_send(text, *, chat_id=None, high_priority=False):
        captured["chat_id"] = chat_id
        captured["text"] = text
        return True

    monkeypatch.setattr(owner_notify, "send", fake_send)
    asyncio.run(bot.cmd_notifyme(upd, ctx))
    assert captured.get("chat_id") == 111
    assert "Özet" in captured.get("text", "")