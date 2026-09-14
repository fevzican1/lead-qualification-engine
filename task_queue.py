"""
Durumsuz Kuyruk Motoru — SQLite-backed durable task queue (WAL).

Bot ve form gönderici tek dosya/süreç içinde çalışmaz; tüm form gönderimleri
ve Telegram bildirimleri yerel SQLite'a kuyruk olarak yazılır. Form gönderen
kod kilitlense bile veritabanındaki kuyruk kaybolmaz; süreç yeniden
başladığında kaldığı görevden devam eder (lease mekanizması + WAL).

Kullanım:
    import task_queue
    task_queue.enqueue("telegram_notify", {"text": "merhaba"})
    task_queue.run_due("telegram_notify", handler)   # handler(task) -> bool
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, Callable

import config

logger = logging.getLogger(__name__)

PATH = config.ROOT / "task_queue.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_run_at REAL    NOT NULL DEFAULT 0,
    lease_until REAL    NOT NULL DEFAULT 0,
    last_error  TEXT    NOT NULL DEFAULT '',
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_kind_status ON tasks(kind, status, next_run_at);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    # Kalıcılık sözü: çökme anında bile kuyruktaki görev kaybolmaz.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def enqueue(kind: str, payload: dict[str, Any], *, max_attempts: int = 5,
            delay_s: float = 0.0) -> int:
    """Görevi kalıcı kuyruğa yaz (milisaniyelik tek commit)."""
    now = time.time()
    conn = _connect()
    try:
        _ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO tasks (kind, payload, status, max_attempts, next_run_at, created_at, updated_at)"
            " VALUES (?, ?, 'pending', ?, ?, ?, ?)",
            (kind, json.dumps(payload, ensure_ascii=False), max(1, int(max_attempts)),
             now + max(0.0, delay_s), now, now),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def claim(kind: str, *, limit: int = 1, lease_s: float = 300.0) -> list[dict[str, Any]]:
    """Vadesi gelen görevleri atomik olarak kirala (status=running)."""
    now = time.time()
    conn = _connect()
    try:
        _ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        # Önce kilitli (lease süresi dolmuş) görevleri geri al — öz-iyileşme.
        conn.execute(
            "UPDATE tasks SET status='pending', lease_until=0, updated_at=?"
            " WHERE kind=? AND status='running' AND lease_until < ?",
            (now, kind, now),
        )
        rows = conn.execute(
            "SELECT * FROM tasks WHERE kind=? AND status='pending' AND next_run_at <= ?"
            " ORDER BY id LIMIT ?",
            (kind, now, max(1, int(limit))),
        ).fetchall()
        claimed: list[dict[str, Any]] = []
        for row in rows:
            conn.execute(
                "UPDATE tasks SET status='running', attempts=attempts+1, lease_until=?, updated_at=?"
                " WHERE id=?",
                (now + lease_s, now, row["id"]),
            )
            claimed.append(dict(row) | {"attempts": int(row["attempts"]) + 1})
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ack(task_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE tasks SET status='done', lease_until=0, updated_at=? WHERE id=?",
            (time.time(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def fail(task_id: int, error: str, *, retry_in_s: float = 30.0) -> None:
    """Başarısız görevi yeniden kuyruğa al; limit dolarsa 'dead' işaretle."""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return
        now = time.time()
        if int(row["attempts"]) >= int(row["max_attempts"]):
            conn.execute(
                "UPDATE tasks SET status='dead', lease_until=0, last_error=?, updated_at=? WHERE id=?",
                (error[:500], now, task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status='pending', lease_until=0, last_error=?, next_run_at=?, updated_at=?"
                " WHERE id=?",
                (error[:500], now + max(0.0, retry_in_s), now, task_id),
            )
        conn.commit()
    finally:
        conn.close()


Handler = Callable[[dict[str, Any]], bool]


def run_due(kind: str, handler: Handler, *, limit: int = 20,
            lease_s: float = 300.0, retry_in_s: float = 30.0) -> dict[str, int]:
    """Vadesi gelen tüm görevleri işle. handler True→ack, False/raise→fail+retry."""
    done = failed = 0
    for task in claim(kind, limit=limit, lease_s=lease_s):
        try:
            if handler(task):
                ack(task["id"])
                done += 1
            else:
                fail(task["id"], "handler returned False", retry_in_s=retry_in_s)
                failed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Task %s#%s failed: %s", kind, task["id"], exc)
            fail(task["id"], str(exc), retry_in_s=retry_in_s)
            failed += 1
    return {"done": done, "failed": failed}


def stats() -> dict[str, int]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        out: dict[str, int] = {}
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
            out[str(row["status"])] = int(row["n"])
        out["total"] = sum(out.values())
        return out
    finally:
        conn.close()


def purge(*, done_after_days: float = 7.0, dead_keep: int = 500) -> int:
    """Tamamlanan eski görevleri ve en eski ölü görevleri temizle."""
    conn = _connect()
    try:
        _ensure_schema(conn)
        cutoff = time.time() - done_after_days * 86400
        cur = conn.execute("DELETE FROM tasks WHERE status='done' AND updated_at < ?", (cutoff,))
        conn.execute(
            "DELETE FROM tasks WHERE status='dead' AND id NOT IN"
            " (SELECT id FROM tasks WHERE status='dead' ORDER BY id DESC LIMIT ?)",
            (max(0, int(dead_keep)),),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()
