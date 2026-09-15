"""
Dayanıklılık katmanı testleri: task_queue, circuit_breaker, heartbeat,
owner_notify kuyruk entegrasyonu (rapor §1 ve §3 — sıfır mesaj kaybı).
"""

from __future__ import annotations

import json
import time

import pytest


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    import circuit_breaker
    import heartbeat
    import task_queue

    monkeypatch.setattr(task_queue, "PATH", tmp_path / "task_queue.db")
    monkeypatch.setattr(circuit_breaker, "STATE_PATH", tmp_path / "breakers.json")
    monkeypatch.setattr(heartbeat, "STATE_DIR", tmp_path)
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    return tmp_path


def test_queue_survives_process_restart(isolated_state):
    import task_queue

    task_id = task_queue.enqueue("telegram_notify", {"text": "selam", "chat_id": 1})
    assert task_id > 0
    claimed = task_queue.claim("telegram_notify")
    assert len(claimed) == 1
    # Süreç burada çökse bile kayıt DB'de; yeni "süreç" kaldığı yerden alır.
    task_queue.ack(claimed[0]["id"])
    assert task_queue.stats()["done"] == 1
    assert task_queue.claim("telegram_notify") == []


def test_queue_retry_then_dead(isolated_state):
    import task_queue

    task_queue.enqueue("form_submit", {"url": "https://x.com"}, max_attempts=2)
    stats = task_queue.run_due("form_submit", lambda task: False, retry_in_s=0.0)
    assert stats["failed"] == 1
    time.sleep(0.05)
    task_queue.run_due("form_submit", lambda task: False, retry_in_s=0.0)
    time.sleep(0.05)
    final = task_queue.run_due("form_submit", lambda task: False, retry_in_s=0.0)
    assert final["done"] == 0
    assert task_queue.stats().get("dead") == 1


def test_queue_lease_reclaim(isolated_state):
    import task_queue

    task_queue.enqueue("form_submit", {"url": "https://y.com"})
    claimed = task_queue.claim("form_submit", lease_s=0.05)
    assert len(claimed) == 1
    time.sleep(0.06)  # lease süresi doldu — sürekli kiralanır (öz-iyileşme)
    reclaimed = task_queue.claim("form_submit", lease_s=300)
    assert len(reclaimed) == 1


def test_circuit_breaker_opens_after_three(isolated_state):
    import circuit_breaker

    for _ in range(2):
        circuit_breaker.record("telegram_notify", False, threshold=3, cooldown_s=30)
    assert circuit_breaker.allow("telegram_notify")  # 2 hata: henüz kapalı
    circuit_breaker.record("telegram_notify", False, threshold=3, cooldown_s=30)
    assert not circuit_breaker.allow("telegram_notify")  # 3 hata: şalter AÇIK
    circuit_breaker.record("telegram_notify", True, threshold=3, cooldown_s=30)
    assert circuit_breaker.allow("telegram_notify")  # başarı: şalter kapandı


def test_circuit_breaker_half_open_after_cooldown(isolated_state):
    import circuit_breaker

    for _ in range(3):
        circuit_breaker.record("telegram_notify", False, threshold=3, cooldown_s=0.05)
    assert not circuit_breaker.allow("telegram_notify", cooldown_s=0.05)
    time.sleep(0.07)
    assert circuit_breaker.allow("telegram_notify", cooldown_s=0.05)  # yarı-açık prob


def test_heartbeat_pulse_and_health(isolated_state):
    import heartbeat

    heartbeat.pulse("salesbot")
    assert heartbeat.healthy("salesbot", max_age_s=10)
    age = heartbeat.age_seconds("salesbot")
    assert age is not None and age < 2
    time.sleep(0.05)
    assert not heartbeat.healthy("salesbot", max_age_s=0.01)


def test_owner_notify_failure_queues_and_replays(isolated_state, monkeypatch):
    import owner_notify
    import task_queue

    monkeypatch.setattr(owner_notify, "load_notify_chat_id", lambda: 42)

    calls = {"n": 0}

    def _fail_post(target, body, *, silent):
        calls["n"] += 1
        raise RuntimeError("api down")

    monkeypatch.setattr(owner_notify, "_post_message", _fail_post)
    assert owner_notify.send("test bildirimi", chat_id=42) is False
    assert calls["n"] == 3
    # Bildirim kaybolmadı — kuyrukta bekliyor:
    stats = task_queue.stats()
    assert stats.get("pending") == 1
    monkeypatch.setattr(owner_notify, "_post_message", lambda t, b, *, silent: True)
    import circuit_breaker
    circuit_breaker.reset("telegram_notify")  # soğuma (60s) sonrası hat döndü
    conn = task_queue._connect()  # 60 sn gecikmeli vadeyi, hat dönme anına çek
    conn.execute("UPDATE tasks SET next_run_at=0")
    conn.commit()
    conn.close()
    relay = task_queue.run_due("telegram_notify", owner_notify.deliver_queued_notify)
    assert relay == {"done": 1, "failed": 0}


class _Resp:
    """Telegram yanıtı taklidi — sadece .json() kullanılır."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_mask_token_sizintisini_engeller(monkeypatch):
    """Telegram hata metni URL içinde token taşır — log'a sızmamalı."""
    import config
    import owner_notify

    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_BOT_TOKEN", "123:AA-notify-secret")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "456:BB-sales-secret")
    leak = ("HTTPStatusError for url "
            "https://api.telegram.org/bot123:AA-notify-secret/sendMessage")
    masked = owner_notify._mask(leak)
    assert "123:AA-notify-secret" not in masked
    assert "456:BB-sales-secret" not in masked
    assert "***" in masked


def test_probe_delivery_hat_kapaliyken_false(isolated_state, monkeypatch):
    """Token/hedef 'ayarlı' görünse bile hat KAPALI ise can_deliver=False döner.

    Canlı vaka: notify token iptal (404) + owner chat bir bot hesabı (403).
    Eski kod yalnızca \"ayarlı mı\" baktığı için deploy çıktısı 'kanal: True'
    diyordu; lead bildirimleri ise sessizce kayboluyordu.
    """
    import config
    import owner_notify

    monkeypatch.setattr(owner_notify, "PATH", isolated_state / "owner.json")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_BOT_TOKEN", "gecersiz-notify-token")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "gecerli-satis-token")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_CHAT_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_OWNER_CHAT_ID", "777")
    # notify token iptal edilmiş (Telegram 404), satış tokeni geçerli:
    monkeypatch.setattr(owner_notify, "_token_ok", lambda tok: tok == "gecerli-satis-token")

    def _deny(url, **_kw):
        assert "getChat" in url  # mesaj GÖNDERİLMEZ, yalnızca sorgu
        return _Resp({"ok": False,
                      "description": "Forbidden: the bot can't send messages to the bot"})

    monkeypatch.setattr(owner_notify.httpx, "post", _deny)
    probe = owner_notify.probe_delivery()
    assert probe["notify_token_valid"] is False
    assert probe["sales_token_valid"] is True
    assert probe["token_used"] == "sales"
    assert probe["target_reachable"] is False
    assert probe["can_deliver"] is False  # \"kanal: True\" yanılsaması yok
    assert "bot" in probe["target_detail"]


def test_probe_delivery_hat_acikken_true(isolated_state, monkeypatch):
    import config
    import owner_notify

    monkeypatch.setattr(owner_notify, "PATH", isolated_state / "owner.json")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_BOT_TOKEN", "gecerli-notify-token")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY_CHAT_ID", "555")
    monkeypatch.setattr(config, "TELEGRAM_OWNER_CHAT_ID", "")
    monkeypatch.setattr(owner_notify, "_token_ok", lambda tok: bool(tok))
    monkeypatch.setattr(owner_notify.httpx, "post",
                        lambda url, **_kw: _Resp({"ok": True, "result": {"type": "private"}}))

    probe = owner_notify.probe_delivery()
    assert probe["notify_token_valid"] is True
    assert probe["token_used"] == "notify"
    assert probe["target_chat_id"] == 555
    assert probe["can_deliver"] is True


def test_lead_digest_satirlari_yapismaz_ve_tekrarlanmaz(isolated_state, monkeypatch):
    """Özet mesajı: yapışan satır (implicit concat) ve tekrar eden blok olmamalı."""
    import config
    import domain_store
    import enterprise_apply
    import enterprise_targets
    import knowledge
    import owner_notify
    import telegram_sessions

    rows = [{"status": "submitted"}, {"status": "pending"}]
    leads = isolated_state / "leads.json"
    leads.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(config, "LEADS_PATH", leads)
    monkeypatch.setattr(config, "OLLAMA_MODEL", "test-model")
    monkeypatch.setattr(domain_store, "queue_depth", lambda: 7)
    monkeypatch.setattr(domain_store, "ready_pool_size", lambda *a, **k: 3)
    monkeypatch.setattr(domain_store, "http_budget_label", lambda: "gün 1/2 saat 1/2")
    monkeypatch.setattr(enterprise_targets, "load_all", lambda *a, **k: [])
    monkeypatch.setattr(enterprise_apply, "enterprise_counts", lambda *a, **k: (0, 0))
    monkeypatch.setattr(telegram_sessions, "_load", lambda: {})

    lines = owner_notify.lead_digest().split("\n")
    body = [ln for ln in lines if ln]

    # 1) Hiçbir satır ikinci kez yazılmaz (Funnel/Kuyruk bloğu iki kez yok).
    assert len(body) == len(set(body)), f"özet tekrar eden satır içeriyor: {body}"

    # 2) 'Model:' satırı 'Kuyruk:' satırına yapışmamalı (implicit concatenation).
    assert sum(ln.startswith("Model: ") for ln in lines) == 1
    assert not any("Kuyruk:" in ln and "Model:" in ln for ln in lines), f"yapışmış: {lines}"

    # 3) Tek Funnel + tek Kuyruk satırı, bilgi kaybı olmadan.
    funnel_lines = [ln for ln in lines if ln.startswith("Funnel (")]
    kuyruk_lines = [ln for ln in lines if ln.startswith("Kuyruk: ")]
    assert len(funnel_lines) == 1
    assert len(kuyruk_lines) == 1
    assert kuyruk_lines[0].startswith("Kuyruk: 7/")
    assert "(max " in kuyruk_lines[0]
    assert "hazır 3" in kuyruk_lines[0]
    assert "HTTP gün 1/2 saat 1/2" in kuyruk_lines[0]

    # 4) Rakamlar gerçek veriden gelir (uydurma/tekrar yok).
    expected_submitted = sum(1 for r in rows
                             if r["status"] in knowledge.CONFIRMED_SUBMIT_STATUSES)
    assert "Toplam lead: 2" in lines
    assert f"Form gönderildi: {expected_submitted}" in lines
    assert "Model: test-model" in lines
    assert any(ln.startswith("Durumlar: ") for ln in lines)
