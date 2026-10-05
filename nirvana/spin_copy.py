"""Form metni varyasyonu — Hook + Değer + Düşük Sürtünmeli CTA (Spintax'lı).

Rapor (Form Mesaj Metni Optimizasyonu): jenerik reklam metni yerine
1) sektörel iğneleme (Hook), 2) somut değer önerisi (ücretsiz analiz/rapor/demo),
3) "satın alın" yerine düşük sürtünmeli eylem çağrısı.

`vary()` mevcut (kanıtlanmış) `telegram_handoff.form_copy` çıktısını BOZMADAN
insan benzeri varyasyon ekler: selamlama/kapanış kalıpları döner. `compose()`
sıfırdan Hook/Değer/CTA metni üretir — doğrudan POST/AJAX hattı ve testler için.
"""
from __future__ import annotations

import random
from typing import Any

from nirvana import spintax

GREETING_TR = "{Merhaba|Selamlar|İyi çalışmalar|Merhaba}"
GREETING_EN = "{Hello|Hi|Good day|Hello}"
CLOSING_TR = ("{Uygun değilse|İlginizi çekmezse|Konu dışıysa} takip yapmayacağız; "
              "STOP ile çıkabilirsiniz.")
CLOSING_EN = ("{If this is not relevant|If this is not for you|If not a fit} we will not follow up; "
              "reply STOP to opt out.")
SUBJECT_TAIL_TR = "{— kısa teknik özet|— hızlı bulgu özeti|— 2 dakikalık özet|— ücretsiz analiz notu}"
SUBJECT_TAIL_EN = "{— quick technical summary|— short finding note|— 2-minute summary|— free audit note}"

HOOKS_TR = (
    "{Form gönderimlerinin|İletişim formu mesajlarının|Web formu kayıtlarının} bir kısmı "
    "CRM'e düşmeden {kaybolabiliyor|yarıda kalabiliyor|e-posta kutusunda kalabiliyor}.",
    "{Sipariş|Ödeme} webhook'ları ile {CRM|ERP} kayıtları arasında "
    "{kopukluk|gecikme|tekrar kayıt} oluşabiliyor.",
    "{Formdan gelen talepler|Site talepleri} {24 saat|bir iş günü} içinde "
    "{otomatik izlenmiyor|kayıt altına alınmıyor} olabilir.",
)
HOOKS_EN = (
    "{A share of|Some|Part of} contact-form submissions "
    "{never reaches|stalls before|sits in the inbox of} your CRM.",
    "Between {payment|order} webhooks and {CRM|ERP} records there can be "
    "{a gap|a delay|duplicate rows}.",
    "{Inbound form leads|Website enquiries} may not be "
    "{tracked automatically|logged|followed up} within {24 hours|one business day}.",
)
VALUE_TR = (
    "Biz bunu {ücretsiz|maliyetsiz} bir {analiz|ön ölçüm|kısa denetim} ile "
    "{ölçüyoruz|görünür kılıyoruz}: hangi adımın koptuğunu ve kaybın nerede oluştuğunu raporluyoruz.",
    "İlk adım olarak {ücretsiz|maliyetsiz} {tek sayfalık|kısa} bir "
    "{teknik analiz|bulgu raporu|akış ölçümü} hazırlıyoruz.",
)
VALUE_EN = (
    "We {measure|surface} this with a {free|no-cost} {analysis|baseline check|short audit}: "
    "which step breaks and where the loss sits.",
    "As a first step we prepare a {free|no-cost} {one-page|short} "
    "{technical analysis|findings report|flow check}.",
)
CTA_TR = (
    "Sitenizdeki bu eksikliği içeren 2 dakikalık analiz raporunu webchat üzerinden "
    "anında görmek ister misiniz?",
    "Bu kopukluğun ölçüm özetini sohbet üzerinden 2 dakikada paylaşalım mı?",
    "Aynı bulguyu kendi siteniz için görmek isterseniz webchat'ten tek satır yazmanız yeterli.",
)
CTA_EN = (
    "Would you like to see the 2-minute analysis report for your own site right in the webchat?",
    "Shall we share the measurement summary over chat in two minutes?",
    "One line in the webchat is enough and we will show the same finding for your site.",
)
_GREET_WORDS = {"merhaba", "selamlar", "hello", "hi", "iyi", "good"}


def _turkish_of(text: str) -> bool:
    low = (text or "").lower()
    return any(t in low for t in ("merhaba", "ekibi", "için", "değilse", "sayfa", "webchat'"))


def _swap_greeting(line: str, greeting: str) -> str:
    parts = line.split(" ", 1)
    if parts and parts[0].strip(",.:!").lower() in _GREET_WORDS:
        return greeting + (" " + parts[1] if len(parts) > 1 else "")
    return line


def vary(subject: str, body: str, *, host: str = "", company: str = "",
         seed: int | float | None = None, vary_subject: bool = False,
         turkish: bool | None = None) -> tuple[str, str]:
    """Mevcut metni bozmadan selamlama/kapanış varyasyonu (+isteğe bağlı konu)."""
    rng = random.Random(seed)
    out_body = str(body or "")
    out_subject = str(subject or "")
    tr = _turkish_of(out_body) if turkish is None else bool(turkish)
    greeting = spintax.render(GREETING_TR if tr else GREETING_EN, rng=rng,
                              values={"company": company or host})
    if out_body:
        first, sep, rest = out_body.partition("\n")
        out_body = f"{_swap_greeting(first, greeting)}{sep}{rest}"
    if out_body and "STOP" not in out_body.upper():
        closing = spintax.render(CLOSING_TR if tr else CLOSING_EN, rng=rng)
        out_body = f"{out_body.rstrip()}\n\n{closing}"
    if vary_subject and out_subject:
        tail = spintax.render(SUBJECT_TAIL_TR if tr else SUBJECT_TAIL_EN, rng=rng)
        out_subject = f"{out_subject} {tail}"[:120]
    return out_subject, out_body



def compose(*, host: str, company: str = "", hook: str = "", value: str = "",
            booking: str = "", turkish: bool = True, seed: int | float | None = None,
            cta: str = "") -> tuple[str, str]:
    """Sıfırdan Hook/Değer/CTA metni (rapor yapısına birebir uyumlu)."""
    rng = random.Random(seed)
    values: dict[str, Any] = {"company": company or host}
    hello = spintax.render(GREETING_TR if turkish else GREETING_EN, rng=rng, values=values)
    hook_txt = hook or spintax.render((HOOKS_TR if turkish else HOOKS_EN)[rng.randrange(3)],
                                      rng=rng, values=values)
    value_txt = value or spintax.render((VALUE_TR if turkish else VALUE_EN)[rng.randrange(2)],
                                        rng=rng, values=values)
    cta_txt = cta or spintax.render((CTA_TR if turkish else CTA_EN)[rng.randrange(3)],
                                    rng=rng, values=values)
    if booking:
        cta_txt = f"{cta_txt} {booking}"
    body = (f"{hello} {(company or host)}, {hook_txt} {value_txt}\n\n{cta_txt}\n\n"
            f"{spintax.render(CLOSING_TR if turkish else CLOSING_EN, rng=rng)}")
    subject = spintax.render(
        "{Teknik özet|Kısa bulgu|Ölçüm notu} — {company}" if turkish
        else "{Technical summary|Short finding|Measurement note} — {company}",
        rng=rng, values=values,
    )[:120]
    return subject, body


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Lane çıktısı: metin varyasyon motorunun sağlık raporu."""
    samples = [compose(host="ornek.com", company="Örnek", seed=s)[1] for s in range(8)]
    return {
        "samples": len(samples),
        "uniqueness": spintax.uniqueness_ratio(samples),
        "subject_tail_variants": len(spintax.variants(SUBJECT_TAIL_TR, 4, seed=7)),
        "languages": ["tr", "en"],
    }
