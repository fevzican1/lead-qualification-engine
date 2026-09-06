from unittest.mock import Mock

import pytest

import auto_runner
import config
import domain_store
import enterprise_apply
import feed_ingest
import knowledge


def test_enterprise_only_runner_syncs_feed_without_smb_import(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "SMB_LANE_ENABLED", False)
    monkeypatch.setattr(auto_runner.os, "chdir", lambda _: None)
    for name in ["hydrate_from_leads", "prune_dead_queue", "prune_enterprise_queue", "prune_noise_queue",
                 "purge_unverified_queue", "reclaim_false_kills", "clamp_long_defers", "evict_below"]:
        monkeypatch.setattr(domain_store, name, lambda *a: 0)
    monkeypatch.setattr(domain_store, "queue_depth", lambda: 0)
    monkeypatch.setattr(domain_store, "http_budget_label", lambda: "unused")
    monkeypatch.setattr(knowledge, "reload_overlays", lambda: None)
    monkeypatch.setattr(knowledge, "refresh", lambda: None)
    monkeypatch.setattr(knowledge, "submit_counts", lambda: (0, 0))
    monkeypatch.setattr(knowledge, "oracle_safe", lambda: True)
    monkeypatch.setattr(auto_runner, "_warn_if_starving", lambda **kw: None)
    sync = Mock(return_value={"count": 0, "updated_at": "test"})
    monkeypatch.setattr(feed_ingest, "sync_enterprise_feed", sync)
    legacy = Mock(side_effect=AssertionError("Legacy feed must remain disabled"))
    monkeypatch.setattr(feed_ingest, "sync_github_feed", legacy)
    monkeypatch.setattr(enterprise_apply, "run_batch", lambda: {"ran": False, "why": "test"})
    monkeypatch.setattr(auto_runner, "_sleep_after_cycle", lambda: 20)
    def end_cycle(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(auto_runner.time, "sleep", end_cycle)
    with pytest.raises(KeyboardInterrupt):
        auto_runner.main()
    sync.assert_called_once()
    legacy.assert_not_called()


def test_enterprise_idle_polling_does_not_use_smb_twenty_second_loop(monkeypatch):
    monkeypatch.setattr(config, "SMB_LANE_ENABLED", False)
    assert auto_runner._sleep_after_cycle() >= 900