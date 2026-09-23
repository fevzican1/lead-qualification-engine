"""CRM / n8n aktarımı ve randevu bağlama akışı (görüşme sonrası otomasyon).

Rapor (Otonom Randevu Bağlama ve Lead Scoring Akışı): görüşme özeti n8n webhook'u
üzerinden CRM'e aktarılır; yüksek skorlu adaylar sohbetten çıkmadan entegre
takvimden (Cal.com / Google Calendar) randevu seçebilir.

- N8N_WEBHOOK_URL tanımlıysa özet POST edilir (httpx, 6 sn, $0).
- Ağ/talep hatasında kayıt `handoff_outbox.jsonl` kuyruğuna yazılır; sonraki
  turlarda otomatik denenir (5 denemede dead-letter).
- Webhook tanımsızsa akış sessizce no-op olur (hattı durdurmaz).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Iterable

import config

logger = logging.getLogger(__name__)

STATE_DIR = config.ROOT / "nirvana" / "state"
OUTBOX = STATE_DIR / "handoff_outbox.jsonl"
MAX_ATTEMPTS = 5


def webhook_url() -> str:
    for key in ("N8N_WEBHOOK_URL", "CRM_WEBHOOK_URL"):
        url = (os.getenv(key) or "").strip()
        if url.startswith("http"):
            return url
    return ""


def payload(session: dict[str, Any], *, score: int = 0, summary: str = "",
            stage: str = "", contact: str = "", booking: str = "",
            kind: str = "webchat") -> dict[str, Any]:
    """n8n/CRM için tek tip görüşme zarfı (PII minimum: kullanıcının verdiği kadar)."""
    return {
        "kind": kind,
        "sid": str((session or {}).get("sid") or ""),
        "name": str((session or {}).get("name") or "")[:80],
        "lang": str((session or {}).get("lang") or "tr")[:8],
        "score": int(score or 0),
        "stage": stage,
        "summary": (summary or "")[:800],
        "contact": (contact or "")[:200],
        "booking_url": booking,
        "brief": str((session or {}).get("brief") or "")[:400],
        "history_tail": [
            {"role": str((m or {}).get("role") or ""),
             "content": str((m or {}).get("content") or "")[:400]}
            for m in list((session or {}).get("history") or [])[-4:]
        ],
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def enqueue(payload_row: dict[str, Any]) -> None:
    """Dayanıklı kuyruk: webhook ulaşmazsa kayıt diskte bekler."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with OUTBOX.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"attempts": 0, "payload": payload_row},
                                ensure_ascii=False) + "\n")


def send(payload_row: dict[str, Any], *, timeout: float = 6.0) -> dict[str, Any]:
    """Webhook'a tek deneme; tanımsızsa {skipped: True} döner (no-op)."""
    url = webhook_url()
    if not url:
        return {"delivered": False, "skipped": True, "reason": "webhook_not_configured"}
    try:
        import httpx

        response = httpx.post(url, json=payload_row, timeout=float(timeout))
        ok = 200 <= int(response.status_code) < 300
        if not ok:
            logger.info("n8n handoff HTTP %s", response.status_code)
        return {"delivered": ok, "status": int(response.status_code)}
    except Exception as exc:  # noqa: BLE001
        logger.info("n8n handoff hatası: %s", exc)
        return {"delivered": False, "error": str(exc)[:140]}


def notify(payload_row: dict[str, Any], *, timeout: float = 6.0) -> dict[str, Any]:
    """Gönder; başarısızsa outbox'a yaz (kayıp yok)."""
    result = send(payload_row, timeout=timeout)
    if not result.get("delivered") and not result.get("skipped"):
        enqueue(payload_row)
        result["queued"] = True
    return result



def _read_outbox() -> list[dict[str, Any]]:
    if not OUTBOX.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in OUTBOX.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return rows


def _write_outbox(rows: Iterable[dict[str, Any]]) -> None:
    tmp = OUTBOX.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                   encoding="utf-8")
    tmp.replace(OUTBOX)


def flush(*, limit: int = 20, timeout: float = 6.0) -> dict[str, Any]:
    """Bekleyen kayıtları yeniden dene; kalıcı hatalar dead-letter olur."""
    rows = _read_outbox()
    if not rows:
        return {"pending": 0, "delivered": 0, "dead": 0}
    keep: list[dict[str, Any]] = []
    delivered = dead = 0
    for row in rows[: int(limit)]:
        attempt = int(row.get("attempts") or 0) + 1
        result = send(row.get("payload") or {}, timeout=timeout)
        if result.get("delivered"):
            delivered += 1
            continue
        if result.get("skipped"):
            keep.append(row)  # webhook sonradan tanımlanabilir: kayıt korunur
            continue
        if attempt >= MAX_ATTEMPTS:
            dead += 1
            logger.warning("n8n handoff dead-letter (5 deneme): %s",
                           str((row.get("payload") or {}).get("sid"))[:24])
            continue
        row["attempts"] = attempt
        keep.append(row)
    keep.extend(rows[int(limit):])
    _write_outbox(keep)
    return {"pending": len(keep), "delivered": delivered, "dead": dead}


def outbox_size() -> int:
    return len(_read_outbox())


def status() -> dict[str, Any]:
    from nirvana import spin_engine  # type: ignore

    return {
        "webhook_configured": bool(webhook_url()),
        "outbox": outbox_size(),
        "max_attempts": MAX_ATTEMPTS,
        "booking_configured": bool(spin_engine.booking_url()),
    }


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Lane giriş noktası: bekleyen handoff kuyruğunu boşalt."""
    result = flush(limit=int(kwargs.get("limit", 20) or 20))
    result.update(status())
    return result
