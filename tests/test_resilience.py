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
