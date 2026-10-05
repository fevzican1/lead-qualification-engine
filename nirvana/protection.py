"""Dört katmanlı koruma çekirdeği — ana akışı (auto_runner) asla kilitlemez.

1. Jitter & rate-limit : istek döngülerinde ``random.uniform(3.0, 9.0)`` insan
   ritmi (``REQUEST_JITTER_MIN/MAX_SECONDS``); domain başı saatlik sınır zaten
   ``nirvana/domain_rate_limit.py`` içinde.
2. SQLite circuit breaker : ``hot_fuel.db`` üzerinde ``domain_health`` tablosu —
   istikrarsız / kalıcı hata veren domainleri işaretler, soğuma boyunca boşa
   istek atmayı durdurur.
3. Proxy havuzu rotasyonu : ``PROXY_POOL`` env'i (virgülle) veya
   ``nirvana/state/proxies.txt`` (satır başına bir proxy, ``#`` yorum) — her
   istekte havuzdan rastgele seçim; havuz boşsa $0 kuralı korunur (doğrudan).
4. Header çeşitlendirmesi : ``fingerprint_rotator.UA_POOL`` güncel tarayıcı
   imza listesinden rastgele User-Agent + locale header seti.

Tüm katmanlar fail-open'dır: bir koruma mekanizması patlarsa istek yine gider,
hattı (form doldurma / keşif) asla düşürmez.
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Any

import config

logger = logging.getLogger(__name__)

PROXY_FILE = config.ROOT / "nirvana" / "state" / "proxies.txt"
_PROXY_FILE_TTL_S = 30.0
_file_cache: dict[str, Any] = {"mtime": -1.0, "rows": []}

_HEALTH_DDL = """
CREATE TABLE IF NOT EXISTS domain_health (
    root        TEXT PRIMARY KEY,
    fails       INTEGER DEFAULT 0,
    open_until  REAL DEFAULT 0,
    last_error  TEXT,
    updated_at  REAL
)
"""


# --- Katman 1: jitter & rate-limiting ---------------------------------------

def jitter_bounds() -> tuple[float, float]:
    """İnsan ritmi aralığı: .env > config > varsayılan (3.0–9.0 sn)."""
    lo = float(getattr(config, "REQUEST_JITTER_MIN_SECONDS", 3.0) or 3.0)
    hi = float(getattr(config, "REQUEST_JITTER_MAX_SECONDS", 9.0) or 9.0)
    return min(lo, hi), max(lo, hi)


def jitter_seconds(*, rng: random.Random | None = None) -> float:
    """``random.uniform(low, high)`` ile rastgele insansı bekleme (sn)."""
    lo, hi = jitter_bounds()
    src = rng or random
    return round(src.uniform(lo, hi), 2)


def human_delay(*, rng: random.Random | None = None,
                sleep: Any = time.sleep) -> float:
    """İstek döngüsü arasında uniform jitter uygular; bekleme saniyesini döner."""
    seconds = jitter_seconds(rng=rng)
    try:
        sleep(seconds)
    except Exception:  # noqa: BLE001 — jitter hatası hattı durdurmaz
        pass
    return seconds

# --- Katman 2: SQLite circuit breaker (hot_fuel.db) -------------------------

def _connect() -> Any:
    from nirvana import hot_fuel  # döngüsel importu önlemek için yerel

    conn = hot_fuel.connect()
    conn.execute(_HEALTH_DDL)
    return conn


def _root(url_or_host: str) -> str:
    from nirvana import hot_fuel

    return hot_fuel.root_of(url_or_host) or ""


def allow(url_or_host: str) -> bool:
    """Şalter açıksa False (istek ATILMAZ); fail-open: hata = izin."""
    try:
        root = _root(url_or_host)
        if not root:
            return True
        with _connect() as conn:
            row = conn.execute(
                "SELECT open_until FROM domain_health WHERE root = ?", (root,)
            ).fetchone()
        if not row:
            return True
        return float(row["open_until"] or 0.0) <= time.time()
    except Exception:  # noqa: BLE001 — koruma katmanı hattı asla düşürmez
        return True


def record(url_or_host: str, ok: bool, *, error: str = "",
           threshold: int | None = None,
           cooldown_s: float | None = None) -> None:
    """Sonucu işle: başarı şalteri kapatır; ardışık `threshold` hata açar.

    Domain bazlıdır (root); açılış soğuması ``CIRCUIT_BREAKER_COOLDOWN_SECONDS``.
    """
    try:
        root = _root(url_or_host)
        if not root:
            return
        thr = int(threshold if threshold is not None
                  else int(getattr(config, "CIRCUIT_BREAKER_THRESHOLD", 3) or 3))
        cool = float(cooldown_s if cooldown_s is not None
                     else float(getattr(config, "CIRCUIT_BREAKER_COOLDOWN_SECONDS",
                                        3600.0) or 3600.0))
        now = time.time()
        err = str(error or "")[:160]
        with _connect() as conn:
            row = conn.execute(
                "SELECT fails FROM domain_health WHERE root = ?", (root,)
            ).fetchone()
            if ok:
                conn.execute(
                    "INSERT INTO domain_health (root, fails, open_until, last_error,"
                    " updated_at) VALUES (?, 0, 0, '', ?)"
                    " ON CONFLICT(root) DO UPDATE SET fails = 0, open_until = 0,"
                    " last_error = '', updated_at = excluded.updated_at",
                    (root, now),
                )
                return
            fails = int((row["fails"] if row else 0) or 0) + 1
            open_until = 0.0
            if fails >= max(1, thr):
                open_until = now + cool
                logger.warning(
                    "Circuit OPEN: %s (%s ardışık hata, %.0fs soğuma) %s",
                    root, fails, cool, err,
                )
            conn.execute(
                "INSERT INTO domain_health (root, fails, open_until, last_error,"
                " updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(root) DO UPDATE SET fails = excluded.fails,"
                " open_until = excluded.open_until,"
                " last_error = excluded.last_error,"
                " updated_at = excluded.updated_at",
                (root, fails, open_until, err, now),
            )
    except Exception:  # noqa: BLE001 — fail-open
        logger.debug("circuit record failed for %s", url_or_host, exc_info=True)


def health() -> dict[str, Any]:
    """Şalter panosu: açık domain sayısı + örnekler (/status, tanılama)."""
    try:
        now = time.time()
        with _connect() as conn:
            rows = conn.execute(
                "SELECT root, fails, open_until, last_error FROM domain_health"
                " ORDER BY open_until DESC LIMIT 50"
            ).fetchall()
        open_rows = [r for r in rows if float(r["open_until"] or 0) > now]
        return {
            "open": len(open_rows),
            "tracked": len(rows),
            "worst": [
                {"root": r["root"], "fails": int(r["fails"] or 0),
                 "until": float(r["open_until"] or 0),
                 "error": str(r["last_error"] or "")[:80]}
                for r in open_rows[:10]
            ],
        }
    except Exception:  # noqa: BLE001 — şalter panosu hattı düşürmez
        return {"open": 0, "tracked": 0, "worst": []}


# --- Katman 3: proxy havuzu rotasyonu ---------------------------------------

def _env_pool() -> list[str]:
    raw = str(os.getenv("PROXY_POOL") or getattr(config, "PROXY_POOL", "") or "")
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


def _file_proxies() -> list[str]:
    try:
        mtime = PROXY_FILE.stat().st_mtime
        if mtime != _file_cache["mtime"]:
            rows = [
                line.strip()
                for line in PROXY_FILE.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            _file_cache["mtime"] = mtime
            _file_cache["rows"] = rows
        return list(_file_cache["rows"])
    except Exception:  # noqa: BLE001 — dosya yok/okunamadı: havuz boş
        return []


def proxies() -> list[str]:
    """Birleşik havuz: PROXY_POOL env + proxies.txt (tekil, sıralı)."""
    seen: list[str] = []
    for proxy in _env_pool() + _file_proxies():
        if proxy not in seen:
            seen.append(proxy)
    return seen


def pick_proxy() -> str | None:
    """Her istekte havuzdan rastgele; havuz boşsa None (doğrudan, $0)."""
    pool = proxies()
    if not pool:
        return None
    return random.choice(pool)


def random_headers(*, turkish_site: bool = True) -> dict[str, str]:
    """Güncel tarayıcı imza listesinden rastgele UA + locale header seti."""
    try:
        from nirvana.fingerprint_rotator import http_headers

        return http_headers(turkish_site=turkish_site)
    except Exception:  # noqa: BLE001 — yedek minimal header
        return {"User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Tanılama çıktısı: dört katmanın anlık durumu."""
    lo, hi = jitter_bounds()
    return {
        "jitter_seconds": [lo, hi],
        "circuit": health(),
        "proxy_pool": len(proxies()),
        "uas": "rotating (fingerprint_rotator.UA_POOL)",
        "fail_open": True,
        "ts": time.time(),
    }

