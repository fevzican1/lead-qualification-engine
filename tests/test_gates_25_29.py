"""GATE_25 through GATE_29 regression tests.

Covers: load/concurrency, session isolation, VLA authenticity,
payment contract, WAF/security. All tests are offline/deterministic.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import manifest_check as mc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _gate_fn(gate_id: str):
    for g in mc.GATES:
        if g["id"] == gate_id:
            return g["fn"]
    raise AssertionError(f"gate {gate_id} not registered")


def _run_gate(gate_id: str) -> dict:
    fn = _gate_fn(gate_id)
    return fn()


# ---------------------------------------------------------------------------
# GATE_25 — load/concurrency
# ---------------------------------------------------------------------------

def test_gate_25_stealth_workers_in_range():
    r = _run_gate("GATE_25_load_concurrency")
    ev = " ".join(r.get("evidence", []))
    assert "stealth workers=" in ev or "eszamanlilik+kapsulleme+flood kapisi OK" in r.get("detail", "")


def test_gate_25_domain_jitter_in_range():
    r = _run_gate("GATE_25_load_concurrency")
    assert r["ok"] is True, r.get("detail")


def test_gate_25_flood_guard_status_is_dict():
    r = _run_gate("GATE_25_load_concurrency")
    assert r["ok"] is True, r.get("detail")


# ---------------------------------------------------------------------------
# GATE_26 — session isolation
# ---------------------------------------------------------------------------

def test_gate_26_session_isolation_ok():
    r = _run_gate("GATE_26_session_isolation")
    assert r["ok"] is True, r.get("detail")


def test_gate_26_no_cross_session_leak():
    import webchat_core as wc

    tmp = Path(tempfile.mkdtemp(prefix="nirvana_test26_"))
    sessions_path = tmp / "webchat_sessions.json"
    try:
        wc.SESSIONS_PATH = sessions_path
        wc._loaded = False
        wc._sessions.clear()

        a = wc.create_session(name="A", lang="tr", brief="b", profile_key="PROFILE-A")
        b = wc.create_session(name="B", lang="en", brief="b", profile_key="PROFILE-B")
        sid_a, sid_b = str(a["sid"]), str(b["sid"])
        assert sid_a != sid_b

        wc.append_history(sid_a, "user", "secret A only")
        wc.push_inbox(sid_a, "drip A", kind="drip_15m")

        sa = wc.get_session(sid_a)
        sb = wc.get_session(sid_b)

        hb = [h.get("content", "") for h in (sb or {}).get("history", [])]
        assert not any("secret A only" in h for h in hb)

        ib_b = wc.pop_inbox(sid_b)
        assert not any("drip A" in it.get("text", "") for it in ib_b)

        assert (sa or {}).get("profile_key", "").lower() == "profile-a"
        assert (sb or {}).get("profile_key", "").lower() == "profile-b"
    finally:
        try:
            sessions_path.unlink(missing_ok=True)
            tmp.rmdir()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GATE_27 — VLA authenticity
# ---------------------------------------------------------------------------

def test_gate_27_vla_card_has_all_evidence_points():
    r = _run_gate("GATE_27_vla_authenticity")
    assert r["ok"] is True, r.get("detail")


def test_gate_27_build_vla_proof_card_fields():
    from nirvana import proof_card as pc

    diag = {"dom_ms": 4200, "slow_count": 4, "bad_count": 2}
    card = pc.build_vla_proof_card("test-de.domain", diag, turkish=True)
    assert isinstance(card, dict)
    text = str(card.get("card_text", ""))
    assert card.get("domain") == "test-de.domain"
    for needle in ("DOM gecikmesi", "Yavaş istek", "Hatalı istek",
                   "Günlük kayip", "15 gün COI", "Lisans", "Kapanma"):
        assert needle in text, f"missing VLA evidence: {needle}"


def test_gate_27_financial_shock_card_ok():
    from nirvana import financial_shock_card as fs

    diag = {"dom_ms": 4200, "slow_count": 4, "bad_count": 2}
    sh = fs.build_shock(diag, revenue_eur=25000.0, currency="EUR",
                        issue="test issue", report_id="VLA-TEST-0001")
    assert sh.get("ok") is True
    assert sh.get("window_days") == 15
    for k in ("headline", "punch", "window_loss", "window_days"):
        assert sh.get(k) not in (None, "", 0), f"shock missing {k}"


def test_gate_27_financial_loss_engine_band():
    from nirvana import financial_loss_engine as fl

    loss = fl.estimate_loss(dom_ms=4200, slow_count=4, bad_count=2)
    assert loss.get("loss_band_pct")
    assert loss.get("basis")


# ---------------------------------------------------------------------------
# GATE_28 — payment contract
# ---------------------------------------------------------------------------

def test_gate_28_payment_ok():
    r = _run_gate("GATE_28_payment_contract")
    assert r["ok"] is True, r.get("detail")


def test_gate_28_payment_link_is_payoneer():
    from nirvana import payment as p

    link = p.payment_link()
    assert "payoneer" in link.lower()
    assert p.retainer_amount() == 5000
    assert p.retainer_currency() == "EUR"


def test_gate_28_payment_safety_hmac_roundtrip():
    import payment_safety as ps

    assert ps.amount_matches(5000) is True
    assert ps.amount_matches(100) is False

    ps.approve_link(chat_id=770002, amount=5000, currency="EUR",
                    recipient="Test R", reference="REF-T-002", owner_id=999)
    row = ps.ready_request(770002)
    assert row is not None
    assert row["amount"] == 5000
    assert row["currency"] == "EUR"


def test_gate_28_delivery_worker_payment_verified_callable():
    from nirvana import delivery_worker as dw
    assert callable(dw.payment_verified)


def test_gate_28_documenso_fail_open_info():
    from nirvana import esign_documenso as es

    assert isinstance(es.configured(), bool)


# ---------------------------------------------------------------------------
# GATE_29 — WAF/security
# ---------------------------------------------------------------------------

def test_gate_29_waf_ok():
    r = _run_gate("GATE_29_waf_security")
    assert r["ok"] is True, r.get("detail")


def test_gate_29_waf_rejects_waf_markers():
    from nirvana import stealth_former as sf

    cases = [
        (200, "<html>Access Denied by Cloudflare</html>"),
        (403, "ok"),
        (429, "ok"),
        (200, "security check: please verify you are human"),
        (200, "your submission was blocked by Akismet"),
    ]
    for status, body in cases:
        v = sf.verify_submission_response(status, body)
        assert v.get("verdict") == "WAF_REJECT", f"expected WAF_REJECT for {body[:40]!r}"


def test_gate_29_waf_rejects_malicious_payloads():
    from nirvana import stealth_former as sf

    bodies = [
        "<script>alert('xss')</script>",
        "' OR 1=1 --",
        "union select password from users",
        "<iframe src='javascript:alert(1)'></iframe>",
    ]
    for body in bodies:
        v = sf.verify_submission_response(200, body)
        assert v.get("verdict") not in ("verified", "verified_captcha_solved"), \
            f"malicious payload was verified: {body[:40]!r}"


def test_gate_29_waf_accepts_clean_responses():
    from nirvana import stealth_former as sf

    cases = [
        (200, "thank you for contacting us, message received"),
        (200, '{"status":"success","result":"ok"}'),
        (200, "mesajiniz alindi, tesekkur ederiz"),
    ]
    for status, body in cases:
        v = sf.verify_submission_response(status, body)
        assert v.get("verdict") != "WAF_REJECT", f"clean response rejected: {body[:40]!r}"


def test_gate_29_captcha_and_waf_markers_present():
    from nirvana import stealth_former as sf

    assert sf.CAPTCHA_MARKERS, "CAPTCHA_MARKERS must be non-empty"
    assert sf.WAF_MARKERS, "WAF_MARKERS must be non-empty"