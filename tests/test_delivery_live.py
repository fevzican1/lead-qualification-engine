"""Teslimat iscisi canli testi [CI/dev]: odeme kapisi -> kuyruk -> servis ->
rapor -> musteri karismazligi. Gercek Telegram'a dokunmaz (notify kapali).
"""
from nirvana import delivery_worker as dw


def test_payment_gate_blocks_unverified():
    dw.save_jobs([])
    dw.save_reports([])
    r = dw.start_job(chat_id=900001, domain="ornek-test-sirketi.de", service="infra-sweep")
    assert r["ok"] is True and r["job"]["status"] == "awaiting_payment"
    out = dw.run_batch(notify=False)
    assert out["delivered"] == 0, out
    assert dw.load_jobs()[0]["status"] == "awaiting_payment"


def test_verified_job_delivers_and_isolates():
    import telegram_sessions as ts
    dw.save_jobs([])
    dw.save_reports([])
    r1 = dw.start_job(chat_id=900002, domain="sirket-a-test.de", service="infra-sweep")
    r2 = dw.start_job(chat_id=900003, domain="sirket-b-test.de", service="contact-audit")
    assert r1["ok"] and r2["ok"]
    # Odeme kapisini simule et: tek kaynaktan dogrulama
    orig = ts.fulfillment_ready
    ts.fulfillment_ready = lambda cid: int(cid) == 900002
    try:
        out = dw.run_batch(notify=False)
    finally:
        ts.fulfillment_ready = orig
    assert out["delivered"] == 1, out
    jobs = {j["chat_id"]: j for j in dw.load_jobs()}
    assert jobs[900002]["status"] == "delivered"
    assert jobs[900002]["last_report_id"].startswith("RPT-")
    # Odenmemis is beklemede kaldi (karismadi)
    assert jobs[900003]["status"] == "awaiting_payment"
    reps = dw.load_reports()
    assert len(reps) == 1 and reps[0]["chat_id"] == 900002
    # Musteri hafizasi dogru musteriyi hatirliyor
    mem = dw.customer_memory(900002)
    assert mem["returning"] is True and mem["last_report"] == reps[0]["report_id"]
    assert dw.customer_memory(900003)["returning"] is False


def test_unknown_service_rejected():
    dw.save_jobs([])
    dw.save_reports([])
    r = dw.run_service({"service": "yok-boyle-hizmet", "domain": "x.de"})
    assert r["status"] == "error"


def test_report_text_has_ids():
    rep = {"report_id": "RPT-20250101-0001", "job_id": "job1", "chat_id": 5,
           "domain": "x.de", "service": "infra-sweep",
           "status": "ok", "findings": ["x.de: HTTP 200, 100 ms"]}
    txt = dw.report_text(rep)
    assert "RPT-20250101-0001" in txt and "x.de" in txt
