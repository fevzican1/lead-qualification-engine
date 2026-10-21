"""Lane AX - error_classifier: gecici ariza vs mantiksal/kodsal kirilma ayirici.

- GECICI (self-heal cozer, KESINTI mesaji YOK, manifest TRUE kalir): timeout,
  port dusmesi, WAL kilidi, ag kopmasi, Chromium asilmasi, servis olmesi.
- MANTIKSAL (guvenli mod: manifest FALSE + Telegram + durdurma): ImportError,
  NameError, SyntaxError, AttributeError, ValueError (config/kod), ya da ayni
  imzanin STUCK_AFTER_HEALS kez tekrarlanmasi (onarim cozemedi -> kod suphesi).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from nirvana.registry import state_path

STATE_NAME = "error_classifier.json"
STUCK_AFTER_HEALS = 3
COOLDOWN_S = 3600.0

_TRANSIENT_RE = re.compile(
    r"timeout|timed out|TimeoutExpired|deadline|temporar|transient|"
    r"connection (reset|refused|aborted|closed)|network|no route to host|dns|"
    r"socket|broken pipe|http 5\d\d|502|503|504|port.*(in use|busy|locked)|"
    r"address already in use|wal|database is locked|memoryerror|out of memory|"
    r"chromium.*(hung|stuck|kill|crash)|playwright.*timeout|target (closed|crashed)|"
    r"browser.*(closed|crashed)|websocket|429|flood|retry|stale|lock|busy|"
    r"service.*(down|dead|failed|inactive)|watchdog|heartbeat|kesinti",
    re.I)
_LOGIC_RE = re.compile(
    r"ModuleNotFoundError|ImportError|cannot import|No module named|SyntaxError|"
    r"IndentationError|NameError|is not defined|AttributeError|has no attribute|"
    r"TypeError|KeyError|IndexError|AssertionError|ValueError|ZeroDivisionError|"
    r"RecursionError|NotImplementedError|config.*(eksik|bos|hatali|invalid)|"
    r"ENV.*(eksik|bos)|SENDER_.*bos|WEBCHAT.*bos|retainer.*degil|"
    r"Payoneer.*(yer tutucu|placeholder)|placeholder|BURAYA_YENI",
    re.I)


def signature(exc_text: str) -> str:
    txt = (exc_text or "")[:2000]
    txt = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?", "", txt)
    txt = re.sub(r"0x[0-9a-fA-F]+", "", txt)
    txt = re.sub(r"line \d+", "line N", txt)
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    core = " | ".join(lines[:3])[:500]
    return hashlib.sha256(core.encode("utf-8", "ignore")).hexdigest()[:16]


def _load() -> dict[str, Any]:
    try:
        data = json.loads(state_path(STATE_NAME).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"seen": {}}


def _save(state: dict[str, Any]) -> None:
    try:
        tmp = state_path(STATE_NAME).with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(state_path(STATE_NAME))
    except OSError:
        pass


def classify(exc_text: str, *, exit_code: int | None = None) -> dict[str, Any]:
    """Donus: {kind: transient|logic, reason, sig, count, notify_now}.

    KRITIK SIRALAMA: gecici (transient) sinyali ONCE denetlenir. Aksi halde
    "port in use" / "timeout" gibi kendi kendini onaran bir arıza 3 kez
    tekrarlanınca STUCK sayacına takılıp MANTIKSAL sayılır ve yanlışlıkla
    kesinti (KESINTI) mesaji atılır — otonom onarim hâlâ saglamken.
    Yalnizca HICBIR gecici ipucu tasimayan (bilinmeyen) hatalar sayaca girer.
    """
    txt = exc_text or ""
    sig = signature(txt)
    now = time.time()
    state = _load()
    seen = state.setdefault("seen", {})
    row = seen.get(sig) if isinstance(seen.get(sig), dict) else {}
    count = int(row.get("count") or 0) + 1
    last = float(row.get("last") or 0)
    seen[sig] = {"count": count, "last": now, "sample": txt[:300]}
    _save(state)
    m = _LOGIC_RE.search(txt)
    if m:
        return {"kind": "logic",
                "reason": "kodsal/mantiksal kirilma: %s" % m.group(0)[:80],
                "sig": sig, "count": count, "notify_now": (now - last) >= COOLDOWN_S}
    # GECICI sinyali: kendi kendini onaran ariza — asla mantiksal sayilmaz.
    if exit_code == 124 or _TRANSIENT_RE.search(txt) or not txt.strip():
        return {"kind": "transient", "reason": "gecici ariza (self-heal devrede)",
                "sig": sig, "count": count, "notify_now": False}
    # Bilinmeyen hata: yalnizca tekrarlanmaya devam ederse kod suphesi.
    if count >= STUCK_AFTER_HEALS:
        return {"kind": "logic",
                "reason": "tekrarlayan ariza x%d (self-heal cozemedi -> kod suphesi)" % count,
                "sig": sig, "count": count, "notify_now": (now - last) >= COOLDOWN_S}
    return {"kind": "transient", "reason": "ilk gorulen genel hata (gozlem altinda)",
            "sig": sig, "count": count, "notify_now": False}


def reset(sig: str) -> None:
    state = _load()
    if isinstance(state.get("seen"), dict) and sig in state["seen"]:
        del state["seen"][sig]
        _save(state)


def self_test() -> int:
    r1 = classify("pipeline.py exceeded 600s - killed hung Chromium", exit_code=124)
    assert r1["kind"] == "transient", r1
    r2 = classify("ModuleNotFoundError: No module named 'foo_xyz_123'")
    assert r2["kind"] == "logic", r2
    sig = signature("gizemli hata test tekrar 12345")
    for _ in range(STUCK_AFTER_HEALS - 1):
        assert classify("gizemli hata test tekrar 12345", exit_code=1)["kind"] == "transient"
    rr = classify("gizemli hata test tekrar 12345", exit_code=1)
    assert rr["kind"] == "logic" and rr["count"] >= STUCK_AFTER_HEALS, rr
    reset(sig)
    print("ERROR_CLASSIFIER SELF-TEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
