"""İnsan devri (Human-in-the-Loop) buton ve devralma testleri.

Rapor kriteri: canlı müşteri talebinde bildirim altında
[ Sohbete Bağlan / Reply] Inline butonu olmalı; patron bastığı anda otonom
yanıtlayıcı duraklamalı ve patronun mesajı doğrudan müşteriye gitmeli.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import optout
import owner_notify
import telegram_sales_bot as bot
import telegram_sessions as sessions


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(optout, "PATH", tmp_path / "optouts.json")
    monkeypatch.setattr(sessions, "PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(owner_notify, "PATH", tmp_path / "owner.json")
    monkeypatch.setattr(config, "TELEGRAM_OWNER_CHAT_ID", "900900")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "sales-token")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_CHAT_ID", "")
    bot._histories.clear()
    bot._briefs.clear()
    bot._proof_tasks.clear()
    return tmp_path


# --- Buton ve rota ----------------------------------------------------------

def test_buton_metni_ve_callback_verisi():
    kb = owner_notify.handoff_keyboard(123456)
    rows = kb["inline_keyboard"]
    assert len(rows) == 1 and len(rows[0]) == 1
    assert rows[0][0]["text"] == owner_notify.HANDOFF_BUTTON_TEXT
    assert "Sohbete Bağlan" in rows[0][0]["text"]
    assert rows[0][0]["callback_data"] == "handoff:123456"


def test_rota_satis_sohbetini_secer():
    owner_notify.save_chat_id(4242)          # /notifyme ile kayıtlı satış sohbeti
    route = owner_notify.handoff_route()
    assert route["chat_id"] == 4242 and route["button"] is True
    assert route["token_kind"] == "sales"


def test_rota_kayitli_sohbet_yoksa_butonsuz_ve_guvenli(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_BOT_TOKEN", "notify-token")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_CHAT_ID", "555")
    route = owner_notify.handoff_route()
    assert route["chat_id"] == 555 and route["button"] is False


def test_send_handoff_alert_butonu_gonderir(monkeypatch):
    owner_notify.save_chat_id(4242)
    seen: dict = {}

    def fake_critical(target, body, *, reply_markup=None):
        seen.update({"target": target, "body": body,
                     "reply_markup": reply_markup})
        return True

    # Kritik hat (flood harici): butonlu bildirim buradan gider.
    monkeypatch.setattr(owner_notify, "send_critical", fake_critical)
    ok = owner_notify.send_handoff_alert("CANLI MUSTERI TALEBI",
                                         target_chat_id=777)
    assert ok is True
    assert seen["target"] == 4242
    assert seen["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "handoff:777"


def test_send_handoff_alert_hatasi_kuyruga_duser_ve_false_doner(monkeypatch):
    # Kritik hat bile ulasamazsa haber KAYBOLMAZ: kuyruga yazilir, False doner.
    owner_notify.save_chat_id(4242)
    monkeypatch.setattr(owner_notify, "send_critical", lambda *a, **k: False)
    assert owner_notify.send_handoff_alert("acil", target_chat_id=777) is False


def test_send_handoff_alert_hedef_yoksa_false(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_BOT_TOKEN", "")
    assert owner_notify.send_handoff_alert("acil", target_chat_id=777) is False


# --- Callback işleyici ------------------------------------------------------

def _callback_update(owner_chat_id: int, data: str):
    query = SimpleNamespace(data=data, answer=AsyncMock(),
                            edit_message_reply_markup=AsyncMock())
    return SimpleNamespace(effective_chat=SimpleNamespace(id=owner_chat_id, type="private"),
                           effective_user=SimpleNamespace(id=owner_chat_id),
                           callback_query=query, message=None)


def test_callback_verisi_ayristirilir():
    assert bot._handoff_target_from_callback("handoff:555") == 555
    assert bot._handoff_target_from_callback("handoff:-100") == -100
    assert bot._handoff_target_from_callback("handoff:abc") is None
    assert bot._handoff_target_from_callback("other:5") is None
    assert bot._handoff_target_from_callback("") is None


def test_operator_butona_basinca_sohbet_baglanir():
    upd = _callback_update(900900, "handoff:555")
    ctx = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(bot.on_handoff_callback(upd, ctx))
    assert sessions.armed_target(900900) == 555
    assert sessions.is_takeover(555) is True
    text = ctx.bot.send_message.call_args.kwargs["text"]
    assert "Otonom yanıtlayıcı DURDU" in text and "/disarm" in text


def test_musteri_butona_basinca_hicbir_yetki_acilmaz():
    upd = _callback_update(555777, "handoff:555")
    ctx = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(bot.on_handoff_callback(upd, ctx))
    assert sessions.armed_target(555777) is None
    assert sessions.is_takeover(555) is False
    ctx.bot.send_message.assert_not_awaited()


# --- Devralma sırasında mesaj iletimi ---------------------------------------

def test_bagli_patronun_mesaji_musteriye_gider(monkeypatch):
    sessions.arm_reply(900900, 555)
    sent: dict = {}

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.update({"chat_id": chat_id, "text": text})

    monkeypatch.setattr(bot.flood_guard, "acquire", AsyncMock(return_value=True))
    monkeypatch.setattr(bot, "_app_for_chat", lambda target, default=None: None)
    upd = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    ctx = SimpleNamespace(application=SimpleNamespace(), bot=FakeBot())
    handled = asyncio.run(bot._relay_owner_reply(upd, ctx, 900900, "Merhaba, ben kurucu."))
    assert handled is True
    assert sent == {"chat_id": 555, "text": "Merhaba, ben kurucu."}
    upd.message.reply_text.assert_not_awaited()


def test_bagli_degilse_mesaj_iletilmez():
    upd = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    ctx = SimpleNamespace(application=SimpleNamespace(), bot=SimpleNamespace())
    assert asyncio.run(bot._relay_owner_reply(upd, ctx, 900900, "alakasız")) is False


def test_disarm_devri_kapatir():
    sessions.arm_reply(900900, 555)
    upd = SimpleNamespace(effective_chat=SimpleNamespace(id=900900, type="private"),
                          effective_user=SimpleNamespace(id=900900),
                          message=SimpleNamespace(text="/disarm", reply_text=AsyncMock()))
    asyncio.run(bot.cmd_disarm(upd, SimpleNamespace(args=[], bot=None)))
    assert sessions.armed_target(900900) is None
    assert sessions.is_takeover(555) is False
    assert "Devir kapandı" in upd.message.reply_text.call_args[0][0]


# --- Uçtan uca: müşteri "patronunla görüşmek istiyorum" yazar ----------------

def test_handoff_talebi_butonlu_bildirim_tetikler(monkeypatch):
    alerts: list[dict] = []

    def fake_alert(text, *, target_chat_id, **kw):
        alerts.append({"text": text, "target": target_chat_id})
        return True

    monkeypatch.setattr(owner_notify, "send_handoff_alert", fake_alert)
    monkeypatch.setattr(bot, "_is_owner", lambda cid: False)
    reply = AsyncMock()
    upd = SimpleNamespace(
        effective_chat=SimpleNamespace(id=555, type="private"),
        effective_user=SimpleNamespace(id=555, language_code="tr", username="musteri"),
        message=SimpleNamespace(text="Patronunla görüşmek istiyorum", reply_text=reply),
        callback_query=None,
    )
    asyncio.run(bot.on_text(upd, SimpleNamespace(bot=None, application=None)))
    assert alerts and alerts[0]["target"] == 555
    assert "CANLI MÜŞTERİ TALEBİ" in alerts[0]["text"]
    assert sessions.is_takeover(555) is True
    assert reply.await_count >= 1