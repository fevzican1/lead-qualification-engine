"""Lane E — objection_handler_agent [GitHub Actions / Telegram].

Deterministic objection -> soft-landing replies. No model call, no cost: the
top objections each get one honest answer whose pivot is the MEASURED audit
evidence and the concrete paid remediation plan. NO free implementation work
is ever offered: payment-gated evidence only; code/patch/delivery is paid.
Used by telegram_sales_bot's offline fallback and available as a CLI module.
"""
from __future__ import annotations

import json
import re
from typing import Any

# (pattern, turkish_reply, english_reply) — SIRALAMA ONEMLI: spesifikten genele.
RULES: tuple[tuple[str, str, str], ...] = (
    (
        r"(ucretsiz (deneme|pilot|is|yama|hizmet)|free (trial|pilot|fix|work)|\btrial\b)",
        "Bu cozum yalnizca aylik retainer kapsaminda sunulur. Odeme dogrulanana kadar "
        "hicbir teknik islem yapilmaz. Seffaf kanit zinciri: olculmus darbogaz -> numarali rapor -> "
        "kanit karti -> adim adim kapanis plani -> dogrulanmis odeme sonrasi tek seferde uygulama. "
        "Odeme oncesi yalnizca cozum yol haritasini ve anlik mikro-kaniti gosteririz.",
        "This solution is offered exclusively under the monthly retainer. No technical work is performed "
        "before verified payment. Transparent evidence chain: measured bottleneck -> numbered report -> "
        "proof card -> step-by-step closure plan -> one-shot implementation after verified payment. "
        "Before payment we only show the solution roadmap and instant micro-proof.",
    ),
    (
        r"(fiyat\w*\s*(cok\s*)?(yuksek|yuksek)|pahali|butce\w*\s*yok|budget|too expensive|price is high|cost is high)",
        "Once degeri konusalim: olctugumuz darbogaz (raporda numarali) her ay ciro "
        "kaybettiriyor ve kapanisi bu kaybi dogrudan keser. Ayrica Oracle izleme "
        "slotlarimiz sinirlidir ve 24 saatlik rezervasyonla ilerleriz; onay gecikirse "
        "slot baska firmaya acilir. Retainer, bu darbogazin mimari olarak kapatilmasi "
        "ve surekli izlemedir. Uygulama dogrulanmis odeme (PAID) sonrasi baslar; "
        "odeme oncesi yalnizca kanit (rapor + kart + kapanis plani) ve mikro-kanit "
        "sunulur, kod/yapilandirma PAID sonrasina saklanir.",
        "Value first: the bottleneck we measured (numbered in the report) leaks "
        "revenue every month and closing it stops the loss directly. Also, our Oracle "
        "monitoring slots are limited and we proceed on a 24-hour reservation; if "
        "approval is delayed the slot opens to another company. The retainer closes "
        "that bottleneck architecturally plus continuous monitoring. Implementation "
        "starts only after verified payment (PAID); before payment we share only the "
        "evidence (report + card + closure plan) and micro-proof — code/config is "
        "held back until PAID.",
    ),
    (
        r"(guvenlik|risc|veri(\s|nin|ye)?\s*(guven|risk|ihlali)?|erisim|security|data (safety|risk|breach)|access risk)",
        "Haklisiniz, erisim en hassas konu. Denetim salt okunur ve disaridandir: panel, "
        "kimlik veya veri indirmesi yok. Rapor acik standartlarla numaralanir; odeme "
        "oncesi yalnizca bu kanit paylasilir, uretim erisimi sozlesmeden sonra baslar.",
        "Fair concern. The audit is read-only and external: no panel, no credentials, "
        "no data download. Reports are numbered against open standards; before payment "
        "you only receive the evidence.",
    ),
    (
        r"(zaman[iı]m yok|mesgul|daha sonra|no time|too busy|later)",
        "Anliyorum. O yuzden surec size az dokunur: kanit ve kapanis plani bu sohbette, "
        "odeme sonrasi ilk tur otomatik baslar, haftalik ozet buraya duser.",
        "Understood. That is why the process is light for you: evidence and the closure "
        "plan are in this chat, after payment the first sweep starts automatically and "
        "the weekly summary lands here.",
    ),
    (
        r"(dusunelim|dusunmem lazim|karar veremem|let me think|we'll think|not sure)",
        "Tabii, dusunun. Karar icin ihtiyaciniz olan uc sey zaten onunuzde: (1) olculmus "
        "darbogaz, (2) rapor numarasi ve kanit karti, (3) adim adim kapanis plani. "
        "Belirsizlik kalmadiginda karar kolaylasir.",
        "Of course. Everything you need to decide is already in front of you: (1) the "
        "measured bottleneck, (2) the report number and proof card, (3) the step-by-step "
        "closure plan. Decisions get easy when ambiguity is gone.",
    ),
)


PLAN_LINE_TR = ("Once kanit, sonra plan, sonra cozum: olculmus darbogaz -> numarali rapor -> "
                "adim adim kapanis plani -> dogrulanmis odeme sonrasi otomatik uygulama.")
PLAN_LINE_EN = ("Evidence first, then plan, then the fix: measured bottleneck -> numbered "
                "report -> step-by-step closure plan -> automatic execution after verified payment.")

# Odeme oncesi "nasil cozeceksiniz?" anlatimi: plan + mikro simulasyon gosterilir,
# ama HICBIR is yapilmaz — uygulama yalnizca dogrulanmis odeme sonrasi.
SOLUTION_WALKTHROUGH_TR = (
    "Nasil cozecegimizi adim adim gosterelim (simdi yalnizca anlatim; uygulama "
    "odeme dogrulandiktan sonra baslar):\n"
    "1) Olculmus darbogaz: raporunuzda numarali bulgu — {metric}.\n"
    "2) Kok neden: {root} — kanit kartindaki isaretli nokta tam orasi.\n"
    "3) Kapanis adimlari: hook'un izole edilmesi -> idempotent event koprusu -> "
    "retry/kuyruk -> dogrulama. Her adimin ciktisi size raporlanir.\n"
    "4) Mikro simulasyon: bu darbogazin en kucuk dilimi icin once/sonra gecikme "
    "karsilastirmasini simdi burada, verilerle gosterebilirim.\n"
    "Uygulama, aylik {retainer} retainer kapsaminda ve odeme dogrulaninca otomatik baslar."
)
SOLUTION_WALKTHROUGH_EN = (
    "Here is how we would close it, step by step (narration only now; execution "
    "starts after payment is verified):\n"
    "1) Measured bottleneck: the numbered finding in your report.\n"
    "2) Root cause: the flagged point on the proof card is exactly there.\n"
    "3) Closure steps: isolate the hook -> idempotent event bridge -> retry/queue -> "
    "verification. Every step's output is reported back to you.\n"
    "4) Micro-simulation: I can show a before/after latency comparison for the "
    "smallest slice of this bottleneck right here, with data.\n"
    "Execution runs under the monthly retainer and starts automatically once payment is verified."
)

_SOLUTION_RE = (r"(nasil\s*(coz|duzelt|gider|hallet)|cozum\s*(plan|surec)|ne\s*yapacaksiniz|"
                r"how\s*(will|would|do)\s*you\s*(fix|solve)|fix\s*it\s*how|solution\s*(plan|approach)|"
                r"mikro\s*simulasyon|micro\s*simulation|once\s*/\s*sonra)")


def _norm(text: str) -> str:
    """Turkce karakterleri ASCII'ye indirger (coz icin) — kural eslesmesi icin."""
    table = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
                           "Ç": "c", "Ğ": "g", "İ": "i", "Ö": "o", "Ş": "s", "Ü": "u"})
    return (text or "").translate(table)


def solution_walkthrough(*, turkish: bool = True, metric: str = "olculen gecikme",
                         root: str = "kopuk webhook/event akisi") -> str:
    if turkish:
        tpl = SOLUTION_WALKTHROUGH_TR
        return tpl.format(metric=metric, root=root, retainer="2.500 EUR")
    return SOLUTION_WALKTHROUGH_EN


def handle(text: str, *, turkish: bool = False) -> str | None:
    """Return the matched objection reply, or None if this is not an objection."""
    raw = (text or "").strip()
    if not raw:
        return None
    norm = _norm(raw)
    # Kullanicinin cozum sorusu -> yol haritasi + mikro-kanit anlatimi
    # (yalnizca anlatim; uygulama odeme sonrasi). Reaktif: kullanici sorunca.
    if re.search(_SOLUTION_RE, norm, re.IGNORECASE):
        return solution_walkthrough(turkish=turkish)
    for pattern, tr, en in RULES:
        if re.search(pattern, norm, re.IGNORECASE):
            return tr if turkish else en
    return None


def run_batch() -> dict[str, Any]:
    from nirvana.registry import state_path
    out_path = state_path("objection_rules.json")
    payload = {"rules": [p for p, _, _ in RULES],
               "plan_line": PLAN_LINE_TR,
               "plan_line_en": PLAN_LINE_EN,
               "solution_walkthrough": solution_walkthrough(turkish=True),
               "retainer": "2.500 EUR"}
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"rules": len(RULES), "out": str(out_path)}
