"""Lane K — meta_orchestrator [GitHub Actions, günlük].

Kendini geliştiren strateji katmanı: tüm modül çıktılarını (A/B kazananı,
şerit verimleri, captcha oranı) harmanlar ve şerit ağırlıklarını gerçek
dönüşüm verisiyle EMA güncellemesi yapar. KESTİRMeler sadece 400/gün, 32/saat
kotası İÇİNDE pay kaydırır — hiçbir koşulda kota/limit yükseltemez ve
pacing/optout/audit güvencelerine dokunamaz.
"""
from __future__ import annotations

import json
import time
from typing import Any

from nirvana.registry import state_path

META_NAME = "meta_state.json"
BOUNDS = (0.20, 0.80)   # hiçbir şerit %20'nin altına düşüp ölmez
ALPHA = 0.35            # EMA adımı — yavaş, aşırı tepkisiz öğrenme
LANES = ("smb", "enterprise")


def channel_yields(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Her şeridin gerçek kabul/dönüşüm oranı (veri yoksa boş)."""
    agg: dict[str, dict[str, int]] = {}
    for row in rows:
        lane = str(row.get("lane") or "").lower()
        if lane not in LANES:
            continue
        slot = agg.setdefault(lane, {"n": 0, "won": 0})
        slot["n"] += 1
        if row.get("converted") or row.get("accepted"):
            slot["won"] += 1
    return {lane: round(v["won"] / v["n"], 4) for lane, v in agg.items() if v["n"] >= 10}


def update_weights(weights: dict[str, float], yields: dict[str, float],
                   *, alpha: float = ALPHA) -> dict[str, float]:
    """EMA + normalize + clamp. Veri olmayan şerit ağırlığını korur."""
    merged = {lane: float(weights.get(lane, 0.5)) for lane in LANES}
    observed = {lane: yields[lane] for lane in LANES if lane in yields}
    if not observed:
        return {lane: round(merged[lane], 4) for lane in LANES}
    best = max(observed.values())
    for lane, rate in observed.items():
        # verimi 0..1 normalize et, kaybeden şeride ceza değil — kazanan ödül
        edge = 0.0 if best == 0 else (rate / best)
        merged[lane] = (1 - alpha) * merged[lane] + alpha * edge
    lo, hi = BOUNDS
    for lane in merged:
        merged[lane] = min(hi, max(lo, merged[lane]))
    total = sum(merged.values()) or 1.0
    return {lane: round(merged[lane] / total, 4) for lane in LANES}


def captcha_share(rows: list[dict[str, Any]]) -> float:
    """Form kilitli hedef oranı → LinkedIn şeridine ayrılacak pay sinyali."""
    statuses = [str(r.get("status") or "") for r in rows]
    relevant = [s for s in statuses if s.startswith("skipped") or s == "submitted_confirmed"]
    if not relevant:
        return 0.0
    locked = sum(1 for s in relevant if s in {"skipped_captcha", "skipped_no_open_form"})
    return round(locked / len(relevant), 4)


def recommendations(outcomes: list[dict[str, Any]], weights: dict[str, float],
                    strategy: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    winner = str((strategy.get("decision") or {}).get("winner") or strategy.get("winner") or "")
    if winner:
        recs.append(f"aktif kanca varyantı: {winner} (strategy_pivot kararı)")
    top = max(weights, key=weights.get)
    recs.append(f"şerit ağırlığı: {top} lehine ({weights[top]:.0%}) — kota 400/32 içinde kaydırılır")
    share = captcha_share(outcomes)
    if share > 0:
        recs.append(f"form kilitli oranı {share:.0%} → linkedin_router günlük kartı açık kalsın")
    recs.append("güvence: pacing/optout/audit fail-closed dokunulmaz; kota yükseltilmez")
    return recs


def run_batch(*, outcomes_path: Any = None, notify: bool = True) -> dict[str, Any]:
    source = outcomes_path or state_path("outcomes.json")
    try:
        outcomes = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        outcomes = []
    if not isinstance(outcomes, list):
        outcomes = []

    meta_path = state_path(META_NAME)
    try:
        current = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    try:
        strategy = json.loads(state_path("strategy_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        strategy = {}

    yields = channel_yields(outcomes)
    weights = update_weights(current.get("lane_weights") or {"smb": 0.5, "enterprise": 0.5}, yields)
    recs = recommendations(outcomes, weights, strategy)

    state = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lane_weights": weights,
        "yields": yields,
        "captcha_share": captcha_share(outcomes),
        "recommendations": recs,
        "hard_limits": {"daily": 400, "hourly": 32, "per_esp_hour": 3},
    }
    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(meta_path)

    digest = ("[META] strateji güncellendi\n" + "\n".join(f"- {r}" for r in recs))
    sent = False
    if notify:
        try:
            import owner_notify
            sent = owner_notify.send(digest)
        except Exception:
            sent = False
    return {"weights": weights, "yields": yields, "recommendations": recs,
            "notified": sent, "out": str(meta_path)}
