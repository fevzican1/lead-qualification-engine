"""Admin kimliği (TELEGRAM_ADMIN_ID / TELEGRAM_ADMIN_TOKEN) + /notifyme testleri."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from types import SimpleNamespace

import config
import owner_notify
import telegram_sales_bot as bot


@pytest.fixture(autouse=True)
def isolated_owner(tmp_path, monkeypatch):
    """owner.json test dizininde kalsın (gerçek operatör kaydı bozulmasın)."""
    monkeypatch.setattr(owner_notify, "PATH", tmp_path / "owner.json")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_CHAT_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_BOT_TOKEN", "")
    return tmp_path


def _update(chat_id: int, text: str, args: list[str] | None = None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
        effective_user=SimpleNamespace(id=chat_id, language_code="tr", username="patron"),
        message=SimpleNamespace(text=text, reply_text=AsyncMock()),
        callback_query=None,
        _args=args,
    )


def _ctx(args: list[str] | None = None):
    return SimpleNamespace(args=args or [], bot=SimpleNamespace(send_message=AsyncMock()))


def test_admin_id_env_admin_listesine_girer(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_OWNER_CHAT_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_ID", "424242")
    assert owner_notify.load_admin_chat_ids() == {424242}
    assert owner_notify.load_admin_chat_id() == 424242


def test_admin_token_fail_closed(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_TOKEN", "")
    assert owner_notify.admin_token_ok("hersey") is False
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_TOKEN", "gizli-token-42")
    assert owner_notify.admin_token_ok("gizli-token-42") is True
    assert owner_notify.admin_token_ok("gizli-token-43") is False
    assert owner_notify.admin_token_ok("") is False


def test_notifyme_token_ile_kayit_ve_senkron_mesaji(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_OWNER_CHAT_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_TOKEN", "sahip-token")
    upd = _update(777001, "/notifyme sahip-token")
    asyncio.run(bot.cmd_notifyme(upd, _ctx(["sahip-token"])))
    sent = upd.message.reply_text.call_args[0][0]
    assert "Sistem Sahibi Taptaze Senkronize Edildi" in sent
    # Kayıt gerçekten yapıldı ve bir sonraki çağrıda operatör tanınır:
    saved = json.loads(owner_notify.PATH.read_text(encoding="utf-8"))
    assert int(saved["chat_id"]) == 777001
    assert bot._is_owner(777001) is True


def test_notifyme_yanlis_token_musteri_hint_verir(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_OWNER_CHAT_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_TOKEN", "sahip-token")
    upd = _update(777002, "/notifyme yanlis")
    asyncio.run(bot.cmd_notifyme(upd, _ctx(["yanlis"])))
    sent = upd.message.reply_text.call_args[0][0]
    assert "yalnızca operatör" in sent
    assert not owner_notify.PATH.exists()


def test_musteri_notifyme_cagirsa_ozet_almaz(monkeypatch):
    """Özet yalnızca operatöre gider; müşteri sohbetine pipeline verisi düşmez."""
    monkeypatch.setattr(config, "TELEGRAM_OWNER_CHAT_ID", "111")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_ID", "222")
    upd = _update(999888, "/notifyme")
    asyncio.run(bot.cmd_notifyme(upd, _ctx([])))
    sent = upd.message.reply_text.call_args[0][0]
    assert "Operatör" in sent and "SICAK TEMASLAR" not in sent


def test_iki_kaynak_birlikte_calisir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TELEGRAM_OWNER_CHAT_ID", "10")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_ID", "20")
    owner_notify.PATH.write_text(json.dumps({"chat_id": 30}), encoding="utf-8")
    assert owner_notify.load_admin_chat_ids() == {10, 20, 30}