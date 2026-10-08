"""Odeme gucu yuksek, sorunsuz pazar filtresi (TR HARIC).

Hedef: ikna gerektirmeyen, 5.000 EUR'yu zorluk cikarmadan odeyen sirketler.
- TR: host .tr ile biterse VEYA TR sinyali varsa MUTLAK RED (hicbir yere gonderim yok).
- Kabul: US + EU + DACH + Nordics + UK (premium odeme gecmisi guclu pazarlar).
- B2C / kisisel blog / sosyal profil RED.
- Butce < 5000 EUR RED.
"""
from __future__ import annotations
import re
from urllib.parse import urlparse
PREMIUM_TLDS = frozenset({"de", "at", "ch", "us", "uk", "co.uk", "se", "no", "dk", "fi", "nl", "ie", "lu", "be"})
PREMIUM_EU = frozenset({"fr", "es", "it", "pt", "pl", "cz", "sk", "hu", "ro", "bg", "hr", "si", "ee", "lv", "lt", "gr", "cy", "mt"})
PREMIUM_GENERIC = frozenset({"com", "net", "io", "co", "app", "eu", "org", "biz"})
MIN_BUDGET_EUR = 5000
_TR_HOST_RE = re.compile(r"\.tr\.?$|\.com\.tr\.?$", re.I)
_TR_WORD_RE = re.compile(r"turkiye|türkiye|istanbul|ankara|izmir|iletisim|iletişim|hizmetler|firmas|magaza|mağaza|siparis|sipariş|odeme|ödeme|kvkk|turkce|türkçe", re.I)
_TR_CHAR_RE = re.compile(r"[ğĞİışŞçÇöÖüÜ]")
_B2C_RE = re.compile(r"blogspot|wixsite|linktr|instagram|facebook|tiktok|personal blog|gmail\.com|hotmail|outlook", re.I)
def _host(url: str) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower().strip().rstrip(".")
    except ValueError:
        return ""
def is_turkey(url: str, text: str = "") -> bool:
    """TR sinyali: host ya da metin TR ise True (fail-closed: suphe = TR)."""
    h = _host(url)
    if not h:
        return True
    if _TR_HOST_RE.search(h):
        return True
    blob = "%s %s" % (h, text or "")
    if _TR_WORD_RE.search(blob):
        return True
    # TR karakter yogunlugu: tek harf degil, en az 2 TR karakter.
    if len(_TR_CHAR_RE.findall(blob)) >= 2:
        return True
    return False
def _tld_ok(host: str) -> bool:
    if not host or "." not in host:
        return False
    parts = host.split(".")
    last = parts[-1]
    last2 = ".".join(parts[-2:])
    if last2 in PREMIUM_TLDS or last in PREMIUM_TLDS or last in PREMIUM_EU:
        return True
    return last in PREMIUM_GENERIC
def qualifies_premium(url: str, text: str = "", budget: int = 5000) -> dict:
    """TR haric + premium pazar + B2B + 5k EUR. Donus: {ok, reason}."""
    if is_turkey(url, text):
        return {"ok": False, "reason": "TR_excluded"}
    blob = "%s %s" % (url or "", text or "")
    if _B2C_RE.search(blob):
        return {"ok": False, "reason": "b2c_excluded"}
    if not _tld_ok(_host(url)):
        return {"ok": False, "reason": "non_premium_market"}
    try:
        if int(budget or 0) < MIN_BUDGET_EUR:
            return {"ok": False, "reason": "budget_below_5000"}
    except (TypeError, ValueError):
        return {"ok": False, "reason": "budget_below_5000"}
    return {"ok": True, "reason": "premium_no_friction"}
