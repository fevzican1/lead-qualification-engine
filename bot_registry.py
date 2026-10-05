"""Dinamik Bot Havuzu + Ortak Beyin kayit defteri (saf Python, $0).

Havuzdaki TUM satis botlari ayni DB'yi (telegram_sessions.json /
leads.json / handoffs), ayni LLM prompt'larini (knowledge +
ollama_client) ve ayni satis/handoff mantigini (telegram_sales_bot +
nirvana.self_serve_close) kullanir — bu modul yalnizca botlarin
CANLILIK durumunu tutar:

  ACTIVE  -> form linki rotasyonuna girer, normal mesaj gonderir.
  PASSIVE -> FLOOD_WAIT yemis bot; cooldown_until dolana dek rotasyondan
             CIKARILIR (form linki verilmez). Sure dolunca HICBIR KOMUTA
             gerek kalmadan otomatik ACTIVE'e doner (okuma aninda reaktive).

Durum diske yazilir (nirvana/state/bot_registry.json) — restart'ta
kaybolmaz. Windows gelistirme ve Oracle Linux'ta ayni calisir.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import config

logger = logging.getLogger(__name__)

STATE_PATH = config.ROOT / "nirvana" / "state" / "bot_registry.json"

ACTIVE = "active"
PASSIVE = "passive"

_lock = threading.Lock()


def _load() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save(data: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError:
        logger.warning("bot_registry yazilamadi", exc_info=True)


def owner_for_hint(token_hint: str) -> str:
    """Token parmak izinden bot username'i (getMe ÇAĞIRMADAN — zero-touch)."""
    hint = str(token_hint or "").strip()
    if not hint:
        return ""
    with _lock:
        data = _load()
        bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
        for uname, entry in bots.items():
            if isinstance(entry, dict) and str(entry.get("token_hint") or "") == hint:
                return str(uname)
    return ""


def register_pool(usernames: list[str]) -> None:
    """Havuzu kaydet (bilinmeyen bot duser, yeni bot ACTIVE dogar)."""
    cleaned = [str(u or "").strip().lstrip("@") for u in usernames or []]
    cleaned = [u for u in cleaned if u]
    with _lock:
        data = _load()
        bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
        data["bots"] = {u: bots.get(u, {}) for u in cleaned}
        _save(data)


def mark_passive(username: str, retry_after: float) -> float:
    """Botu PASSIVE'a cek + cooldown_until damgala. Bitis epoch'u doner."""
    uname = str(username or "").strip().lstrip("@")
    try:
        wait = max(0.05, min(float(retry_after), 2 * 86400.0))
    except (TypeError, ValueError):
        wait = 60.0
    until = time.time() + wait
    with _lock:
        data = _load()
        bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
        entry = bots.get(uname, {}) if isinstance(bots.get(uname), dict) else {}
        entry.update({"status": PASSIVE, "cooldown_until": until,
                      "last_flood_wait": wait})
        bots[uname] = entry
        data["bots"] = bots
        _save(data)
    logger.warning("Bot havuzu: @%s PASSIVE (FLOOD_WAIT %.0fs)", uname, wait)
    return until


def set_token_hint(username: str, token_hint: str) -> None:
    """Token parmak izini bot kaydına yaz (token -> username çözümü için).

    owner_notify._token_owner, 429 cezasını DOĞRU bota yazabilmek için bu
    ipucunu kullanır; getMe HTTP çağrısı YAPMAZ (zero-touch)."""
    uname = str(username or "").strip().lstrip("@")
    hint = str(token_hint or "").strip()
    if not uname or not hint:
        return
    with _lock:
        data = _load()
        bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
        entry = bots.get(uname, {}) if isinstance(bots.get(uname), dict) else {}
        entry["token_hint"] = hint
        bots[uname] = entry
        data["bots"] = bots
        _save(data)


def _sweep_locked(data: dict[str, Any]) -> bool:
    """Suresi dolan pasif botlari otomatik ACTIVE'e dondur."""
    now = time.time()
    changed = False
    bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
    for uname, entry in bots.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == PASSIVE and float(entry.get("cooldown_until") or 0) <= now:
            entry["status"] = ACTIVE
            entry.pop("cooldown_until", None)
            entry["reactivated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
            changed = True
            logger.info("Bot havuzu: @%s otomatik reaktive — rotasyonda", uname)
    return changed


def is_active(username: str) -> bool:
    """Cooldown dolmussa otomatik reaktive edilir (komut gerekmez)."""
    uname = str(username or "").strip().lstrip("@")
    if not uname:
        return False
    with _lock:
        data = _load()
        if _sweep_locked(data):
            _save(data)
        bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
        if uname not in bots:
            return True  # kayitli degilse engelleme (fail-open)
        entry = bots.get(uname)
        return entry.get("status") != PASSIVE if isinstance(entry, dict) else True


def active_usernames(fallback: list[str] | None = None) -> list[str]:
    """ACTIVE botlar (sweep sonrasi). Hepsi pasifse fail-open: tum havuz doner."""
    if fallback:
        pool = [str(u or "").strip().lstrip("@") for u in fallback]
    else:
        with _lock:
            data = _load()
            bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
            pool = [u for u in bots.keys() if u]
        if not pool:
            pool = [str(u or "").strip().lstrip("@")
                    for u in config.bot_pool_usernames()]
    pool = [u for u in pool if u]
    if not pool:
        return []
    live = [u for u in pool if is_active(u)]
    if live:
        return live
    logger.warning("Bot havuzu: TUM botlar pasif — fail-open, rotasyon tum havuzdan")
    return pool


def next_active_username() -> str:
    """Form linki rotasyonu SADECE aktif botlardan (pasifler atlanir)."""
    live = active_usernames()
    if not live:
        return ""
    cursor = int(getattr(config, "_BOT_POOL_CURSOR", 0) or 0)
    name = live[cursor % len(live)]
    try:
        config._BOT_POOL_CURSOR = cursor + 1
    except Exception:  # noqa: BLE001
        pass
    return name


def pool_snapshot() -> dict[str, Any]:
    """Operator ozeti (/status, deploy ciktisi). Sweep'i de calistirir."""
    with _lock:
        data = _load()
        if _sweep_locked(data):
            _save(data)
        bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
        now = time.time()
        out: dict[str, Any] = {}
        for uname, entry in bots.items():
            entry = entry if isinstance(entry, dict) else {}
            remaining = max(0.0, float(entry.get("cooldown_until") or 0) - now)
            out[uname] = {"status": entry.get("status", ACTIVE),
                          "cooldown_remaining_s": round(remaining, 1),
                          "token_hint": str(entry.get("token_hint") or "")}
        return out
