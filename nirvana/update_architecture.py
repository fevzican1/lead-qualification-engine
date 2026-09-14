"""Guncelleme mimarileri omurgasi [GitHub + Oracle, light, $0]."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Strategy:
    name: str
    infra_cost: str
    downtime: str
    risk: str
    rollback_speed: str
    use_case: str
    mttr_minutes: float


STRATEGIES: dict[str, Strategy] = {
    "blue_green": Strategy("blue_green", "high (%100 yedek/staging)",
                           "zero", "low", "instant (symlink/backup)",
                           "Kritik finansal & monolitik", 1.0),
    "canary": Strategy("canary", "medium", "zero", "very_low",
                       "fast (trafik yonlendirme)",
                       "Yuksek trafikli uygulamalar", 3.0),
    "rolling": Strategy("rolling", "low (mevcut kaynaklar)",
                        "zero/minimum", "medium",
                        "gradual (asamali geri alma)",
                        "Konteynerize mikroservisler", 8.0),
    "dark_launch": Strategy("dark_launch", "high", "zero", "low",
                            "instant (feature flag)",
                            "Arka plan algoritma testleri", 0.5),
}


def availability(mtbf_hours: float, mttr_minutes: float) -> float:
    """A = MTBF / (MTBF + MTTR). Birimler saate normalize edilir."""
    if mtbf_hours <= 0:
        return 0.0
    mttr_h = max(0.0, float(mttr_minutes)) / 60.0
    return float(mtbf_hours) / (float(mtbf_hours) + mttr_h)


def compare_strategies(*, mtbf_hours: float = 720.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in STRATEGIES.values():
        out.append({"strategy": s.name, "infra_cost": s.infra_cost,
                    "downtime": s.downtime, "risk": s.risk,
                    "rollback": s.rollback_speed, "use_case": s.use_case,
                    "availability": round(availability(mtbf_hours, s.mttr_minutes), 6)})
    return out


def select_strategy(*, infra_budget: str = "low",
                    risk_tolerance: str = "low", traffic: str = "low") -> str:
    infra_budget = (infra_budget or "low").lower()
    risk_tolerance = (risk_tolerance or "low").lower()
    traffic = (traffic or "low").lower()
    if traffic in {"high", "very_high"} and risk_tolerance == "low":
        return "canary"
    if infra_budget == "high" and risk_tolerance == "low":
        return "blue_green"
    if traffic == "background":
        return "dark_launch"
    return "rolling"


def canary_plan(*, total: int = 100,
                steps: tuple[int, ...] = (5, 25, 50, 100),
                error_threshold: float = 0.02) -> dict[str, Any]:
    pcts = sorted({p for p in steps if 0 < p <= 100} | {100})
    waves = [{"pct": p, "count": max(1, round(total * p / 100)),
              "gate": f"hata_orani<{error_threshold:.0%} ve WAF_REJECT==0"}
             for p in pcts]
    return {"total": total, "error_threshold": error_threshold,
            "waves": waves,
            "policy": "esik asilirsa trafik eski surume kaydirilir"}


def rolling_batches(instances: list[str], *, batch_size: int = 1) -> list[list[str]]:
    batch_size = max(1, int(batch_size))
    return [instances[i:i + batch_size] for i in range(0, len(instances), batch_size)]


def dark_launch_enabled(flags: dict[str, Any], key: str) -> bool:
    return bool((flags or {}).get(key) is True)


def deploy_allowed(*, in_cooling: bool, deploys_last_24h: int,
                   max_deploys_per_day: int = 4) -> dict[str, Any]:
    """Fail-closed: sogutmada veya asiri sik guncellemede DUR (felc freni)."""
    if in_cooling:
        return {"allowed": False, "reason": "watchdog_cooling"}
    if int(deploys_last_24h) >= int(max_deploys_per_day):
        return {"allowed": False, "reason": "update_paralysis_guard"}
    return {"allowed": True, "reason": "ok"}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    mtbf = float(kwargs.get("mtbf_hours") or 720.0)
    return {"strategies": compare_strategies(mtbf_hours=mtbf),
            "default_for_oracle": select_strategy(),
            "out": "nirvana/update_architecture.py"}
