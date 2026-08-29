from __future__ import annotations

import json
from pathlib import Path

import domain_store
import target_pool


def test_auto_approve_moves_high_score_review_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(target_pool, "REVIEW_QUEUE_PATH", tmp_path / "review_queue.json")
    monkeypatch.setattr(target_pool, "AUTHORIZED_TARGETS_PATH", tmp_path / "authorized_targets.txt")
    monkeypatch.setattr(domain_store, "QUEUE_PATH", tmp_path / "unprocessed_leads.json")
    monkeypatch.setattr(domain_store, "PROCESSED_PATH", tmp_path / "processed_domains.json")
    monkeypatch.setattr(domain_store, "BUDGET_PATH", tmp_path / "http_budget.json")

    target_pool.stage_candidate("https://shop.example/contact", easy_score=82, source="feed")
    approved = target_pool.auto_approve()

    assert approved == 1
    assert (tmp_path / "authorized_targets.txt").exists()
    assert "shop.example" in (tmp_path / "authorized_targets.txt").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "review_queue.json").read_text(encoding="utf-8"))
    assert payload["urls"] == []
    assert domain_store.queue_depth() == 1
    row = domain_store.pending_rows(limit=1)[0]
    assert row["authorized_contact"] is True


def test_promote_queue_authorization_upgrades_existing_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(target_pool, "REVIEW_QUEUE_PATH", tmp_path / "review_queue.json")
    monkeypatch.setattr(target_pool, "AUTHORIZED_TARGETS_PATH", tmp_path / "authorized_targets.txt")
    monkeypatch.setattr(domain_store, "QUEUE_PATH", tmp_path / "unprocessed_leads.json")
    monkeypatch.setattr(domain_store, "PROCESSED_PATH", tmp_path / "processed_domains.json")
    monkeypatch.setattr(domain_store, "BUDGET_PATH", tmp_path / "http_budget.json")

    domain_store.enqueue("https://merchant.example", source="feed", easy_score=85, authorized_contact=False)
    promoted = target_pool.promote_queue_authorization()

    assert promoted == 1
    row = domain_store.pending_rows(limit=1)[0]
    assert row["authorized_contact"] is True


def test_pending_rows_preserves_form_verified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(domain_store, "QUEUE_PATH", tmp_path / "unprocessed_leads.json")
    monkeypatch.setattr(domain_store, "PROCESSED_PATH", tmp_path / "processed_domains.json")
    domain_store.enqueue(
        "https://verified.example/contact",
        source="authorized-discovery",
        easy_score=88,
        authorized_contact=True,
        form_verified=True,
    )
    row = domain_store.pending_rows(limit=1)[0]
    assert row["form_verified"] is True
