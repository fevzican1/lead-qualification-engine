"""Lane AX - manifest_state: dinamik manifest durumu (kalici TRUE/FALSE).

- Baslangic TRUE. Bekciler (sentinel/job_watchdog/quality_audit) yalnizca
  MANTIKSAL kirilma bulursa set_false() cagirir -> surec guvenli moda alinir,
  mal sahibine nokta atisi Telegram gider.
- set_false() bir kez ateslenir (cooldown 6 saat; ayni katmandan spam yok).
- clear() yalnizca insan duzeltmesi sonrasi (dagitim onayi) cagrilir.
- manifest_check.py bu dosyayi okur: FALSE kaydi + sogumamissa deploy FALSE.
"""
from __future__ import annotations

import json
import time
from typing import Any

from nirvana.registry import state_path

STATE_NAME = "manifest_state.json"
FALSE_COOLDOWN_S = 6 * 3600.0


def _load() -> dict[str, Any]:
    try:
        data = json.loads(state_path(STATE_NAME).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"status": "TRUE", "history": []}


def _save(state: dict[str, Any]) -> None:
    try:
        tmp = state_path(STATE_NAME).with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(state_path(STATE_NAME))
    except OSError:
        pass


def is_false() -> dict[str, Any] | None:
    """Sogumamis FALSE kaydi varsa dondur (deploy kapisi icin)."""
    st = _load()
    if st.get("status") != "FALSE":
        return None
    at = float(st.get("at") or 0)
    if (time.time() - at) >= FALSE_COOLDOWN_S:
        return None  # sogudu: bekciler yeniden degerlendirir
    return {"layer": st.get("layer"), "reason": st.get("reason"), "at": at}


def set_false(layer: str, reason: str) -> bool:
    """FALSE'a cek + Telegram'a nokta atisi bildir. Cooldown icindeyse False doner."""
    st = _load()
    now = time.time()
    if st.get("status") == "FALSE" and (now - float(st.get("at") or 0)) < FALSE_COOLDOWN_S:
        return False
    st["status"] = "FALSE"
    st["layer"] = str(layer)
    st["reason"] = str(reason)[:400]
    st["at"] = now
    hist = st.get("history") if isinstance(st.get("history"), list) else []
    hist.append({"layer": str(layer), "reason": str(reason)[:200], "at": now})
    st["history"] = hist[-50:]
    _save(st)
    try:
        import owner_notify  # type: ignore
        owner_notify.send(
            "[DevSolve Ops] MANIFEST FALSE — %s katmani guvenli moda alindi.\n"
            "Nokta atisi: %s\n"
            "Duzeltme yapilmadan dagitim kilitli." % (layer, str(reason)[:300]),
            high_priority=True)
    except Exception:
        pass
    return True


def clear(note: str = "") -> None:
    """Insan duzeltmesi sonrasi TRUE'ya dondur."""
    st = _load()
    st["status"] = "TRUE"
    st["cleared_at"] = time.time()
    st["clear_note"] = str(note)[:200]
    _save(st)


def self_test() -> int:
    assert is_false() is None
    assert set_false("test_layer", "test nedeni") is True
    row = is_false()
    assert row and row["layer"] == "test_layer", row
    assert set_false("test_layer", "spam denemesi") is False
    clear("test temizligi")
    assert is_false() is None
    print("MANIFEST_STATE SELF-TEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
