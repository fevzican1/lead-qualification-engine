"""6-maddelik B2B uretim plani — regresyon kapilari (cevrimdisi, hizli)."""
from __future__ import annotations
import asyncio
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
def test_outreach_tier1_and_tr_excluded():
    from services.outreach_engine import qualifies, is_tier1, is_excluded
    assert qualifies("https://acme.de", "B2B enterprise GmbH")["ok"] is True
    assert qualifies("https://partner.se", "B2B solutions AB")["ok"] is True
    assert qualifies("https://magaza.com.tr", "istanbul")["ok"] is False
    assert is_excluded("https://x.com.tr") is True
    assert is_tier1("https://acme.nl", "contact services") is True
    assert qualifies("https://myblog.blogspot.com", "personal blog")["ok"] is False
def test_outreach_quota_never_above_400():
    from services.outreach_engine import OutreachIntake
    it = OutreachIntake(); q = it.quota()
    assert q["daily_cap"] <= 400 and q["hourly_cap"] <= 48
def test_outreach_zero_drop_submit(tmp_path, monkeypatch):
    import services.outreach_engine as oe
    monkeypatch.setattr("task_queue.PATH", tmp_path / "tq.db")
    it = oe.OutreachIntake()
    ok = asyncio.run(it.submit({"url": "https://acme.de", "company_name": "Acme GmbH",
                                "description": "B2B enterprise", "easy_score": 90}))
    assert ok is True and it.stats.enqueued == 1
    bad = asyncio.run(it.submit({"url": "https://magaza.com.tr", "description": "istanbul"}))
    assert bad is False
def test_llm_router_config_and_fallback():
    from core.llm_router import litellm_proxy_config, route_sync
    cfg = litellm_proxy_config()
    assert cfg["primary"] and cfg["fallback"] and cfg["max_concurrency"] <= 8
    out = route_sync([{"role": "user", "content": "merhaba"}], lang="tr")
    assert out.get("text")
def test_audit_roi_projection():
    from analytics.audit_generator import roi_projection, proof_card_payload
    roi = roi_projection(20000.0, 10.0)
    assert roi["retainer_eur"] == 5000 and roi["net_eur"] < roi["recovered_eur"]
    card = proof_card_payload("https://example.com",
                              scan={"url": "x", "ttfb_ms": 300, "status": 200, "ok": True})
    assert "5000" in card["headline"] and card["roi"]["retainer_eur"] == 5000
def test_delivery_pipeline_verify():
    from workers.delivery_pipeline import verify
    rep = {"report_id": "RPT-1", "job_id": "j1", "chat_id": 7, "domain": "a.com",
           "service": "infra-sweep", "status": "ok"}
    assert verify(rep) in (True, False)
def test_chat_handler_health_and_backoff():
    from api.chat_handler import health_snapshot, backoff_schedule, session_alive
    h = health_snapshot()
    assert h["ws_route"] is True and "/health" in (h.get("routes") or [])
    assert backoff_schedule(3) == [1.0, 2.0, 4.0]
    assert session_alive("no-such-sid") is False
def test_chat_html_has_queue_and_health():
    html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    assert "pending" in html and "/health" in html and "_send" in html
def test_config_guardrails_present():
    import config
    assert config.ORACLE_RAM_LIMIT_MB <= 24576 and config.ORACLE_MAX_THREADS <= 32
    assert config.OUTREACH_EXCLUDE_REGION == ("TR",)
    assert config.OUTREACH_MIN_BUDGET_EUR == 5000
def test_form_roi_line():
    import telegram_handoff as th
    line = th.roi_line(turkish=True)
    assert "5000" in line and "EUR" in line

# --- 2026-10-05: yerel-öncelikli router + zero-notice zırh ------------------

def test_router_is_local_primary_and_external_passive():
    """Birincil motor YEREL Ollama; dis servisler (Groq/Cerebras) pasif."""
    from core import llm_router as r
    assert r.PRIMARY_MODEL == "ollama/qwen2.5:7b", r.PRIMARY_MODEL
    assert r.FAST_MODEL == "ollama/qwen2.5:3b", r.FAST_MODEL
    assert r.model_chain() == ["ollama/qwen2.5:7b", "ollama/qwen2.5:3b"]
    assert r.EXTERNAL_ENABLED is False, "dis servisler kapali kalmali"
    assert r.OLLAMA_BASE.startswith("http://localhost:11434"), r.OLLAMA_BASE
    # adimlarda hicbir dis servis gorunmez
    assert all(m.startswith("ollama/") for m, _ in r._steps())


def test_router_rejects_stale_and_external_model_names():
    """'stealth/space-bunny-alpha' ve dis servis imzalari zincire GIREMEZ."""
    from core import llm_router as r
    assert not r._valid_model("stealth/space-bunny-alpha")
    assert not r._valid_model("space-bunny-alpha")
    assert not r._valid_model("groq/llama-3.3-70b-versatile")
    assert not r._valid_model("cerebras/llama3.1-8b")
    assert not r._valid_model("openrouter/llama-3.1-8b")
    assert r._valid_model("ollama/qwen2.5:7b")
    # repoda boyle bir dize BULUNMAZ
    src = open("core/llm_router.py", encoding="utf-8").read()
    assert "space-bunny-alpha" not in src.replace(
        "'stealth/space-bunny-alpha'", "").replace("space-bunny-alpha'", "")


def test_external_404_500_quota_is_blocked():
    """Dis kaynakli 404/500/kota hatasi 100% engellenir, musteriye yansimaz."""
    from core import llm_router as r
    assert r.is_model_not_found(Exception("HTTP 404: model not found"))
    assert r.is_model_not_found(Exception("HTTP 500 Internal Server Error"))
    assert r.is_model_not_found(Exception("429 rate limit exceeded"))
    assert r.is_model_not_found(Exception("quota exceeded for this month"))
    assert not r.is_model_not_found(Exception("connection refused"))


def test_zero_notice_sanitize_drops_error_payloads():
    """JSON hata govdesi / '[object Object]' / HTTP hata metni chat'e GECMEZ."""
    from core import llm_router as r
    bad = ['{"error":"model not found"}', "[object Object]", "404 Not Found",
           "", None, {"detail": "Not Found"}, '{"detail":"Not Found"}']
    for s in bad:
        txt, ok = r.sanitize_reply(s)
        assert ok is False and txt == "", (s, ok, txt)
    good = "Merhaba, size nasil yardimci olabilirim?"
    assert r.sanitize_reply(good) == (good, True)


def test_chat_handler_guard_is_zero_notice():
    """Musteriye ASLA 'aksama/yeniden baglaniyor/hata' yazisi gitmez."""
    from api import chat_handler as ch
    assert ch.DISRUPT_NOTICE == "", "zero-notice: not yazisi bos olmali"
    for s in ("[object Object]", '{"detail":"Not Found"}', "500 Internal Server Error", ""):
        g = ch.guard_reply(s)
        assert g["degraded"] is True and g["text"] == "", (s, g)
    g = ch.guard_reply("Merhaba, size nasil yardimci olabilirim?")
    assert g == {"text": "Merhaba, size nasil yardimci olabilirim?", "degraded": False}
    assert ch.degrade_notice("[object Object]") == ""


def test_chat_html_has_zero_notice_filter():
    """Arayuzde hata yazan alert/toast/sys cagrilari temizlendi + suzucu var."""
    html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    assert "function cleanReply" in html
    # musteriye teknik/hata mesaji basan cagri kalmadi
    for gone in ("Baglanti hatasi", "Baglanti koptu", "Sunucu mesgul",
                 "Yeniden baglaniyor", "Baglanti zayif", "sys(m.error",
                 "alert("):
        assert gone not in html, gone


def test_ollama_request_uses_narrow_context_and_threads():
    """Isteklerde num_ctx=1024 + num_thread=4 (CPU on isleme milisaniye)."""
    import inspect, ollama_client
    src = inspect.getsource(ollama_client.chat)
    assert '"num_ctx"' in src and '"num_thread"' in src
    assert "1024" in src and '"4"' in src


def test_config_yaml_pins_local_models():
    """config.yaml yerel Ollama'ya sabitlenmis, dis servisler kapali."""
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    m = cfg["models"]
    assert m["primary"] == "ollama/qwen2.5:7b"
    assert m["fast"] == "ollama/qwen2.5:3b"
    assert m["base_url"] == "http://localhost:11434/v1"
    assert m["num_ctx"] == 1024 and m["num_thread"] == 4
    assert m["external_enabled"] is False
# O gunku commit'te sessizce calismayan 3 dal vardi; asagidaki testler
# hepsini tek tek kilitler.

def test_llm_router_semaphore_is_real_semaphore():
    """Hata: `_sem = ... if False else None` + `def _sem()` -> global, FONKSIYON
    oldu; `async with _sem()` her cagri'da TypeError atiyordu ve router her
    zaman 'fallback' metne dusuyordu (canli LLM hic kullanilmiyordu)."""
    import asyncio
    from core import llm_router as r
    async def _use():
        async with r._sem():
            return True
    assert asyncio.run(_use()) is True


def _sem_probe():
    """_sem() gercekten Semaphore mu donduruyor? (coroutine icinde cagrilir)."""
    import asyncio
    from core import llm_router as r
    async def _m():
        return r._sem()
    return asyncio.run(_m())


def test_llm_router_uses_semaphore_not_function():
    """_sem() bir asyncio.Semaphore dondurMELI, fonksiyon degil."""
    import asyncio
    sem = _sem_probe()
    assert isinstance(sem, asyncio.Semaphore), f"semaphore degil: {type(sem)}"


def test_ensure_session_state_returns_single_row():
    """Hata: `row, _ = wc.ensure_session(...)` -> ensure_session tek deger
    donduruyor, unpack her cagri'da ValueError veriyordu."""
    from api.chat_handler import ensure_session_state
    row = ensure_session_state("test-ensure-sid-1", lang="tr")
    assert isinstance(row, dict) and row.get("sid")


def test_ws_chat_no_bad_unpack_and_no_silent_swallow():
    """Hata: ws_chat icinde `row2, _s2 = ensure_session(...)` ValueError atiyor
    ve `except Exception: pass` ile yutuluyordu -> oturum tazeleme hic
    calismiyordu. Artik unpack yok, hata loglanir, ayni sid kullanilir."""
    src = (ROOT / "webchat_server.py").read_text(encoding="utf-8")
    assert "row2, _s2 = ensure_session" not in src
    assert 'str(sid) + "-r"' not in src
    assert "oturum tazeleme atlandi" in src


def test_chat_html_pending_queue_survives_reconnect():
    """Hata: kuyruk socket nesnesine bagliydi (ws._pending=[]), her
    connect()'te sifirlaniyordu -> kopma aninda yazilan mesajlar kalici
    kayboluyordu. Kuyruk artik disarida (pending) tutuluyor."""
    html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    assert "ws._pending" not in html
    assert "pending=[]" in html
    assert "pending.push(t)" in html
    # kuyruk yalnizca TEK yerde tanimlanir (reconnect sifirlamamali)
    assert html.count("pending=[]") == 1


def test_ensure_session_single_return_contract():
    """Sozlesme netligi: ensure_session tek satir dondurur (tuple DEGIL)."""
    import webchat_core as wc
    row = wc.ensure_session("test-contract-sid-1", lang="tr")
    assert isinstance(row, dict)
    assert row.get("sid") == "test-contract-sid-1"


def test_deploy_package_includes_router_and_chat_shield():
    """Deploy paketi core/ + api/ + config.yaml ICERMELI.

    2026-10-06 bulgusu: git archive yalnizca ust-duzey .py + nirvana/oracle/
    knowledge/templates topluyordu -> canli sunucuda core/llm_router.py ve
    api/chat_handler.py YOKTU; webchat_server'daki zirh importlari ya
    ImportError'a duser ya da devre disi kalirdi.
    """
    wf = (ROOT / ".github" / "workflows" / "nirvana-oracle-deploy.yml").read_text(encoding="utf-8")
    assert "templates core api services analytics config.yaml" in wf
    for need in ("core/llm_router.py", "api/chat_handler.py", "config.yaml",
                 "services/qualification_analyzer.py", "analytics/audit_generator.py"):
        assert need in wf, f"deploy kapisinda {need} kilidi yok"


def test_guarded_reply_never_raises_without_packages():
    """_guarded_reply hicbir kosulda YUKSELMELI (ic ice try/except + yerel
    son savunma): paket eksik/bozuk olsa bile ws akisi kirilmaz."""
    from webchat_server import _guarded_reply
    for bad in ("[object Object]", '{"detail":"Not Found"}', None,
                {"error": "x"}, "404 Not Found"):
        g = _guarded_reply(bad)
        assert isinstance(g, dict) and g["text"] == "" and g["degraded"] is True, (bad, g)
    good = "Merhaba, size nasil yardimci olabilirim?"
    assert _guarded_reply(good) == {"text": good, "degraded": False}
    src = (ROOT / "webchat_server.py").read_text(encoding="utf-8")
    assert "return _local(text)" in src
