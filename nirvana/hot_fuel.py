"""Sıcak Havuz (Hot-Fuel Reservoir) — SQLite WAL tabanlı ön-doğrulanmış yakıt tankı.

Rapor (Ayrıştırılmış Arka Plan Yakıt Tankı): veri toplama (crawling) ile veri
gönderme (submitting) mimari olarak ayrılır. Arka plandaki tarayıcı (nice -n 19)
havuzu sürekli dolu tutar; gönderim motoru başlatıldığında dış ağ senkronizasyonu
beklemeden YEREL havuzdan çeker -> soğuk başlangıç 0 saniye.

- Motor: SQLite, `journal_mode=WAL` (eşzamanlı okuma/yazma, kilitlenme yok).
- Kapasite: HOT_FUEL_TARGET (varsayılan 2000) hazır/doğrulanmış sıcak lead.
- Kaynak: GitHub feed'i (`feed_ingest.sync_github_feed`) veya yerel `feeds/*.json`
  — ek Oracle HTTP kotası tüketmez, paralı servis yok ($0).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import config

logger = logging.getLogger(__name__)

STATE_DIR = config.ROOT / "nirvana" / "state"
DB_PATH = STATE_DIR / "hot_fuel.db"
DEFAULT_TARGET = int(os.getenv("HOT_FUEL_TARGET", "2000") or 2000)
LEASE_SECONDS = float(os.getenv("HOT_FUEL_LEASE_SECONDS", "1800") or 1800)
FEED_MIN_SCORE = int(getattr(config, "FEED_MIN_SCORE", 80) or 80)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hot_fuel (
    url         TEXT PRIMARY KEY,
    host        TEXT,
    root        TEXT,
    score       INTEGER DEFAULT 0,
    source      TEXT,
    profile     TEXT,
    stack       TEXT,
    payload     TEXT,
    verified_at REAL,
    taken_at    REAL,
    attempts    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_hot_fuel_ready ON hot_fuel (taken_at, score DESC);
CREATE TABLE IF NOT EXISTS hot_fuel_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def target() -> int:
    """Havuz hedefi: .env > knowledge/oracle.json > varsayılan (min 2000 önerilir)."""
    try:
        import knowledge  # type: ignore

        locked = int(knowledge.oracle_lock().get("hot_fuel_target") or 0)
        if locked > 0:
            return max(locked, DEFAULT_TARGET) if os.getenv("HOT_FUEL_TARGET") else locked
    except Exception:  # noqa: BLE001
        pass
    return max(1, DEFAULT_TARGET)


def root_of(url_or_host: str) -> str:
    try:
        from nirvana.domain_rate_limit import root_domain  # type: ignore

        return root_domain(url_or_host)
    except Exception:  # noqa: BLE001
        return (url_or_host or "").split("/")[2] if "//" in (url_or_host or "") else ""


def connect() -> sqlite3.Connection:
    """WAL modunda bağlantı: eşzamanlı okuyucu/yazıcı güvenli."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA wal_autocheckpoint=512")
    return conn


def init() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


def release_stale(*, now: float | None = None) -> int:
    """Süresi geçmiş kiralamaları (lease) havuza geri ver."""
    stamp = time.time() if now is None else float(now)
    with connect() as conn:
        cur = conn.execute(
            "UPDATE hot_fuel SET taken_at=NULL WHERE taken_at IS NOT NULL AND taken_at < ?",
            (stamp - LEASE_SECONDS,),
        )
        return int(cur.rowcount or 0)


def ready_depth(*, now: float | None = None) -> int:
    init()
    release_stale(now=now)
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM hot_fuel WHERE taken_at IS NULL").fetchone()
    return int(row["n"] if row else 0)


def depth() -> int:
    init()
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM hot_fuel").fetchone()
    return int(row["n"] if row else 0)


def push(rows: Iterable[dict[str, Any]], *, source: str = "feed") -> int:
    """Ön-doğrulanmış sıcak lead'leri havuza yaz (URL bazında tekilleştirme)."""
    init()
    now = time.time()
    payloads: list[tuple[Any, ...]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url.lower().startswith("http"):
            continue
        score = int(row.get("easy_score") or row.get("score") or 0)
        if score and score < FEED_MIN_SCORE:
            continue
        host = str(row.get("host") or "").strip()
        payloads.append((
            url,
            host,
            root_of(host or url),
            score,
            str(row.get("source") or source)[:60],
            str(row.get("profile") or "")[:30],
            str(row.get("stack") or "")[:40],
            json.dumps(row, ensure_ascii=False)[:4000],
            now,
        ))
    if not payloads:
        return 0
    with connect() as conn:
        cur = conn.executemany(
            "INSERT INTO hot_fuel (url, host, root, score, source, profile, stack, payload, verified_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(url) DO UPDATE SET score=excluded.score, payload=excluded.payload,"
            " verified_at=excluded.verified_at",
            payloads,
        )
        return int(cur.rowcount or 0)



def take(limit: int = 20) -> list[dict[str, Any]]:
    """Havuzdan N sıcak lead kirala (lease). Motor bunları doğrudan gönderime alır."""
    init()
    release_stale()
    out: list[dict[str, Any]] = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM hot_fuel WHERE taken_at IS NULL ORDER BY score DESC, verified_at ASC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        if not rows:
            return []
        conn.executemany(
            "UPDATE hot_fuel SET taken_at=?, attempts=attempts+1 WHERE url=?",
            [(time.time(), row["url"]) for row in rows],
        )
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            payload.setdefault("url", row["url"])
            payload.setdefault("easy_score", int(row["score"] or 0))
            out.append(payload)
    return out


def mark_done(url: str, *, status: str = "submitted_confirmed", keep: bool = True) -> None:
    """Kiralamayı kapat: keep=False -> havuzdan tamamen çıkar (gönderildi)."""
    init()
    with connect() as conn:
        if keep:
            conn.execute("UPDATE hot_fuel SET taken_at=? WHERE url=?", (time.time(), url))
        else:
            conn.execute("DELETE FROM hot_fuel WHERE url=?", (url,))


def feed_files() -> list[Path]:
    """Yerel feed kaynakları: ready_queue.json + shard dosyaları (ağ YOK)."""
    feeds = config.ROOT / "feeds"
    paths: list[Path] = []
    ready = feeds / "ready_queue.json"
    if ready.exists():
        paths.append(ready)
    shards = feeds / "shards"
    if shards.exists():
        paths.extend(sorted(shards.glob("*.json")))
    return paths


def _rows_from_file(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows = payload.get("urls") if isinstance(payload, dict) else payload
    return [row for row in (rows or []) if isinstance(row, dict)]


def prime_from_files(*, limit_files: int = 0) -> int:
    """Yerel feed dosyalarından havuzu doldur (ağ erişimi yok, $0)."""
    added = 0
    files = feed_files()
    if limit_files:
        files = files[: int(limit_files)]
    for path in files:
        rows = _rows_from_file(path)
        if rows:
            added += push(rows, source=path.stem)
    return added


def prime_from_network() -> dict[str, Any]:
    """GitHub feed'ini çek ve havuza yaz (mevcut feed_ingest hattını kullanır)."""
    try:
        import feed_ingest  # type: ignore

        meta = feed_ingest.sync_github_feed()
    except Exception as exc:  # noqa: BLE001
        logger.info("Feed senkronu başarısız: %s", exc)
        return {"synced": False, "reason": str(exc)[:120]}
    if not meta:
        return {"synced": False, "reason": "empty_feed"}
    rows, _ = feed_ingest._load_file()  # noqa: SLF001 — aynı paket içi yardımcı
    added = push(rows, source="github-feed")
    return {"synced": True, "rows": len(rows), "added": added, **meta}


def refill(*, allow_network: bool = True) -> dict[str, Any]:
    """Havuzu hedefe tamamla: önce yerel dosyalar, gerekirse ağ senkronu."""
    init()
    want = target()
    before = ready_depth()
    added = 0
    if before < want:
        added += prime_from_files()
    after = ready_depth()
    net: dict[str, Any] = {"skipped": True}
    if after < want and allow_network:
        net = prime_from_network()
        after = ready_depth()
    snapshot = status()
    snapshot.update({"before": before, "added": added, "network": net})
    logger.info("Hot fuel: %s -> %s (hedef %s)", before, after, want)
    return snapshot


def prime_queue(limit: int = 200) -> int:
    """Motor açılışında havuzdan kuyruğa aktar -> soğuk başlangıç 0 saniye."""
    rows = take(limit)
    if not rows:
        return 0
    pushed = 0
    try:
        import domain_store  # type: ignore

        for row in rows:
            url = str(row.get("url") or "")
            if not url:
                continue
            # enqueue() keyword-only (easy_score/source) — positional çağrı
            # TypeError yutup sessizce 0 satır ekliyordu (yakıt kıtlığı nedeni).
            if domain_store.enqueue(
                url,
                easy_score=int(row.get("easy_score") or 0),
                source=str(row.get("source") or "hot-fuel"),
            ):
                pushed += 1
    except Exception as exc:  # noqa: BLE001
        logger.info("Kuyruk beslemesi atlandı: %s", exc)
    return pushed


def cold_start_seconds() -> float:
    """Hazır havuz varsa 0 sn; boşsa 16 dakikalık soğuk başlangıç riski raporlanır."""
    return 0.0 if ready_depth() > 0 else 960.0


def status() -> dict[str, Any]:
    init()
    ready = ready_depth()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(verified_at) AS newest, MIN(verified_at) AS oldest"
            " FROM hot_fuel"
        ).fetchone()
        journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    total = int(row["n"] if row else 0)
    newest = float((row["newest"] if row else 0) or 0)
    return {
        "db": str(DB_PATH),
        "depth": total,
        "ready": ready,
        "target": target(),
        "full": ready >= target(),
        "cold_start_seconds": 0.0 if ready else 960.0,
        "newest_age_s": round(time.time() - newest, 1) if total and newest else None,
        "wal": journal == "wal",
    }


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Lane giriş noktası: havuzu hedefe tamamla ve durumu raporla."""
    allow_network = bool(kwargs.get("allow_network", True))
    result = refill(allow_network=allow_network)
    result["lane"] = "hot_fuel_reservoir (SQLite WAL, arka plan refill)"
    return result
