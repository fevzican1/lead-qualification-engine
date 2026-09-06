"""Nirvana registry / workflow / payment-config integrity tests."""
from __future__ import annotations

from pathlib import Path

import config
from nirvana.registry import MODULES, load_registry, module
from nirvana import runner as nirvana_runner

ROOT = Path(config.ROOT)


def test_registry_defines_exactly_nine_modules():
    modules = MODULES()
    assert len(modules) == 9
    letters = sorted(m["letter"] for m in modules.values())
    assert letters == list("ABCDEFGHI")


def test_host_assignment_matches_architecture():
    modules = MODULES()
    github = {name for name, m in modules.items() if m["host"] == "github"}
    oracle = {name for name, m in modules.items() if m["host"] == "oracle"}
    assert github == {"discovery_agent", "enrichment_agent", "audit_verifier_agent",
                      "strategy_pivot_agent", "objection_handler_agent", "retention_agent"}
    assert oracle == {"onboarding_agent", "delivery_runner", "watchdog_quota_agent"}


def test_every_module_has_entrypoint_and_runner_binding():
    for name, meta in MODULES().items():
        assert meta["entrypoint"].startswith("python -m nirvana.runner "), name
        assert meta["schedule"], name
        assert name in nirvana_runner.RUNNERS
        import importlib
        importlib.import_module(nirvana_runner.RUNNERS[name])


def test_nirvana_workflows_exist():
    heavy = ROOT / ".github" / "workflows" / "nirvana-heavy.yml"
    strategy = ROOT / ".github" / "workflows" / "nirvana-strategy.yml"
    for path in (heavy, strategy):
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "nirvana.runner" in text
    assert "audit_verifier_agent" in heavy.read_text(encoding="utf-8")
    stext = strategy.read_text(encoding="utf-8")
    assert "strategy_pivot_agent" in stext and "retention_agent" in stext


def test_oracle_units_exist():
    for name in ("nirvana-watchdog.service", "nirvana-watchdog.timer",
                 "nirvana-delivery.service", "nirvana-delivery.timer",
                 "nirvana_oracle_install.sh"):
        assert (ROOT / "oracle" / name).exists(), name


def test_payment_defaults_are_2500_eur():
    row = load_registry()["payment"]
    assert row["amount"] == 2500 and row["currency"] == "EUR"
    assert config.PAYMENT_AMOUNT == 2500
    assert config.PAYMENT_CURRENCY == "EUR"


def test_module_lookup_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        module("does_not_exist")
