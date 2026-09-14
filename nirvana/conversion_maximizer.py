"""
Dönüşüm Maksimize Edici — Telegram satış kapanış katmanı.

Tek görevi var: müşteriyi Telegram'a çekmek ve niyet yüksek olduğu AN satışı
kapatmak. Oracle kotasına dokunmaz: saf Python + dosya, model çağrısı $0.

Dış kaynak beslemesi (hepsi mtime ile sıcak yüklenir):
  - knowledge/conversion.json  : taktik bankası (ağırlıklı, stage bazlı)
  - knowledge/b2b.json         : platform/sorun playbook'u
  - knowledge_state.json       : kapanan işlerden kazanan yığınlar
  - financial_loss_engine      : ölçülmüş kayıp bandı (uydurma yok)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import knowledge

logger = logging.getLogger(__name__)

# --- Huni aşaması tespiti (TR + EN) -----------------------------------------
_STAGE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("close", r"kabul\s*ediyorum|ödeme\s*yap(?:mak|acağım|acagim)|satın\s*al|başlayalım|"
              r"i\s*(?:accept|want\s*to\s*pay)|let'?s\s*start|pay(?:ment)?\s*now"),
    ("objection_price", r"pahalı|pahali|çok\s*yüksek|cok\s*yuksek|bütçe|butce|"
                        r"expensive|too\s*much|budget|pricey"),
    ("objection_delay", r"sonra\s*(dönüş|donus|görüş|gorus|konuşalim)|düşüneyim|dusuneyim|"
                        r"ileride|daha\s*sonra|let\s*me\s*think|maybe\s*later|later"),
    ("value", r"fiyat|ücret|ucret|ne\s*kadar|kaç\s*(?:dolar|eur|avro)|maliyet|"
              r"price|how\s*much|cost|quote"),
    ("proof", r"kanıt|kanit|ispat|rapor|delil|evidence|proof|report"),
    ("urgency", r"hemen|acil|bugün|bugun|asap|right\s*now|today|immediately"),
)


def detect_stage(user_text: str) -> str:
    text = (user_text or "").lower()
    for stage, pattern in _STAGE_PATTERNS:
        if re.search(pattern, text, re.I):
            return stage
    return "curiosity"


_BUY_RE = re.compile(_STAGE_PATTERNS[0][1], re.I)
_INTENT_MID_RE = re.compile(r"fiyat|ücret|ucret|ne kadar|kac|price|how much|"
                            r"kanıt|kanit|rapor|proof|report|nasıl|nasil|how", re.I)


def intent_level(user_text: str) -> str:
    """Low: bilgi topluyor · Mid: değerlendiriyor · High: satın almaya hazır."""
    text = user_text or ""
    if _BUY_RE.search(text):
        return "high"
    if _INTENT_MID_RE.search(text):
        return "mid"
    return "low"


def segment(brief: str | None) -> str:
    """SMB lane (form handoff) vs Enterprise lane (ilan başvurusu)."""
    if "ENTERPRISE APPLICATION BRIEF" in (brief or ""):
        return "enterprise"
    return "smb"


def loss_framing(brief: str | None, *, turkish: bool) -> str:
    """Ölçülmüş kayıp bandı + retainer amorti cümlesi. UYDURMA YOK."""
    from nirvana.financial_loss_engine import estimate_loss, loss_line

    dom_ms = 0
    blob = brief or ""
    try:
        start = blob.find("{")
        if start >= 0:
            data = json.loads(blob[start:])
            diag = data.get("diagnostics") or {}
            dom_ms = int(diag.get("dom_ms") or diag.get("dom_interaction_ms") or 0)
    except Exception:  # noqa: BLE001
        dom_ms = 0
    loss = estimate_loss(dom_ms=dom_ms)
    line = loss_line(loss, turkish=turkish)
    price = 2500
    currency = "EUR"
    try:
        from nirvana.payment import retainer_amount, retainer_currency
        price = int(retainer_amount())
        currency = str(retainer_currency())
    except Exception:  # noqa: BLE001
        pass
    if turkish:
        return (f"{line} Sabit kapsam {price} {currency} — sorunun ay oluşturduğu "
                "kayıp karşısında kendini amorti eden tek kalem.")
    return (f"{line} Fixed scope {price} {currency} — a single line item that "
            "pays itself back against the monthly loss.")


def _pick_tactics(stage: str, *, limit: int = 3) -> list[dict[str, str]]:
    rows = [t for t in knowledge.live_tactics() if str(t.get("stage")) == stage]
    rows.sort(key=lambda t: -float(str(t.get("weight") or 1)))
    return rows[:limit]


def _pacing_rule(intent: str, *, turkish: bool) -> str:
    if intent == "high":
        return ("YÜKSEK NİYET: TEK NET CTA. Şart metnini göster; 'kabul ediyorum' → sözleşme + "
                "doğrulanmış Payoneer talebi zinciri. Ek soru SORMA, gereksiz metin YOK."
                if turkish else
                "HIGH INTENT: ONE clean CTA. Show the terms; 'I accept' triggers the contract + "
                "verified Payoneer chain. No extra questions, no filler.")
    if intent == "mid":
        return ("ORTA NİYET: değer + kayıp çerçevesi; fiyat sorulursa kurala göre en sonda ver; "
                "tek kapsam sorusuyla bitir."
                if turkish else
                "MID INTENT: value + loss framing; price only at the end per rules; "
                "end with one scope question.")
    return ("DÜŞÜK NİYET: ödeme/sözleşme sözü ASLA açma; merak + kanıt ver, "
            "tek kapsam sorusuyla bitir."
            if turkish else
            "LOW INTENT: NEVER open payment/contract; give curiosity + evidence, "
            "end with one scope question.")


def close_block(*, user_text: str, brief: str | None, chat_id: int | None = None) -> str:
    """System prompt'a eklenen kapanış katmanı. Her turda dış kaynaktan beslenir."""
    stage = detect_stage(user_text)
    intent = intent_level(user_text)
    seg = segment(brief)
    turkish = bool(re.search(r"[çğıöşüÇĞİÖŞÜ]", user_text or ""))
    tactics = _pick_tactics(stage)
    lines = [f"\n[DÖNÜŞÜM KAPANIŞ KATMANI — DIŞ KAYNAK TAKTİK BANKASI | "
             f"aşama={stage} niyet={intent} segment={seg}]"]
    for i, t in enumerate(tactics, 1):
        body = (t.get("tr") if turkish and t.get("tr") else t.get("en") or t.get("tr") or "")
        lines.append(f"TAKTİK {i}: {body}")
    lines.append(f"NİYET TEMPOSU: {_pacing_rule(intent, turkish=turkish)}")
    if intent == "mid" or stage in {"value", "objection_price", "objection_delay"}:
        try:
            lines.append(f"ROI ÇERÇEVESİ: {loss_framing(brief, turkish=turkish)}")
        except Exception:  # noqa: BLE001
            logger.exception("loss framing failed")
    lines.append("KESİN KURALLAR: ücretsiz deneme/indirim ASLA; tek CTA; uydurma metrik yok; "
                 "müşterinin dilinde kıdemli mühendis tonu.")
    record(stage=stage, intent=intent, segment=seg, chat_id=chat_id, n_tactics=len(tactics))
    return "\n".join(lines)


def record(*, stage: str, intent: str, segment: str, chat_id: int | None,
           n_tactics: int) -> None:
    try:
        from nirvana.registry import state_path

        path = state_path("conversion_events.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "chat_id": chat_id, "stage": stage, "intent": intent,
               "segment": segment, "tactics": n_tactics}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        logger.exception("conversion record failed")


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Event özeti + taktik bankası sağlığı. Oracle kotası: 0 (saf dosya)."""
    del kwargs
    from collections import Counter

    from nirvana.registry import state_path

    path = state_path("conversion_events.jsonl")
    events: list[dict[str, Any]] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines()[-2000:]:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
    stages = Counter(str(e.get("stage")) for e in events)
    intents = Counter(str(e.get("intent")) for e in events)
    out = {
        "tactic_bank": len(knowledge.live_tactics()),
        "stages": dict(stages),
        "intents": dict(intents),
        "events": len(events),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    summary = state_path("conversion_summary.json")
    tmp = summary.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(summary)
    return out
