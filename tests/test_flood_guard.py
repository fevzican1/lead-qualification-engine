"""flood_guard birim testleri — Telegram limitlerine takılma garantisi."""
import asyncio

import config
import flood_guard


def setup_function(_fn) -> None:
    flood_guard.reset()


def test_note_retry_after_kapigi_kapatir():
    flood_guard.note_retry_after(120)
    rem = flood_guard.remaining()
    assert 110 < rem <= 120, rem
    # Daha kısa ceza kapıyı kısaltmaz:
    flood_guard.note_retry_after(5)
    assert flood_guard.remaining() > 100


def test_retry_after_ust_sinira_kisilir():
    flood_guard.note_retry_after(10**9)  # saçma büyük ceza
    assert flood_guard.remaining() <= flood_guard.MAX_RETRY_AFTER


def test_acquire_ceza_uzunken_false():
    flood_guard.note_retry_after(3600)
    got = asyncio.run(flood_guard.acquire(42))
    assert got is False


def test_acquire_ceza_kısayken_bekleyip_true():
    flood_guard.note_retry_after(0.01)
    got = asyncio.run(flood_guard.acquire(42))
    assert got is True


def test_install_butun_cagrilari_kapidan_gecirir():
    calls = []

    class FakeBot:
        async def _post(self, endpoint, data, *a, **kw):
            calls.append((endpoint, data.get("chat_id")))
            return {"ok": True}

    bot = FakeBot()
    flood_guard.install(bot)
    r1 = asyncio.run(bot._post("getUpdates", {}))
    r2 = asyncio.run(bot._post("sendMessage", {"chat_id": 7, "text": "x"}))
    assert r1["ok"] and r2["ok"]
    assert ("getUpdates", None) in calls and ("sendMessage", 7) in calls
    assert bot._flood_guard_installed


def test_install_flood_uzunken_mesaji_duşurur():
    import pytest

    flood_guard.note_retry_after(3600)

    class FakeBot:
        async def _post(self, endpoint, data, *a, **kw):
            raise AssertionError("flood varken Telegram'a istek gidemez")

    bot = FakeBot()
    flood_guard.install(bot)
    import pytest as _pytest

    with _pytest.raises(flood_guard.FloodBlocked):
        asyncio.run(bot._post("sendMessage", {"chat_id": 7, "text": "x"}))


def test_install_retry_after_yakalar_ve_botu_pasife_ceker(tmp_path, monkeypatch):
    import telegram.error as terr

    import bot_registry
    import flood_guard as fg_mod

    monkeypatch.setattr(bot_registry, "STATE_PATH", tmp_path / "bot_registry.json")
    monkeypatch.setattr(fg_mod, "_STATE_PATH", tmp_path / "flood_gate.json")
    fg_mod.reset()
    bot_registry.register_pool(["Deneme_Bot"])

    class FakeBot:
        username = "Deneme_Bot"

        async def _post(self, endpoint, data, *a, **kw):
            if data.get("fail"):
                raise terr.RetryAfter(77)

    bot = FakeBot()
    flood_guard.install(bot, bot_username="Deneme_Bot")
    try:
        asyncio.run(bot._post("sendMessage", {"chat_id": 7, "text": "x", "fail": True}))
    except terr.RetryAfter:
        pass  # beklendiği gibi fırlatıyor
    rem = flood_guard.remaining()
    assert 60 < rem <= 77, rem
    # Dinamik havuz: cezayı yiyen bot PASSIVE + cooldown damgalı.
    assert bot_registry.is_active("Deneme_Bot") is False
    snap = bot_registry.pool_snapshot()
    assert snap["Deneme_Bot"]["status"] == "passive"
    assert snap["Deneme_Bot"]["cooldown_remaining_s"] > 60
    # Form rotasyonu pasif botu atlar.
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "Deneme_Bot")
    config.set_bot_pool(["Deneme_Bot", "Yedek_Bot"])
    bot_registry.register_pool(["Deneme_Bot", "Yedek_Bot"])
    flood_guard.note_retry_after(3600, bot_username="Deneme_Bot")
    assert config.next_bot_username() == "Yedek_Bot"


def test_per_chat_pacing_rezerve_eder():
    async def run():
        ok1 = await flood_guard.acquire(1)
        ok2 = await flood_guard.acquire(1)  # aynı sohbet: 1.5s bekleme
        return ok1, ok2

    import time

    t0 = time.monotonic()
    ok1, ok2 = asyncio.run(run())
    elapsed = time.monotonic() - t0
    assert ok1 and ok2
    assert elapsed >= flood_guard.CHAT_MIN_INTERVAL - 0.3
