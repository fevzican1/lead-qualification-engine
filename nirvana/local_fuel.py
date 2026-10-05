"""Lane AN — local_fuel [Oracle VM, light]: içeriden sınırsız yakıt beslemesi.

Yakıt kıtlığının KÖK çözümü: rezervuar artık dışa (GitHub/CDN feed) bağımlı
değil. Oracle VM kendi başına, API anahtarı gerektirmeyen ÜCRETSİZ liste
servislerinden domain üretir ve ``hot_fuel.db`` tankını hedefin üstünde tutar:

- ``tranco`` : Tranco Top-1M liste dosyası (tek GET, günlük; 1M+ domain havuzu,
  reservoir sampling ile rastgele dilim -> her koşuda farklı hedef).
- ``cc``     : Common Crawl CDX contact-form URL örneklemesi
  (``scripts/cc_discover.harvest``; index.commoncrawl.org, anahtarsız).
- ``crt``    : Certificate Transparency (crt.sh) JSON — taze domainler
  (varsayılan KAPALI; ``LOCAL_FUEL_SOURCES`` ile açılır).
- ``seeds``  : yerel tohum listesi ``nirvana/state/seed_domains.txt``
  (satır başına domain/URL; ``import_targets.py`` ile aynı normalizasyon).

Tasarım kuralları (IP itibarı = retainer'ın sigortası):

1. HEDEF SİTEYE ASLA İSTEK ATILMAZ. Yalnızca liste servisleri çağrılır
   (tranco-list.eu / index.commoncrawl.org / crt.sh). Form doğrulaması mevcut
   hatta (preflight + stealth_former + Chromium) aittir; böylece hedef WAF'lar
   Oracle IP'sini hiç görmez, ban riski oluşmaz.
2. Günlük liste-servisi GET bütçesi (``LOCAL_FUEL_DAILY_GETS``, varsayılan 40)
   + çağrılar arası insansı jitter (``nirvana.protection``) uygulanır.
3. Dört katman koruma miras alınır: circuit breaker (``protection.allow``),
   UA rotasyonu (``protection.random_headers``), proxy havuzu
   (``protection.pick_proxy``) ve jitter.
4. Fail-open: bir kaynak düşerse diğerleri devam eder; hiçbir hata ana hattı
   (auto_runner) veya form gönderimini etkilemez.
5. Mükerrer engeli: ``hot_fuel`` PRIMARY KEY (url) + ``push`` upsert.

Kullanım:
    python -m nirvana.runner local_fuel              # canlı besleme
    python -m nirvana.runner local_fuel --self-test  # kuru çalışma (ağ/DB yok)
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import config
from nirvana import hot_fuel, protection
from nirvana.registry import state_path

logger = logging.getLogger(__name__)

STATE_NAME = "local_fuel.json"
SEED_NAME = "seed_domains.txt"

TRANCO_URL = os.getenv(
    "LOCAL_FUEL_TRANCO_URL",
    "https://tranco-list.eu/download/latest/1000000/short.csv",
)
CRT_URL_TEMPLATE = os.getenv(
    "LOCAL_FUEL_CRT_URL", "https://crt.sh/?q=%25.{suffix}&output=json"
)
CRT_SUFFIXES = ("com.tr", "com", "co", "io", "net", "co.uk", "de", "nl")

DEFAULT_SOURCES = "tranco,cc,seeds"
DEFAULT_DAILY_GETS = 40
DEFAULT_PER_SOURCE = 1200
DEFAULT_PRIME = 200
TRANCO_MAX_BYTES = 24 * 1024 * 1024

# TR profili: Türkçe iletişim yolu; global: İngilizce contact yolu.
CONTACT_PATH = {"tr": "/iletisim", "eu": "/contact", "all": "/contact"}
EU_TLDS = (".de", ".nl", ".pl", ".se", ".dk", ".at", ".ch", ".es", ".it", ".fr", ".be")
HOST_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,251}\.[a-z]{2,}$")


# --- yapılandırma -----------------------------------------------------------

def sources() -> list[str]:
    raw = str(os.getenv("LOCAL_FUEL_SOURCES", DEFAULT_SOURCES) or DEFAULT_SOURCES)
    return [s.strip().lower() for s in raw.replace(";", ",").split(",") if s.strip()]


def daily_get_budget() -> int:
    try:
        return max(1, int(os.getenv("LOCAL_FUEL_DAILY_GETS", str(DEFAULT_DAILY_GETS))))
    except ValueError:
        return DEFAULT_DAILY_GETS


def enabled() -> bool:
    raw = str(getattr(config, "LOCAL_FUEL_ENABLED", True)).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path(STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if data.get("day") != _today():
        data = {"day": _today(), "gets_used": 0, "history": data.get("history") or []}
    data.setdefault("gets_used", 0)
    return data


def _save_state(state: dict[str, Any]) -> None:
    path = state_path(STATE_NAME)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def budget_left() -> int:
    state = _load_state()
    return max(0, daily_get_budget() - int(state.get("gets_used") or 0))


def spend_gets(n: int = 1) -> None:
    state = _load_state()
    state["gets_used"] = int(state.get("gets_used") or 0) + max(0, int(n))
    _save_state(state)


# --- saf yardımcılar (test edilebilir) --------------------------------------

def clean_host(raw: str) -> str:
    """'https://www.Acme.com.tr/iletisim' -> 'acme.com.tr' (geçersizse '')."""
    text = (raw or "").strip().strip("\"'").lower()
    if not text or text in {"domain", "domains", "url", "host"}:
        return ""
    if "://" in text:
        text = urlparse(text).hostname or ""
    else:
        text = text.split("/")[0].split("?")[0]
    text = text.strip().removeprefix("www.").strip(".")
    return text if HOST_RE.match(text) else ""


def profile_of(host: str) -> str:
    return "tr" if host.endswith(".tr") else ("eu" if host.endswith(EU_TLDS) else "all")


def contact_url(host: str, *, profile: str | None = None) -> str:
    path = CONTACT_PATH.get((profile or profile_of(host)).lower(), "/contact")
    return f"https://{host}{path}"


def _tld_ok(host: str, profile: str) -> bool:
    profile = (profile or "all").lower()
    if profile == "tr":
        return host.endswith(".tr")
    if profile == "eu":
        return host.endswith(EU_TLDS)
    return True


# --- kaynak ayrıştırıcıları (saf; ağ yok) -----------------------------------

def _decode(payload: bytes) -> str:
    import zipfile

    raw = payload
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if names:
                raw = archive.read(names[0])
    except Exception:  # noqa: BLE001 — zip değilse düz metin
        pass
    return raw.decode("utf-8", errors="replace")


def _row_for(host: str, *, source: str, score: int | None = None,
             stack: str = "") -> dict[str, Any]:
    url = contact_url(host)
    if score is None:
        try:
            import easy_score

            value, detected = easy_score.from_contact_url(url)
            score = int(value)
            stack = stack or str(detected or "")
        except Exception:  # noqa: BLE001 — skor yoksa taban skor
            score = 0
    floor = int(getattr(config, "FEED_MIN_SCORE", 80) or 80)
    return {
        "url": url,
        "host": host,
        "easy_score": max(int(score or 0), floor),
        "source": source,
        "profile": profile_of(host),
        "stack": stack,
    }


def parse_tranco(payload: bytes, *, profile: str = "all", limit: int = DEFAULT_PER_SOURCE,
                 rng: random.Random | None = None) -> list[dict[str, Any]]:
    """Tranco CSV -> aday satırlar (rezervuar örnekleme; O(limit) bellek)."""
    rng = rng or random.Random()
    sample: list[str] = []
    seen = 0
    for row in csv.reader(io.StringIO(_decode(payload))):
        if not row:
            continue
        host = clean_host(row[-1])
        if not host or not _tld_ok(host, profile):
            continue
        seen += 1
        if len(sample) < limit:
            sample.append(host)
            continue
        j = rng.randint(0, seen - 1)
        if j < limit:
            sample[j] = host
    return [_row_for(host, source="local-fuel:tranco") for host in sample]


def parse_crt(payload: bytes, *, profile: str = "all",
              limit: int = DEFAULT_PER_SOURCE) -> list[dict[str, Any]]:
    """crt.sh JSON -> taze domain adayları (name_value alt alan adları dahil)."""
    try:
        data = json.loads(_decode(payload))
    except ValueError:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (data if isinstance(data, list) else []):
        if not isinstance(item, dict):
            continue
        blob = str(item.get("name_value") or item.get("common_name") or "")
        for piece in blob.split("\n"):
            if str(piece).strip().startswith("*"):
                continue
            host = clean_host(piece)
            if not host or host in seen or not _tld_ok(host, profile):
                continue
            seen.add(host)
            out.append(_row_for(host, source="local-fuel:crt"))
            if len(out) >= limit:
                return out
    return out


def parse_seeds(text: str, *, profile: str = "all",
                limit: int = DEFAULT_PER_SOURCE) -> list[dict[str, Any]]:
    """Yerel tohum listesi: satır başına domain/URL, ``#`` yorum, CSV kabul."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        for cell in value.split(","):
            host = clean_host(cell)
            if not host or host in seen or not _tld_ok(host, profile):
                continue
            seen.add(host)
            out.append(_row_for(host, source="local-fuel:seeds"))
            if len(out) >= limit:
                return out
    return out


def seed_rows(limit: int = DEFAULT_PER_SOURCE, profile: str = "all") -> list[dict[str, Any]]:
    try:
        text = state_path(SEED_NAME).read_text(encoding="utf-8-sig")
    except OSError:
        return []
    return parse_seeds(text, profile=profile, limit=limit)


# --- ağ katmanı (yalnızca liste servisleri; hedef site YOK) ------------------

def _http_get(url: str, *, timeout: float = 30.0,
              max_bytes: int = 4 * 1024 * 1024) -> bytes | None:
    """Koruma katmanlı GET: circuit + UA rotasyonu + proxy + kayıt (fail-open)."""
    if not protection.allow(url):
        logger.info("local_fuel: circuit açık, atlandı %s", url)
        return None
    import httpx

    headers = protection.random_headers()
    try:
        proxy = protection.pick_proxy()
    except Exception:  # noqa: BLE001 — proxy katmanı hatası isteği düşürmez
        proxy = None
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            follow_redirects=True,
            headers=headers,
            proxy=proxy,  # httpx >= 0.27; eski sürümde TypeError -> fallback
        ) as client:
            chunks: list[bytes] = []
            total = 0
            with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    protection.record(url, False, error=f"http_{response.status_code}")
                    return None
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        break
                    chunks.append(chunk)
        protection.record(url, True)
        return b"".join(chunks)
    except TypeError:
        return _http_get_plain(url, timeout=timeout, max_bytes=max_bytes, headers=headers)
    except Exception as exc:  # noqa: BLE001 — liste servisi düşerse kaynak atlanır
        protection.record(url, False, error=f"{type(exc).__name__}")
        logger.info("local_fuel GET hatası %s: %s", url, str(exc)[:120])
        return None


def _http_get_plain(url: str, *, timeout: float, max_bytes: int,
                    headers: dict[str, str]) -> bytes | None:
    """Proxy parametresi olmayan httpx sürümleri için düz indirme (fail-open)."""
    import httpx

    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True, headers=headers)
        response.raise_for_status()
        protection.record(url, True)
        return bytes(response.content[:max_bytes])
    except Exception as exc:  # noqa: BLE001
        protection.record(url, False, error=f"{type(exc).__name__}")
        return None


def _pace() -> None:
    """Liste servisleri arası insansı jitter (hedef site yok; insan ritmi korunur)."""
    try:
        protection.human_delay()
    except Exception:  # noqa: BLE001 — jitter hatası beslemeyi düşürmez
        pass


def tranco_rows(limit: int = DEFAULT_PER_SOURCE, *, profile: str = "all",
                rng: random.Random | None = None,
                allow_network: bool = True) -> list[dict[str, Any]]:
    if not allow_network or budget_left() <= 0:
        return []
    payload = _http_get(TRANCO_URL, timeout=45.0, max_bytes=TRANCO_MAX_BYTES)
    spend_gets(1)
    if not payload:
        return []
    return parse_tranco(payload, profile=profile, limit=limit, rng=rng)


def crt_rows(limit: int = 600, *, profile: str = "all", rng: random.Random | None = None,
             allow_network: bool = True) -> list[dict[str, Any]]:
    if not allow_network or budget_left() <= 0:
        return []
    rng = rng or random.Random()
    url = CRT_URL_TEMPLATE.format(suffix=rng.choice(CRT_SUFFIXES))
    payload = _http_get(url, timeout=40.0, max_bytes=6 * 1024 * 1024)
    spend_gets(1)
    if not payload:
        return []
    return parse_crt(payload, profile=profile, limit=limit)


def cc_rows(limit: int = DEFAULT_PER_SOURCE, *, profile: str = "all",
            deadline_s: float = 120.0, allow_network: bool = True) -> list[dict[str, Any]]:
    """Common Crawl CDX dilimi (ücretsiz; hedef siteye istek YOK)."""
    if not allow_network or budget_left() <= 0:
        return []
    scripts = str(Path(str(config.ROOT)) / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import cc_discover  # type: ignore
    except Exception as exc:  # noqa: BLE001 — betik yoksa kaynak atlanır
        logger.info("local_fuel: cc_discover yüklenemedi: %s", str(exc)[:120])
        return []
    try:
        rows = cc_discover.harvest(
            per_page=max(200, min(1200, int(limit))),
            deadline_s=max(30.0, float(deadline_s)),
            seed=random.randint(1, 10**9),
            workers=4,
            profile=profile if profile in {"tr", "global", "eu", "all"} else "all",
            shard_index=0,
            shard_count=1,
        )
    except Exception as exc:  # noqa: BLE001 — harvest hatası diğer kaynakları durdurmaz
        logger.info("local_fuel cc harvest hatası: %s", str(exc)[:120])
        return []
    spend_gets(1)
    floor = int(getattr(config, "FEED_MIN_SCORE", 80) or 80)
    out: list[dict[str, Any]] = []
    for row in list(rows)[:limit]:
        if not isinstance(row, dict):
            continue
        host = clean_host(str(row.get("host") or row.get("url") or ""))
        if not host:
            continue
        out.append({
            "url": str(row.get("url") or "") or contact_url(host),
            "host": host,
            "easy_score": max(int(row.get("easy_score") or 0), floor),
            "source": "local-fuel:cc",
            "profile": str(row.get("profile") or profile_of(host)),
            "stack": str(row.get("stack") or ""),
        })
    return out


FETCHERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "tranco": tranco_rows,
    "cc": cc_rows,
    "crt": crt_rows,
}


# --- orkestrasyon ------------------------------------------------------------

def collect(*, per_source: int = DEFAULT_PER_SOURCE, profile: str = "all",
            allow_network: bool = True,
            rng: random.Random | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Etkin kaynaklardan aday topla — kaynak bazlı hata diğerlerini durdurmaz."""
    rng = rng or random.Random()
    rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {"sources": {}, "profile": profile, "network": allow_network}
    for name in sources():
        if name == "seeds":
            found = seed_rows(per_source, profile)
        else:
            fetcher = FETCHERS.get(name)
            if fetcher is None:
                stats["sources"][name] = {"skipped": "unknown_source"}
                continue
            try:
                found = fetcher(per_source, profile=profile, rng=rng,
                                allow_network=allow_network)
            except TypeError:  # dar imzalı fetcher (uyumluluk)
                found = fetcher(per_source)
            except Exception as exc:  # noqa: BLE001
                logger.info("local_fuel kaynak hatası %s: %s", name, str(exc)[:120])
                found = []
        stats["sources"][name] = {"rows": len(found)}
        rows.extend(found)
        if name != "seeds" and allow_network and found:
            _pace()
    stats["collected"] = len(rows)
    stats["budget_left"] = budget_left()
    return rows, stats


def dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """URL bazlı tekilleştirme: aynı URL'de YÜKSEK skor korunur (hot_fuel.push
    ile aynı kural; düşük skor yüksek skoru ezemez)."""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        prev = best.get(url)
        if prev is None or int(row.get("easy_score") or 0) > int(prev.get("easy_score") or 0):
            best[url] = dict(row)
    return list(best.values())


def self_test() -> int:
    """Kuru çalışma: ağ ve DB yazımı olmadan karar zincirini doğrular."""
    sample = (
        b"1,example.com\n2,ornek.com.tr\n3,shop-example.io\n"
    )
    rows = parse_tranco(sample, profile="all", limit=10, rng=random.Random(7))
    assert rows, "tranco ayrıştırıcı boş döndü"
    seeds = parse_seeds("tohum.com.tr\n# yorum\nbozuk-url!!\n", profile="tr")
    assert seeds and seeds[0]["url"] == "https://tohum.com.tr/iletisim", seeds
    merged = dedupe(rows + seeds)
    assert len(merged) >= len(seeds)
    print(f"LOCAL_FUEL SELF-TEST OK: tranco={len(rows)} seeds={len(seeds)} "
          f"unique={len(merged)} (ağ/DB yok)")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nirvana.local_fuel")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--per-source", type=int, default=DEFAULT_PER_SOURCE)
    parser.add_argument("--profile", default="all")
    parser.add_argument("--prime", type=int, default=DEFAULT_PRIME)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    result = run_batch(dry_run=args.dry_run, per_source=args.per_source,
                       profile=args.profile, prime=args.prime)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Lane giriş noktası: liste servislerinden topla -> hot_fuel rezervuarına yaz.

    ``rows=[...]`` verilirse ağa hiç dokunulmadan doğrudan rezervuara yazılır
    (testler + acil manuel besleme). ``collector=callable`` verilirse satırlar
    ondan alınır. İkisi de yoksa normal akış: kaynaklardan ``collect``.
    """
    dry_run = bool(kwargs.get("dry_run", False))
    allow_network = bool(kwargs.get("allow_network", not dry_run)) and not dry_run
    per_source = int(kwargs.get("per_source") or DEFAULT_PER_SOURCE)
    profile = str(kwargs.get("profile") or os.getenv("LOCAL_FUEL_PROFILE", "all") or "all")
    prime = int(kwargs.get("prime", DEFAULT_PRIME) or 0)
    direct_rows = kwargs.get("rows")
    collector = kwargs.get("collector")

    ready_now = 0
    try:
        ready_now = int(hot_fuel.status().get("ready") or 0)
    except Exception:  # noqa: BLE001 — durum okunamazsa besleme yine denenir
        ready_now = 0

    full_gate = int(hot_fuel.target() * 1.25)
    if not dry_run and not kwargs.get("force") and ready_now >= full_gate:
        return {
            "lane": "local_fuel",
            "skipped": "reservoir_full",
            "ready": ready_now,
            "target": hot_fuel.target(),
            "sources": {},
            "added": 0,
            "primed": 0,
            "budget_left": budget_left(),
            "note": "Rezervuar hedefin üzerinde — liste servisi çağrılmadı ($0).",
        }

    if direct_rows is not None:
        rows = [dict(r) for r in (direct_rows or []) if isinstance(r, dict)]
        stats: dict[str, Any] = {"sources": {"direct": {"rows": len(rows)}},
                                 "profile": profile, "collected": len(rows)}
    elif collector is not None:
        rows = list(collector())
        stats = {"sources": {"custom": {"rows": len(rows)}},
                 "profile": profile, "collected": len(rows)}
    else:
        rows, stats = collect(per_source=per_source, profile=profile,
                              allow_network=allow_network)

    rows = dedupe(rows)
    added = 0
    if rows and not dry_run:
        try:
            added = int(hot_fuel.push(rows, source="local_fuel"))
        except Exception as exc:  # noqa: BLE001 — DB hatası hattı düşürmez
            logger.info("local_fuel push hatası: %s", str(exc)[:120])
            stats["push_error"] = str(exc)[:120]
    primed = 0
    if prime > 0 and added and not dry_run:
        try:
            primed = int(hot_fuel.prime_queue(limit=prime))
        except Exception as exc:  # noqa: BLE001
            stats["prime_error"] = str(exc)[:120]

    ready_after = ready_now
    if not dry_run:
        try:
            ready_after = int(hot_fuel.status().get("ready") or 0)
        except Exception:  # noqa: BLE001
            ready_after = ready_now
        state = _load_state()
        state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        state["last_added"] = added
        state["last_primed"] = primed
        state["last_ready"] = ready_after
        state["last_sources"] = stats.get("sources") or {}
        history = list(state.get("history") or [])[-29:]
        history.append({"at": state["last_run"], "added": added,
                        "collected": stats.get("collected", len(rows))})
        state["history"] = history
        _save_state(state)

    return {
        "lane": "local_fuel",
        "dry_run": dry_run,
        "enabled": enabled(),
        "sources": stats.get("sources") or {},
        "collected": stats.get("collected", len(rows)),
        "unique": len(rows),
        "added": added,
        "primed": primed,
        "ready_before": ready_now,
        "ready_after": ready_after,
        "target": hot_fuel.target(),
        "budget_left": budget_left(),
        "daily_gets": daily_get_budget(),
        "profile": profile,
        "note": ("İç kaynaklı besleme: hedef siteye istek YOK; yalnızca liste "
                 "servisleri + hot_fuel rezervuarı ($0)."),
    }
