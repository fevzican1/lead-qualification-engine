"""Lane AX-2 — financial_shock_card: Anlik VLA tabanli "Kayip Para" kanit karti.

Raporun "Financial Shock" taktigi: musteriye soyle baglamak yerine, ONUN
sistemindeki O ANKI somut hatayi (yavaslik, kirik akis, kacan musteri)
gorsel olarak onune koymak ve bunun GUNLUK PARA KAYBINA donusmus halini
gostermek.

UYDURMA YOK KURALI (her katman icin ayni):
  - Rakam YALNIZCA olculmus veriden turetilir. Kaynak yoksa kart uretilmez.
  - `estimate_loss` yalnizca dom_ms / slow_count / bad_count girdilerinden
    bir kayip BANDI uretir (yuzde araligi) — mutlak EUR uydurmaz.
  - EUR rakami, olculmus gelir/donusum tabanindan hesaplanir:
        gunluk_kayip = aylik_ciro x kayip_bandi
        15 gunluk     = gunluk_kayip x 15
    girdi yoksa bu degerler de uretilmez.
  - Kart her zaman "olculdu / hesaplandi" etiketini tasir.

Entegrasyon noktasi:
  - `conversion_maximizer.loss_framing` ile ayni bandi kullanir (tek dogruluk).
  - `retainer_report_agent` PDF'ine "Financial Shock" blogu ekleyebilir.
  - `webchat` ilk mesajda 7 kanit noktasinin ozetini verirken bu blogu
    uretir (bkz. build_shock_summary).

Maliyet: sifir (saf Python, model yok, ag yok).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from nirvana.financial_loss_engine import estimate_loss
from nirvana.registry import state_path

logger = logging.getLogger(__name__)

# 15 gunluk eylemsizlik maliyeti penceresi (raporda tanimli).
COI_WINDOW_DAYS = 15

# Kayip bandi -> orta nokta (oranlama) — yalnizca hesap icin.
_BAND_MID = {"2-5": 0.035, "5-8": 0.065, "8-10": 0.09, "10-12": 0.11}

_RE_MONEY = re.compile(
    r"(?P<cur>€|\$|EUR|USD|GBP)?\s?(?P<num>[\d][\d.,]{2,})\s*(k|K|bin|milyon)?",
    re.IGNORECASE)


def _num(text: str) -> float | None:
    """'22.000 EUR' / '$3.5M' -> sayi. Uydurma yok: cozulemezse None."""
    raw = (text or "").strip()
    if not raw:
        return None
    m = _RE_MONEY.search(raw)
    if not m:
        return None
    s = m.group("num").replace(".", "").replace(",", ".")
    try:
        val = float(s)
    except ValueError:
        return None
    suffix = (m.group(3) or "").lower()
    if suffix in ("k", "bin"):
        val *= 1_000
    elif suffix == "milyon":
        val *= 1_000_000
    return val


def _currency(text: str, default: str = "EUR") -> str:
    m = _RE_MONEY.search(text or "")
    cur = (m.group("cur") if m else "") or ""
    cur = cur.strip().upper()
    if cur in ("EUR", "€"):
        return "EUR"
    if cur in ("USD", "$"):
        return "USD"
    if cur == "GBP":
        return "GBP"
    return default


def extract_revenue(text: str) -> dict[str, Any]:
    """Metinden OLCULMUS gelir/donusum tabanini cikar. Bulunamazsa bos."""
    blob = text or ""
    out: dict[str, Any] = {}
    for pat in (
        r"(?:aylik\s*(?:ciro|gelir|revenue)|monthly\s*(?:revenue|turnover))"
        r"\D{0,40}?(?P<v>[\d][\d.,]{2,})",
        r"(?P<v>[\d][\d.,]{2,})\s*(?:EUR|€|USD|\$)\s*(?:/\s*(?:ay|month))?"
        r"\D{0,25}(?:ciro|gelir|revenue|turnover)",
    ):
        m = re.search(pat, blob, re.IGNORECASE)
        if m:
            val = _num(m.group("v"))
            if val and val > 0:
                out["revenue_eur"] = val
                out["currency"] = _currency(blob)
                break
    return out


def build_shock(diagnostics: dict[str, Any] | None = None,
                *, revenue_eur: float | None = None,
                currency: str = "EUR",
                issue: str = "",
                report_id: str = "",
                days: int = COI_WINDOW_DAYS) -> dict[str, Any]:
    """Financial Shock kanit blogu. Kaynak yoksa `ok=False` doner (uydurma yok)."""
    diag = dict(diagnostics or {})
    dom_ms = int(diag.get("dom_ms") or diag.get("dom_interactive_ms") or 0)
    slow = int(diag.get("slow_count") or diag.get("slow_requests") or 0)
    bad = int(diag.get("bad_count") or diag.get("bad_requests") or 0)

    loss = estimate_loss(dom_ms=dom_ms, slow_count=slow, bad_count=bad)
    band = str(loss.get("loss_band_pct") or "")
    mid = _BAND_MID.get(band)

    rev = float(revenue_eur or 0.0)
    daily = 0.0
    window = 0.0
    if rev > 0 and mid:
        daily = rev * mid
        window = daily * max(1, int(days))

    proof: list[dict[str, Any]] = []
    if dom_ms:
        proof.append({"label": "DOM gecikmesi (olculdu)", "value": f"{dom_ms} ms",
                      "source": "tarayici olcumu"})
    if slow:
        proof.append({"label": "Yavas istek sayisi (olculdu)", "value": str(slow),
                      "source": "tarayici olcumu"})
    if bad:
        proof.append({"label": "Hatali istek sayisi (olculdu)", "value": str(bad),
                      "source": "tarayici olcumu"})
    if rev > 0 and mid:
        proof.append({"label": "Gunluk kayip (hesaplandi)",
                      "value": f"{daily:,.0f} {currency}",
                      "source": f"aylik ciro x %{band} kayip bandi"})
        proof.append({"label": f"{days} gunluk birikmis kayip (hesaplandi)",
                      "value": f"{window:,.0f} {currency}",
                      "source": f"gunluk kayip x {days} gun"})

    if not proof:
        return {"ok": False,
                "reason": "olculmus veri yok - kart uretilmedi (uydurma yasagi)",
                "band_pct": band, "loss": loss}

    if daily > 0:
        headline = f"Her gun ortalama {daily:,.0f} {currency} kaybediyorsunuz."
        en = f"You are losing an average of {daily:,.0f} {currency} per day."
    else:
        headline = ("Olculen darbogaz donusum hunisinde %{0} bandinda kayip riski "
                    "tasiyor.").format(band)
        en = f"The measured bottleneck carries a {band}% conversion-loss risk band."
    punch = "Bu altyapi bu kaybi saniyeler icinde kapatir."
    punch_en = "This infrastructure closes that leak within seconds."

    return {
        "ok": True,
        "band_pct": band,
        "loss": loss,
        "revenue_eur": rev,
        "currency": currency,
        "daily_loss": round(daily, 2),
        "window_days": int(days),
        "window_loss": round(window, 2),
        "headline": headline,
        "punch": punch,
        "headline_en": en,
        "punch_en": punch_en,
        "issue": issue or "hizmet akisi darbogazi",
        "report_id": report_id,
        "proof": proof,
        "disclaimer": ("Rakamlar yalnizca olculmus tarayici verisinden turetilmistir; "
                       "tahmin bandi acikca belirtilmistir."),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build_shock_summary(lead: dict[str, Any] | None = None) -> str:
    """Tek satirlik vurus cümlesi (form / webchat / Telegram icin)."""
    lead = lead or {}
    blob = json.dumps(lead, ensure_ascii=False, default=str)
    rev = extract_revenue(blob)
    # Dogrudan alan varsa onu kullan (metin cikarimi ikincil).
    direct = lead.get("revenue_eur")
    if not rev.get("revenue_eur") and direct:
        try:
            val = float(direct)
            if val > 0:
                rev = {"revenue_eur": val,
                       "currency": str(lead.get("currency") or "EUR").upper()}
        except (TypeError, ValueError):
            pass
    diag = lead.get("diagnostics") if isinstance(lead.get("diagnostics"), dict) else {}
    sh = build_shock(diag, revenue_eur=rev.get("revenue_eur"),
                     currency=rev.get("currency", "EUR"),
                     issue=str(lead.get("issue") or lead.get("pain_point") or ""),
                     report_id=str(lead.get("report_id") or ""))
    if not sh.get("ok"):
        return ""
    return f"{sh['headline']} {sh['punch']}"


def state_path_for(domain: str) -> Any:
    safe = re.sub(r"[^a-z0-9.\-]", "_", (domain or "unknown").strip().lower())
    return state_path(f"shock_{safe}.json")


def save_shock(domain: str, shock: dict[str, Any]) -> Any:
    p = state_path_for(domain)
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(shock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError:
        logger.debug("shock kaydedilemedi", exc_info=True)
    return p


def load_shock(domain: str) -> dict[str, Any] | None:
    p = state_path_for(domain)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return None


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Runner lane: kayitli son olculmus tanilardan kart uret."""
    out: dict[str, Any] = {"cards": [],
                           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    src = kwargs.get("leads")
    if not src:
        try:
            p = state_path("verified_queue.json")
            src = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except (OSError, ValueError):
            src = []
    if isinstance(src, dict):
        src = src.get("rows") or src.get("items") or []
    for row in (src or [])[:20]:
        if not isinstance(row, dict):
            continue
        domain = str(row.get("domain") or row.get("url") or "").strip()
        if not domain:
            continue
        sh = build_shock(row.get("diagnostics") or {},
                         revenue_eur=(row.get("revenue_eur")),
                         issue=str(row.get("issue") or row.get("pain_point") or ""),
                         report_id=str(row.get("report_id") or ""))
        if sh.get("ok"):
            save_shock(domain, sh)
            out["cards"].append({"domain": domain, "daily_loss": sh.get("daily_loss"),
                                 "band_pct": sh.get("band_pct")})
    p = state_path("financial_shock.json")
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass
    return {**out, "out": str(p)}


def self_test() -> int:
    # 1) Kaynak yoksa kart uretilmez (uydurma yasagi).
    empty = build_shock({}, revenue_eur=None)
    assert empty.get("ok") is False, "olculmus veri olmadan kart uretilmemeli"

    # 2) Olculmus veriyle kart uretilir ve rakamlar tutarli.
    sh = build_shock({"dom_ms": 4200, "slow_count": 4, "bad_count": 2},
                     revenue_eur=22000, currency="EUR", report_id="DS-TEST-1")
    assert sh.get("ok") is True
    assert sh["band_pct"] == "10-12", sh["band_pct"]
    # 22000 * 0.11 = 2420/gun ; 15 gun = 36300
    assert abs(sh["daily_loss"] - 2420.0) < 0.01, sh["daily_loss"]
    assert abs(sh["window_loss"] - 36300.0) < 0.01, sh["window_loss"]
    assert "2,420 EUR" in sh["headline"] or "2420 EUR" in sh["headline"], sh["headline"]
    assert "saniyeler icinde kapatir" in sh["punch"]
    assert sh["proof"] and all(p.get("source") for p in sh["proof"])

    # 3) Metinden ciro cikarimi.
    rev = extract_revenue("Aylik ciro 45.000 EUR, gunluk 1200 EUR")
    assert rev.get("revenue_eur") == 45000.0, rev

    # 4) Ozet cumlesi uretilir.
    s = build_shock_summary({"diagnostics": {"dom_ms": 4200, "slow_count": 4,
                                             "bad_count": 2},
                             "revenue_eur": 22000})
    assert "kaybediyorsunuz" in s, s

    # 5) Kayit/oku round-trip.
    save_shock("shock-test.de", sh)
    got = load_shock("shock-test.de")
    assert got and got.get("band_pct") == "10-12"

    print("FINANCIAL_SHOCK_CARD SELF-TEST OK: uydurma-yok + hesap tutarli + round-trip")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
