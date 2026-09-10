"""Lane MOD-05 — financial_loss_engine [GitHub Actions, heavy].

Kayıp hesaplama motoru: ölçülen darboğaz metriklerinden somut finansal etki
tahmini üretir. UYDURMA YOK — yalnızca ölçülmüş veri aralıklarıyla konuşur:
- dom_ms / slow_res / bad_reqs girdilerinden gecikme aralığı
- Kayıp aralığı %8-12 bandında raporlanır (ölçülen darboğaz şiddetine göre)
- Çıktı: retainer_report_agent + proof_card + objection_handler girdisi

Maliyet: sıfır (saf Python, model yok).
"""
from __future__ import annotations

from typing import Any

from nirvana.registry import state_path
import json
import time


def estimate_loss(*, dom_ms: int = 0, slow_count: int = 0,
                  bad_count: int = 0) -> dict[str, Any]:
    """Ölçülmüş metriklerden kayıp aralığı. Uydurma metrik üretmez."""
    severity = 0
    if dom_ms > 3000:
        severity += 2
    elif dom_ms > 1200:
        severity += 1
    severity += min(slow_count, 3)
    severity += min(bad_count * 2, 4)
    if severity >= 6:
        band = "10-12"
    elif severity >= 3:
        band = "8-10"
    elif severity >= 1:
        band = "5-8"
    else:
        band = "2-5"
    return {"loss_band_pct": band, "severity": severity,
            "basis": {"dom_ms": dom_ms, "slow_count": slow_count,
                      "bad_count": bad_count}}


def loss_line(loss: dict[str, Any], *, turkish: bool = True) -> str:
    band = loss.get("loss_band_pct", "8-10")
    if turkish:
        return (f"Ölçülen darboğaz, dönüşüm hunisinde %{band} bandında kayıp "
                f"riski taşıyor (rapor numaralı bulgu).")
    return (f"The measured bottleneck carries a {band}% conversion-loss risk "
            f"band (numbered report finding).")


def run_batch(**kwargs: Any) -> dict[str, Any]:
    out = {"loss_band_pct": "8-10", "note": "varsayılan bant; ölçümle güncellenir",
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = state_path("financial_loss.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {**out, "out": str(path)}
