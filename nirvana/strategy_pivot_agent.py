"""Lane D — strategy_pivot_agent [GitHub Actions].

Autonomous A/B over the outreach levers (hook variant, free-pilot offer,
timing) using recorded outcomes only. Deterministic: winner needs both a real
conversion-rate edge and a minimum sample, otherwise the incumbent holds.
"""
from __future__ import annotations

import json
import time
from typing import Any

from nirvana.registry import state_path

DEFAULT_OUT = "strategy_state.json"
MIN_SAMPLE_PER_ARM = 20
MIN_EDGE = 0.05  # winner must beat incumbent by >= 5 points


def analyze(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """outcomes: [{variant: 'A'|'B'|..., converted: bool}, ...] -> per-arm stats."""
    arms: dict[str, dict[str, int]] = {}
    for row in outcomes:
        variant = str(row.get("variant") or "").strip().upper()
        if not variant:
            continue
        arm = arms.setdefault(variant, {"n": 0, "conversions": 0})
        arm["n"] += 1
        if row.get("converted"):
            arm["conversions"] += 1
    rates: dict[str, float] = {}
    for variant, arm in arms.items():
        rates[variant] = round(arm["conversions"] / arm["n"], 4) if arm["n"] else 0.0
    return {"arms": arms, "rates": rates}


def pick_winner(stats: dict[str, Any], incumbent: str = "A") -> dict[str, Any]:
    rates = stats["rates"]
    arms = stats["arms"]
    if not rates or incumbent not in rates:
        return {"winner": incumbent, "changed": False, "reason": "no_data"}
    incumbent_rate = rates[incumbent]
    best, best_rate = incumbent, incumbent_rate
    for variant, rate in rates.items():
        if arm_ready(arms, variant) and rate > best_rate:
            best, best_rate = variant, rate
    if best == incumbent:
        return {"winner": incumbent, "changed": False, "reason": "incumbent_holds"}
    if best_rate - incumbent_rate < MIN_EDGE:
        return {"winner": incumbent, "changed": False, "reason": "edge_below_threshold"}
    return {"winner": best, "changed": True, "reason": "significant_edge",
            "rate": best_rate, "incumbent_rate": incumbent_rate}


def arm_ready(arms: dict[str, dict[str, int]], variant: str) -> bool:
    return arms.get(variant, {}).get("n", 0) >= MIN_SAMPLE_PER_ARM


def load_outcomes(path: Any = None) -> list[dict[str, Any]]:
    """Outcomes feed: telegram handoffs + review queue exports, if present."""
    source = path or state_path("outcomes.json")
    try:
        rows = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def run_batch(*, in_path: Any = None, out_name: str = DEFAULT_OUT) -> dict[str, Any]:
    outcomes = load_outcomes(in_path)
    stats = analyze(outcomes)
    out_path = state_path(out_name)
    try:
        current = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    incumbent = str(current.get("winner") or "A")
    decision = pick_winner(stats, incumbent=incumbent)
    state = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "winner": decision["winner"],
        "decision": decision,
        "rates": stats["rates"],
        "arms": stats["arms"],
        "offer_variants": {
            "A": "Ücretsiz 3 günlük pilot tarama",
            "B": "Pilot: ilk hafta ücretsiz kurulum, sonra retainer",
        },
        "timing": {"send_window_utc": "07:00-09:00", "followup_days": 1},
    }
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"winner": decision["winner"], "changed": decision["changed"], "out": str(out_path)}
