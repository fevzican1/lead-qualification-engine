"""Lane T — service_readiness [GitHub Actions, heavy].

Hizmet altyapisi saglamligi kontrolu:
- Musterinin altyapisi (DNS, SSL, sunucu yaniti, API endpoint'leri) saglam mi?
- Hizmet karisasi var mi? (ornegin musteri zaten birkac servis kullaniyorsa)
- Entegrasyon sorunu olacak mi?
- Hizmet verilmezse musteri memnun kalir mi?

Rapor: service_readiness.json (her musteri icin ayri)
"""
from __future__ import annotations

import json
import socket
import ssl
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from nirvana.registry import state_path


def check_dns(host: str) -> dict[str, Any]:
    """DNS cozuluyor mu?"""
    import socket
    try:
        ip = socket.gethostbyname(host)
        return {"ok": True, "ip": ip}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


def check_ssl(host: str) -> dict[str, Any]:
    """SSL sertifikasi gecerli mi?"""
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=host) as s:
            s.settimeout(5)
            s.connect((host, 443))
            cert = s.getpeercert()
            return {"ok": True, "issuer": str(cert.get("issuer", ""))}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


def check_response(url: str) -> dict[str, Any]:
    """Sunucu yaniti ve suresi."""
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True)
        return {"ok": r.status_code < 400, "status": r.status_code, "time_ms": r.elapsed.total_seconds() * 1000}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


def check_api_endpoint(url: str) -> dict[str, Any]:
    """API endpoint'leri erisilebilir mi?"""
    try:
        r = httpx.get(url, timeout=8, headers={"Accept": "application/json"})
        return {"ok": r.status_code < 500, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


def assess_service_conflict(row: dict[str, Any]) -> dict[str, Any]:
    """Hizmet karisasi riski degerlendirmesi."""
    stack = row.get("detected_stack", {}) if isinstance(row.get("detected_stack"), dict) else {}
    techs = stack.get("techs", []) if isinstance(stack, dict) else []
    
    # Bilinen hizmetlerle cakisma riski
    conflicting = []
    known_services = ["Cloudflare", "Sucuri", "Akamai"]
    for t in techs:
        if t in known_services:
            conflicting.append(t)
    
    return {
        "conflict_risk": "low" if not conflicting else "medium",
        "conflicting_services": conflicting,
        "recommendation": "proceed" if not conflicting else "review"
    }


def run_readiness_check(target_url: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tek hedef icin hazirlik kontrolu."""
    host = urlparse(target_url).netloc.removeprefix("www.")
    
    dns = check_dns(host)
    ssl_check = check_ssl(host)
    response = check_response(target_url)
    conflict = assess_service_conflict(row or {})
    
    ready = dns["ok"] and ssl_check["ok"] and response["ok"] and conflict["recommendation"] == "proceed"
    
    return {
        "url": target_url,
        "host": host,
        "dns": dns,
        "ssl": ssl_check,
        "response": response,
        "conflict": conflict,
        "ready": ready,
        "ts": time.time()
    }


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """GitHub Actions'ta tetiklenir."""
    targets = kwargs.get("targets", [])
    results = []
    for url in targets[:50]:  # Her batch'te max 50
        results.append(run_readiness_check(url))
    
    ready_count = sum(1 for r in results if r["ready"])
    return {"checked": len(results), "ready": ready_count, "not_ready": len(results) - ready_count, "results": results}


def gateway_ready(*, payment_confirmed: bool, contract: bool) -> bool:
    """Teslimat kapısı: ödeme onayı + sözleşme İKİSİ birden doğrulanmadan
    hiçbir teknik işlem/pipeline başlamaz (pay-first threshold, fail-closed)."""
    return bool(payment_confirmed) and bool(contract)


def _authorized_targets() -> set[str]:
    """Yalnızca doğrulanmış (audit'ten geçmiş) hedeflere hizmet verilir.
    Kaynak yoksa boş küme döner — unauthorized hedefe asla başlanmaz."""
    targets: set[str] = set()
    for name in ("oracle_queue.json", "verified_queue.json"):
        try:
            data = json.loads(state_path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows: Any = []
        if isinstance(data, dict):
            rows = data.get("targets") or data.get("rows") or []
        elif isinstance(data, list):
            rows = data
        for r in rows:
            if isinstance(r, dict):
                verdict = str(r.get("verdict") or r.get("status") or "")
                url = str(r.get("url") or r.get("form_url") or "")
                if url and (verdict == "pass" or r.get("verified")):
                    targets.add(url)
    return targets

