"""Lane N2 — slot_gate: yüksek-otoriteli kapanış mesajları (Diagnostic Gate).

Müşteri "fiyat nedir?" / "ne yapıyorsunuz?" dediğinde satıcı pozisyonuna düşmeden
süreci bir kullanım kotası + kabul protokolü olarak yönetir:

  G1 Karantina & Slot  — Oracle analiz motoru doğrulaması + sınırlı canlı izleme slotu
  G2 Sıfır Sunucu Yükü — teşhis tamamen kendi izole katmanında; müşteri sunucusuna
                         tek satır yük / kod müdahalesi YOK
  G3 24s Rezervasyon   — analiz verisi + ayrılan instance, onay bekleyene kadar
                         bu sohbette adınıza rezerve; onay = Payoneer + SLA

Kural her dilde aynı: "Kodunuza dokunmadan Oracle üstünde izliyoruz, slot sınırlı,
fiyat 2.500 EUR sabit, alıyorsan şartları onayla linki atayım" — yüksek otorite.
Ödeme öncesi hiçbir teknik işlem teklif edilmez; link SSK'sı self_serve_close'ta.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from nirvana.payment import price_retainer, retainer_label
import config

SLOT_LEFT = 1
RESERVE_HOURS = 24


def _report_id(row: dict[str, Any] | None, chat_id: int = 0) -> str:
    """Deterministic report id: handoff'taki rapor no ya da domain'e bağlı sabit no."""
    rid = str((row or {}).get("report_id") or "").strip()
    if rid:
        rid = re.sub(r"[^A-Za-z0-9_-]", "", rid)
        return f"#{rid}" if not rid.startswith("#") else rid
    seed = str((row or {}).get("host") or (row or {}).get("company") or f"chat-{chat_id}")
    digest = hashlib.sha256(seed.lower().encode()).hexdigest()[:4].upper()
    return f"#TR-{digest}"


def _company(row: dict[str, Any] | None) -> str:
    name = str((row or {}).get("company") or (row or {}).get("host") or (row or {}).get("target_domain") or "")
    name = name.strip()
    if name and len(name) > 24:
        pieces = [p for p in re.split(r"[/ ._-]+", name) if p]
        name = " ".join(dict.fromkeys(pieces))[:28] + ("…" if len(name) > 28 else "")
    return name or "siteniz"


def diagnostic_gate(*, turkish: bool = True, row: dict[str, Any] | None = None,
                    chat_id: int = 0, slot_left: int = SLOT_LEFT) -> str:
    """G1 — Karantina & Slot kapanışı. 'Fiyat nedir?' ve niyet yolunda kullanılır."""
    rid = _report_id(row, chat_id)
    retainer = price_retainer()  # "€2.500" — açık fiyat (fiyat-sorusu yolu)
    who = _company(row)
    if turkish:
        return (
            "Bu sistem genel bir danışmanlık veya standart yazılım satışı değildir. "
            f"Tarafınıza iletilen {rid} numaralı denetim verisi, Oracle altyapımız üzerinde "
            "çalışan canlı analiz motoruyla doğrulanmıştır.\n\n"
            "Oracle sunucu kapasitemizde şu an "
            f"{slot_left} boş canlı izleme slotu mevcuttur. Entegrasyonun başlatılması için "
            f"teknik şartnameyi ve {retainer} sabit aylık retainer onayını sunuyorum. "
            "Onay verdiğiniz an Payoneer linki ve SLA protokolü üretilir."
        )
    name_lead = f" prepared for {who} ({rid})" if who and who != "siteniz" else f" ({rid})"
    return (
        "This is not general consulting and not a standard software sale. "
        "The audit data" + name_lead + " was verified by the live analysis engine "
        "running on our Oracle infrastructure.\n\n"
        f"We currently hold {slot_left} open live-monitoring slot on our Oracle capacity. "
        "To start the integration I present the technical scope and approval of the fixed "
        f"{retainer} monthly retainer. On your approval, the Payoneer request and the "
        "SLA protocol are generated immediately."
    )


def zero_resource(*, turkish: bool = True, row: dict[str, Any] | None = None) -> str:
    """G2 — Sıfır Sunucu Yükü & Isolation kapanışı (CTO/sysadmin korkusunu kapatır)."""
    rid = _report_id(row)
    if turkish:
        return (
            "Tüm teşhis ve izleme süreçleri tamamen kendi Oracle Cloud isolation "
            "katmanımız üzerinden yürütülür; mevcut sunucunuza, veritabanınıza veya kod "
            "altyapınıza tek bir satır yük ya da kod müdahalesi yapılmaz.\n\n"
            f"24 saat içinde ilk performans ve doğrulama raporu ({rid}) hesabınıza tanımlanır. "
            "Kararı onaylıyorsanız hemen resmi kabul ve ödeme linkini üretiyorum."
        )
    return (
        "All diagnostics and monitoring run entirely on our own Oracle Cloud isolation "
        "layer; not a single line of load or code intervention touches your server, "
        "database or codebase.\n\n"
        f"The first performance and verification report ({rid}) is provisioned to your "
        "account within 24 hours. If you approve, I generate the formal acceptance and "
        "payment request right away."
    )


def reservation(*, turkish: bool = True, row: dict[str, Any] | None = None,
                chat_id: int = 0, hours: int = RESERVE_HOURS) -> str:
    """G3 — 24 saatlik Oracle instance rezervasyonu (Lock-In Clock)."""
    rid = _report_id(row, chat_id)
    retainer = price_retainer()
    if turkish:
        return (
            f"Siteniz için üretilen canlı analiz verisi ve ayırdığımız Oracle izleme "
            f"kaynağı (instance) {hours} saat boyunca bu sohbet üzerinden adınıza "
            f"rezerve edilmiştir ({rid}).\n\n"
            "Aşağıdaki şartları onayladığınızda Payoneer altyapısı üzerinden "
            f"{retainer} retainer işlemi tamamlanır ve mühendis ekibimiz izlemeyi "
            "anında canlıya alır.\n\nŞartları onaylıyor musunuz?"
        )
    return (
        f"The live analysis data produced for your site and the Oracle monitoring "
        f"instance we allocated is reserved on your name in this chat for {hours} hours "
        f"({rid}).\n\n"
        "On your approval of the terms below, the "
        f"{retainer} retainer is settled through Payoneer and our engineering team "
        "activates the monitoring immediately.\n\nDo you approve the terms?"
    )


def price_response(*, turkish: bool = True, row: dict[str, Any] | None = None,
                   chat_id: int = 0) -> str:
    """'Fiyat nedir?' sorusu için G1 + G3: direkt rakam satışı değil, kota onayı."""
    return (diagnostic_gate(turkish=turkish, row=row, chat_id=chat_id) + "\n\n" +
            reservation(turkish=turkish, row=row, chat_id=chat_id))


def intent_package(*, turkish: bool = True, row: dict[str, Any] | None = None,
                   chat_id: int = 0) -> str:
    """Satın alma niyetinde G1+G2+G3 paketi: otoriteli, sonra şart onayına köprü."""
    return (diagnostic_gate(turkish=turkish, row=row, chat_id=chat_id) + "\n\n" +
            zero_resource(turkish=turkish, row=row) + "\n\n" +
            reservation(turkish=turkish, row=row, chat_id=chat_id))


def run_batch() -> dict[str, Any]:
    """CLI/gösterim dökümü — bot dışında da doğrulanabilir (ücretsiz)."""
    demo = {"company": "Acme Engineering", "host": "acme.example"}
    import json
    from nirvana.registry import state_path
    payload = {
        "retainer": retainer_label(),
        "slot_left": SLOT_LEFT,
        "reserve_hours": RESERVE_HOURS,
        "diagnostic_gate_tr": diagnostic_gate(turkish=True, row=demo),
        "diagnostic_gate_en": diagnostic_gate(turkish=False, row=demo),
        "zero_resource_tr": zero_resource(turkish=True, row=demo),
        "zero_resource_en": zero_resource(turkish=False, row=demo),
        "reservation_tr": reservation(turkish=True, row=demo),
        "reservation_en": reservation(turkish=False, row=demo),
    }
    out = state_path("slot_gate.json")
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    return {"messages": 6, "out": str(out)}