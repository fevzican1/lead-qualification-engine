"""Guard against accidentally restarting legacy fleets during enterprise rollout."""
from pathlib import Path
import re

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
LEGACY_JOBS = [
    ("discover-cc-eu.yml", "shard"),
    ("discover-cc-global.yml", "shard"),
    ("discover-cc-platform.yml", "shard"),
    ("discover-cc-tr.yml", "shard"),
    ("discover.yml", "shard"),
    ("discover-tranco-sitemap.yml", "harvest"),
    ("harvest-shard.yml", "gate"),
    ("publish-feed.yml", "publish"),
    ("payload_optimizer.yml", "optimize"),
    ("pipeline-watchdog.yml", "wake"),
    ("discovery-watchdog.yml", "watchdog"),
    ("refill-on-low.yml", "refill"),
    ("discovery-pipeline.yml", "pipeline"),
]


@pytest.mark.parametrize("filename,job", LEGACY_JOBS)
def test_legacy_entry_job_requires_explicit_opt_in(filename, job):
    text = (WORKFLOWS / filename).read_text(encoding="utf-8")
    match = re.search(r"^  " + re.escape(job) + r":\n    if: (.+)$", text, re.M)
    assert match, f"Missing gate on {filename}:{job}"
    expression = match.group(1)
    assert expression.startswith("${{ vars.ENABLE_LEGACY_SMB_WORKFLOWS == 'true'")
    assert expression.endswith("}}")
    # Optional event constraints must narrow, not OR past, the opt-in.
    remainder = expression.split("== 'true'", 1)[1].strip()
    assert remainder == "}}" or remainder.startswith("&& (")


def test_enterprise_discovery_is_not_legacy_gated_or_high_frequency():
    text = (WORKFLOWS / "enterprise-feed.yml").read_text(encoding="utf-8")
    assert "ENABLE_LEGACY_SMB_WORKFLOWS" not in text
    assert 'cron: "33 */6 * * *"' in text
    assert "timeout-minutes: 15" in text
    assert "python scripts/enterprise_demand_feed.py --scan" in text