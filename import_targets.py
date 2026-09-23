"""İç aktarım: dış CSV/TXT domain listeleri -> hot_fuel.db (mükerrer engelli).

Yakıt kıtlığının önlenmesi mekanizması: herhangi bir dış kaynaktan (CSV/TXT)
toplu domain alır, normalize eder, tekilleştirir ve sıcak havuza yazar.
``auto_runner._feed_hot_fuel()`` her turda havuzu ana kuyruğa aktardığı için
motorun günlük 400+ kapasitesi kesintisiz beslenir. Ağ isteği YOK ($0).

Kullanım:
    python import_targets.py hedefler.csv
    python import_targets.py liste.txt liste2.csv --dry-run
    python import_targets.py hedefler.csv --prime 200   # sonra kuyruğa da bas

Kurallar:
- CSV: başlık satırında url/domain/site/host kolonu aranır; yoksa ilk kolon.
  ``easy_score``/``score`` kolonu varsa satır skoru olarak kullanılır.
- TXT: satır başına tek domain/URL; ``#`` yorum ve boş satırlar atlanır.
- Normalize: ``nirvana.urlutil.safe_url`` (çift şema onarımı, https ekleme).
- Mükerrer engelleme: (a) dosya içi URL kümesi, (b) hot_fuel.db PRIMARY KEY
  (``ON CONFLICT(url) DO UPDATE`` — aynı URL ikinci kez satır üretmez).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Any

from nirvana import hot_fuel
from nirvana import urlutil

logger = logging.getLogger(__name__)

URL_COLUMN_NAMES = ("url", "urls", "domain", "domains", "site", "host", "address")
SCORE_COLUMN_NAMES = ("easy_score", "score", "points")
DEFAULT_SCORE = 80  # FEED_MIN_SCORE eşiği: havuza girip kuyruğa aktarılabilir


def _cell_name(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_").replace("-", "_")


def rows_from_csv(path: Path) -> list[dict[str, Any]]:
    """CSV'den {url, easy_score?} satırları; başlık algılama dayanıklıdır."""
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header: list[str] | None = None
            url_idx, score_idx = 0, -1
            for raw in reader:
                if not raw or not any(cell.strip() for cell in raw):
                    continue
                if header is None and any(_cell_name(c) in URL_COLUMN_NAMES for c in raw):
                    cells = [_cell_name(c) for c in raw]
                    header = cells
                    url_idx = next(
                        (i for i, c in enumerate(cells) if c in URL_COLUMN_NAMES), 0
                    )
                    score_idx = next(
                        (i for i, c in enumerate(cells) if c in SCORE_COLUMN_NAMES), -1
                    )
                    continue
                url = raw[url_idx].strip() if url_idx < len(raw) else ""
                if not url:
                    continue
                row: dict[str, Any] = {"url": url}
                if 0 <= score_idx < len(raw):
                    try:
                        row["easy_score"] = int(float(raw[score_idx]))
                    except ValueError:
                        pass
                rows.append(row)
    except OSError as exc:
        logger.error("CSV okunamadı %s: %s", path, exc)
    return rows


def rows_from_txt(path: Path) -> list[dict[str, Any]]:
    """TXT: satır başına tek domain/URL; # yorum ve boş satırlar atlanır."""
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            rows.append({"url": value.split(",")[0].strip()})
    except OSError as exc:
        logger.error("TXT okunamadı %s: %s", path, exc)
    return rows

def rows_from_path(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        return rows_from_csv(path)
    return rows_from_txt(path)


def normalize(rows: list[dict[str, Any]], *, default_score: int = DEFAULT_SCORE
              ) -> tuple[list[dict[str, Any]], int, int]:
    """(tekil-normalize satırlar, okunan, atlanan). Dosya içi mükerrer kesilir."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        url = urlutil.safe_url(str(row.get("url") or ""))
        domain = urlutil.clean_domain(url)
        if not url or not domain:
            skipped += 1
            continue
        if url in seen:
            skipped += 1  # dosya içi mükerrer
            continue
        seen.add(url)
        try:
            score = int(row.get("easy_score") or default_score)
        except (TypeError, ValueError):
            score = int(default_score)
        out.append({
            "url": url,
            "host": domain,
            "easy_score": max(0, min(100, score)),
            "source": "import_targets",
        })
    return out, len(rows), skipped


def import_files(paths: list[str], *, default_score: int = DEFAULT_SCORE,
                 dry_run: bool = False) -> dict[str, Any]:
    """Dosyaları hot_fuel.db'ye aktar; URL bazında tekilleştirme yapılır."""
    raw: list[dict[str, Any]] = []
    files_ok = 0
    for name in paths:
        path = Path(name)
        if not path.exists():
            logger.warning("Dosya yok, atlandı: %s", path)
            continue
        files_ok += 1
        raw.extend(rows_from_path(path))
    rows, read, deduped = normalize(raw, default_score=default_score)
    added = 0
    if rows and not dry_run:
        hot_fuel.init()
        with hot_fuel.connect() as conn:
            existing = {
                str(r["url"])
                for r in conn.execute("SELECT url FROM hot_fuel").fetchall()
            }
        fresh = [r for r in rows if r["url"] not in existing]
        deduped += len(rows) - len(fresh)  # DB'de zaten vardı -> mükerrer say
        added = hot_fuel.push(fresh, source="import_targets")
    depth = ({"depth": None, "ready": None} if dry_run else hot_fuel.status())
    return {
        "files": files_ok,
        "read": read,
        "normalized": len(rows),
        "deduped": deduped,
        "added": added,
        "dry_run": dry_run,
        "depth": depth.get("depth"),
        "ready": depth.get("ready"),
        "target": hot_fuel.target(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CSV/TXT domain listesini hot_fuel.db havuzuna aktar (mükerrer engelli).",
    )
    parser.add_argument("paths", nargs="+", help="CSV veya TXT dosyaları")
    parser.add_argument("--score", type=int, default=DEFAULT_SCORE,
                        help=f"Skor kolonu olmayan satırların easy_score değeri "
                             f"(varsayılan {DEFAULT_SCORE})")
    parser.add_argument("--prime", type=int, default=0, metavar="N",
                        help="Aktarımdan sonra hot_fuel'den N hedefi ana kuyruğa aktar")
    parser.add_argument("--dry-run", action="store_true",
                        help="Yalnızca raporla, havuza yazma")
    args = parser.parse_args(argv)

    result = import_files(args.paths, default_score=args.score,
                          dry_run=args.dry_run)
    if args.prime > 0 and not args.dry_run:
        result["primed"] = hot_fuel.prime_queue(limit=int(args.prime))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["files"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

