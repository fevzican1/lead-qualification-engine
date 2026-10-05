"""Domain başı sınırlama + insan benzeri Gaussian jitter (spam/engel koruması).

Rapor (Domain İtibarını Koruyan Micro-Batching):
- Aynı kök domaine (aynı hosting'deki siteler dahil) 1 saat içinde ikinci gönderim
  YAPILMAZ; şablonlu seri gönderim yerine insan ritmi uygulanır.
- Gönderimler arası bekleme t ∈ [3.5, 8.2] sn, Gauss dağılımına uygun.

Saf Python + JSON durum dosyası; ek servis yok ($0).
Ortam değişkenleri:
- DOMAIN_HOURLY_LIMIT (varsayılan 1): kök domain başına saatlik üst sınır
- DOMAIN_RATE_WINDOW_HOURS (varsayılan 24): durum dosyasında tutulan pencere
- SUBMIT_JITTER_MIN_SECONDS / SUBMIT_JITTER_MAX_SECONDS (3.5 / 8.2)
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import config

logger = logging.getLogger(__name__)

PATH = config.ROOT / "domain_rate_limit.json"
HOURLY_LIMIT = int(os.getenv("DOMAIN_HOURLY_LIMIT", "1") or 1)
WINDOW_HOURS = int(os.getenv("DOMAIN_RATE_WINDOW_HOURS", "24") or 24)
JITTER_MIN = float(os.getenv("SUBMIT_JITTER_MIN_SECONDS", "3.5") or 3.5)
JITTER_MAX = float(os.getenv("SUBMIT_JITTER_MAX_SECONDS", "8.2") or 8.2)

# Çok parçalı üst düzey alanlar: naive eTLD+1 çözümü için (paralı tldextract yok).
MULTI_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk",
    "com.tr", "net.tr", "org.tr", "gov.tr", "edu.tr", "av.tr", "bel.tr",
    "com.br", "com.ar", "com.mx", "com.co", "com.pe", "com.ve",
    "com.au", "net.au", "org.au", "co.nz", "co.za", "co.jp", "co.kr",
    "co.in", "co.id", "co.il", "com.sg", "com.hk", "com.tw", "com.cn",
    "com.my", "com.ph", "com.vn", "com.pk", "com.ua", "com.pl", "com.es",
})


def root_domain(url_or_host: str) -> str:
    """Kök domain (eTLD+1) — 'blog.firma.com.tr' -> 'firma.com.tr'."""
    raw = (url_or_host or "").strip()
    if not raw:
        return ""
    host = urlparse(raw).hostname if "//" in raw else urlparse(f"//{raw}").hostname
    host = (host or "").lower().removeprefix("www.")
    if not host or host.replace(".", "").isdigit():
        return host
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    last_two = ".".join(parts[-2:])
    if last_two in MULTI_SUFFIXES:
        return ".".join(parts[-3:])
    return last_two


def jitter_seconds(*, low: float | None = None, high: float | None = None,
                   rng: random.Random | None = None) -> float:
    """Gauss dağılımlı, [low, high] aralığına kırpılmış insan ritmi beklemesi."""
    lo = JITTER_MIN if low is None else float(low)
    hi = JITTER_MAX if high is None else float(high)
    lo, hi = min(lo, hi), max(lo, hi)
    src = rng or random
    center = (lo + hi) / 2.0
    sigma = max(0.25, (hi - lo) / 4.0)
    for _ in range(6):
        value = src.gauss(center, sigma)
        if lo <= value <= hi:
            return round(value, 2)
    return round(max(lo, min(hi, center)), 2)


def _load() -> dict[str, Any]:
    if not PATH.exists():
        return {"hosts": {}}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — bozuk dosya hattı durdurmaz
        return {"hosts": {}}
    if not isinstance(data, dict):
        return {"hosts": {}}
    hosts = data.get("hosts")
    data["hosts"] = hosts if isinstance(hosts, dict) else {}
    return data


def _save(data: dict[str, Any]) -> None:
    cutoff = time.time() - WINDOW_HOURS * 3600
    hosts = data.get("hosts") or {}
    data["hosts"] = {
        str(k): [float(t) for t in (v or []) if float(t) >= cutoff]
        for k, v in hosts.items()
        if [float(t) for t in (v or []) if float(t) >= cutoff]
    }
    tmp = PATH.with_suffix(PATH.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(PATH)


def recent_count(root: str, *, now: float | None = None, window_s: float = 3600.0) -> int:
    stamp = time.time() if now is None else float(now)
    rows = (_load().get("hosts") or {}).get(root) or []
    return sum(1 for t in rows if stamp - float(t) <= window_s)


def allow(url_or_host: str, *, now: float | None = None, limit: int | None = None,
          window_s: float = 3600.0) -> tuple[bool, str]:
    """(izin, gerekçe). Reddedilirse gönderim kuyrukta bekletilir — asla zorlanmaz."""
    root = root_domain(url_or_host)
    if not root:
        return True, "no_host"
    cap = HOURLY_LIMIT if limit is None else int(limit)
    used = recent_count(root, now=now, window_s=window_s)
    if cap > 0 and used >= cap:
        logger.info("Domain ritmi: %s son 1 saatte %s gönderim (üst sınır %s) — bekle", root, used, cap)
        return False, f"domain_hour:{root}"
    return True, root


def record(url_or_host: str, *, status: str = "submitted", now: float | None = None) -> None:
    """Gönderim kaydı — yalnızca gerçekten form gönderilen çağrılar buraya yazar."""
    root = root_domain(url_or_host)
    if not root:
        return
    stamp = time.time() if now is None else float(now)
    data = _load()
    rows = list((data.get("hosts") or {}).get(root) or [])
    rows.append(stamp)
    data.setdefault("hosts", {})[root] = rows
    data["last_status"] = str(status)[:40]
    data["last_at"] = datetime.fromtimestamp(stamp, tz=timezone.utc).replace(microsecond=0).isoformat()
    _save(data)


def status() -> dict[str, Any]:
    data = _load()
    hosts = data.get("hosts") or {}
    now = time.time()
    return {
        "root_domains": len(hosts),
        "hourly_limit": HOURLY_LIMIT,
        "window_hours": WINDOW_HOURS,
        "last_hour": {h: sum(1 for t in rows if now - float(t) <= 3600) for h, rows in hosts.items()},
        "jitter_seconds": [JITTER_MIN, JITTER_MAX],
    }
