"""RAG-first bilgi guncelleme [light, $0]. Non-parametrik oncelikli."""
from __future__ import annotations
import json
from typing import Any
from nirvana.registry import state_path


def rag_answer(query: str, *, limit: int = 3) -> dict[str, Any]:
    """Vektor DB yerine knowledge overlay + tactic evidence'dan baglam kur."""
    import knowledge
    book = knowledge.live_playbook()
    q = (query or "").lower()
    hits = [k for k in book if k.lower() in q][:limit]
    ctx = {k: book[k] for k in hits}
    return {"mode": "rag", "hits": hits, "context": ctx,
            "cost": "low", "forget_risk": "none",
            "note": "model agirligi degismez; bilgi DB seviyesinde guncellenir"}


def lora_vs_full() -> list[dict[str, Any]]:
    return [
        {"method": "full_retrain", "cost": "very_high",
         "freq": "monthly/yearly", "forget": "none", "hallucination": "low"},
        {"method": "lora_peft", "cost": "medium_high",
         "freq": "weekly/monthly", "forget": "high", "hallucination": "medium"},
        {"method": "rag_vector", "cost": "low", "freq": "realtime",
         "forget": "none", "hallucination": "very_low"},
        {"method": "model_unlearning", "cost": "high",
         "freq": "on_demand", "forget": "medium", "hallucination": "variable"},
    ]


def record_unlearn_request(domain: str, reason: str) -> dict[str, Any]:
    """Unutulma hakki: parametre silinmez, kayit + opt-out zinciri isler."""
    import optout
    optout.add_domain(domain, reason=f"unlearn:{reason}")
    path = state_path("unlearn_requests.json")
    try:
        cur = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cur = []
    cur.append({"domain": domain, "reason": reason})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur[-200:], ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return {"ok": True, "domain": domain, "queued": len(cur)}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    return {"methods": lora_vs_full(),
            "policy": "RAG-first; agirlik degisikligi yok"}
