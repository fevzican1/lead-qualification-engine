"""Lane E — objection_handler_agent [GitHub Actions / Telegram].

Deterministic objection -> soft-landing replies. No model call, no cost: the
top objections ("fiyat yüksek", "güvenlik riski", "zaman yok", "düşünelim")
each get one honest answer whose pivot is the MEASURED audit evidence and the
concrete paid remediation plan. NO free implementation work is ever offered:
ödeme öncesi yalnızca kanıt (rapor/kart) sunulur; kod/yama/teslimat ücretli
retainer kapsamındadır.
Used by telegram_sales_bot's offline fallback and available as a CLI module.
"""
from __future__ import annotations

import json
import re
from typing import Any

from nirvana.payment import retainer_label

# (pattern, turkish_reply, english_reply)
RULES: tuple[tuple[str, str, str], ...] = (
    (
        r"(fiyat\w*\s*(çok\s*)?yüksek|pahalı|bütçe\w*\s*yok|budget|too expensive|price is high|cost is high)",
        "Fiyatı kaybınla kıyaslayın: ölçtüğümüz darboğaz (raporda numaralı) her ay ciro "
        "kaybettiriyor. Retainer, bu darboğazın mimari olarak kapatılması ve sürekli "
        "izlemedir; ücretsiz iş vermiyoruz çünkü ücretli mühendislik taahhüdüdür. "
        "Kanıt kartı ve kapanış planı elimizde — bak, karar ver.",
        "Compare the price to the loss: the bottleneck we measured (numbered in the "
        "report) leaks revenue every month. The retainer is closing that bottleneck "
        "architecturally plus continuous monitoring. We do not do unpaid engineering; "
        "the proof card and closure plan are in front of you — review and decide.",
    ),
    (
        r"(güvenlik|veri|erişim|security|data (safety|risk)|access risk)",
        "Haklısınız, erişim en hassas konu. Denetim salt okunur ve dışarıdandır: panel, "
        "kimlik veya veri indirmesi yok. Rapor açık standartlarla numaralanır; ödeme "
        "öncesi yalnızca bu kanıt paylaşılır, üretim erişimi sözleşmeden sonra başlar.",
        "Fair concern. The audit is read-only and external: no panel, no credentials, "
        "no data download. Reports are numbered against open standards; before payment "
        "you only receive the evidence — production access starts after the contract.",
    ),
    (
        r"(zaman[ıi]m yok|meşgul|daha sonra|no time|too busy|later)",
        "Anlıyorum. O yüzden süreç size az dokunur: kanıt ve kapanış planı bu sohbette, "
        "ödeme sonrası ilk tur otomatik başlar, haftalık özet buraya düşer. Sizin "
        "cüzdanınızdaki kayıp ise her hafta devam ediyor.",
        "Understood. That is why the process is light for you: evidence and the closure "
        "plan are in this chat, after payment the first sweep starts automatically and "
        "the weekly summary lands here. Meanwhile the leak keeps running.",
    ),
    (
        r"(düşünelim|düşünmem lazım|karar veremem|let me think|we'll think|not sure)",
        "Tabii, düşünün. Karar için ihtiyacınız olan üç şey zaten önünüzde: (1) ölçülmüş "
        "darboğaz, (2) rapor numarası ve kanıt kartı, (3) adım adım kapanış planı. "
        "Belirsizlik kalmadığında karar kolaylaşır.",
        "Of course. Everything you need to decide is already in front of you: (1) the "
        "measured bottleneck, (2) the report number and proof card, (3) the step-by-step "
        "closure plan. Decisions get easy when ambiguity is gone.",
    ),
    (
        r"(pilot (nasıl|nedir)|free pilot|ücretsiz pilot|trial|ücretsiz (iş|yama|hizmet))",
        f"Ücretsiz pilot/ücretsiz yama yok: ciddi mühendislik taahhüdür. Ödeme öncesi "
        f"sadece KANIT alırsınız — ölçülmüş bulgu, rapor no, kanıt kartı ve kapanış "
        f"planı. İşin kendisi aylık {retainer_label()} retainer kapsamında, ödeme "
        f"doğrulandığı an başlar.",
        f"No free pilot and no free patches: real engineering is a commitment. Before "
        f"payment you only receive EVIDENCE — the measured finding, report number, "
        f"proof card and closure plan. The work itself runs under the monthly "
        f"{retainer_label()} retainer and starts the moment payment is verified.",
    ),
)

PLAN_LINE_TR = ("Önce kanıt, sonra plan, sonra çözüm: ölçülmüş darboğaz → numaralı rapor → "
                "adım adım kapanış planı → doğrulanmış ödeme sonrası otomatik uygulama.")
PLAN_LINE_EN = ("Evidence first, then plan, then the fix: measured bottleneck → numbered "
                "report → step-by-step closure plan → automatic execution after verified payment.")

# Ödeme öncesi "nasıl çözeceksiniz?" anlatımı: plan + mikro simülasyon gösterilir,
# ama HİÇBİR iş yapılmaz — uygulama yalnızca doğrulanmış ödeme sonrası.
SOLUTION_WALKTHROUGH_TR = (
    "Nasıl çözeceğimizi adım adım gösterelim (şimdi yalnızca anlatım; uygulama "
    "ödeme doğrulandıktan sonra başlar):\n"
    "1) Ölçülmüş darboğaz: raporunuzda numaralı bulgu — {metric}.\n"
    "2) Kök neden: {root} — kanıt kartındaki işaretli nokta tam orası.\n"
    "3) Kapanış adımları: hook'un izole edilmesi → idempotent event köprüsü → "
    "retry/kuyruk → doğrulama. Her adımın çıktısı size raporlanır.\n"
    "4) Mikro simülasyon: bu darboğazın en küçük dilimi için önce/sonra gecikme "
    "karşılaştırmasını şimdi burada, verilerle gösterebilirim.\n"
    "Uygulama, aylık {retainer} retainer kapsamında ve ödeme doğrulanınca otomatik başlar."
)
SOLUTION_WALKTHROUGH_EN = (
    "Here is how we would close it, step by step (narration only now; execution "
    "starts after payment is verified):\n"
    "1) Measured bottleneck: the numbered finding in your report — {metric}.\n"
    "2) Root cause: {root} — the flagged point on the proof card is exactly there.\n"
    "3) Closure steps: isolate the hook → idempotent event bridge → retry/queue → "
    "verification. Every step's output is reported back to you.\n"
    "4) Micro-simulation: I can show a before/after latency comparison for the "
    "smallest slice of this bottleneck right here, with data.\n"
    "Execution runs under the monthly {retainer} retainer and starts automatically once payment is verified."
)

_SOLUTION_RE = (r"(nasıl\s*(çöz|düzelt|gider|hallet)|çözüm\s*(plan|sürec)|ne\s*yapacaksınız|"
                r"how\s*(will|would|do)\s*you\s*(fix|solve)|fix\s*it\s*how|solution\s*(plan|approach)|"
                r"mikro\s*simülasyon|micro\s*simulation|önce\s*/\s*sonra)")


def solution_walkthrough(*, turkish: bool = True, metric: str = "ölçülen gecikme",
                         root: str = "kopuk webhook/event akışı") -> str:
    tpl = SOLUTION_WALKTHROUGH_TR if turkish else SOLUTION_WALKTHROUGH_EN
    return tpl.format(metric=metric, root=root, retainer=retainer_label())


def handle(text: str, *, turkish: bool = False) -> str | None:
    """Return the matched objection reply, or None if this is not an objection."""
    raw = (text or "").strip()
    if not raw:
        return None
    for pattern, tr, en in RULES:
        if re.search(pattern, raw, re.IGNORECASE):
            return tr if turkish else en
    return None


def run_batch() -> dict[str, Any]:
    from nirvana.registry import state_path
    out_path = state_path("objection_rules.json")
    payload = {"rules": [p for p, _, _ in RULES],
               "plan_line": PLAN_LINE_TR,
               "plan_line_en": PLAN_LINE_EN,
               "solution_walkthrough": solution_walkthrough(turkish=True),
               "retainer": retainer_label()}
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"rules": len(RULES), "out": str(out_path)}
