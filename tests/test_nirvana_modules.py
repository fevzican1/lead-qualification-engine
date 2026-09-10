"""Nirvana lane unit/integration tests — all offline (no network, no paid API)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    # "Ele/çöpe at" yok: üretim kuyruğu yalnızca pass alır; fail olanlar
    # ÇÖPE ATILMAZ, Taktik Matrisi'ne rotalanır (routed_to_matrix).
    assert summary["audited"] == 1 and summary["routed_to_matrix"] == 1
    rows = audit.oracle_queue_rows()
    assert [r["domain"] for r in rows] == ["good.com"]
    assert all((r["audit"]["verdict"]) == "pass" for r in rows)
    # fail olan bad.com matriste; asla çöpe gitmez
    pending = json.loads(state_path("tactic_matrix_pending.json").read_text(encoding="utf-8"))
    assert any(str(p.get("domain")) == "bad.com" for p in pending)


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
    # Ücretsiz iş yok: kollar kanıt + çözüm haritası sunar, iş taahhüdü değil
    assert "Ücretsiz" not in json.dumps(state["offer_variants"], ensure_ascii=False)
    assert "haritası" in state["offer_variants"]["A"]


# --- E. objection handler ---------------------------------------------------

def test_objection_price_gets_value_pivot_no_free_work():
    reply = objection.handle("Fiyatınız çok yüksek, bütçemiz yok", turkish=True)
    low = reply.lower()
    # Profesyonel dil: müşteriye "ücretsiz" kelimesi HİÇ geçmez —
    # ne teklif ne ret cümlesi. Değer + kapanış planı anlatılır.
    assert reply and "cretsiz" not in low and "pilot" not in low
    assert "retainer" in low or "darboğaz" in low
    assert "kanıt" in low or "plan" in low


def test_non_free_tone_policy_dimension():
    """Müşteriye 'ücretsiz' kelimesi hiç geçmez (profesyonel dil politikası)."""
    from nirvana.tactic_router import hook_for
    h = hook_for("A", {"delay_band_pct": "8-10", "evidence_metric": 2100,
                       "reason": {"latency_ms": 2100, "threshold_ms": 1200}},
                 company="acme.com")
    assert "cretsiz" not in h.lower()
    assert "8-10" in h or "2100ms" in h


def test_tactic_router_classify_cascade():
    """Fallback cascade: A -> B -> C; hiçbir hedef boş kalmaz."""
    from nirvana.tactic_router import classify
    slow = classify({"ms": 2500, "headers": {"server": "nginx"}, "body": "hello"})
    assert slow["tactic"] == "A" and slow["delay_band_pct"] == "8-10"
    shop = classify({"ms": 300, "headers": {"x-powered-by": "Shopify"},
                     "body": "<div class=product>shop"})
    assert shop["tactic"] == "B" and shop["reason"]["platform"] == "Shopify"
    ok_fast = classify({"ms": 200, "headers": {"server": "nginx"},
                        "body": "<html>landing</html>"})
    assert ok_fast["tactic"] == "C" and "missing_headers" in ok_fast["reason"]


def test_tactic_router_runs_without_network_targets():
    """Boş girdi ile sorunsuz çalışır (ağ gerektirmez)."""
    from nirvana.tactic_router import run_batch
    import tempfile, pathlib
    from nirvana.registry import state_path
    out = state_path("tactic_matrix_test.json")
    # Geçici boş verified_queue yok -> boş çalışır
    result = run_batch(in_name="tactic_matrix_nonexistent.json", out_name="tactic_matrix_test.json")
    assert result["routed"] == 0
    try:
        out.unlink()
    except OSError:
        pass


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
    # SADECE captcha-kilitli hedefler LinkedIn'e gider; submit edilen/normal akış gitmez
    assert [i["domain"] for i in items] == ["cap.com"]


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


# --- M. retainer_report_agent (value-in-advance PDF) -------------------------

def test_report_pdf_generated_from_proofs(isolated_state, monkeypatch):
    from nirvana import retainer_report_agent as rr
    monkeypatch.setattr(rr, "REPORT_DIR", isolated_state / "reports")
    state_path("proof_hooks.json").write_text(json.dumps({
        "proofs": [{"domain": "shop.example", "mode": "proof",
                    "metrics": {"dom_ms": 5200,
                                "slow_res": [{"url": "https://shop.example/cart", "ms": 2200}],
                                "bad_reqs": []}}]}), encoding="utf-8")
    result = rr.run_batch(notify=False)
    assert result["reports"] == 1
    pdf = isolated_state / "reports" / "shop.example.pdf"
    assert pdf.exists() and pdf.read_bytes()[:4] == b"%PDF"
    saved = json.loads(state_path("retainer_reports.json").read_text(encoding="utf-8"))
    assert saved["reports"][0]["report_url"].endswith("shop.example.pdf")


def test_report_findings_are_observed_only(isolated_state, monkeypatch):
    from nirvana import retainer_report_agent as rr
    findings = rr._findings_from_proof({"metrics": {"dom_ms": 3300, "slow_res": [], "bad_reqs": []}})
    assert any("3300 ms" in f for f in findings)
    full_text = "\n".join(findings)
    assert "12-18" not in full_text and "35%" not in full_text  # uydurma istatistik yok


# --- N. contract_pack --------------------------------------------------------

def test_contract_pack_contains_sla_nda_ip_and_disclaimer():
    from nirvana import contract_pack as cp
    text = cp.pack_text(company="Acme", domain="acme.com")
    assert "SLA" in text and "Gizlilik" in text and "mülkiyet" in text
    assert "hukuki danışmanlık değildir" in text
    assert "2.500 EUR" in text


def test_onboarding_packet_includes_contract_pack(monkeypatch):
    import telegram_sessions
    from nirvana import onboarding_agent as onb
    monkeypatch.setattr(telegram_sessions, "fulfillment_ready", lambda cid: True)
    monkeypatch.setattr(telegram_sessions, "_row", lambda cid: {"chat_id": 9, "company": "Acme"})
    packet = onb.packet_for(9)
    assert "Sözleşme paketi" in packet and "SLA" in packet


# --- Sahip kimliği (LinkedIn) ------------------------------------------------

def test_identity_suffix_appears_in_proof_hook(monkeypatch):
    from nirvana import micro_audit_proof as mp
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", "https://www.linkedin.com/in/fevzican-aytekin-0b5501105")
    hook = mp.build_hook("shop.example",
                         {"dom_ms": 3000, "slow_res": [], "bad_reqs": []},
                         "https://raw.githubusercontent.com/x/master/nirvana/proof-cards/shop.example.png")
    hook += mp._identity_suffix()
    assert "linkedin.com/in/fevzican-aytekin" in hook


def test_identity_prompt_line_off_when_unset(monkeypatch):
    import telegram_sales_bot as bot
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", "")
    assert bot._identity_prompt_line() == ""
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", "https://www.linkedin.com/in/fevzican-aytekin-0b5501105")
    assert "verifiable human engineer" in bot._identity_prompt_line()


# --- Semantik önbellek (Oracle kota dostu) -----------------------------------

def test_semantic_cache_roundtrip_and_ttl(isolated_state, monkeypatch):
    from nirvana import semantic_cache as sc
    monkeypatch.setattr(sc, "DB_PATH", isolated_state / "cache.db")
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "Fiyat nedir?"}]
    assert sc.get(msgs) is None
    assert sc.put(msgs, "2.500 EUR") is True
    assert sc.get(msgs) == "2.500 EUR"
    # farklı soru → farklı anahtar
    assert sc.get([{"role": "user", "content": "SLA var mı?"}]) is None
    # TTL: eski kayıt dönmez
    monkeypatch.setattr(sc, "TTL_SECONDS", -1)
    assert sc.get(msgs) is None


# --- Self-Serve Close (owner /reply olmadan profesyonel kapanış) -------------

def test_ssc_terms_ack_regex():
    from nirvana import self_serve_close as ssc
    assert ssc.terms_acknowledged("Şartları kabul ediyorum")
    assert ssc.terms_acknowledged("I accept the terms")
    assert not ssc.terms_acknowledged("SLA nedir?")


def test_ssc_gate_requires_terms_and_artifact(isolated_state, monkeypatch):
    from nirvana import self_serve_close as ssc
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL", "https://link.payoneer.com/live")
    r = ssc.evaluate(42, "hadi başlayalım", brief=None, row={}, link="https://link.payoneer.com/live")
    assert not r["ok"] and r["reason"] == "terms"
    r = ssc.evaluate(42, "şartları kabul ediyorum", brief=None, row={},
                     link="https://link.payoneer.com/live")
    assert not r["ok"] and r["reason"] == "artifact"
    r = ssc.evaluate(42, "hadi", brief={"report_id": "DS-1"},
                     row={"terms_acknowledged": True}, link="https://link.payoneer.com/live")
    assert r["ok"] and "link.payoneer.com/live" in r["message"]


def test_ssc_blocked_when_already_or_not_allowed():
    from nirvana import self_serve_close as ssc
    r = ssc.evaluate(1, "kabul ediyorum", brief={"report_id": "x"},
                     row={"payment_sent": True}, link="l")
    assert r["reason"] == "already"
    r = ssc.evaluate(1, "kabul ediyorum", brief={"report_id": "x"}, row={}, allowed=False, link="l")
    assert r["reason"] == "not_allowed"


def test_ssc_message_carries_identity_and_retainer(monkeypatch):
    from nirvana import self_serve_close as ssc
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", "https://www.linkedin.com/in/fevzican-aytekin-0b5501105")
    r = ssc.evaluate(1, "şartları kabul ediyorum", brief={"report_id": "DS-9"}, row={},
                     link="https://link.payoneer.com/live")
    assert "2.500 EUR" in r["message"] and "linkedin.com/in/fevzican-aytekin" in r["message"]


def test_ssc_find_report_url_from_state(isolated_state):
    from nirvana import self_serve_close as ssc
    state_path("retainer_reports.json").write_text(json.dumps({
        "reports": [{"domain": "acme.com", "report_url": "https://raw.githubusercontent.com/x/master/nirvana/audit-reports/acme.com.pdf"}]
    }), encoding="utf-8")
    assert ssc.find_report_url("www.acme.com") and "acme.com.pdf" in ssc.find_report_url("acme.com")
    assert ssc.find_report_url("none.example") is None


def test_bot_self_serve_close_sends_link_without_reply(isolated_state, monkeypatch):
    import telegram_sales_bot as bot
    import telegram_sessions
    monkeypatch.setattr(telegram_sessions, "PATH", isolated_state / "sessions.json")
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL", "https://link.payoneer.com/live-2500eur")
    monkeypatch.setattr(bot, "_is_owner", lambda cid: False)
    monkeypatch.setattr(bot, "_hot_ping", AsyncMock())
    monkeypatch.setattr(bot.owner_notify, "send", lambda *a, **kw: True)
    monkeypatch.setattr(bot.optout, "is_chat_opted_out", lambda cid: False)
    monkeypatch.setattr(bot, "_briefs", {55: {"report_id": "DS-77", "company": "Acme", "turkish": True}})
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=55, type="private"),
                             effective_user=SimpleNamespace(id=55, language_code="tr", username="t"),
                             message=SimpleNamespace(text="Şartları kabul ediyorum, ödeme linkini gönderin.",
                                                     reply_text=reply))
    asyncio.run(bot.on_text(update, SimpleNamespace(bot=None)))
    sent = reply.call_args.args[0]
    assert "link.payoneer.com/live-2500eur" in sent
    assert telegram_sessions._row(55).get("self_serve_link_sent")


def test_bot_terms_question_gets_pack_not_link(isolated_state, monkeypatch):
    import telegram_sales_bot as bot
    import telegram_sessions
    monkeypatch.setattr(telegram_sessions, "PATH", isolated_state / "sessions.json")
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL", "https://link.payoneer.com/live-2500eur")
    monkeypatch.setattr(bot, "_is_owner", lambda cid: False)
    monkeypatch.setattr(bot, "_hot_ping", AsyncMock())
    monkeypatch.setattr(bot.owner_notify, "send", lambda *a, **kw: True)
    monkeypatch.setattr(bot.optout, "is_chat_opted_out", lambda cid: False)
    # Ollama yerel server yoksa fallback calisir — mock ile cevabı sabitle
    monkeypatch.setattr(bot, "_complete", lambda messages: "sözleşme paketi: SLA + NDA. kabul ediyorum yazın.")
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=56, type="private"),
                             effective_user=SimpleNamespace(id=56, language_code="tr", username="t"),
                             message=SimpleNamespace(text="SLA ve sözleşme var mı?", reply_text=reply))
    asyncio.run(bot.on_text(update, SimpleNamespace(bot=None)))
    sent = reply.call_args.args[0]
    assert "link.payoneer.com" not in sent
    assert "kabul ediyorum" in sent


# --- LinkedIn Router: captcha'lı hedeflere outreach draft ------------------------


def test_linkedin_outreach_draft_turkish(monkeypatch):
    from nirvana import linkedin_router as lr
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", "https://www.linkedin.com/in/fevzican-aytekin-0b5501105")
    draft = lr.build_outreach_draft("acme.com", "Acme", turkish=True)
    assert "Acme" in draft
    assert "2.500 EUR/ay" in draft
    assert "linkedin.com/in/fevzican-aytekin" in draft
    # Ücretsiz iş yok: kanıt gösterilir, uygulama doğrulanmış ödeme sonrası başlar
    assert "ücretsiz" not in draft.lower()
    assert "doğrulanmış ödeme" in draft


def test_linkedin_outreach_draft_english(monkeypatch):
    from nirvana import linkedin_router as lr
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", "")
    draft = lr.build_outreach_draft("acme.com", "Acme", turkish=False)
    assert "Acme" in draft
    assert "2.500 EUR/ay" in draft
    assert "free" not in draft.lower()
    assert "verified payment" in draft


def test_linkedin_outreach_draft_includes_report_url(isolated_state, monkeypatch):
    from nirvana import linkedin_router as lr
    state_path("retainer_reports.json").write_text(json.dumps({
        "reports": [{"domain": "acme.com", "report_url": "https://raw.githubusercontent.com/x/master/nirvana/audit-reports/acme.com.pdf"}]
    }), encoding="utf-8")
    monkeypatch.setattr(config, "OWNER_LINKEDIN_URL", "")
    draft = lr.build_outreach_draft("acme.com", "Acme", turkish=True)
    assert "acme.com.pdf" in draft


def test_linkedin_router_run_includes_drafts(isolated_state, monkeypatch):
    from nirvana import linkedin_router as lr
    import telegram_sessions
    # Create a leads.json with a captcha'd target
    leads = [{"host": "acme.com", "company": "Acme", "status": "skipped_captcha"}]
    (isolated_state / "leads.json").write_text(json.dumps(leads), encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", isolated_state)
    result = lr.run_batch(notify=False)
    assert result["routed"] == 1
    assert "drafts" in result
    assert len(result["drafts"]) == 1
    assert "Acme" in result["drafts"][0]["draft"]


# --- Lane O: stealth_former (CAPTCHA detection + routing) -----------------


def test_stealth_detects_captcha():
    from nirvana import stealth_former as sf
    html_with_captcha = '<div class="g-recaptcha" data-sitekey="6Le..."></div>'
    html_clean = '<form action="/contact"><input name="email"></form>'
    assert sf.detect_captcha(html_with_captcha)
    assert not sf.detect_captcha(html_clean)


def test_stealth_marks_captcha_and_routes(isolated_state, monkeypatch):
    from nirvana import stealth_former as sf
    monkeypatch.setattr(config, "ROOT", isolated_state)
    # Mock httpx.get to return captcha HTML
    class FakeResp:
        text = '<div class="cf-turnstile" data-sitekey="x"></div>'
        status_code = 200
    monkeypatch.setattr(sf.httpx, "get", lambda *a, **kw: FakeResp())
    result = sf.run_batch(urls=["https://acme.com/contact"])
    assert result["captcha_routed"] == 1
    assert result["submitted"] == 0


# --- Lane P: message_optimizer (PAS framework) --------------------------


def test_message_optimizer_pas_structure():
    from nirvana import message_optimizer as mo
    msg = mo.build_message(company="Acme", page="checkout", metric="2.1s gecikme", lang="tr")
    assert "Acme" in msg
    assert "checkout" in msg or "2.1s" in msg


def test_message_optimizer_best_variant(isolated_state, monkeypatch):
    from nirvana import message_optimizer as mo
    monkeypatch.setattr(config, "ROOT", isolated_state)
    mo.record_outcome("a.com", "A", replied=True, lang="tr")
    mo.record_outcome("b.com", "A", replied=True, lang="tr")
    mo.record_outcome("c.com", "B", replied=False, lang="tr")
    assert mo.best_variant(lang="tr") == "A"


# --- Lane Q: github_orchestrator (dry-run without token) -----------------


def test_github_orchestrator_no_token(monkeypatch):
    from nirvana import github_orchestrator as go
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    result = go.run_batch()
    assert result["ok"] is False
    assert result["reason"] == "no_token"


# --- Lane R: email_infra_audit -------------------------------------------


def test_email_infra_risk_score():
    from nirvana import email_infra_audit as eia
    good = {"spf": {"present": True}, "dmarc": {"present": True}, "mx": {"present": True}}
    bad = {"spf": {"present": False}, "dmarc": {"present": False}, "mx": {"present": False}}
    assert eia.risk_score(good) == 0
    assert eia.risk_score(bad) == 100


def test_email_infra_evidence():
    from nirvana import email_infra_audit as eia
    audit = {"spf": {"present": False}, "dmarc": {"present": True}, "mx": {"present": True}}
    ev = eia.build_evidence(audit)
    assert "SPF" in ev
    assert "Risk" in ev


# --- Lane S: tech_stack_detector ----------------------------------------


def test_tech_stack_detect_from_html():
    from nirvana import tech_stack_detector as tsd
    html = '<html><script src="wp-content/themes/x.js"></script><script>var woocommerce</script></html>'
    techs = tsd.detect_from_html(html)
    assert "WordPress" in techs
    assert "WooCommerce" in techs


def test_tech_stack_detect_from_headers():
    from nirvana import tech_stack_detector as tsd
    headers = {"server": "nginx", "x-powered-by": "PHP/8.1"}
    techs = tsd.detect_from_headers(headers)
    assert "Nginx" in techs
    assert "PHP" in techs


def test_tech_stack_evidence():
    from nirvana import tech_stack_detector as tsd
    stack = {"techs": ["WordPress", "WooCommerce", "Cloudflare"]}
    ev = tsd.build_evidence(stack)
    assert "WordPress" in ev
    assert "Cloudflare" in ev


# --- Lane T: service_readiness -------------------------------------------


def test_service_readiness_conflict_detection():
    from nirvana import service_readiness as sr
    row = {"detected_stack": {"techs": ["WordPress", "Cloudflare"]}}
    conflict = sr.assess_service_conflict(row)
    assert conflict["conflict_risk"] == "medium"
    assert "Cloudflare" in conflict["conflicting_services"]


def test_service_readiness_no_conflict():
    from nirvana import service_readiness as sr
    row = {"detected_stack": {"techs": ["WordPress", "WooCommerce"]}}
    conflict = sr.assess_service_conflict(row)
    assert conflict["conflict_risk"] == "low"
    assert conflict["recommendation"] == "proceed"


def test_service_readiness_batch():
    from nirvana import service_readiness as sr
    result = sr.run_batch(targets=["https://example.com"])
    assert result["checked"] == 1
    assert "ready" in result


# --- Lane U: multi_service_runner ---------------------------------------


def test_multi_service_start(isolated_state, monkeypatch):
    from nirvana import multi_service_runner as msr
    monkeypatch.setattr(config, "ROOT", isolated_state)
    result = msr.start_service(12345, "Acme Corp", "acme.com")
    assert result["ok"] is True
    assert result["active_count"] == 1


def test_multi_service_return_visit(isolated_state, monkeypatch):
    from nirvana import multi_service_runner as msr
    monkeypatch.setattr(config, "ROOT", isolated_state)
    msr.start_service(12345, "Acme Corp", "acme.com")
    result = msr.record_return_visit(12345)
    assert result["ok"] is True
    assert result["returning"] is True
    assert result["visit_count"] == 2


def test_multi_service_quota_limit(isolated_state, monkeypatch):
    from nirvana import multi_service_runner as msr
    monkeypatch.setattr(config, "ROOT", isolated_state)
    # 5 farklı müşteri ekle
    for i in range(5):
        msr.start_service(1000 + i, f"Company{i}", f"company{i}.com")
    # 6. başarısız olmalı
    result = msr.start_service(9999, "Overflow", "overflow.com")
    assert result["ok"] is False
    assert result["reason"] == "quota_full"


# --- Lane V: free_captcha_solver -----------------------------------------


def test_free_captcha_no_captcha():
    from nirvana import free_captcha_solver as fcs
    result = fcs.detect_and_solve("<html><form>no captcha</form></html>")
    assert result["has_captcha"] is False
    assert result["action"] == "proceed"


def test_free_captcha_detects_captcha():
    from nirvana import free_captcha_solver as fcs
    html = '<html><div class="g-recaptcha" data-sitekey="x"></div></html>'
    result = fcs.detect_and_solve(html)
    assert result["has_captcha"] is True


def test_free_captcha_without_tesseract(monkeypatch):
    from nirvana import free_captcha_solver as fcs
    monkeypatch.setattr(fcs, "_TESSERACT_AVAILABLE", False)
    result = fcs.solve_text_captcha(b"fake_image_bytes")
    assert result["ok"] is False
    assert result["reason"] == "tesseract_not_installed"


# --- Lane W: forget_guard (anti-karışıklık bekçisi) -----------------------


def test_forget_guard_domain_of():
    from nirvana import forget_guard as fg
    assert fg.domain_of("https://www.acme.com/contact") == "acme.com"
    assert fg.domain_of("acme.com") == "acme.com"
    assert fg.domain_of("") == ""


def test_forget_guard_company_conflict():
    from nirvana import forget_guard as fg
    assert fg.company_conflict("Acme Corp", {"company": "Other Labs"})
    assert not fg.company_conflict("Acme Corp", {"company": "Acme Corp"})
    assert not fg.company_conflict("Acme Corp", None)


def test_forget_guard_check_requires_domain(isolated_state):
    from nirvana import forget_guard as fg
    r = fg.check("Acme", "")
    assert not r["ok"]
    assert "no_form_domain" in r["reasons"]


def test_forget_guard_blocks_when_conflict(isolated_state):
    from nirvana import forget_guard as fg
    r = fg.check("Acme", "https://other.com/contact", brief={"company": "Other"})
    assert not r["ok"]
    assert "company_conflict" in r["reasons"]


def test_ssc_blocks_confusion(isolated_state, monkeypatch):
    """Oturum şirketi ≠ form şirketi → link YOK, reason confusion."""
    from nirvana import self_serve_close as ssc
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL", "https://link.payoneer.com/live")
    r = ssc.evaluate(99, "şartları kabul ediyorum",
                     brief={"report_id": "DS-9", "company": "Acme", "host": "acme.com"},
                     row={"company": "Other Corp", "terms_acknowledged": True},
                     link="https://link.payoneer.com/live")
    assert r["reason"] == "confusion"
    assert not r["ok"]


# --- Lane W/: proof_card + self_serve_close payment functions ------------------


def test_self_serve_payment_link_hit(monkeypatch):
    from nirvana import self_serve_close as ssc
    monkeypatch.setattr(config, "PAYONEER_PAYMENT_URL",
                        "https://link.payoneer.com/Token?t=TEST")
    msg = ssc.payment_link_hit(domain="")
    assert "link.payoneer.com" in msg
    assert "Tutar" in msg


def test_self_serve_send_close_to_turkish():
    from nirvana import self_serve_close as ssc
    msg = ssc.send_close_to("https://link.payoneer.com/x", turkish=True)
    assert "Anlaştık" in msg
    assert "link.payoneer.com" in msg
    assert "2.500" in msg


def test_self_serve_send_close_to_english():
    from nirvana import self_serve_close as ssc
    msg = ssc.send_close_to("https://link.payoneer.com/x", turkish=False)
    assert "Agreed" in msg
    assert "link.payoneer.com" in msg
    assert "2.500" in msg


def test_self_serve_payment_mention():
    from nirvana import self_serve_close as ssc
    tr = ssc.payment_mention(turkish=True)
    en = ssc.payment_mention(turkish=False)
    assert "EUR" in tr
    assert "EUR" in en


def test_proof_card_url_for_domain(isolated_state):
    from nirvana import proof_card as pc
    # No state file → None is fine (graceful fallback)
    result = pc.proof_url("example.com")
    assert result is None or result.startswith("http")


def test_proof_card_build_local():
    from nirvana import proof_card as pc
    path = pc.build_card("example.com", "checkout drop-off", "8-12% conversion loss")
    if path:
        assert path.suffix == ".png"


