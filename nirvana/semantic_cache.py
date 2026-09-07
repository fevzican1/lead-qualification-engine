"""Nirvana yanıt önbelleği [Oracle VM, hafif].

SQLite tabanlı, FTS5'siz basit anahtar-değer semantik yaklaşık önbellek:
normalize edilmiş soru + model + dil → yanıt. Ollama çağrılarını azaltarak
Oracle CPU kotasını korur. TTL ve satır sınırı vardır; hata durumunda
sessizce devre dışı kalır (asla botu bloklamaz).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

import config

DB_PATH: Any = config.ROOT / "reply_cache.db"
TTL_SECONDS = 24 * 3600
MAX_ROWS = 500


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def cache_key(messages: list[dict[str, Any]]) -> str:
    """Yalnız system+son-user çifti için deterministik anahtar."""
    sys_text = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    user_text = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    raw = json.dumps({"s": _norm(sys_text)[:4000], "u": _norm(user_text)[:2000]},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def get(messages: list[dict[str, Any]], *, ttl: int | None = None) -> str | None:
    ttl = TTL_SECONDS if ttl is None else ttl
    try:
        key = cache_key(messages)
        with sqlite3.connect(DB_PATH, timeout=3) as con:
            row = con.execute("SELECT reply, at FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        reply, at = row
        if time.time() - float(at) > ttl:
            return None
        return str(reply)
    except Exception:
        return None


def put(messages: list[dict[str, Any]], reply: str) -> bool:
    try:
        key = cache_key(messages)
        now = time.time()
        with sqlite3.connect(DB_PATH, timeout=3) as con:
            con.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, reply TEXT, at REAL)")
            con.execute("INSERT OR REPLACE INTO cache (key, reply, at) VALUES (?,?,?)", (key, reply, now))
            con.execute("DELETE FROM cache WHERE key NOT IN "
                        "(SELECT key FROM cache ORDER BY at DESC LIMIT ?)", (MAX_ROWS,))
            con.commit()
        return True
    except Exception:
        return False
