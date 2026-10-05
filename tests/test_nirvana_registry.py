"""Nirvana registry / workflow / payment-config integrity tests."""
from __future__ import annotations

from pathlib import Path

import config
from nirvana.registry import MODULES, load_registry, module
from nirvana import runner as nirvana_runner

ROOT = Path(config.ROOT)


def test_registry_defines_exactly_thirty_modules():
    modules = MODULES()
    assert len(modules) == 41
    letters = sorted((m["letter"] for m in modules.values()),
                     key=lambda L: (len(L), L))
    assert letters == (list("ABCDEFGH")
                       + ["I", "J", "K", "L", "M", "N", "O", "P", "Q", "R",
                          "S", "T", "U", "V", "W", "X"]
                       + ["Y", "Z", "AA", "AB", "AC", "AD", "AE", "AF", "AG"]
                       + ["AH", "AI", "AJ", "AK", "AL", "AM", "AN", "AO"])


def test_host_assignment_matches_architecture():
    modules = MODULES()
    github = {name for name, m in modules.items() if m["host"] == "github"}
    oracle = {name for name, m in modules.items() if m["host"] == "oracle"}
    assert github == {"discovery_agent", "enrichment_agent", "audit_verifier_agent",
                      "strategy_pivot_agent", "objection_handler_agent", "retention_agent",
                      "meta_orchestrator", "micro_audit_proof_agent", "retainer_report_agent",
                      "message_optimizer", "email_infra_audit",
                      "tech_stack_detector", "service_readiness",
                      "financial_loss_engine", "hash_tokenizer", "tactic_router",
                      "slot_gate", "update_architecture", "data_sync",
                      "knowledge_updater", "supply_guard", "keepalive_guard"}
    assert oracle == {"onboarding_agent", "delivery_runner", "watchdog_quota_agent",
                      "linkedin_router", "contract_pack", "github_orchestrator",
                      "multi_service_runner", "forget_guard", "proof_card",
                      "queue_fuel_guard", "anti_spam_cadence", "interaction_tracker",
                      "delivery_worker", "stealth_former", "idle_guard",
                      "free_captcha_solver", "free_captcha_worker",
                      "local_fuel", "job_watchdog"}


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
    meta = ROOT / ".github" / "workflows" / "nirvana-meta.yml"
    for path in (heavy, strategy, meta):
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "nirvana.runner" in text
    assert "audit_verifier_agent" in heavy.read_text(encoding="utf-8")
    stext = strategy.read_text(encoding="utf-8")
    assert "strategy_pivot_agent" in stext and "retention_agent" in stext
    assert "meta_orchestrator" in meta.read_text(encoding="utf-8")


def test_oracle_units_exist():
    for name in ("nirvana-watchdog.service", "nirvana-watchdog.timer",
                 "nirvana-delivery.service", "nirvana-delivery.timer",
                 "nirvana-deliveryworker.service", "nirvana-deliveryworker.timer",
                 "nirvana-linkedin.service", "nirvana-linkedin.timer",
                 "nirvana-captcha.service", "nirvana-captcha.timer",
                 "nirvana-dispatch.service", "nirvana-dispatch.timer",
                 "nirvana-idleguard.service", "nirvana-idleguard.timer",
                 "nirvana-fuel.service", "nirvana-fuel.timer",
                 "nirvana-jobwatch.service", "nirvana-jobwatch.timer",
                 "nirvana-salesbot.service",
                 "nirvana_oracle_install.sh"):
        assert (ROOT / "oracle" / name).exists(), name
    install = (ROOT / "oracle" / "nirvana_oracle_install.sh").read_text(encoding="utf-8")
    # Yeni lane'ler canlıya alma betiğine de bağlanmalı (kurulum unutulmasın).
    assert "nirvana-idleguard.timer" in install
    assert "nirvana-dispatch.timer" in install
    assert "nirvana-salesbot.service" in install
    # İç yakıt + iş bekçisi canlı kurulumda etkinleşir.
    assert "nirvana-fuel.timer" in install
    assert "nirvana-jobwatch.timer" in install
    assert "local_fuel --self-test" in install


def test_keepalive_workflow_guards_60_day_disable():
    path = ROOT / ".github" / "workflows" / "keepalive.yml"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "keepalive_guard" in text
    assert "contents: write" in text


def test_idleguard_unit_low_priority_and_bounded():
    text = (ROOT / "oracle" / "nirvana-idleguard.service").read_text(encoding="utf-8")
    # Sentetik yük gerçek işi asla aç bırakmaz: en düşük öncelik + sınırlı bellek.
    assert "Nice=19" in text
    assert "MemoryMax=" in text
    assert "idle_guard" in text


def test_payment_defaults_are_5000_eur():
    row = load_registry()["payment"]
    assert row["amount"] == 5000 and row["currency"] == "EUR"
    assert config.PAYMENT_AMOUNT == 5000
    assert config.PAYMENT_CURRENCY == "EUR"


def test_module_lookup_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        module("does_not_exist")
