"""Lane E — objection_handler_agent [GitHub Actions / Telegram].

Deterministic objection -> soft-landing replies. No model call, no cost: the
top objections ("fiyat yüksek", "güvenlik riski", "zaman yok", "düşünelim")
each get one honest answer whose pivot is the free 3-day pilot.
Used by telegram_sales_bot's offline fallback and available as a CLI module.
"""
from __future__ import annotations

import json
import re
from typing import Any

import config
from nirvana.payment import retainer_label

# (pattern, turkish_reply, english_reply)
RULES: tuple[tuple[str, str, str], ...] = (
    (
        r"(fiyat\w*\s*(çok\s*)?yüksek|pahalı|bütçe\w*\s*yok|budget|too expensive|price is high|cost is high)",
        "Fiyat konusunu anlıyorum. Riski size yıkmadan denememenizi istemem: önce 3 günlük "
        "ücretsiz pilot tarama yapalım; somut bulgu çıkarsa retainer konuşuruz, çıkmazsa "
        "uygulamayı kapatırım.",
        "Understood on price. Let's de-risk it: run the free 3-day pilot scan first — "
        "if it surfaces concrete findings we discuss the retainer, if not I close it.",
    ),
    (
        r"(güvenlik|veri|erişim|security|data (safety|risk)|access risk)",
        "Haklısınız, erişim en hassas konu. Pilot tarama salt okunur, dışarıdan; herhangi "
        "bir panel, kimlik veya veri indirmesi yok. Rapor açık standartlarla numaralanır "
        "ve istediğiniz an silinir.",
        "Fair concern. The pilot scan is read-only and external: no panel, no credentials, "
        "no data download. Reports are numbered against open standards and deleted on request.",
    ),
    (
        r"(zaman[ıi]m yok|meşgul|daha sonra|no time|too busy|later)",
        "Tamam, sizin yerinizde olsam ben de meşgul olurduk. Sessiz mod: haftada bir tek "
        "e-posta/Telegram özeti alırsınız, pilot 3 gün kendi kendine çalışır, siz sadece "
        "sonucu okursunuz.",
        "Understood. Quiet mode: one weekly Telegram/email summary, the 3-day pilot runs "
        "itself, you only read the result.",
    ),
    (
        r"(düşünelim|düşünmem lazım|karar veremem|let me think|we'll think|not sure)",
        "Tabii, düşünün. Bu arada pilot slotunu 3 gün için ayırayım: ücretsiz, taahhütsüz "
        "ve sonunda rapor sizin. Dönmezseniz kapanır.",
        "Of course, take your time. Meanwhile I'll hold a free 3-day pilot slot: no "
        "commitment, report is yours, it closes if you don't return.",
    ),
    (
        r"(pilot (nasıl|nedir)|free pilot|ücretsiz pilot|trial)",
        f"Pilot: 3 gün ücretsiz tarama + rapor; sonra isterseniz aylık {retainer_label()} "
        "retainer ile devam eder, istemezseniz orada biter.",
        f"Pilot: free 3-day scan + report; afterwards the monthly {retainer_label()} "
        "retainer is optional — stop there if you prefer.",
    ),
)

PILOT_LINE_TR = "Önce 3 gün ücretsiz pilot yapalım — somut bulgu yoksa retainer yok."
PILOT_LINE_EN = "Start with the free 3-day pilot — no concrete findings, no retainer."


def handle(text: str, *, turkish: bool = False) -> str | None:
    """Return the matched objection reply, or None if this is not an objection."""
    raw = (text or "").strip()
    if not raw:
        return None
    for pattern, tr, en in RULES:
        if re.search(pattern, raw, re.IGNORECASE):
            return tr if turkish else en
    return None


def run_batch() -> dict[str, Any]:
    from nirvana.registry import state_path
    out_path = state_path("objection_rules.json")
    payload = {"rules": [p for p, _, _ in RULES],
               "pilot_line": PILOT_LINE_TR,
               "pilot_line_en": PILOT_LINE_EN,
               "retainer": retainer_label()}
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"rules": len(RULES), "out": str(out_path)}
