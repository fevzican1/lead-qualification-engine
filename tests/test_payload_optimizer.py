from __future__ import annotations

import json
from pathlib import Path

import domain_store
import optimized_ingest
import optimized_payload
import payload_analyzer
import payload_builder
import telegram_handoff


def test_payload_analyzer_detects_seo_gaps() -> None:
    html = "<html><body><p>checkout cart iyzico</p></body></html>"
    result = payload_analyzer.analyze(html)
    assert "missing_or_weak_title" in result["gaps"]
    assert result["checkout_surface"] is True
    assert result["payment_present"] is True


def test_payload_builder_creates_handoff_and_deeplink() -> None:
    html = (
        "<html><head><title>Acme Shop</title>"
        '<meta name="description" content="WooCommerce store with checkout and iyzico payments"/>'
        "</head><body>WooCommerce checkout iyzico webhook</body></html>"
    )
    built = payload_builder.build_target(
        url="https://acme-example.com/contact",
        html=html,
        headers={"content-type": "text/html"},
        easy_score=88,
    )
    assert built is not None
    assert built["authorized_contact"] is True
    assert built["telegram_token"].startswith("ds")
    assert "t.me" in built["telegram_deeplink"] or built["telegram_deeplink"]
    assert built["form_subject"]
    assert built["value_proposition"]
    assert built["handoff"]["host"] == "acme-example.com"


def test_optimized_ingest_queues_and_caches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(domain_store, "QUEUE_PATH", tmp_path / "unprocessed_leads.json")
    monkeypatch.setattr(domain_store, "PROCESSED_PATH", tmp_path / "processed_domains.json")
    monkeypatch.setattr(domain_store, "BUDGET_PATH", tmp_path / "http_budget.json")
    monkeypatch.setattr(optimized_payload, "CACHE_PATH", tmp_path / "optimized_cache.json")
    monkeypatch.setattr(telegram_handoff, "PATH", tmp_path / "telegram_handoffs.json")
    monkeypatch.setattr(
        optimized_ingest.target_pool,
        "REVIEW_QUEUE_PATH",
        tmp_path / "review_queue.json",
    )
    monkeypatch.setattr(
        optimized_ingest.target_pool,
        "AUTHORIZED_TARGETS_PATH",
        tmp_path / "authorized_targets.txt",
    )

    payload = {
        "targets": [
            {
                "url": "https://merchant.example",
                "easy_score": 90,
                "authorized_contact": True,
                "telegram_token": "dsabc1234567",
                "form_subject": "Test",
                "value_proposition": "Body",
                "handoff": {"host": "merchant.example", "url": "https://merchant.example"},
            }
        ]
    }
    stats = optimized_ingest.ingest_batch(payload)
    assert stats["accepted"] == 1
    assert stats["cached"] == 1
    assert stats["queued"] == 1
    assert telegram_handoff.lookup("dsabc1234567") is not None
    assert optimized_payload.get_for_url("https://merchant.example") is not None
