"""idle_guard — OCI Always-Free idle geri alim korumasi testleri."""

from __future__ import annotations

import json

import pytest

from nirvana import idle_guard as ig
from nirvana.registry import state_path


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """State dosyalari gercek depoya yazilmasin (Test izolasyonu)."""
    monkeypatch.setattr("nirvana.registry.STATE_DIR", tmp_path / "state")
    return tmp_path


def test_decide_esikleri():
    assert ig.decide(None) == "unknown"
    assert ig.decide(0.05) == "idle"      # %20 altinda -> sentetik yuk
    assert ig.decide(0.20) == "ok"        # tam esik: yeterli
    assert ig.decide(0.45) == "ok"
    assert ig.decide(0.60) == "high"      # tavana yaklasti -> yuk uretme
    assert ig.decide(0.90) == "high"


def test_burst_sinirlari():
    # Watchdog tavani (0.85) ile alt esik (0.20) arasinda kalinmali.
    assert 0.0 < ig.FLOOR_PCT < ig.CEIL_PCT < 0.85
    assert ig.BURST_SECONDS <= 45 and ig.MEM_TOUCH_MB <= 512


def test_mem_touch_sayfalara_dokunur():
    assert ig.mem_touch(1) == 1
    assert ig.mem_touch(0) == 0


def test_synthetic_burst_hafif_ve_donuk():
    out = ig.synthetic_burst(seconds=0.2, workers=1, mem_mb=1)
    assert out["workers"] == 1 and out["mem_touched_mb"] == 1
    assert out["seconds"] == 0.2


def test_run_batch_dry_run_yuk_uretmez(monkeypatch):
    calls: list[dict] = []

    def fake_burst(**kw):
        calls.append(kw)
        return {"seconds": kw.get("seconds"), "workers": 1, "mem_touched_mb": 0}

    monkeypatch.setattr(ig, "synthetic_burst", fake_burst)
    out = ig.run_batch(dry_run=True)
    assert out["dry_run"] is True
    assert calls == [], "dry-run modunda sentetik yuk uretilmemeli"
    assert out["verdict"] in {"idle", "ok", "high", "unknown"}
    saved = json.loads(state_path("idle_guard.json").read_text(encoding="utf-8"))
    assert saved["dry_run"] is True and saved["floor_pct"] == ig.FLOOR_PCT


def test_run_batch_idle_durumunda_burst_calistirir(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(ig, "load_pct", lambda snapshot=None: 0.05)
    monkeypatch.setattr(ig, "synthetic_burst",
                        lambda **kw: calls.append(kw) or
                        {"seconds": kw.get("seconds"), "workers": 1, "mem_touched_mb": 256})
    monkeypatch.setattr(ig.time, "sleep", lambda _s: None)
    out = ig.run_batch(seconds=0.1)
    assert out["verdict"] == "idle"
    assert calls and calls[0]["seconds"] == 0.1
    assert out["synthetic"]["mem_touched_mb"] == 256


def test_run_batch_yuk_yeterliyse_dokunmaz(monkeypatch):
    monkeypatch.setattr(ig, "load_pct", lambda snapshot=None: 0.35)
    monkeypatch.setattr(ig, "synthetic_burst",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("yuk uretilmemeli")))
    out = ig.run_batch()
    assert out["verdict"] == "ok" and out["synthetic"] == {}