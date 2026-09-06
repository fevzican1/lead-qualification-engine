"""Nirvana lane unit/integration tests — all offline (no network, no paid API)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import config
import payment_safety
from nirvana import audit_verifier_agent as audit
from nirvana import delivery_runner
from nirvana import discovery_agent as discovery
from nirvana import enrichment_agent as enrich
from nirvana import objection_handler_agent as objection
from nirvana import onboarding_agent as onboarding
from nirvana import retention_agent as retention
from nirvana import strategy_pivot_agent as strategy
from nirvana import watchdog_quota_agent as watchdog
from nirvana import payment as nirvana_payment
from nirvana.registry import state_path


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect every nirvana state file into a temp dir."""
    monkeypatch.setattr("nirvana.registry.STATE_DIR", tmp_path / "state")
    return tmp_path


# --- payment (2.500 EUR Payoneer) ------------------------------------------

def test_retainer_label_is_2500_eur():
    assert config.PAYMENT_AMOUNT == 2500 and config.PAYMENT_CURRENCY == "EUR"
    assert nirvana_payment.retainer_amount() == 2500
    assert nirvana_payment.retainer_currency() == "EUR"
    assert nirvana_payment.retainer_label() == "2.500 EUR"


def test_payment_link_refuses_placeholder(monkeypatch):
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL",
                        "https://www.payoneer.com/[BURAYA_YENI_PAYONEER_LINKINI_EKLEYIN]")
    with pytest.raises(nirvana_payment.PaymentLinkMissing):
        nirvana_payment.payment_link()
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL", "https://www.payoneer.com/req/ABC123")
    assert nirvana_payment.payment_link().endswith("ABC123")


def test_payment_safety_accepts_eur_request(isolated_state, monkeypatch):
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL", "https://link.payoneer.com/eur-2500")
    monkeypatch.setattr(payment_safety, "PATH", isolated_state / "payment_readiness.json")
    payment_safety.approve_link(chat_id=7, amount=2500, currency="EUR",
                                recipient="ExampleRecipient", reference="REQ-EUR-1", owner_id=12)
    row = payment_safety.ready_request(7)
    assert row is not None and row["currency"] == "EUR" and row["amount"] == 2500
    # legacy USD lane still works
    payment_safety.approve_link(chat_id=8, amount=2500, currency="USD",
                                recipient="ExampleRecipient", reference="REQ-USD-1", owner_id=12)
    assert payment_safety.ready_request(8)["currency"] == "USD"


def test_renewal_message_carries_link_and_amount(monkeypatch):
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL", "https://www.payoneer.com/req/REN1")
    msg = nirvana_payment.renewal_message("Acme")
    assert "2.500 EUR" in msg and "payoneer.com/req/REN1" in msg


# --- A. discovery -----------------------------------------------------------

def test_discovery_filters_sales_support_newsletter_pages():
    assert discovery.classify({"domain": "acme.com", "url": "https://acme.com/newsletter"}) == "reject_blocked_page"
    assert discovery.classify({"domain": "acme.com", "url": "https://acme.com/support"}) == "reject_blocked_page"
    assert discovery.classify({"domain": "acme.com", "url": "https://acme.com/sales-partner"}) == "reject_blocked_page"
    assert discovery.classify({"domain": "acme.com", "url": "https://acme.com/careers/apply"}) == "fit"
    assert discovery.classify({"domain": "acme.com", "url": "https://acme.com/partners",
                               "page_text": "become a vendor"}) == "fit"
    assert discovery.classify({"domain": "acme.com", "url": "https://acme.com/"}) == "reject_no_open_application"
    assert discovery.classify({}) == "reject_no_target"


def test_discovery_run_batch_dedupes(tmp_path):
    feed = tmp_path / "feed.json"
    feed.write_text(json.dumps([
        {"domain": "fit.com", "url": "https://fit.com/vendors/apply", "company": "Fit", "score": 90},
        {"domain": "nope.com", "url": "https://nope.com/newsletter", "company": "Nope"},
    ]), encoding="utf-8")
    first = discovery.run_batch(feed_path=feed)
    assert first["accepted"] == 1 and first["verdicts"].get("reject_blocked_page") == 1
    second = discovery.run_batch(feed_path=feed)
    assert second["accepted"] == 1  # deduped


# --- B. enrichment ----------------------------------------------------------

def test_build_hook_is_deterministic_and_factual():
    recon = {"security": {"present": False, "status": 404, "contact": False},
             "homepage_ok": True, "http_probe_ms": 900, "stack": ["nginx"]}
    a = enrich.build_hook("acme.com", recon)
    b = enrich.build_hook("acme.com", recon)
    assert a == b and "security.txt" in a and "nginx" in a


def test_enrichment_run_batch_offline(tmp_path, monkeypatch):
    (state_path("discovery.json")).write_text(json.dumps([
        {"domain": "acme.com", "company": "Acme"},
    ]), encoding="utf-8")
    monkeypatch.setattr(enrich, "dns_resolves", lambda d: d == "acme.com")
    monkeypatch.setattr(enrich, "fetch_security_txt", lambda d: {"present": False, "status": 404, "contact": False})
    monkeypatch.setattr(enrich, "fetch_homepage", lambda d: {"ok": True, "html": "", "elapsed_ms": 120})
    result = enrich.run_batch()
    assert result["probed"] == 1
    rows = json.loads(state_path("enrichment.json").read_text(encoding="utf-8"))
    assert rows[0]["verdict"] == "enriched" and "security.txt" in rows[0]["hook"]


# --- C. audit (fail-closed) -------------------------------------------------

def test_audit_registrable_domain_reduction():
    assert audit.registrable("jobs.acme.com") == "acme.com"
    assert audit.registrable("acme.com") == "acme.com"
    assert audit.registrable("acme.com.tr") in {"acme.com", "com.tr"}


def test_audit_verify_fail_closed_and_pass(monkeypatch):
    def fake_get(url, **kwargs):
        return SimpleNamespace(status_code=200,
                               text="<html>partner application form apply vendor</html>")
    monkeypatch.setattr(httpx, "get", fake_get)
    good = audit.verify({"domain": "acme.com", "url": "https://acme.com/partners/apply"})
    assert good["verdict"] == "pass"

    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
    unreachable = audit.verify({"domain": "acme.com", "url": "https://acme.com/partners/apply"})
    assert unreachable["verdict"] == "fail" and "page_unreachable" in unreachable["reasons"]


def test_oracle_queue_only_gets_pass_rows(monkeypatch):
    def fake_get(url, **kwargs):
        return SimpleNamespace(status_code=200, text="<html>apply now vendor</html>")
    monkeypatch.setattr(httpx, "get", fake_get)
    state_path("enrichment.json").write_text(json.dumps([
        {"domain": "good.com", "verdict": "enriched", "company": "Good", "url": "https://good.com/apply", "hook": "h"},
        {"domain": "bad.com", "verdict": "reject_dns", "company": "Bad", "url": "https://bad.com/apply", "hook": "h"},
    ]), encoding="utf-8")
    summary = audit.run_batch()
    assert summary["audited"] == 1 and summary["rejected"] == 0
    rows = audit.oracle_queue_rows()
    assert [r["domain"] for r in rows] == ["good.com"]
    assert all((r["audit"]["verdict"]) == "pass" for r in rows)


# --- D. strategy ------------------------------------------------------------

def test_strategy_holds_incumbent_on_low_sample():
    outcomes = [{"variant": "A", "converted": True}, {"variant": "B", "converted": False}]
    stats = strategy.analyze(outcomes)
    assert strategy.pick_winner(stats, incumbent="A")["winner"] == "A"


def test_strategy_flips_on_significant_edge():
    outcomes = [{"variant": "A", "converted": bool(i < 4)} for i in range(40)]
    outcomes += [{"variant": "B", "converted": True} for _ in range(24)]
    outcomes += [{"variant": "B", "converted": False} for _ in range(16)]
    stats = strategy.analyze(outcomes)
    decision = strategy.pick_winner(stats, incumbent="A")
    assert decision["winner"] == "B" and decision["changed"] is True


def test_strategy_run_batch_writes_state(tmp_path):
    src = tmp_path / "outcomes.json"
    src.write_text(json.dumps([{"variant": "A", "converted": True}]), encoding="utf-8")
    result = strategy.run_batch(in_path=src)
    state = json.loads(state_path("strategy_state.json").read_text(encoding="utf-8"))
    assert result["winner"] == state["winner"] == "A"
    assert "Ücretsiz 3 günlük pilot" in state["offer_variants"]["A"]


# --- E. objection handler ---------------------------------------------------

def test_objection_price_gets_free_pilot_pivot():
    reply = objection.handle("Fiyatınız çok yüksek, bütçemiz yok", turkish=True)
    assert reply and "3 gün" in reply and "ücretsiz pilot" in reply.lower()


def test_objection_security_reply():
    reply = objection.handle("is there a security risk with access?", turkish=False)
    assert reply and "read-only" in reply


def test_objection_neutral_text_returns_none():
    assert objection.handle("merhaba nasılsınız", turkish=True) is None
    assert objection.handle("") is None


# --- F. onboarding (Oracle) -------------------------------------------------

def test_onboarding_is_gated_on_fulfillment(monkeypatch):
    import telegram_sessions
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: False)
    assert onboarding.packet_for(42) == ""
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    monkeypatch.setattr(telegram_sessions, "_row", lambda cid: {"chat_id": 42, "company": "Acme"})
    packet = onboarding.packet_for(42)
    assert "Erişim kılavuzu" in packet and "read-only" in packet and "2.500 EUR" in packet


# --- G. delivery (Oracle) ---------------------------------------------------

def test_delivery_classifies_ok_degraded_down():
    import nirvana.delivery_runner as dr
    real_get = httpx.get
    class Ok:
        status_code = 200
    class Err5xx:
        status_code = 503
    try:
        httpx.get = lambda *a, **k: Ok()
        assert dr.check_domain("up.com")["status"] == "ok"
        httpx.get = lambda *a, **k: Err5xx()
        assert dr.check_domain("down.com")["status"] == "down"
        httpx.get = lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("t"))
        assert dr.check_domain("timeout.com")["status"] == "down"
    finally:
        httpx.get = real_get


def test_delivery_run_batch_reports_and_respects_notify_off(tmp_path):
    import nirvana.delivery_runner as dr
    clients = tmp_path / "clients.json"
    clients.write_text(json.dumps([{"domain": "up.com", "chat_id": 42}]), encoding="utf-8")
    real_get = httpx.get
    httpx.get = lambda *a, **k: SimpleNamespace(status_code=200)
    try:
        result = dr.run_batch(clients_path=clients, notify=False)
    finally:
        httpx.get = real_get
    assert result["checked"] == 1 and "✅" in result["report"] and result["notified"] is False


# --- H. retention -----------------------------------------------------------

def test_retention_monthly_stats_and_renewal_link(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL", "https://www.payoneer.com/req/REN2")
    history = tmp_path / "history.json"
    history.write_text(json.dumps([
        {"at": "2026-09-02T06:00:00Z", "results": [
            {"domain": "a.com", "status": "down", "ms": 0, "http": 0},
            {"domain": "b.com", "status": "ok", "ms": 100, "http": 200}]},
        {"at": "2026-08-02T06:00:00Z", "results": [
            {"domain": "a.com", "status": "down", "ms": 0, "http": 0}]},
    ]), encoding="utf-8")
    result = retention.run_batch(history_path=history, notify=False, client="Acme")
    assert result["stats"]["sweeps"] == 1
    assert result["stats"]["blocked_outages"] == 1
    assert "2.500 EUR" in result["report"]
    assert "payoneer.com/req/REN2" in result["report"]


# --- I. watchdog ------------------------------------------------------------

def test_watchdog_evaluates_cooling_mode():
    snap = {"available": True, "load_pct": 0.95, "mem_used_pct": 0.50}
    quotas = {"daily": {"used": 10, "cap": 400, "pct": 0.025},
              "hourly": {"used": 1, "cap": 32, "pct": 0.03}}
    assert watchdog.evaluate(snap, quotas)["mode"] == "cooling"
    snap2 = {"available": True, "load_pct": 0.30, "mem_used_pct": 0.40}
    assert watchdog.evaluate(snap2, quotas)["mode"] == "normal"


def test_watchdog_http_quota_triggers_cooling():
    quotas = {"daily": {"used": 490, "cap": 500, "pct": 0.98},
              "hourly": {"used": 1, "cap": 32, "pct": 0.03}}
    verdict = watchdog.evaluate({"available": False}, quotas)
    assert verdict["mode"] == "cooling" and any(r.startswith("http_daily") for r in verdict["reasons"])


def test_watchdog_cooldown_file_roundtrip():
    assert watchdog.in_cooldown() is False
    watchdog.set_cooling(minutes=1)
    assert watchdog.in_cooldown() is True
    watchdog.clear_cooling()
    assert watchdog.in_cooldown() is False


def test_watchdog_run_batch_dry_run_writes_status():
    status = watchdog.run_batch(notify=False, dry_run=True)
    assert status["mode"] in {"normal", "cooling"}
    saved = json.loads(state_path("watchdog_state.json").read_text(encoding="utf-8"))
    assert saved["mode"] == status["mode"]


# --- J. linkedin_router (insan-onaylı, otomasyonsuz) ------------------------

def test_linkedin_guess_and_token():
    from nirvana import linkedin_router as lr
    assert lr.domain_token("www.acme.co.uk") == "acme"
    assert lr.linkedin_guess("acme.com") == "https://www.linkedin.com/company/acme/"
    assert "site%3Alinkedin.com" in lr.search_link("acme.com")


def test_linkedin_candidates_pick_captcha_only(tmp_path):
    from nirvana import linkedin_router as lr
    leads = tmp_path / "leads.json"
    leads.write_text(json.dumps([
        {"host": "cap.com", "status": "skipped_captcha", "company": "Cap"},
        {"host": "ok.com", "status": "submitted_confirmed", "company": "Ok"},
        {"host": "cap2.com", "status": "skipped_no_open_form", "company": "Cap2"},
    ]), encoding="utf-8")
    items = lr.candidates(leads, limit=10)
    assert [i["domain"] for i in items] == ["cap2.com", "cap.com"]  # en yeni önce


def test_linkedin_run_batch_card_and_never_spam(tmp_path, monkeypatch):
    from nirvana import linkedin_router as lr
    leads = tmp_path / "leads.json"
    leads.write_text(json.dumps([
        {"host": f"cap{i}.com", "status": "skipped_captcha", "company": f"Cap{i}"}
        for i in range(15)
    ]), encoding="utf-8")
    monkeypatch.setattr(lr, "check_linkedin", lambda url: "found")
    sent_boxes: list[str] = []
    monkeypatch.setattr(lr.owner_notify, "send", lambda text, **kw: sent_boxes.append(text) or True)
    result = lr.run_batch(leads_path=leads, notify=True)
    assert result["routed"] == 10  # günlük cap
    assert len(sent_boxes) == 1    # tek kart — spam yok
    assert "LinkedIn inceleme kartı" in sent_boxes[0]
    again = lr.run_batch(leads_path=leads, notify=False)
    assert again["routed"] == 5    # ilk turda gönderilmeyen kalan hedefler
    third = lr.run_batch(leads_path=leads, notify=False)
    assert third["routed"] == 0    # hepsi yönlendirildi — tekrar yok


# --- K. meta_orchestrator (kendini geliştiren, kota asla yükseltemez) -------

def test_meta_weights_move_toward_winner_and_stay_bounded():
    from nirvana import meta_orchestrator as mo
    outcomes = ([{"lane": "enterprise", "converted": True}] * 12 +
                [{"lane": "enterprise", "converted": False}] * 8 +
                [{"lane": "smb", "converted": True}] * 2 +
                [{"lane": "smb", "converted": False}] * 18)
    yields = mo.channel_yields(outcomes)
    weights = mo.update_weights({"smb": 0.5, "enterprise": 0.5}, yields)
    assert weights["enterprise"] > weights["smb"]
    lo, hi = mo.BOUNDS
    assert all(lo - 1e-9 <= w <= hi + 1e-9 for w in weights.values())
    # boş veri: ağırlıklar korunur
    assert mo.update_weights({"smb": 0.5, "enterprise": 0.5}, {}) == {"smb": 0.5, "enterprise": 0.5}


def test_meta_run_batch_never_raises_limits(tmp_path):
    from nirvana import meta_orchestrator as mo
    src = tmp_path / "outcomes.json"
    src.write_text(json.dumps([{"lane": "smb", "converted": True}]), encoding="utf-8")
    result = mo.run_batch(outcomes_path=src, notify=False)
    saved = json.loads(state_path("meta_state.json").read_text(encoding="utf-8"))
    assert saved["hard_limits"] == {"daily": 400, "hourly": 32, "per_esp_hour": 3}
    assert any("kota yükseltilmez" in r for r in result["recommendations"])


# --- L. micro_audit_proof_agent (offline: Playwright mock'lu) ----------------

def test_proof_hook_is_observed_only_and_empty_without_image():
    from nirvana import micro_audit_proof as mp
    metrics = {"dom_ms": 4100,
               "slow_res": [{"url": "https://shop.example/cart/api", "ms": 2100}],
               "bad_reqs": []}
    hook = mp.build_hook("shop.example", metrics, "https://raw.githubusercontent.com/x/master/nirvana/proof-cards/shop.example.png")
    assert "shop.example" in hook and "2100 ms" in hook and "kanıt kartı" in hook
    assert mp.build_hook("shop.example", metrics, None) == ""  # fail-safe
    assert hook.count("12-18") == 0  # uydurma yüzde YOK


def test_proof_load_targets_https_only(tmp_path):
    from nirvana import micro_audit_proof as mp
    src = tmp_path / "queue.json"
    src.write_text(json.dumps([
        {"domain": "a.com", "url": "https://a.com/checkout", "hook": "h1"},
        {"domain": "b.com", "url": "http://b.com/", "hook": "h2"},
        {"domain": "", "url": "https://c.com/", "hook": "h3"},
    ]), encoding="utf-8")
    items = mp.load_targets(str(src), limit=10)
    assert [i["domain"] for i in items] == ["a.com"]


def test_proof_run_proofs_emits_card_and_cleans_tmp(isolated_state, monkeypatch):
    from nirvana import micro_audit_proof as mp
    from PIL import Image
    monkeypatch.setattr(mp, "PROOF_DIR", isolated_state / "cards")
    made_png = []
    def fake_probe(url, tmp_dir):
        png = tmp_dir / "shot.png"
        Image.new("RGB", (400, 200), (200, 200, 200)).save(png)
        made_png.append(png)
        return {"failed": False, "dom_ms": 3000,
                "slow_res": [{"url": "https://x.example/api", "ms": 1800}],
                "bad_reqs": [], "screenshot": png, "probed_url": url}
    monkeypatch.setattr(mp, "_probe", fake_probe)
    result = mp.run_proofs([{"domain": "x.example", "url": "https://x.example/checkout", "fallback_hook": "fb"}])
    assert result["proofs"] == 1 and result["fallbacks"] == 0
    assert (mp.PROOF_DIR / "x.example.png").exists()
    # temp hiç kalmadı (cleanup)
    assert all(not p.exists() for p in made_png)
    hooks = json.loads(state_path("proof_hooks.json").read_text(encoding="utf-8"))
    assert hooks["proofs"][0]["mode"] == "proof"
    assert "kanıt kartı" in hooks["proofs"][0]["hook"]


def test_proof_falls_back_on_probe_failure(isolated_state, monkeypatch):
    from nirvana import micro_audit_proof as mp
    monkeypatch.setattr(mp, "PROOF_DIR", isolated_state / "cards")
    monkeypatch.setattr(mp, "_probe", lambda url, td: {"failed": True, "reason": "timeout"})
    result = mp.run_proofs([{"domain": "y.example", "url": "https://y.example/", "fallback_hook": "fb-hook"}])
    assert result["proofs"] == 0 and result["fallbacks"] == 1
    hooks = json.loads(state_path("proof_hooks.json").read_text(encoding="utf-8"))
    assert hooks["proofs"][0]["mode"] == "fallback" and hooks["proofs"][0]["hook"] == "fb-hook"


def test_proof_run_batch_empty_queue(isolated_state):
    from nirvana import micro_audit_proof as mp
    result = mp.run_batch(in_name="verified_queue.json")
    assert result["probed"] == 0

