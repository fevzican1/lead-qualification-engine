"""
Dil Bekçisi / Donanım Dil Denetçisi — küresel dil + üslup + bot-sızıntı denetimi.

Üç görevi var ($0, saf Python — Oracle kotasına dokunmaz):
  1. LOCALE MATCHING : hedef sitenin dilini (HTML lang etiketi + DOM kelimeleri)
     tespit eder; yanlış dilde form/mesaj gitme riskini sıfırlar.
  2. BOT SIZINTISI FİLTRESİ: "yapay zeka modeli olarak", "otomasyonla tespit
     etti" gibi botu ele veren ifadeleri gönderim ÖNCESİ temizler.
  3. KURUMSAL TON + İMLA: kıdemli teknik asistan ciddiyeti, %100 dil bilgisi.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# --- 1) Locale matching ------------------------------------------------------

LOCALE_LANG_CODES = {
    "tr": "tr", "en": "en", "de": "de", "fr": "fr", "es": "es", "it": "it",
    "pt": "pt", "nl": "nl", "ar": "ar", "ru": "ru", "pl": "pl",
}

# HTML lang yoksa DOM içerik kelimelerinden tespit (en sık diller).
_LOCALE_WORD_HINTS: tuple[tuple[str, str], ...] = (
    ("de", r"\b(und|der|die|das|kontakt|impressum|wir\s*über)\b"),
    ("fr", r"\b(nous|contactez|société|à\s*propos|le|la)\b"),
    ("es", r"\b(nosotros|contacto|empresa|sobre)\b"),
    ("it", r"\b(noi|contatti|azienda|chi\s*siamo)\b"),
    ("tr", r"\b(iletişim|iletisim|hakkımızda|hakkimizda|kurumsal|bize\s*ulaşın)\b"),
    ("pt", r"\b(sobre|contato|empresa)\b"),
    ("nl", r"\b(over\s*ons|contact|bedrijf)\b"),
    ("ru", r"\b(контакты|о\s*нас|компания)\b"),
    ("ar", r"\b(اتصل|من\s*نحن|شركة)\b"),
)

LOCALE_GREETINGS = {
    "tr": "Merhaba,", "en": "Hello,", "de": "Guten Tag,", "fr": "Bonjour,",
    "es": "Hola,", "it": "Salve,", "pt": "Olá,", "nl": "Goedendag,",
    "ru": "Здравствуйте,", "ar": "مرحباً,", "pl": "Dzień dobry,",
}


def detect_locale(html: str | None, *, fallback: str = "en") -> str:
    """HTML lang etiketi → yoksa içerik kelimeleri → fallback."""
    text = html or ""
    match = re.search(r'<html[^>]*\blang=["\']([a-zA-Z-]{2,10})["\']', text, re.I)
    if match:
        code = match.group(1).split("-")[0].lower()
        if code in LOCALE_LANG_CODES:
            return code
    for code, pattern in _LOCALE_WORD_HINTS:
        if re.search(pattern, text, re.I):
            return code
    return fallback


def greeting(locale: str) -> str:
    return LOCALE_GREETINGS.get(locale, LOCALE_GREETINGS["en"])


def locale_for_lead(lead: dict[str, Any] | None, *, fallback: str = "en") -> str:
    """Lead kaydından (html/page içerik alanlarından) dil tespiti."""
    if not isinstance(lead, dict):
        return fallback
    for key in ("html", "page_html", "content", "page_text", "body"):
        html = lead.get(key)
        if isinstance(html, str) and html:
            return detect_locale(html, fallback=fallback)
    meta = lead.get("locale") or lead.get("lang")
    code = str(meta or "").split("-")[0].lower()
    return code if code in LOCALE_LANG_CODES else fallback


# --- 2) Bot sızıntısı filtresi ----------------------------------------------

# Bu kalıpları içeren CÜMLELER tamamen çıkarılır (botu ele verir).
_BOT_LEAK_RE = re.compile(
    r"yapay\s*zek[aâ]|yapay\s*zeka\s*modeli|\bAI\b|\bA\.I\.\b|\bLLM\b|\bGPT\b|"
    r"deepseek|chatbot|\bbot(?:um|unuz|tur)\b|\brobot\b|robot\s*(?:değilim|degilim)|"
    r"insan\s*değilim|insan\s*degilim|dil\s*modeli|"
    r"otomasyonla\s*(?:tespit|algı|algi)|otomatik\s*(?:sistem|olarak)|"
    r"algoritma(?:m)?\s*(?:olarak|bana)|sistem\s*(?:bana|tarafımdan)\s*(?:bildirildi|algılandı)",
    re.I,
)

# Bot-reveal cümlesi silinince ton bozulmasın: eklenen imza.
_TONE_PATCH_TR = "Teknik inceleme ekibimizin ölçümü:"
_TONE_PATCH_EN = "Per our technical review team's measurement:"

# Ücretsiz/indirim teklifi ASLA (madde 57) — içeren cümle çıkarılır.
_FREE_OFFER_RE = re.compile(
    r"ücretsiz\s*(?:deneme|danış|daniş|inceleme|analiz)|indirim|kampanya|"
    r"free\s*(?:trial|consult|audit|analysis)|discount|no\s*charge|ücretsiz\s*teklif",
    re.I,
)

# İzinli linkler dışındaki URL'ler model çıktısından silinir (uydurma link yok).
_ALLOWED_URL_RE = re.compile(r"^(https?://)?(link\.payoneer\.com|t\.me/|www\.linkedin\.com/)", re.I)
_URL_RE = re.compile(r"https?://\S+", re.I)

_TR_SPELL: tuple[tuple[str, str], ...] = (
    ("Yalnış", "Yanlış"), ("yalnış", "yanlış"),
    ("müsteri", "müşteri"), ("Müsteri", "Müşteri"),
    ("tesekkur", "teşekkür"), ("Tesekkur", "Teşekkür"),
    ("lutfen", "lütfen"), ("Lutfen", "Lütfen"),
    ("bilgileriniz gecmemis", "bilgileriniz geçmemiş"),
    ("deil", "değil"), ("herhangı", "herhangi"),
)


def _strip_sentences(text: str, pattern: re.Pattern[str]) -> tuple[str, bool]:
    """Kalıbı içeren cümleleri siler; URL'leri korur."""
    if not pattern.search(text):
        return text, False
    parts = re.split(r"(https?://\S+)", text)  # URL'ler ayrı parça kalır
    out: list[str] = []
    hit = False
    for part in parts:
        if part.startswith(("http://", "https://")):
            out.append(part)
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", part)
        kept = [s for s in sentences if not pattern.search(s)]
        if len(kept) != len(sentences):
            hit = True
        out.append(" ".join(kept))
    return "".join(out), hit


# --- 3) Kurumsal ton + imla --------------------------------------------------

def _tone_fixes(text: str, *, turkish: bool, issues: list[str]) -> str:
    for bad, good in _TR_SPELL:
        if bad in text:
            text = text.replace(bad, good)
            issues.append(f"spelling:{bad}->{good}")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if text:
        first = text[0]
        if first.islower():
            text = first.upper() + text[1:]
            issues.append("capitalize")
        if turkish and text[0] == "I":
            text = "İ" + text[1:]
    if text and text[-1] not in ".!?…:":
        text += "."
        issues.append("punctuation")
    return text


def _trim(text: str, *, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "? ", "! ", "\n"):
        pos = cut.rfind(sep)
        if pos > limit * 0.5:
            return cut[: pos + 1].rstrip()
    return cut.rstrip() + "…"


def _sanitize_urls(text: str, issues: list[str]) -> str:
    def _keep(match: re.Match[str]) -> str:
        url = match.group(0)
        return url if _ALLOWED_URL_RE.match(url) else ""

    fixed = _URL_RE.sub(_keep, text)
    if fixed != text:
        issues.append("url:unallowed-removed")
    return fixed


def audit(text: str, *, turkish: bool = False, user_text: str = "",
          limit: int = 1200) -> tuple[str, list[str]]:
    """Gönderim öncesi son kapı: (düzeltilmiş metin, sorun listesi) döner.

    limit: Telegram mesajı 1200; FORM metni daha uzun olduğu için çağıran
    daha büyük bir sınır verir (form metni kırpılmaz).
    """
    issues: list[str] = []
    reply = text or ""
    reply, hit = _strip_sentences(reply, _BOT_LEAK_RE)
    if hit:
        issues.append("bot-leak:sentence-removed")
        reply = reply.rstrip()
        if not reply.endswith((".", "!", "?", ":")):
            reply += (" " + _TONE_PATCH_TR if turkish else " " + _TONE_PATCH_EN)
    reply, hit = _strip_sentences(reply, _FREE_OFFER_RE)
    if hit:
        issues.append("free-offer:sentence-removed")
    reply = _sanitize_urls(reply, issues)
    reply = _tone_fixes(reply, turkish=turkish, issues=issues)
    reply = _trim(reply, limit=max(200, int(limit)))
    return reply, issues


def audit_report(rows: list[str]) -> dict[str, Any]:
    """Toplu denetim (GitHub Actions / test için): örnek metinler → rapor."""
    out = {"checked": len(rows), "issues": []}
    for row in rows:
        _, issues = audit(row)
        if issues:
            out["issues"].append({"text": row[:80], "issues": issues})
    out["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return out


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Dil denetimi sağlık turu — örnek korpusu denetler, state'e yazar ($0)."""
    del kwargs
    from nirvana.registry import state_path

    samples = [
        "Merhaba, ben bir yapay zeka modeliyim. Sitenizde darboğaz tespit ettim.",
        "Ücretsiz deneme sunuyoruz, ayrıca indirim yapabiliriz.",
        "  ölçülen darboğaz checkout hunisinde kayıp üretiyor  ",
        "Rapor hazır: https://link.payoneer.com/Token?t=ABC ve https://evil.example/x",
    ]
    report = audit_report(samples)
    path = state_path("language_audit.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return report

