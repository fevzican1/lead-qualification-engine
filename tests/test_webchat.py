"""Webchat regression gate — Telegram musteriye kapali, trafik WEBCHAT_PUBLIC_URL'den akar.

Kapsar: dil algisi, butce skorlamasi, 3'lu worker rotasyonu, drip zamanlari,
oturum disk senkronu, admin flood-gate (kritik kuyruga duser), chat.html ve
systemd/nginx/deploy dosyalarinin varligi.
"""
from __future__ import annotations

import json
from pathlib import Path

import webchat_core as core

ROOT = Path(__file__).resolve().parent.parent


def test_detect_lang_tr_and_en():
    assert core.detect_lang("Merhaba, fiyat nedir? Tesekkurler") == "tr"
    assert core.detect_lang("Hello, what is the price?") == "en"


def test_score_lead_vip_close_vs_educate():
    vip = core.score_lead("satin almak istiyorum, butcemiz 8000 euro, odeme linki gonderin")
    assert vip["score"] >= 70 and vip["tone"] == "vip_close"
    assert "budget:vip" in vip["signals"]
    cold = core.score_lead("merhaba")
    assert cold["tone"] == "educate" and cold["score"] < 40


def test_wants_to_buy_guards_questions():
    assert core.wants_to_buy("satın almak istiyorum, başlayalım") is True
    assert core.wants_to_buy("nasıl çalışıyor?") is False


def test_worker_rotation_covers_three_workers():
    seen = {core.worker_for(f"sid-{i}") for i in range(30)}
    assert seen == {"w1", "w2", "w3"}


def test_drip_schedule_and_texts():
    assert [s for s, _ in core.DRIP_STEPS] == ["drip_15m", "drip_2h", "drip_24h"]
    assert core.DRIP_STEPS[0][1] == 15 * 60
    assert core.DRIP_STEPS[1][1] == 2 * 3600
    assert core.DRIP_STEPS[2][1] == 24 * 3600
    assert "STOP" in core.drip_text("drip_24h", lang="tr", name="A")


def test_fallback_never_empty_and_no_free_trial():
    for tone in ("vip_close", "educate", "consult"):
        for lang in ("tr", "en"):
            txt = core.fallback_reply(lang=lang, tone=tone, name="Ayse")
            assert txt.strip()
            assert "cretsiz" not in txt and "free trial" not in txt.lower()


def test_session_disk_sync_roundtrip(tmp_path, monkeypatch):
    target = tmp_path / "webchat_sessions.json"
    monkeypatch.setattr(core, "SESSIONS_PATH", target)
    monkeypatch.setattr(core, "_loaded", False)
    core._sessions.clear()
    row = core.create_session(name="Test", lang="tr", brief="b")
    assert target.exists()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert row["sid"] in saved
    core.append_history(row["sid"], "user", "merhaba")
    core.push_inbox(row["sid"], "drip", kind="drip_15m")
    assert core.pop_inbox(row["sid"])
    # Yeniden yukleme RAM'den degil diskten gelir
    core._sessions.clear()
    monkeypatch.setattr(core, "_loaded", False)
    assert core.get_session(row["sid"])["name"] == "Test"


def test_admin_notify_without_target_is_safe():
    assert core.notify_admin("ping", high_priority=True) in (True, False)


def test_customer_facing_files_exist():
    assert (ROOT / "templates" / "chat.html").exists()
    html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    assert "/ws/" in html and "audio" in html.lower()
    unit = (ROOT / "oracle" / "nirvana-webchat.service").read_text(encoding="utf-8")
    assert "Restart=always" in unit and "WatchdogSec" in unit and "webchat_server.py" in unit
    deploy = (ROOT / "oracle" / "deploy_webchat.sh").read_text(encoding="utf-8")
    assert "nirvana-webchat" in deploy and "nginx" in deploy.lower()


# --- MUSTERI HATTI MIGRATION: Telegram yerine web sohbet ---------------------


def test_config_customer_link_prefers_webchat(monkeypatch):
    import config

    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", "http://1.2.3.4")
    link = config.customer_chat_link("dsABC")
    assert link == "http://1.2.3.4/chat?sid=dsABC"
    assert config.require_live_webchat_link("dsABC") == link
    assert config.require_live_customer_link("dsABC") == link
    assert config.webchat_customer_only() is True


def test_customer_link_fail_closed_without_any_channel(monkeypatch):
    import config

    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "")
    try:
        config.require_live_customer_link()
    except RuntimeError:
        pass
    else:  # pragma: no cover - kapı açık kalırsa tıklanamayan form gider
        raise AssertionError("link yokken form gonderimi acik (fail-closed ihlali)")
    # Gecis donemi: webchat yoksa t.me hala gecerli musteri linki.
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "B2B_SalesAssistant_Bot")
    assert config.require_live_customer_link() .startswith("https://t.me/")


def test_language_auditor_keeps_webchat_link(monkeypatch):
    """Dil Bekçisi web sohbet linkini 'uydurma URL' sayip SILEMEZ (donusum kapisi)."""
    import config

    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", "http://1.2.3.4")
    from nirvana import language_auditor as la

    text = ("Merhaba, akış şeması hazır: http://1.2.3.4/chat?sid=dsABC "
            "Bağlantı tarayıcıda açılır. STOP ile çıkabilirsiniz.")
    fixed, _issues = la.audit(text, turkish=True, limit=4000)
    assert "http://1.2.3.4/chat?sid=dsABC" in fixed


def test_form_copy_points_to_webchat_not_telegram(monkeypatch):
    import config
    import telegram_handoff as handoff

    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", "http://1.2.3.4")
    link = config.customer_chat_link("dsABC")
    for turkish in (True, False):
        _subject, body = handoff.form_copy(
            host="shop.example", hints=["woocommerce"], link=link, turkish=turkish,
        )
        assert link in body
        assert "resmi t.me önizlemesi" not in body


def test_telegram_customer_entry_closed_when_webchat_live(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import config
    import telegram_sales_bot as bot

    reply = AsyncMock()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=4242, type="private"),
        effective_user=SimpleNamespace(id=4242, language_code="tr"),
        message=SimpleNamespace(text="merhaba", reply_text=reply),
    )
    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", "http://1.2.3.4")
    assert asyncio.run(bot._redirect_customer_to_webchat(update, 4242)) is True
    assert "http://1.2.3.4/chat" in reply.call_args.args[0]
    # WEBCHAT_PUBLIC_URL bos ise eski Telegram akisi korunur (gecis donemi).
    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", "")
    assert asyncio.run(bot._redirect_customer_to_webchat(update, 4242)) is False


def test_webchat_server_is_core_backed():
    """Canli sunucu ile test edilen core ayrisamaz (tek dogruluk kaynagi)."""
    import webchat_core as core
    import webchat_server as server

    for name in ("detect_lang", "score_lead", "wants_to_buy", "notify_admin",
                 "worker_for", "get_session", "drip_text", "create_session",
                 "all_sessions", "push_inbox", "pop_inbox", "append_history"):
        assert getattr(server, name) is getattr(core, name), name


def test_form_token_seeds_webchat_session(monkeypatch):
    """Form linkindeki ds-token oturumu acar ve sirket/teshis bilgisini tasir."""
    import telegram_handoff

    monkeypatch.setattr(telegram_handoff, "lookup", lambda token: {
        "session_token": token, "company": "Shop Example", "host": "shop.example",
        "turkish": True, "diagnostics": {"detected_issues": ["REST webhook retry delay"]},
    })
    monkeypatch.setattr(telegram_handoff, "brief_block", lambda row: "Şirket: Shop Example\nBulgu: REST webhook retry delay")
    core._sessions.clear()
    monkeypatch.setattr(core, "_loaded", False)
    row = core.ensure_session("dsABC123")
    assert row["sid"] == "dsABC123"
    assert row["name"] == "Shop Example" and row["lang"] == "tr"
    assert "REST webhook" in row["brief"]
    # Ayni token tekrar gelirse YENI oturum acilmaz (oturum kimligi sabit).
    assert core.ensure_session("dsABC123")["sid"] == "dsABC123"


def test_greeting_seeded_mentions_form_context():
    tr = core.greeting(name="Shop Example", lang="tr", seeded=True)
    assert "Merhaba" in tr and "Shop" in tr and "form" in tr.lower()
    en = core.greeting(name="Shop Example", lang="en", seeded=True)
    assert "Hello" in en and "Shop" in en and "form" in en.lower()
    plain = core.greeting(name="", lang="tr", seeded=False)
    assert plain.strip() and "Merhaba" in plain


def test_deploy_assets_cover_restart_health_and_git_push():
    deploy = (ROOT / "oracle" / "deploy_webchat.sh").read_text(encoding="utf-8")
    assert "systemctl restart" in deploy
    assert "/health" in deploy and "git push" in deploy
    assert "iptables" in deploy and "Security List" in deploy
    nginx = (ROOT / "oracle" / "webchat-nginx.conf").read_text(encoding="utf-8")
    assert "proxy_set_header Upgrade $http_upgrade" in nginx
    assert "127.0.0.1:8765" in nginx

