"""Whitelabel segment kontenjan defteri: territory/nis munhasirligi kitligi.

Kural: her (segment) basina en fazla MAX_PARTNERS ortak (varsayilan 3).
Kalan kontenjan demo sayfasinda + chat baglaminda gosterilir:
  "Bu segmente ... ortak aliniyor — kalan kontenjan: N".

Durum: nirvana/state/agency_slots.json — {segment_slug: {"claimed": int, "names": [...]}}.
Tum fonksiyonlar hata-izole + idempotent.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from nirvana.registry import state_path as _state_path
except Exception:  # pragma: no cover
    _state_path = None  # type: ignore

MAX_PARTNERS = 3
_SLOTS_NAME = "agency_slots.json"


def _path() -> Path:
    try:
        import config as _cfg  # type: ignore
        root = Path(str(_cfg.ROOT))
        p = root / "nirvana" / "state" / _SLOTS_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        pass
    if _state_path is not None:
        try:
            return _state_path(_SLOTS_NAME)
        except Exception:
            pass
    p = Path(__file__).resolve().parents[1] / "nirvana" / "state" / _SLOTS_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    try:
        raw = _path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        p = _path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
    except Exception:
        logger.debug("slots kayit atlandi", exc_info=True)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def segment_of(agency: str = "", *, clients: Any = None) -> str:
    """Ajans adindan kaba segment slug uretir (territory/nis anahtari)."""
    text = _clean(agency).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "genel"
    return text


def max_partners() -> int:
    try:
        raw = (os.getenv("WHITELABEL_MAX_PARTNERS", "") or "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
    except Exception:
        pass
    return MAX_PARTNERS


def slots_left(segment: str = "") -> int:
    """Segmente kalan kontenjan (0..max)."""
    seg = _clean(segment).lower() or "genel"
    data = _load()
    claimed = 0
    try:
        claimed = int((data.get(seg) or {}).get("claimed") or 0)
    except (TypeError, ValueError):
        claimed = 0
    return max(0, max_partners() - claimed)


def claim(segment: str = "", agency: str = "") -> dict:
    """Kontenjan kap: idempotent (ayni ajans iki kez sayilmaz)."""
    seg = _clean(segment).lower() or "genel"
    name = _clean(agency)
    data = _load()
    row = data.get(seg) if isinstance(data.get(seg), dict) else {"claimed": 0, "names": []}
    names = [str(n) for n in (row.get("names") or []) if str(n).strip()]
    if name and name not in names:
        if len(names) >= max_partners():
            return {"ok": False, "reason": "quota_full", "segment": seg,
                    "left": 0, "max": max_partners()}
        names.append(name)
    row = {"claimed": len(names), "names": names[-50:]}
    data[seg] = row
    _save(data)
    left = max(0, max_partners() - len(names))
    return {"ok": True, "segment": seg, "left": left, "max": max_partners()}


def scarcity_line(segment: str = "", *, lang: str = "tr") -> str:
    """Demo + chat icin tek satirlik kitlik cumlesi (uydurma yok: dosyadan okur)."""
    left = slots_left(segment)
    maximum = max_partners()
    if (lang or "tr").lower().startswith("en"):
        if left <= 0:
            return ("This segment is currently full "
                    f"({maximum}/{maximum} whitelabel partners) — join the waitlist.")
        return (f"Territory exclusivity: only {maximum} whitelabel partners per segment — "
                f"{left} slot{'s' if left != 1 else ''} left.")
    if left <= 0:
        return (f"Bu segment su an dolu ({maximum}/{maximum} whitelabel ortak) — "
                "bekleme listesine yazilabilirsiniz.")
    return (f"Bolge munhasirligi: segment basina yalnizca {maximum} whitelabel ortak — "
            f"kalan kontenjan: {left}.")
