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
import json
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # Pillow optional on lightweight VM
    Image = None  # type: ignore

import config

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


def build_vla_proof_card(domain: str, diagnostics: dict[str, Any], *, turkish: bool = True) -> dict[str, str]:
    """Build an anlık kayıp para kanıt kartı (VLA) - 7 kanıt noktası.

    DOM gecikmesi, yavaş istekler, hatalı istekler, günlük kayip,
    15 gün COI, lisans bedeli farkı, kapanma ihtimali.

    Returns a dict with card-text fields for Telegram proof card rendering.
    """
    from nirvana.financial_shock_card import build_shock, build_shock_summary

    dom_ms = diagnostics.get("dom_ms", 0)
    slow_count = diagnostics.get("slow_count", 0)
    bad_count = diagnostics.get("bad_count", 0)

    sh = build_shock(
        diagnostics,
        revenue_eur=22000.0,
        currency="EUR",
        issue="odeme adimi guven kirilmasi + altyapi yavashligi",
        report_id=f"VLA-{domain}",
    )
    if sh.get("ok"):
        band = sh.get("band_pct", "8-10")
        daily_loss = sh.get("daily_loss", 0)
        window_loss = sh.get("window_loss", 0)
        summary_lines = build_shock_summary({"diagnostics": diagnostics, "revenue_eur": 22000.0}).split("\n")
    else:
        band = "8-10"
        daily_loss = 0
        window_loss = 0
        summary_lines = []

    tr_keys = ["DOM", "Yavaş", "Hatalı", "Günlük Kayip", "15-Gün COI", "Lisans", "Kapanma"]
    en_keys = ["DOM", "Slow", "Bad", "Daily Loss", "15d COI", "License", "Closure"]

    if turkish:
        card = (
            f"VLA Anlık Kayıp Kartı — {domain}\n"
            f"DOM gecikmesi: {dom_ms} ms\n"
            f"Yavaş istek sayısı: {slow_count} (ölçülmüş)\n"
            f"Hatalı istek sayısı: {bad_count} (ölçülmüş)\n"
            f"Günlük kayip: {daily_loss:.0f} EUR\n"
            f"15 gün COI: {window_loss:.0f} EUR\n"
            f"Lisans bedeli farkı: 5.000 EUR - {window_loss:.0f} EUR\n"
            f"Kapanma ihtimali: {band} bandı\n"
            f"\n"
            f"Finansal Shock Card:\n"
            + "\n".join(summary_lines)
            + "\n"
            f"7 Kanıt Noktası: DOM, yavaş istekler, hatalı istekler, günlük kayip, 15 gün COI,\n"
            f"            lisans bedeli farkı, kapanma ihtimali\n"
        )
    else:
        card = (
            f"VLA Instant Loss Proof Card — {domain}\n"
            f"DOM delay: {dom_ms} ms\n"
            f"Slow request count: {slow_count} (measured)\n"
            f"Bad request count: {bad_count} (measured)\n"
            f"Daily loss: {daily_loss:.0f} EUR\n"
            f"15d COI loss: {window_loss:.0f} EUR\n"
            f"License fee difference: 5.000 EUR - {window_loss:.0f} EUR\n"
            f"Closure probability: {band} band\n"
            f"\n"
            f"Financial Shock Card:\n"
            + "\n".join(summary_lines)
            + "\n"
            f"7 Evidence Points: DOM, slow requests, bad requests, daily loss, 15d COI,\n"
            f"             license fee difference, closure probability\n"
        )

    return {"card_text": card, "domain": domain, "diagnostics": diagnostics}

