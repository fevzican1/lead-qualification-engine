"""SPIN Selling + Challenger Sale satış motoru (WebChat AI Setter/Closer).

Rapor (Yüksek Dönüşüm ve Satış Motoru): WebChat ajanı SSS botu değil, gelişmiş bir
satış temsilcisidir. Sistem istemi SPIN'in dört aşamasına göre kurgulanır:

- Situation (Durum)      : mevcut altyapı/platform soruları
- Problem (Sorun)        : verimsizlikleri açığa çıkarma
- Implication (Önem)     : çözülmezse oluşacak finansal/operasyonel zarar
- Need-Payoff (İhtiyaç)  : çözümün net getirisini müşteriye onaylatma

Challenger katmanı: Öğret (Teach) + Uyarla (Tailor) + Kontrolü al (Take Control).
Randevu bağlama: BOOKING_URL (Cal.com / Google Calendar) sohbet içinde sunulur.

Saf Python, LLM'siz de çalışır (kural tabanlı yedek) — $0 maliyet.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Iterable

STAGES = ("situation", "problem", "implication", "need_payoff")
STAGE_TR = {"situation": "Durum", "problem": "Sorun",
            "implication": "Önem", "need_payoff": "İhtiyaç-Kazanç"}

QUESTIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "tr": {
        "situation": (
            "Şu an form/iletişim talepleri hangi platformdan hangi sisteme akıyor?",
            "Sipariş-ödeme tarafında hangi altyapıyı kullanıyorsunuz (hazır platform mu, özel mi)?",
        ),
        "problem": (
            "Hangi adımda manuel müdahale ediyorsunuz?",
            "Son bir ayda kaybolduğunu fark ettiğiniz bir talep oldu mu?",
        ),
        "implication": (
            "Bu kopukluk ayda kaç saatlik iş yükü ve kaç kayıp sipariş demek?",
            "Her kaçan kayıt size yaklaşık ne kadara mal oluyor?",
        ),
        "need_payoff": (
            "Tek akışta izleme + otomatik kayıt olsa ekibinizde ne değişirdi?",
            "İlk 30 günde hangi ölçüde iyileşme sizin için başarı sayılır?",
        ),
    },
    "en": {
        "situation": (
            "Which platforms do enquiries currently flow between today?",
            "What stack handles your orders and payments (off-the-shelf or custom)?",
        ),
        "problem": (
            "Where do you still intervene manually?",
            "Have you noticed an enquiry that never landed in the CRM last month?",
        ),
        "implication": (
            "How many hours a month and how many lost orders does that gap mean?",
            "What does each lost record roughly cost you?",
        ),
        "need_payoff": (
            "If tracking and auto-logging were one flow, what would change for the team?",
            "What improvement in the first 30 days would count as success?",
        ),
    },
}

TEACH_TR = ("Kanıt: iki sistem arasında webhook kaybı genelde callback timeout + "
            "idempotency eksikliğinden çıkar; ölçüm olmadan tahmin edilemez.")
TEACH_EN = ("Evidence: webhook loss between two systems usually comes from callback "
            "timeouts plus missing idempotency; it cannot be guessed without measurement.")
TAILOR_TR = "Kişiselleştir: müşterinin cümlelerini yankıla, sektör ve altyapı ismini kullan."
TAILOR_EN = "Tailor: echo their wording, use their industry and stack by name."
CONTROL_TR = "Kontrol: iki seçenekli tek soru sor (devam / ertele), sonra SUS."
CONTROL_EN = "Take control: ask one either/or question (continue / later), then be quiet."

CONTACT_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]{2,}|\+?\d[\d\s().-]{8,}\d|\b(telefon|phone|whatsapp|e-?posta|mail)\b",
    re.I,
)
BUDGET_RE = re.compile(
    r"(\u20ac|\$|\u00a3|eur[oa]?|usd|dolar|tl|b[üu]t[çc]e|budget|fiyat|price|ücret|ucret|"
    r"\b\d[\d.\s]{2,}\b|\b\d+\s*[kKmM]\b)",
    re.I,
)
NEED_RE = re.compile(
    r"(ihtiya[çc]|need|acil|urgent|kritik|critical|entegrasyon|integration|otomasyon|"
    r"automation|webhook|crm|erp|sipari[şs]|order|ödeme|payment)",
    re.I,
)
TIMELINE_RE = re.compile(
    r"(bug[üu]n|today|yar[ıi]n|tomorrow|bu hafta|this week|asap|en k[ıi]sa|hemen|now|q[1-4])",
    re.I,
)


def booking_url() -> str:
    """Sohbet içi randevu linki (Cal.com / Google Calendar) — tanımsızsa boş."""
    for key in ("BOOKING_URL", "CALCOM_BOOKING_URL", "GOOGLE_BOOKING_URL"):
        url = (os.getenv(key) or "").strip()
        if url.startswith("http"):
            return url
    return ""


def detect_contact(text: str) -> bool:
    return bool(CONTACT_RE.search(text or ""))


def detect_budget(text: str) -> bool:
    return bool(BUDGET_RE.search(text or ""))


def signals(text: str) -> list[str]:
    """Bu turdaki satış sinyalleri (skor artışının gerekçesi)."""
    out: list[str] = []
    body = text or ""
    if detect_contact(body):
        out.append("contact")
    if detect_budget(body):
        out.append("budget")
    if NEED_RE.search(body):
        out.append("need")
    if TIMELINE_RE.search(body):
        out.append("timeline")
    return out


def score_delta(text: str, *, stage: str = "situation") -> tuple[int, list[str]]:
    """Rapor kuralı: iletişim bilgisi veya bütçe/ihtiyaç sinyali anında +10."""
    found = signals(text)
    delta = 10 * len(found)
    if stage in ("implication", "need_payoff") and "need" in found and delta == 0:
        delta += 5
    return min(40, delta), found


def stage_from_history(history: Iterable[dict[str, Any]] | None) -> str:
    """Konuşma uzunluğuna göre sıradaki SPIN aşaması (LLM'siz de çalışır)."""
    turns = sum(1 for m in (history or []) if str((m or {}).get("role")) == "user")
    if turns <= 0:
        return "situation"
    if turns == 1:
        return "problem"
    if turns == 2:
        return "implication"
    return "need_payoff"


def next_question(*, lang: str = "tr", stage: str = "situation",
                  history: Iterable[dict[str, Any]] | None = None) -> str:
    """Aşamanın soru bankasından, daha önce sorulmamış ilk soru."""
    bank = QUESTIONS.get("tr" if (lang or "tr").startswith("tr") else "en",
                         QUESTIONS["en"]).get(stage) or QUESTIONS["en"]["situation"]
    said = " ".join(str((m or {}).get("content") or "") for m in (history or []))
    for question in bank:
        if question[:24] not in said:
            return question
    return bank[0]


def spin_block(*, lang: str = "tr", stage: str | None = None,
               history: Iterable[dict[str, Any]] | None = None) -> str:
    """Sistem istemine eklenecek SPIN + Challenger bloğu."""
    tr = (lang or "tr").startswith("tr")
    current = stage or stage_from_history(history)
    question = next_question(lang=lang, stage=current, history=history)
    if tr:
        return (
            "[SPIN SELLING — ZORUNLU AKIŞ]\n"
            f"Aktif aşama: {STAGE_TR.get(current, current)}. Sıradaki soru: {question}\n"
            "- Durum: mevcut altyapıyı sor; teşhis koymadan çözüm satma.\n"
            "- Sorun: verimsizliği müşterinin kendi cümlesiyle açığa çıkar.\n"
            "- Önem: çözülmezse oluşacak maliyeti/saat kaybını HESAPLAT (uydurma rakam yok).\n"
            "- İhtiyaç-Kazanç: kazanımı müşteriye onaylat, sonra tek adım öner.\n"
            f"[CHALLENGER]\n{TEACH_TR}\n{TAILOR_TR}\n{CONTROL_TR}\n"
            "- Doğrudan satış yok: fiyat/ödeme yalnızca müşteri açıkça isterse açılır.\n"
            "- Her yanıt tek soruyla bitsin; üç cümleyi geçme."
        )
    return (
        "[SPIN SELLING — MANDATORY FLOW]\n"
        f"Active stage: {current}. Next question: {question}\n"
        "- Situation: ask about the current stack; never pitch before diagnosis.\n"
        "- Problem: surface the inefficiency in their own words.\n"
        "- Implication: make them calculate the cost/hours of inaction (never invent numbers).\n"
        "- Need-payoff: get them to confirm the gain, then propose one step.\n"
        f"[CHALLENGER]\n{TEACH_EN}\n{TAILOR_EN}\n{CONTROL_EN}\n"
        "- No hard selling: price/payment only when explicitly requested.\n"
        "- End with a single question; keep replies under three sentences."
    )


def booking_cta(*, lang: str = "tr", url: str = "", name: str = "") -> str:
    """Randevu bağlama satırı — link varsa sohbet içinde sunulur."""
    target = url or booking_url()
    if (lang or "tr").startswith("tr"):
        line = "Uygunsa 15 dakikalık teknik görüşme için uygun slotu doğrudan seçebilirsiniz"
        return f"{line}: {target}" if target else ""
    line = "If it helps, pick a slot directly for a 15-minute technical call"
    return f"{line}: {target}" if target else ""


def handoff_summary(*, history: Iterable[dict[str, Any]] | None, score: int = 0,
                    stage: str | None = None, lang: str = "tr") -> str:
    """CRM/n8n'e gidecek kısa görüşme özeti (LLM'siz, deterministik)."""
    turns = [str((m or {}).get("content") or "") for m in (history or [])
             if str((m or {}).get("role")) == "user"]
    stage_now = stage or stage_from_history(history)
    last = turns[-1][:180] if turns else ""
    if (lang or "tr").startswith("tr"):
        return (f"SPIN aşaması: {STAGE_TR.get(stage_now, stage_now)} | skor: {score} | "
                f"tur: {len(turns)} | son mesaj: {last}")
    return (f"SPIN stage: {stage_now} | score: {score} | turns: {len(turns)} | "
            f"last message: {last}")


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Lane çıktısı: aşama/soru bankası ve randevu entegrasyon durumu."""
    delta, found = score_delta("fiyat nedir, entegrasyon lazım", stage="problem")
    return {
        "stages": list(STAGES),
        "question_bank": {lang: {stage: len(q) for stage, q in rows.items()}
                          for lang, rows in QUESTIONS.items()},
        "booking_configured": bool(booking_url()),
        "sample_signal_delta": delta,
        "sample_signals": found,
        "challenger": ["teach", "tailor", "take_control"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

