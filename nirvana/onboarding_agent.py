"""Lane F — onboarding_agent [Oracle VM].

Payment-verified welcome protocol. Gate is hard: the packet is only produced
when telegram_sessions reports fulfillment_ready (verified settlement + signed
contract). Until then this lane emits nothing at all.
"""
from __future__ import annotations

import json
import time
from typing import Any

import config
import telegram_sessions
from nirvana.payment import retainer_label
from nirvana.registry import state_path


def welcome_packet(row: dict[str, Any] | None, *, turkish: bool = True) -> str:
    """Build the onboarding text for a fulfillment-ready session row."""
    if not row or not row.get("chat_id"):
        return ""
    name = str(row.get("company") or row.get("name") or "").strip()
    hello = f"Merhaba {name} — " if name and turkish else (f"Hello {name} — " if name else "")
    if turkish:
        return (
            f"{hello}ödemeniz doğrulandı, hoş geldiniz. Karşılama protokolü:\n"
            "1) Hizmet şartları: haftalık zamanlanmış altyapı turları; hata, kesinti ve "
            "performans bulguları raporlanır. Kapsam dışı müdahale yok.\n"
            f"2) Retainer: aylık {retainer_label()}, aylık yenilenir.\n"
            "3) Erişim kılavuzu: bize yalnızca okunur izleme erişimi verin (read-only API "
            "anahtarı veya durum sayfası). Yönetici şifresi asla paylaşılmaz.\n"
            "4) İlk tur 24 saat içinde başlar; raporlar bu sohbete düşer.\n"
            "Kapsam sorunuz olursa buradan yazmanız yeterli."
        )
    return (
        f"{hello}payment verified, welcome aboard. Onboarding protocol:\n"
        "1) Terms: weekly scheduled infrastructure sweeps; errors, outages and performance "
        "findings are reported. No out-of-scope intervention.\n"
        f"2) Retainer: {retainer_label()} per month, renewed monthly.\n"
        "3) Access guide: provide read-only monitoring access only (read-only API key or a "
        "status page). Never share admin passwords.\n"
        "4) First sweep starts within 24h; reports land in this chat.\n"
        "Reply here with any scope questions."
    )


def packet_for(chat_id: int, *, turkish: bool = True) -> str:
    """Gated entrypoint: '' unless fulfillment_ready."""
    try:
        if not telegram_sessions.fulfillment_ready(int(chat_id)):
            return ""
    except (OSError, ValueError):
        return ""
    return welcome_packet(telegram_sessions._row(int(chat_id)), turkish=turkish)


def run_batch(*, chat_id: int | None = None) -> dict[str, Any]:
    """CLI/dry-run: emits the packet for one chat, logs state; sends nothing here."""
    if chat_id is None:
        return {"sent": 0, "note": "provide --chat-id; sending is handled by the sales bot"}
    text = packet_for(chat_id)
    out_path = state_path("onboarding_log.json")
    rows = []
    try:
        rows = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    rows.append({"chat_id": chat_id, "emitted": bool(text),
                 "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows[-200:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"chat_id": chat_id, "emitted": bool(text), "packet": text}
