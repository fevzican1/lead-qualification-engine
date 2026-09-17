"""Legacy fleet schedule contract (nirvana-live branch).

Eski strateji: legacy SMB filoları ENABLE_LEGACY_SMB_WORKFLOWS gate'i ile
kapatılırdı. nirvana-live'ta discover filoları BİRİNCİL üretim hattıdır ve
bilinçli olarak AKTİF koşar (3-4 saat kademeli ritim; dispatch_hub Oracle
tarafından 1 saatlik tetiklemede yönetilir). Bu yüzden gate testi yerine
SCHEDULE SÖZLEŞMESİ testi kullanılır: birisi cron'u yanlışlıkla 10dk'ya
çekerse ya da benzersiz dakikaları bozarsa test kırmızı verir.
"""
from pathlib import Path
import re

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

# Onaylı schedule sözleşmesi (3-4 saat kademeli keşif; benzersiz dakikalar).
EXPECTED_CRONS: dict[str, str | None] = {
    "discover-cc-eu.yml": "14 0,4,8,12,16,20 * * *",          # 4 saat, :14
    "discover-cc-global.yml": "23 1,5,9,13,17,21 * * *",      # 4 saat, :23
    "discover-cc-platform.yml": "29 2,6,10,14,18,22 * * *",   # 4 saat, :29
    "discover-cc-tr.yml": "37 3,7,11,15,19,23 * * *",         # 4 saat, :37
    "discover.yml": "43 0,4,8,12,16,20 * * *",                # 4 saat, :43
    "discover-tranco-sitemap.yml": "8 */3 * * *",             # 3 saat, :08
    "harvest-shard.yml": None,                                # zincir parçası — schedule YOK
    "publish-feed.yml": "26,56 * * * *",                      # zincir publish: 30dk
    "payload_optimizer.yml": "17 * * * *",                    # yedek cron: saatte 1
    "pipeline-watchdog.yml": "8 * * * *",                     # schedule-bekçisi: saatte 1
    "discovery-watchdog.yml": "21 * * * *",                   # tazelik bekçisi: saatte 1
    "refill-on-low.yml": "38 */2 * * *",                      # acil yakıt: 2 saat
    "discovery-pipeline.yml": "2 * * * *",                    # omurga: saatte 1
}

FLEET_FILES = ["discover.yml", "discover-cc-eu.yml", "discover-cc-global.yml",
               "discover-cc-platform.yml", "discover-cc-tr.yml",
               "discover-tranco-sitemap.yml"]


@pytest.mark.parametrize("filename", list(EXPECTED_CRONS))
def test_fleet_schedule_contract(filename):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    crons = re.findall(r"^\s*-\s*cron:\s*\"([^\"]+)\"", text, re.M)
    expected = EXPECTED_CRONS[filename]
    if expected is None:
        assert not crons, f"{filename} zincir parçası — schedule eklenmemeli"
        return
    assert expected in crons, f"{filename} cron sözleşmesi bozuldu: {crons} != {expected}"


def test_fleet_minutes_are_staggered_and_at_least_3h_apart():
    """Discover filoları: benzersiz dakikalar + en az 3 saat aralıklı ritim."""
    minutes: set[int] = set()
    for filename in FLEET_FILES:
        text = (WORKFLOWS / filename).read_text(encoding="utf-8")
        cron = re.search(r"^\s*-\s*cron:\s*\"([^\"]+)\"", text, re.M)
        assert cron, f"schedule yok: {filename}"
        fields = cron.group(1).split()
        minute, hours = int(fields[0]), fields[1]
        assert minute not in minutes, f"{filename} dakikası ({minute}) başka filoyla çakışıyor"
        minutes.add(minute)
        if hours.startswith("*/"):
            gap = int(hours[2:])
        else:
            hs = sorted(int(h) for h in hours.split(","))
            gaps = [b - a for a, b in zip(hs, hs[1:])]
            gaps.append(24 - hs[-1] + hs[0])  # gün sarmalı
            gap = min(gaps)
        assert gap >= 3, f"{filename} ritmi {gap} saat — en az 3 saat olmalı (hedef 3-4 saat)"


def test_enterprise_discovery_is_not_legacy_gated_or_high_frequency():
    text = (WORKFLOWS / "enterprise-feed.yml").read_text(encoding="utf-8")
    assert "ENABLE_LEGACY_SMB_WORKFLOWS" not in text
    assert 'cron: "33 */6 * * *"' in text
    assert "timeout-minutes: 15" in text
    assert "python scripts/enterprise_demand_feed.py --scan" in text