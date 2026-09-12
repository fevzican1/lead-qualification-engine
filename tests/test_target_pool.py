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


def test_save_review_uses_pid_suffixed_tmp(tmp_path: Path, monkeypatch) -> None:
    """tmp adı PID-sonekli olmalı: paylaşımlı review_queue.json.tmp iki sürecin
    yarışına girince kaybedenin replace()'i FileNotFoundError fırlatıyordu ve
    pipeline turu kod 1 ile çıkıyordu ('Pipeline turu hata ile bitti')."""
    path = tmp_path / "review_queue.json"
    monkeypatch.setattr(target_pool, "REVIEW_QUEUE_PATH", path)
    seen: list[str] = []
    real_replace = Path.replace

    def spy(self: Path, target) -> Path:
        seen.append(self.name)
        return real_replace(self, Path(target))

    monkeypatch.setattr(Path, "replace", spy)
    monkeypatch.setattr(target_pool.os, "getpid", lambda: 4242)

    target_pool._save_review({"urls": ["x"]})

    assert seen == ["review_queue.json.4242.tmp"]
    assert json.loads(path.read_text(encoding="utf-8"))["urls"] == ["x"]


def test_two_processes_saving_review_queue_do_not_crash(tmp_path: Path, monkeypatch) -> None:
    """Eski paylaşımlı tmp adıyla process 111'in replace()'i, process 222 tmp'yi
    alıp replace ettikten sonra FileNotFoundError ile çöküyordu. PID-sonekli tmp
    ile her iki sürecin replace'i başarılı olmalı."""
    path = tmp_path / "review_queue.json"
    monkeypatch.setattr(target_pool, "REVIEW_QUEUE_PATH", path)
    current_pid = {"v": 111}
    monkeypatch.setattr(target_pool.os, "getpid", lambda: current_pid["v"])

    # process 111: tmp'yi yazdı, replace'ten hemen önce askıya alındı
    tmp_a = path.with_suffix(path.suffix + ".111.tmp")
    tmp_a.write_text(
        json.dumps({"urls": ["a"], "updated_at": ""}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # process 222: kendi tmp'sini yazıp replace etti
    current_pid["v"] = 222
    target_pool._save_review({"urls": ["b"]})
    assert json.loads(path.read_text(encoding="utf-8"))["urls"] == ["b"]

    # process 111 devam etti: artık kendi tmp'si yerinde, replace çökmemeli
    current_pid["v"] = 111
    tmp_a.replace(path)  # eskiden: FileNotFoundError (paylaşımlı tmp silinmişti)
    assert json.loads(path.read_text(encoding="utf-8"))["urls"] == ["a"]
