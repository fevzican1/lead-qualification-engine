"""Lane N — contract_pack [Oracle VM, olay bazlı].

Ödeme öncesi güven paketi: dinamik SLA / NDA / fikri mülkiyet devri taslağı.
ŞABLONDUR — hukuki danışmanlık değildir (metinde açık). Telegram akışında
sözleşme evresinde sunulur; onboarding_agent bu paketi karşılama metnine ekler.
"""
from __future__ import annotations

from typing import Any

import config
from nirvana.payment import retainer_label


def build_pack(*, company: str = "", domain: str = "") -> dict[str, str]:
    who = company.strip() or domain.strip() or "Müşteri"
    ret = retainer_label()
    return {
        "sla": (
            f"Dinamik SLA taslağı ({who}):\n"
            "1) Kapsam: haftalık zamanlanmış altyapı turları; hata, kesinti ve performans bulgularının raporlanması.\n"
            "2) Yanıt süresi: kritik bulgu 24 saat içinde raporlanır, ilk müdahale önerisi 48 saat içinde iletilir.\n"
            "3) Erişim: yalnızca okunur (read-only) izleme; kapsam dışı müdahale yapılmaz.\n"
            f"4) Ücret: aylık {ret}, her ay yenilenir; ilk 7 gün performans yamaları ücretsiz."
        ),
        "nda": (
            f"Gizlilik (NDA) özeti ({who}):\n"
            "1) Tarama sırasında görülen veriler yalnızca denetim amacıyla işlenir.\n"
            "2) Veriler üçüncü tarafla paylaşılmaz, istek üzerine silinir.\n"
            "3) Raporlar numaralanır ve açık standartlarla karşılaştırılabilir."
        ),
        "ip": (
            f"Fikri mülkiyet devri özeti ({who}):\n"
            "1) Retainer kapsamında sizin altyapınız için yazılan yamalar ve kod size aittir.\n"
            "2) Genel araç/betikler tasarımcıda kalır; size kullanım hakkı verilir.\n"
            "3) Teslim edilen çıktılar (rapor, yama, belge) sizin belge arşivinize aittir."
        ),
        "disclaimer": (
            "Not: Bu metinler sözleşme ŞABLONUDUR ve hukuki danışmanlık değildir; "
            "imza öncesi tarafınızca gözden geçirilmelidir."
        ),
    }


def pack_text(*, company: str = "", domain: str = "") -> str:
    pack = build_pack(company=company, domain=domain)
    return "\n\n".join([pack["sla"], pack["nda"], pack["ip"], pack["disclaimer"]])


def run_batch(*, company: str = "", domain: str = "") -> dict[str, Any]:
    pack = build_pack(company=company, domain=domain)
    return {"pack": pack, "text": pack_text(company=company, domain=domain)}
