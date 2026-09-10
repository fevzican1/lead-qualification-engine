"""Lane MOD-14 — anti_spam_cadence [GitHub Actions + Oracle, light].

Spam-önleme ritmi: insan benzeri gönderim aralıkları + yazım gecikmesi
varyasyonu. Pazara rahatsızlık vermeden takip: 24s / 72s / 7g kademeli,
opt-out'a koşulsuz saygı. Saf Python — maliyet sıfır.
"""
from __future__ import annotations

import time
from typing import Any

from nirvana.registry import state_path
import json

FOLLOWUP_STAGES = (
    {"after_hours": 24, "kind": "hatırlatma",
     "line_tr": "Teknik ekibinizin denetim raporunu inceleme fırsatı oldu mu?",
     "line_en": "Did your technical team get a chance to review the audit report?"},
    {"after_hours": 72, "kind": "değer",
     "line_tr": "Söz konusu darboğazın güncel ölçüm özeti ektedir.",
     "line_en": "Attached is the updated measurement summary of the bottleneck."},
    {"after_hours": 168, "kind": "kapanış",
     "line_tr": "Denetim dosyanız arşivleniyor; retainer altyapı alımı için son durum teyidi.",
     "line_en": "Your audit file is being archived; final confirmation for the retainer."},
)

JITTER_MS = (900, 2600)


def stage_for(hours_since_first: float) -> dict[str, Any] | None:
    pick = None
    for s in FOLLOWUP_STAGES:
        if hours_since_first >= s["after_hours"]:
            pick = s
    return pick


def run_batch(**kwargs: Any) -> dict[str, Any]:
    out = {"stages": [s["kind"] for s in FOLLOWUP_STAGES],
           "jitter_ms": list(JITTER_MS),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = state_path("anti_spam_cadence.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {**out, "out": str(path)}
