"""Lane AF — delivery_worker [Oracle VM, event + timer].

TESLİMAT İŞÇİSİ: ödemesi doğrulanmış müşteriye KAYITLI hizmeti kusursuz teslim eder.

Karışmaz (en önemli kural):
- Her iş (chat_id, domain, service) üçlüsüne bağlıdır; işçi yalnızca işin
  KAYITLI servisini çalıştırır — başka işin servisine asla dokunmaz.
- Bir domain yalnızca bir chate bağlanır; başka chat aynı domaine iş açamaz.
- Raporlar RPT numarası + chat_id + job_id ile indekslenir; bot üzerinden
  müşteriye giden rapor yalnızca o chat'in kendi raporudur.

Kota/maliyet:
- Aynı anda en fazla MAX_CONCURRENT hizmet; tur başına LIMIT iş.
- Her hizmet turu = 1 hafif httpx isteği (maliyet sıfır, Always-Free güvenli).

Ödeme kapısı:
- telegram_sessions.fulfillment_ready(chat_id) olmadan teslimat başlamaz.

Müşteri hafızası:
- Müşteri tekrar döndüğünde customer_memory(chat_id) geçmiş rapor numaralarını,
  önceki sorunları ve ödeme durumunu bot sunar; ödeme teyitinden sonra o anki
  istenen hizmet kusursuz teslim edilir.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import httpx

from nirvana.registry import state_path
from nirvana.forget_guard import domain_of

JOBS_NAME = "delivery_jobs.json"
REPORTS_NAME = "delivery_reports.json"
MAX_CONCURRENT = 5
RUN_LIMIT = 2          # tur başına en fazla iş — Oracle Always-Free güvenli
TIMEOUT = 15.0
DEGRADED_MS = 1500

# Hizmet kataloğu: her hizmet TEK handler; dispatch tablodan, serbest çağrı yok.
SERVICES: dict[str, dict[str, Any]] = {
    "infra-sweep": {
        "label_tr": "Altyapı sağlık turu",
        "label_en": "Infrastructure health sweep",
    },
    "contact-audit": {
        "label_tr": "İletişim/form altyapısı denetimi",
        "label_en": "Contact/form infrastructure audit",
    },
    "renewal-monitor": {
        "label_tr": "Yenileme izleme turu",
        "label_en": "Renewal monitoring sweep",
    },
}


# --- state ------------------------------------------------------------------

def _read(path_json: str) -> Any:
    try:
        return json.loads(state_path(path_json).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path_json: str, data: Any) -> None:
    path = state_path(path_json)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_jobs() -> list[dict[str, Any]]:
    rows = _read(JOBS_NAME)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def save_jobs(jobs: list[dict[str, Any]]) -> None:
    _write(JOBS_NAME, jobs[-500:])


def load_reports() -> list[dict[str, Any]]:
    rows = _read(REPORTS_NAME)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def save_reports(reports: list[dict[str, Any]]) -> None:
    _write(REPORTS_NAME, reports[-500:])


def _stamp() -> str:
    return time.strftime("%Y%m%d", time.gmtime())


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def next_report_id(reports: list[dict[str, Any]] | None = None) -> str:
    """RPT-YYYYMMDD-NNNN — gün içinde artan, ünik rapor numarası."""
    rows = reports if reports is not None else load_reports()
    today = _stamp()
    seq = 0
    for r in rows:
        rid = str(r.get("report_id") or "")
        if rid.startswith(f"RPT-{today}-"):
            try:
                seq = max(seq, int(rid.rsplit("-", 1)[1]))
            except (IndexError, ValueError):
                continue
    return f"RPT-{today}-{seq + 1:04d}"


def next_job_id(jobs: list[dict[str, Any]] | None = None) -> str:
    rows = jobs if jobs is not None else load_jobs()
    today = _stamp()
    seq = 0
    for j in rows:
        jid = str(j.get("job_id") or "")
        if jid.startswith(f"JOB-{today}-"):
            try:
                seq = max(seq, int(jid.rsplit("-", 1)[1]))
            except (IndexError, ValueError):
                continue
    return f"JOB-{today}-{seq + 1:04d}"


# --- ödeme kapısı ------------------------------------------------------------

def payment_verified(chat_id: int) -> bool:
    """Tek doğruluk kaynağı: telegram_sessions.fulfillment_ready."""
    try:
        import telegram_sessions
        return bool(telegram_sessions.fulfillment_ready(int(chat_id)))
    except Exception:
        return False


# --- iş açma / izolasyon -----------------------------------------------------

def domain_owner(domain: str, jobs: list[dict[str, Any]] | None = None) -> int | None:
    """Domain'in bağlı olduğu chat (hiçbir iş karışmasın)."""
    dom = domain_of(domain)
    if not dom:
        return None
    for j in (jobs if jobs is not None else load_jobs()):
        if domain_of(str(j.get("domain") or "")) == dom:
            return int(j.get("chat_id") or 0) or None
    return None


def start_job(chat_id: int, domain: str, service: str) -> dict[str, Any]:
    """Yeni teslimat işi aç. Kurallar:
    - service katalogda olmalı (karışan servis YOK),
    - domain başka chate bağlıysa RED (karışıklık bekçisi),
    - aynı chat+domain+service zaten aktifse RED,
    - MAX_CONCURRENT doluysa RED (Oracle kotası).
    """
    jobs = load_jobs()
    if service not in SERVICES:
        return {"ok": False, "reason": "unknown_service", "services": sorted(SERVICES)}
    dom = domain_of(domain)
    if not dom:
        return {"ok": False, "reason": "no_domain"}
    owner = domain_owner(dom, jobs)
    if owner is not None and int(owner) != int(chat_id):
        return {"ok": False, "reason": "domain_bound_to_other_chat", "owner_chat_id": int(owner)}
    active = [j for j in jobs if j.get("status") in ("awaiting_payment", "queued", "running")]
    if len(active) >= MAX_CONCURRENT:
        return {"ok": False, "reason": "quota_full", "active": len(active)}
    if any(int(j.get("chat_id") or 0) == int(chat_id) and domain_of(str(j.get("domain") or "")) == dom
           and j.get("service") == service and j.get("status") in ("awaiting_payment", "queued", "running")
           for j in jobs):
        return {"ok": False, "reason": "already_active"}
    job = {
        "job_id": next_job_id(jobs),
        "chat_id": int(chat_id),
        "domain": dom,
        "service": service,
        "status": "queued" if payment_verified(chat_id) else "awaiting_payment",
        "created_at": _utcnow(),
        "runs": 0,
    }
    jobs.append(job)
    save_jobs(jobs)
    return {"ok": True, "job": job, "active_count": len(active) + 1}


def get_job(job_id: str) -> dict[str, Any] | None:
    for j in load_jobs():
        if j.get("job_id") == job_id:
            return j
    return None


def complete_job(job_id: str, *, reason: str = "completed") -> dict[str, Any]:
    jobs = load_jobs()
    jobs = [j for j in jobs if j.get("job_id") != job_id]
    save_jobs(jobs)
    return {"ok": True, "reason": reason}


def status_summary() -> dict[str, Any]:
    jobs = load_jobs()
    return {
        "active": len([j for j in jobs if j.get("status") in ("awaiting_payment", "queued", "running")]),
        "max_concurrent": MAX_CONCURRENT,
        "awaiting_payment": len([j for j in jobs if j.get("status") == "awaiting_payment"]),
        "queued": len([j for j in jobs if j.get("status") == "queued"]),
        "delivered_total": len([j for j in jobs if j.get("status") == "delivered"]),
        "jobs": [{"job_id": j.get("job_id"), "chat_id": j.get("chat_id"), "domain": j.get("domain"),
                  "service": j.get("service"), "status": j.get("status")} for j in jobs[-10:]],
    }


# --- hizmet handler'ları (her biri yalnız kendi işinin domain'ine bakar) ------

def _svc_infra_sweep(job: dict[str, Any]) -> dict[str, Any]:
    """Altyapı sağlık turu — yalnızca işin kayıtlı domain'i."""
    from nirvana.delivery_runner import check_domain
    result = check_domain(str(job["domain"]))
    findings = [f"{result['domain']}: HTTP {result['http']}, {result['ms']} ms"]
    return {"status": result["status"], "findings": findings}


def _svc_contact_audit(job: dict[str, Any]) -> dict[str, Any]:
    """İletişim/form altyapısı denetimi — tek istek, gözleme dayalı."""
    dom = str(job["domain"])
    started = time.monotonic()
    try:
        r = httpx.get(f"https://{dom}/contact-us", timeout=TIMEOUT,
                      headers={"User-Agent": "nirvana-delivery-worker/1.0"})
        elapsed = int((time.monotonic() - started) * 1000)
        status = "ok" if r.status_code < 400 and elapsed <= DEGRADED_MS else \
            ("down" if r.status_code >= 500 else "degraded")
        findings = [f"{dom}/contact-us: HTTP {r.status_code}, {elapsed} ms"]
    except httpx.HTTPError:
        status = "down"
        findings = [f"{dom}/contact-us: erişilemedi (bağlantı hatası)"]
    return {"status": status, "findings": findings}


def _svc_renewal_monitor(job: dict[str, Any]) -> dict[str, Any]:
    """Yenileme izleme: sağlık turu + geçmiş rapor deltası."""
    from nirvana.delivery_runner import check_domain
    result = check_domain(str(job["domain"]))
    reports = [r for r in load_reports()
               if domain_of(str(r.get("domain") or "")) == domain_of(str(job["domain"]))]
    last = reports[-1] if reports else None
    findings = [f"{result['domain']}: HTTP {result['http']}, {result['ms']} ms"]
    if last is not None:
        findings.append(f"önceki rapor {last.get('report_id')}: {last.get('status')}")
    return {"status": result["status"], "findings": findings}


_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "infra-sweep": _svc_infra_sweep,
    "contact-audit": _svc_contact_audit,
    "renewal-monitor": _svc_renewal_monitor,
}


def run_service(job: dict[str, Any]) -> dict[str, Any]:
    """KARIŞMAMA KAPISI: yalnızca işin kayıtlı servisi çalışır."""
    service = str(job.get("service") or "")
    if service not in _HANDLERS:
        return {"status": "error", "findings": [f"bilinmeyen hizmet: {service}"]}
    return _HANDLERS[service](job)


# --- raporlama ---------------------------------------------------------------

def report_text(report: dict[str, Any], *, turkish: bool = True) -> str:
    badge = {"ok": "✅", "degraded": "⚠️", "down": "🔴", "error": "❌"}.get(str(report.get("status")), "•")
    label = SERVICES.get(str(report.get("service")), {}).get(
        "label_tr" if turkish else "label_en", str(report.get("service")))
    head = (f"Teslimat raporu {report.get('report_id')} — {label}\n"
            f"Hizmet: {report.get('domain')} (iş {report.get('job_id')}, chat {report.get('chat_id')})"
            if turkish else
            f"Delivery report {report.get('report_id')} — {label}\n"
            f"Service: {report.get('domain')} (job {report.get('job_id')}, chat {report.get('chat_id')})")
    lines = [f"{badge} {f}" for f in (report.get("findings") or [])]
    return "\n".join([head, *lines])


def customer_memory(chat_id: int) -> dict[str, Any]:
    """Geri dönen müşteri hafızası: rapor numaraları, sorunlar, ödeme durumu."""
    reports = [r for r in load_reports() if int(r.get("chat_id") or 0) == int(chat_id)]
    jobs = [j for j in load_jobs() if int(j.get("chat_id") or 0) == int(chat_id)]
    past_issues = []
    for r in reports:
        if str(r.get("status")) in ("degraded", "down"):
            past_issues.append({"report_id": r.get("report_id"), "domain": r.get("domain"),
                                "status": r.get("status"), "at": r.get("at")})
    return {
        "returning": bool(reports),
        "chat_id": int(chat_id),
        "report_count": len(reports),
        "reports": [{"report_id": r.get("report_id"), "service": r.get("service"),
                     "domain": r.get("domain"), "status": r.get("status"), "at": r.get("at")}
                    for r in reports[-5:]],
        "last_report": reports[-1].get("report_id") if reports else None,
        "past_issues": past_issues[-5:],
        "payment_verified": payment_verified(chat_id),
        "active_jobs": [j.get("job_id") for j in jobs
                        if j.get("status") in ("awaiting_payment", "queued", "running")],
    }


def reports_for_chat(chat_id: int) -> list[dict[str, Any]]:
    return [r for r in load_reports() if int(r.get("chat_id") or 0) == int(chat_id)]


def find_report(report_id: str) -> dict[str, Any] | None:
    for r in load_reports():
        if r.get("report_id") == report_id:
            return r
    return None


# --- Telegram ----------------------------------------------------------------

def send_customer_message(chat_id: int, text: str) -> bool:
    """Satış botu token'ı ile müşterinin KENDİ sohbetine rapor (satış outreach değil)."""
    try:
        import config
        import optout
        token = (getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()
        if not token or not chat_id or optout.is_chat_opted_out(int(chat_id)):
            return False
        logging.getLogger("httpx").setLevel(logging.WARNING)
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": int(chat_id), "text": text[:3500]},
            timeout=30.0,
        )
        response.raise_for_status()
        return True
    except Exception:
        return False


def notify_owner(text: str) -> bool:
    try:
        import owner_notify
        return bool(owner_notify.send(text))
    except Exception:
        return False


# --- tur (Oracle timer) ------------------------------------------------------

def run_batch(*, notify: bool = True, limit: int = RUN_LIMIT) -> dict[str, Any]:
    """Bekleyen işleri teslim et: ödemesi doğrulanmış → kuyruk → rapor → Telegram."""
    jobs = load_jobs()
    reports = load_reports()
    # Kapı 1: ödeme doğrulanmış awaiting_payment işleri kuyruğa al.
    for j in jobs:
        if j.get("status") == "awaiting_payment" and payment_verified(int(j.get("chat_id") or 0)):
            j["status"] = "queued"
            j["queued_at"] = _utcnow()
    save_jobs(jobs)

    delivered: list[dict[str, Any]] = []
    skipped = 0
    for j in jobs:
        if len(delivered) >= limit:
            skipped += 1
            continue
        if j.get("status") != "queued":
            continue
        chat_id = int(j.get("chat_id") or 0)
        if not payment_verified(chat_id):
            j["status"] = "awaiting_payment"   # kapı: doğrulama kaybolduysa geri çek
            continue
        result = run_service(j)                # yalnız kayıtlı servis
        j["runs"] = int(j.get("runs") or 0) + 1
        j["last_run"] = _utcnow()
        report = {
            "report_id": next_report_id(reports),
            "job_id": j.get("job_id"),
            "chat_id": chat_id,
            "domain": j.get("domain"),
            "service": j.get("service"),
            "status": result.get("status"),
            "findings": result.get("findings") or [],
            "at": _utcnow(),
        }
        reports.append(report)
        save_reports(reports)
        j["status"] = "delivered"
        j["last_report_id"] = report["report_id"]
        delivered.append({"job": j, "report": report})
        save_jobs(jobs)

        text = report_text(report)
        sent_customer = send_customer_message(chat_id, text) if notify else False
        sent_owner = notify_owner(
            f"TESLİMAT — {report['report_id']} | {j.get('domain')} | "
            f"service={j.get('service')} | chat {chat_id} | durum={result.get('status')}\n"
            f"Müşteriye iletildi: {sent_customer}"
        ) if notify else False
        report["sent_customer"] = sent_customer
        report["sent_owner"] = sent_owner

    save_jobs(jobs)
    save_reports(reports)
    return {"delivered": len(delivered), "skipped_over_limit": skipped,
            "reports": [r["report"]["report_id"] for r in delivered],
            "status_summary": status_summary()}

