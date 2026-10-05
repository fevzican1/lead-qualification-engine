"""keepalive_guard — GitHub Actions 60-gun pasiflesme kapisi testleri."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from nirvana import keepalive_guard as kg


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """State dosyalari gercek depoya yazilmasin (Test izolasyonu)."""
    monkeypatch.setattr("nirvana.registry.STATE_DIR", tmp_path / "state")
    return tmp_path


def setup_function(_fn) -> None:
    """Her test taze state ile baslasin."""
    kg.save_state({})


def test_days_since_okur_ve_bilinmeyen_tarihte_none():
    now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    assert kg.days_since(now.isoformat(), now=now) == 0.0
    assert kg.days_since("bozuk-tarih", now=now) is None
    assert kg.days_since("", now=now) is None


def test_esik_ve_pencere_mantigi():
    assert kg.should_commit(None) is True          # ilk kurulum
    assert kg.should_commit(10) is False
    assert kg.should_commit(40) is True
    assert kg.should_commit(61) is True
    # Erken dokunus: 20 gun dolmadan pencere oturmadi.
    assert kg.window_settled(5) is False
    assert kg.window_settled(20) is True


def test_run_batch_taze_state_ile_skip_eder():
    kg.save_state({"last_activity": datetime.now(timezone.utc).isoformat()})
    out = kg.run_batch()
    assert out["due"] is False and out["action"] == "skip"


def test_run_batch_token_yoksa_no_token_dry_run():
    kg.save_state({"last_activity": (datetime.now(timezone.utc)
                                     - timedelta(days=50)).isoformat()})
    out = kg.run_batch()
    # Test ortaminda GITHUB_TOKEN yok: hic commit atilmaz, karar raporlanir.
    assert out["due"] is True
    assert out["action"] in {"no_token", "dry_run", "commit"}
    if out["action"] != "commit":
        assert out["token_present"] is False


def test_synthetic_commit_tek_api_cagrisi_yapar(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, url, token, payload=None):
        calls.append((method, url, payload))
        if method == "GET":
            return {"sha": "abc123"}
        return {"commit": {"sha": "newsha"}}

    monkeypatch.setattr(kg, "_github_request", fake_request)
    out = kg.synthetic_commit(repo="fevzican1/lead-qualification-engine",
                              token="tok", branch="master")
    assert out["committed"] is True and out["sha"] == "newsha"
    assert [c[0] for c in calls] == ["GET", "PUT"]
    put = calls[1][2] or {}
    assert put["sha"] == "abc123" and put["branch"] == "master"
    # Icerik base64: sadece zaman damgasi, sir yok.
    import base64
    decoded = json.loads(base64.b64decode(put["content"]).decode("utf-8"))
    assert decoded["keepalive"] is True and "written_at" in decoded


def test_synthetic_commit_yoksa_sha_gondermez(monkeypatch):
    def fake_request(method, url, token, payload=None):
        if method == "GET":
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return {"content": {"sha": "fresh"}}

    monkeypatch.setattr(kg, "_github_request", fake_request)
    out = kg.synthetic_commit(repo="r", token="t")
    assert out["committed"] is True


def test_run_batch_commit_yolunda_state_tazeler(monkeypatch):
    kg.save_state({"last_activity": (datetime.now(timezone.utc)
                                     - timedelta(days=45)).isoformat(),
                   "run_count": 3})
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "fevzican1/lead-qualification-engine")
    monkeypatch.setattr(kg, "synthetic_commit",
                        lambda **kw: {"committed": True, "sha": "s1"})
    out = kg.run_batch()
    assert out["action"] == "commit" and out["run_count"] == 4
    saved = kg.load_state()
    assert saved["run_count"] == 4 and saved["days_before"] == 45.0
    assert saved["last_commit"] == "s1"


def test_run_batch_erken_dokunusu_engeller(monkeypatch):
    kg.save_state({"last_activity": (datetime.now(timezone.utc)
                                     - timedelta(days=5)).isoformat()})
    monkeypatch.setattr(kg, "synthetic_commit",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("commit atilmamali")))
    out = kg.run_batch()
    assert out["due"] is False and out["action"] == "skip"


def test_force_bayragi_pencereyi_atlar(monkeypatch):
    kg.save_state({"last_activity": datetime.now(timezone.utc).isoformat()})
    monkeypatch.setattr(kg, "synthetic_commit",
                        lambda **kw: {"committed": True, "sha": "forced"})
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    out = kg.run_batch(force=True)
    assert out["due"] is True and out["action"] == "commit"