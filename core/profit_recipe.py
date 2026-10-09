"""Whitelabel kâr reçetesi: kanıtlanabilir aylık net ek gelir hesabı.

Tek doğruluk kaynağı (single source of truth):
  form mesajı -> /demo sayfası -> webchat kapanışı -> Telegram bildirimi
  AYNI rakamı bu modülden okur; URL'deki `profit` parametresi sadece çapraz
  kontrol içindir, hesap HER ZAMAN sunucu tarafında girdilerden üretilir
  (form / demo / kâr marjı karışıklığı formatlar arası sıfır).

Uydurma yok kuralı:
  - Her girdi bir kanıt satırıyla (değer + kaynak) listelenir.
  - Rakam girdilerden hesaplanır; 15.000-30.000 EUR "bant"ı için sonuca
    ZORLAMA YAPILMAZ — banda uymayan hesap olduğu gibi gösterilir
    (band_ok bayrağı ile). Bant, varsayılan girdilerle zaten 22.000 EUR'da.

Ortam değişkenleri (hepsi opsiyonel):
  AGENCY_DEMO_CLIENTS      (12)   portföydeki aktif müşteri sayısı
  AGENCY_PACKAGE_EUR       (1900) müşteri başına aylık paket fiyatı
  AGENCY_ATTACH_RATE       (1.0)  paket satılan müşteri oranı (0-1)
  WHITELABEL_LICENSE_EUR   (5000) altyapı lisans bedeli (tek seferlik)
  WHITELABEL_AMORT_MONTHS  (12)   lisansın amortisman süresi (ay)
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from pathlib import Path
from string import Template
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "nirvana" / "state" / "agency_demo_recipes.json"

BAND_MIN_EUR = 15000
BAND_MAX_EUR = 30000

_DEFAULTS = {
    "AGENCY_DEMO_CLIENTS": 12,
    "AGENCY_PACKAGE_EUR": 1900,
    "AGENCY_ATTACH_RATE": 1.0,
    "WHITELABEL_LICENSE_EUR": 5000,
    "WHITELABEL_AMORT_MONTHS": 12,
}


def _env_number(name: str) -> float:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        try:
            import config as _cfg  # type: ignore
            raw = str(getattr(_cfg, name, "") or "").strip()
        except Exception:
            raw = ""
    try:
        return float(raw.replace(",", "."))
    except (TypeError, ValueError):
        return float(_DEFAULTS[name])


def _clean(value: Any) -> str:
    return str(value or "").strip()


def slugify(agency: str) -> str:
    text = _clean(agency).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:80] or "partner-ajans"


def money(value: float) -> str:
    """TR biçimi: 22000 -> '22.000'."""
    try:
        return ("{:,}".format(int(round(float(value)))).replace(",", "."))
    except (TypeError, ValueError):
        return "0"


def compute(*, clients: Any = None, price_eur: Any = None,
            attach_rate: Any = None, license_eur: Any = None,
            amort_months: Any = None, clients_source: str = "") -> dict:
    """Girdilerden aylık net ek geliri ve kanıt satırlarını üretir."""
    try:
        ok_clients = str(clients or "").strip().isdigit() and int(clients) > 0
        n_clients = int(clients) if ok_clients else int(_env_number("AGENCY_DEMO_CLIENTS"))
    except (TypeError, ValueError):
        n_clients = int(_env_number("AGENCY_DEMO_CLIENTS"))
    src_clients = _clean(clients_source) or (
        "demo linkindeki ?clients= parametresi" if str(clients or "").strip()
        else "AGENCY_DEMO_CLIENTS varsayilani (linkte ?clients= ile gosterilebilir)")
    try:
        pkg = float(str(price_eur).replace(",", ".")) if str(price_eur or "").strip() else _env_number("AGENCY_PACKAGE_EUR")
    except (TypeError, ValueError):
        pkg = _env_number("AGENCY_PACKAGE_EUR")
    pkg = max(0.0, pkg)
    try:
        attach = float(str(attach_rate).replace(",", ".")) if str(attach_rate or "").strip() else _env_number("AGENCY_ATTACH_RATE")
    except (TypeError, ValueError):
        attach = _env_number("AGENCY_ATTACH_RATE")
    attach = min(1.0, max(0.0, attach))
    lic = max(0.0, float(license_eur) if license_eur is not None else _env_number("WHITELABEL_LICENSE_EUR"))
    months = max(1, int(amort_months) if amort_months is not None else int(_env_number("WHITELABEL_AMORT_MONTHS")))

    selling = n_clients * attach
    gross = selling * pkg
    amort = lic / months
    net = gross - amort
    headline = max(0, int(math.floor(net / 500.0) * 500)) if net > 0 else 0
    first_month = gross - lic
    band_ok = BAND_MIN_EUR <= headline <= BAND_MAX_EUR

    proof = [
        {"label": "Aktif musteri portfoyue", "value": "%d musteri" % n_clients, "source": src_clients},
        {"label": "Paket fiyati (musteri basina aylik)", "value": "%s EUR" % money(pkg),
         "source": "AGENCY_PACKAGE_EUR (dis pazar paket ortalamasi varsayimi)"},
        {"label": "Penetrasyon (paket satilan musteri orani)", "value": "%%%d" % round(attach * 100),
         "source": "AGENCY_ATTACH_RATE varsayimi - ajansla netlestirilebilir"},
        {"label": "Brut ek gelir", "value": "%s EUR/ay" % money(gross),
         "source": "hesap: %s x %s EUR" % (("%.2f" % selling).rstrip("0").rstrip("."), money(pkg))},
        {"label": "Altyapi lisansi amortismani", "value": "-%s EUR/ay" % money(amort),
         "source": "hesap: %s EUR lisans / %d ay" % (money(lic), months)},
        {"label": "Net ek gelir (aylik)", "value": "%s EUR" % money(headline),
         "source": "hesap: %s - %s = %s -> muhafazakar yuvarlama (500 asagi)"
                   % (money(gross), money(amort), money(net))},
        {"label": "Ilk ay (lisans tam dusum, en kotu senaryo)", "value": "%s EUR" % money(first_month),
         "source": "hesap: %s - %s" % (money(gross), money(lic))},
    ]
    formula = ("%d musteri x %s EUR/ay x %%%d penetrasyon - %s EUR lisans amortismani "
               "= %s EUR -> %s EUR net ek gelir (muhafazakar yuvarlama)"
               % (n_clients, money(pkg), round(attach * 100), money(amort), money(net), money(headline)))
    return {
        "clients": n_clients,
        "price_eur": round(pkg, 2),
        "attach_rate": attach,
        "license_eur": round(lic, 2),
        "amort_months": months,
        "gross_eur": round(gross, 2),
        "amort_eur": round(amort, 2),
        "net_eur": round(net, 2),
        "headline_eur": int(headline),
        "first_month_eur": round(first_month, 2),
        "band_ok": bool(band_ok),
        "band_min_eur": BAND_MIN_EUR,
        "band_max_eur": BAND_MAX_EUR,
        "formula": formula,
        "proof": proof,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _load_store() -> dict:
    try:
        if PATH.exists():
            data = json.loads(PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        logger.debug("kar recetesi deposu okunamadi", exc_info=True)
    return {}


def _save_store(data: dict) -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(PATH)
    except Exception:
        logger.warning("kar recetesi deposu yazilamadi", exc_info=True)


def _parse_profit(raw: Any) -> int | None:
    text = _clean(raw).replace(".", "").replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return int(float(m.group(0)))
    except ValueError:
        return None


def resolve(agency: str, *, clients: Any = None, requested_profit: Any = None) -> dict:
    """Ajans için kanonik reçeteyi hesaplar ve depoya kaydeder.

    URL'den gelen `profit` yalnizca capraz kontroldur: fark varsa kanonik
    hesap kazanir ve `profit_mismatch` bayragi kaldirilir — demo sayfasi,
    webchat ve bildirim AYNI rakami gosterir.
    """
    name = _clean(agency)[:120] or "Partner Ajans"
    recipe = compute(clients=clients, clients_source=(
        "demo linkindeki ?clients= parametresi" if str(clients or "").strip() else ""))
    recipe["agency"] = name
    recipe["slug"] = slugify(name)
    requested = _parse_profit(requested_profit)
    recipe["requested_profit_eur"] = requested
    recipe["profit_mismatch"] = bool(requested is not None and requested != int(recipe["headline_eur"]))
    store = _load_store()
    store[recipe["slug"]] = recipe
    _save_store(store)
    if recipe["profit_mismatch"]:
        logger.info("demo profit capraz kontrol: URL=%s kanonik=%s (kanonik kazandi)",
                    requested, recipe["headline_eur"])
    return recipe


def recall(agency: str) -> dict | None:
    """Ajansin en son kanonik recetesi (yoksa None)."""
    row = _load_store().get(slugify(agency))
    return row if isinstance(row, dict) and row.get("headline_eur") is not None else None


def context_payload(recipe: dict) -> dict:
    """Webchat oturumuna tasinan minik baglam (form/demo/webchat ayni rakam)."""
    out = {}
    for key in ("agency", "slug", "clients", "price_eur", "attach_rate", "license_eur",
                "amort_months", "gross_eur", "amort_eur", "net_eur", "headline_eur",
                "first_month_eur", "band_ok", "formula", "generated_at"):
        if key in (recipe or {}):
            out[key] = recipe[key]
    return out


def context_block(payload: dict, *, lang: str = "tr") -> str:
    """LLM sistem prompt'una eklenen [KÂR REÇETESİ] bloğu (uydurma rakam yasak)."""
    p = payload or {}
    profit = money(p.get("headline_eur") or 0)
    clients = int(p.get("clients") or 0)
    first = money(p.get("first_month_eur") or 0)
    if str(lang or "").lower().startswith("en"):
        return ("[PROFIT RECIPE - session context]\n"
                "Agency: %s\nCalculated net extra revenue: %s EUR/month "
                "(%d active clients; first month worst case %s EUR after the one-off "
                "5,000 EUR license).\n"
                "Use ONLY this figure. Never invent or change the number, the currency "
                "or the client count. Proof formula: %s"
                % (p.get("agency") or "-", profit, clients, first, p.get("formula") or "-"))
    return ("[KÂR REÇETESİ - oturum bağlamı]\n"
            "Ajans: %s\nHesaplanan net ek gelir: %s EUR/ay "
            "(%d aktif müşteri; ilk ay en kötü senaryo %s EUR, tek seferlik 5.000 EUR "
            "lisans sonrası).\n"
            "Yalnızca bu rakamı kullan. Rakamı, para birimini veya müşteri sayısını "
            "ASLA uydurma/değiştirme. Kanıt formülü: %s"
            % (p.get("agency") or "-", profit, clients, first, p.get("formula") or "-"))


def scarcity_suffix(payload: dict, lang: str = "tr") -> str:
    try:
        from core import whitelabel_slots as _slots  # type: ignore
        ag = ""
        try:
            ag = str((payload or {}).get("agency") or "")
        except Exception:
            ag = ""
        return _slots.scarcity_line(_slots.segment_of(ag), lang=lang) or ""
    except Exception:
        return ""


def context_block_plus(payload: dict, lang: str = "tr") -> str:
    base = context_block(payload, lang=lang)
    try:
        extra = scarcity_suffix(payload, lang)
        if extra and extra not in base:
            base = base + "\n" + extra
    except Exception:
        pass
    return base


def demo_greeting(payload: dict, *, lang: str = "tr") -> str:
    """BÖLÜM 4 karşılama: ajans adı + hesaplanan net kâr + güven cümlesi."""
    p = payload or {}
    agency = _clean(p.get("agency")) or "Partner Ajans"
    profit = money(p.get("headline_eur") or 0)
    clients = int(p.get("clients") or 0)
    if str(lang or "").lower().startswith("en"):
        return ("Welcome %s team. Your calculated monthly net extra revenue is %s EUR "
                "(%d active clients analyzed). We are ready to activate your Whitelabel "
                "infrastructure. Operations stay with us, billing stays with you under "
                "your own brand. One question: how many clients will you pitch first?"
                % (agency, profit, clients))
    return ("Hoş geldiniz %s ekibi. Hesaplanan aylık net ek kâr marjınız %s EUR "
            "(%d aktif müşteri portföyü analizi). Whitelabel altyapınızı aktif etmeye "
            "hazırız. Operasyon bizde, faturalandırma sizin logonuzla sizde. "
            "Tek soru: ilk ay kaç müşteriye paketi sunacaksınız?"
            % (agency, profit, clients))


def pay_url() -> str:
    """Canli Payoneer odeme linki (fail-open: bos ise sablon duzgun duser)."""
    try:
        import config as _cfg  # type: ignore
        return str(getattr(_cfg, "PAYONEER_PAYMENT_URL", "") or "").strip()
    except Exception:
        pass
    try:
        return (os.getenv("PAYONEER_PAYMENT_URL", "") or "").strip()
    except Exception:
        return ""


def demo_expires_ts(hours: int = 35) -> int:
    """Demo sandbox bitis damgasi (epoch sn): simdi + 35 saat."""
    try:
        return int(time.time()) + int(hours) * 3600
    except (TypeError, ValueError):
        return int(time.time()) + 35 * 3600


def pay_banner_tr() -> str:
    return ("Demo sureniz doluyor - Lisansi aktif edin, sandbox kalici Whitelabel ortama donusur.")


def pay_banner_en() -> str:
    return ("Your demo is expiring - activate the license and the sandbox becomes permanent Whitelabel.")


def chat_url(payload: dict) -> str:
    """Demo sayfası kapanış butonunun açacağı webchat adresi."""
    p = payload or {}
    from urllib.parse import urlencode
    return "/chat?" + urlencode({
        "agency": str(p.get("agency") or ""),
        "profit": str(p.get("headline_eur") or ""),
        "clients": str(p.get("clients") or ""),
    })


def render_page(recipe: dict, template_html: str) -> str:
    """BÖLÜM 3 demo sayfası: başlık, kanıt tablosu, işleyiş özeti, kapanış butonu."""
    r = recipe or {}
    rows = "".join(
        '<tr><td class="l">%s</td><td class="v">%s</td><td class="s">%s</td></tr>'
        % (_clean(row.get("label")), _clean(row.get("value")), _clean(row.get("source")))
        for row in (r.get("proof") or []) if isinstance(row, dict))
    if not r.get("band_ok"):
        band_note = ("Hesap %s-%s EUR bandının dışında — sonucu olduğu gibi gösteriyoruz "
                     "(uydurma yapılmaz). Bunu ajansın gerçek portföy hacmiyle birlikte "
                     "değerlendirin."
                     % (money(r.get("band_min_eur") or 0), money(r.get("band_max_eur") or 0)))
    else:
        band_note = ("Hesap %s-%s EUR güven aralığında; rakam girdilerden üretildi, "
                     "URL parametresiyle değiştirilemez."
                     % (money(r.get("band_min_eur") or 0), money(r.get("band_max_eur") or 0)))
    values = {
        "agency": _clean(r.get("agency")) or "Partner Ajans",
        "profit": str(r.get("headline_eur") or 0),
        "profit_fmt": money(r.get("headline_eur") or 0),
        "clients": str(r.get("clients") or 0),
        "gross_fmt": money(r.get("gross_eur") or 0),
        "net_fmt": money(r.get("net_eur") or 0),
        "amort_fmt": money(r.get("amort_eur") or 0),
        "license_fmt": money(r.get("license_eur") or 0),
        "price_fmt": money(r.get("price_eur") or 0),
        "first_month_fmt": money(r.get("first_month_eur") or 0),
        "formula": _clean(r.get("formula")),
        "proof_rows": rows,
        "band_note": band_note,
        "cta_label": "%s €/Ay Ek Gelir İçin Whitelabel Lisansını Aktif Et" % money(r.get("headline_eur") or 0),
        "scarcity": scarcity_suffix(r, "tr"),
        "pay_url": pay_url(),
        "demo_expires_ts": str(demo_expires_ts()),
        "pay_banner": pay_banner_tr(),
        "chat_url": chat_url(r),
        "generated_at": _clean(r.get("generated_at")),
    }
    if not _clean(template_html):
        template_html = FALLBACK_HTML
    try:
        return Template(template_html).safe_substitute(values)
    except Exception:
        logger.warning("demo sablonu render edilemedi", exc_info=True)
        return Template(FALLBACK_HTML).safe_substitute(values)


FALLBACK_HTML = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>${agency} Kâr Reçetesi</title></head><body>
<h1>${agency} İçin Hazırlanan Özel Whitelabel Kâr Reçetesi</h1>
<p>${clients} Aktif Müşteri Portföyü Analizi → Otomasyon Eklentisiyle Aylık ${profit_fmt} € Net Ek Gelir.</p>
<p>${formula}</p>
<p>Müşteriniz size öder, siz altyapı için sadece ${license_fmt} € lisans bedeli ödersiniz.
Operasyon bizden, kâr ve marka sizden.</p>
<p><a href="${chat_url}">${cta_label}</a></p>
</body></html>"""
