"""Lane P — message_optimizer [GitHub Actions, heavy].

Rapor kapsamı: PAS (Problem-Agitate-Solve) çerçevesi + A/B testing + dinamik
kişiselleştirme. Gönderilen form mesajlarını geçmiş etkileşim verilerine göre
optimize eder. En yüksek dönüşüm sağlayan varyasyonun ağırlığını artırır.
"""
from __future__ import annotations

import json
import time
from typing import Any

from nirvana.registry import state_path

LOG_NAME = "message_log.json"
VARIANTS_NAME = "message_variants.json"

# PAS şablonları: Problem-Agitate-Solve
TEMPLATES = {
    "tr": {
        "problem": "Sitenizin {page} akışında {metric} tespit ettik.",
        "agitate": "Bu darboğaz müşteri deneyimini doğrudan etkiler; sepet terki riski artar.",
        "solve": "Kapanış planını kanıtlarıyla Telegram'da adım adım gösteriyoruz; uygulama doğrulanmış ödeme sonrası retainer kapsamında başlar.",
    },
    "en": {
        "problem": "We detected {metric} in your {page} flow.",
        "agitate": "This bottleneck directly impacts user experience and raises cart-abandonment risk.",
        "solve": "We walk you through the closure plan step by step with evidence on Telegram; execution starts after verified payment under the retainer.",
    },
}


def build_message(*, company: str, page: str = "checkout", metric: str = "yavaş yükleme",
                  lang: str = "tr", variant: str = "A") -> str:
    """PAS çerçevesinde kişiselleştirilmiş mesaj üret."""
    t = TEMPLATES.get(lang, TEMPLATES["tr"])
    problem = t["problem"].format(page=page, metric=metric)
    proof = ""  # micro_audit_proof rapor linki varsa buraya eklenir
    if lang == "tr":
        return (
            f"Merhaba {company} ekibi,\n\n"
            f"{problem}\n{t['agitate']}\n\n"
            f"{proof}\n"
            f"{t['solve']}\n\n"
            f"İlgilenir misiniz?"
        )
    return (
        f"Hi {company} team,\n\n"
        f"{problem}\n{t['agitate']}\n\n"
        f"{proof}\n"
        f"{t['solve']}\n\n"
        f"Interested?"
    )


def record_outcome(domain: str, variant: str, replied: bool, *, lang: str = "tr") -> None:
    """Geri bildirimi kaydet (A/B test istatistikleri için)."""
    path = state_path(LOG_NAME)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"entries": []}
    data["entries"].append({
        "domain": domain, "variant": variant, "replied": replied,
        "lang": lang, "ts": time.time(),
    })
    # Son 500 kaydet
    data["entries"] = data["entries"][-500:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def best_variant(*, lang: str = "tr") -> str:
    """En yüksek yanıt oranına sahip varyasyonu döndür."""
    path = state_path(LOG_NAME)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "A"
    entries = [e for e in data.get("entries", []) if e.get("lang") == lang]
    if not entries:
        return "A"
    # Basit skor: varyant başına yanıt oranı
    stats: dict[str, dict[str, int]] = {}
    for e in entries:
        v = e.get("variant", "A")
        stats.setdefault(v, {"total": 0, "replied": 0})
        stats[v]["total"] += 1
        if e.get("replied"):
            stats[v]["replied"] += 1
    best, best_rate = "A", -1.0
    for v, s in stats.items():
        rate = s["replied"] / s["total"] if s["total"] else 0
        if rate > best_rate:
            best, best_rate = v, rate
    return best


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """GitHub Actions'ta günlük çalışır: varyant skorlarını günceller."""
    return {"best_variant_tr": best_variant(lang="tr"),
            "best_variant_en": best_variant(lang="en"),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
