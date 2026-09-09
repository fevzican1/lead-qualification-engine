"""Proof card — visual technical proof with metrics.

Lane W: proof_card.py

Generates a deterministic PNG proof card that the Telegram bot attaches to
customer messages. The bot never generates these itself — a GitHub Actions
heavy job (micro_audit_proof_agent) produces the PNG and uploads it to a
raw.githubusercontent.com URL that is fed back into Oracle VM state.

This module reads the precomputed proof data from
``state/proof_cards.json`` (written by GitHub) and serves it to the bot.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # Pillow optional on lightweight VM
    Image = None  # type: ignore

import config
from nirvana.registry import state_path

PROOF_DIR = config.ROOT / "proof_cards"
STATE_FILE = config.ROOT / "state" / "proof_cards.json"


def card_path(domain: str) -> Path | None:
    """Return the path of the proof card PNG for *domain*, if present."""
    safe = domain.strip().lower().removeprefix("www.")
    if not safe:
        return None
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = PROOF_DIR / f"{safe}{ext}"
        if candidate.exists():
            return candidate
    return None


def find_proof(domain: str) -> dict[str, Any] | None:
    """Lookup precomputed proof record for *domain* in state file (set by GitHub)."""
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    domain = domain.strip().lower().removeprefix("www.")
    for row in data.get("cards") or []:
        if str(row.get("domain") or "").strip().lower().removeprefix("www.") == domain:
            return row  # type: ignore
    return None


def build_card(domain: str, issue: str, metric: str, report_id: str = "",
               *, width: int = 720, height: int = 405) -> Path | None:
    """Generate a minimalist proof card PNG locally (fallback / dev only)."""
    if Image is None:
        return None
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    safe = domain.strip().lower().removeprefix("www.")
    path = PROOF_DIR / f"{safe}.png"

    img = Image.new("RGB", (width, height), "#0f172a")
    d = ImageDraw.Draw(img)
    try:
        font_b = ImageFont.truetype("arialbd.ttf", 22)
        font_r = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font_b = font_r = ImageFont.load_default()

    y = 28
    d.text((28, y), "TECHNICAL PROOF CARD", fill="#38bdfc", font=font_b)
    y += 44
    d.text((28, y), f"Domain: {domain}", fill="#e2e8f0", font=font_r)
    y += 26
    d.text((28, y), f"Issue: {issue}", fill="#fca5a5", font=font_r)
    y += 26
    d.text((28, y), f"Metric: {metric}", fill="#4ade80", font=font_r)
    if report_id:
        y += 26
        d.text((28, y), f"Report ID: {report_id}", fill="#fbbf24", font=font_r)
    y += 34
    d.text((28, y), "Source: public page source only", fill="#64748b", font=font_r)
    y += 22
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    d.text((28, y), stamp, fill="#475569", font=font_r)
    img.save(path, "PNG", optimize=True)
    return path


def proof_url(domain: str) -> str | None:
    """Return the raw.githubusercontent.com URL for the proof card (set by GitHub upload).

    Only registered HTTP(S) URLs are customer-facing. Local file paths are never
    exposed to customers (anti-karışıklık: kanıt GitHub'dan üretilen gerçek kart).
    """
    rec = find_proof(domain)
    if rec:
        url = str(rec.get("url") or "")
        if url.startswith("http"):
            return url
    return None


def run_batch(**_kwargs: Any) -> dict[str, Any]:
    """Lane X runner: inventory the proof-card store (GitHub writes, Oracle serves)."""
    cards: list[dict[str, Any]] = []
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            cards = list(data.get("cards") or [])
        except (OSError, ValueError):
            cards = []
    local = sorted(p.name for p in PROOF_DIR.glob("*.png")) if PROOF_DIR.exists() else []
    return {
        "module": "proof_card",
        "state_cards": len(cards),
        "local_cards": len(local),
        "domains": [str(c.get("domain")) for c in cards[:20]],
    }

