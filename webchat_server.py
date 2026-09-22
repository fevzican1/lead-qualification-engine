"""Nirvana Web Live Chat Engine - Oracle Always Free ($0) musteri hatti.
Telegram musteriye KAPALI; sadece pasif admin bildirimi. $0, tek surec.
Calistir: uvicorn webchat_server:app --host 127.0.0.1 --port 8765
"""
from __future__ import annotations
import asyncio, hashlib, json, logging, os, re, time, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
logger = logging.getLogger("webchat")
try:
    import config  # type: ignore
    ROOT = Path(str(config.ROOT))
except Exception:
    config = None  # type: ignore
    ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "nirvana" / "state"
SESSIONS_PATH = STATE_DIR / "webchat_sessions.json"
VOICE_DIR = STATE_DIR / "voice"
TEMPLATES_DIR = ROOT / "templates"
WEBCHAT_PORT = int(os.getenv("WEBCHAT_PORT", "8765") or 8765)
WEBCHAT_PUBLIC_URL = os.getenv("WEBCHAT_PUBLIC_URL", "").strip().rstrip("/")
WEBCHAT_BIND = os.getenv("WEBCHAT_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
N_WORKERS = 3
MAX_HISTORY = 12
VOICE_MAX_FILES = 60
VOICE_TIMEOUT_S = 22.0
DRIP_STEPS = (("drip_15m", 15*60), ("drip_2h", 2*3600), ("drip_24h", 24*3600))
DRIP_TOLERANCE = {"drip_15m": 150.0, "drip_2h": 900.0, "drip_24h": 2400.0}
_BUDGET_RE = re.compile(r"(\u20ac|\$|\u00a3|eur[oa]?|usd|dolar|tl|b\u00fct\u00e7e|butce|budget|fiyat|\u00fccret|ucret|\b\d[\d.\s]{2,}\b|\b\d+\s*[kKmM]\b)", re.I)
_HIGH_INTENT_RE = re.compile(r"(sat\u0131n al|satin al|ba\u015flayal\u0131m|baslayal|kabul|onayl|\u00f6deme|odeme|anla\u015ft\u0131k|anlastik|fatura|teklif|demo|toplant\u0131|toplanti|\bbuy\b|\bpay\b|ready to (buy|start|pay)|let'?s (start|proceed)|go ahead|send (the )?(payment|invoice|link)|approve|hire you|start the (pilot|retainer))", re.I)
_VIP_BUDGET_RE = re.compile(r"(\u20ac|\$|\u00a3|\b\d\s?000\b|\b[5-9]\d{3}\b|\b\d{2}\s?k\b)", re.I)
_TR_HINT_RE = re.compile(r"[\u00e7\u011f\u0131\u00f6\u015f\u00fc\u00c7\u011e\u0130\u00d6\u015e\u00dc]")  # re.I YOK (case-folding bugi)
_BUY_RE = re.compile(r"sat\u0131n\s*al|ba\u015flayabilir|baslayabilir|haydi\s+ba\u015fla|hadi\s+ba\u015fla|anla\u015ft\u0131k|anlastik|\u00f6deme\s*link|odeme\s*link|kabul\s+ediyoruz|onayl[il]yor|ready\s+to\s+(buy|start|pay)|i\s+want\s+to\s+(buy|start|proceed)|proceed\s+to\s+pay|send\s+(the\s+)?(invoice|payment|link)|let'?s\s+(proceed|start)|go\s+ahead|hire\s+you\b", re.I)
_NEG_BUY_RE = re.compile(r"\b(not|no|never|don't|do not|can't|cannot|won't|if|whether|haven't)\b|hay\u0131r|hayir|istemiyorum|de\u011fil|degil|hen\u00fcz|henuz|\?", re.I)
def wants_to_buy(text: str) -> bool:
    c = (text or "").strip()
    return bool(_BUY_RE.search(c) and not _NEG_BUY_RE.search(c))
def detect_lang(text: str) -> str:
    try:
        from nirvana import language_auditor as la  # type: ignore
        code = la.detect_locale(f"<html><body>{text}</body></html>", fallback="")
        if code in ("tr","en","de","fr","es","it","pt","nl"): return code
    except Exception: pass
    b = f" {text or ''} "
    if _TR_HINT_RE.search(b): return "tr"
    if re.search(r"\b(der|die|das|und|kontakt)\b", b, re.I): return "de"
    if re.search(r"\b(bonjour|merci|contactez)\b", b, re.I): return "fr"
    if re.search(r"\b(hola|gracias|contacto)\b", b, re.I): return "es"
    return "en"
def score_lead(text: str) -> dict[str, Any]:
    t = text or ""; s = 20; sig: list[str] = []
    if _HIGH_INTENT_RE.search(t): s += 40; sig.append("intent:high")
    elif re.search(r"(fiyat|price|how much|ne kadar|bilgi|info|nas\u0131l|nasil|how)", t, re.I): s += 15; sig.append("intent:mid")
    else: sig.append("intent:low")
    if _VIP_BUDGET_RE.search(t): s += 25; sig.append("budget:vip")
    elif _BUDGET_RE.search(t): s += 10; sig.append("budget:mentioned")
    if wants_to_buy(t): s += 10; sig.append("buy:explicit")
    s = max(0, min(100, s))
    tone = "vip_close" if s >= 70 else ("educate" if s < 40 else "consult")
    return {"score": s, "tone": tone, "signals": sig}
# __PART2__ prompts
def build_system_prompt(*, name="", lang="tr", score=None, brief="", history=None):
    score = score or {"tone": "educate"}
    rag = ""
    try:
        import knowledge  # type: ignore
        rag = knowledge.assistant_context(limit=10)
    except Exception: rag = "Hizmet: e-ticaret/CRM/ERP entegrasyonu, odeme webhook onarimi."
    tac = ""
    try:
        from nirvana import conversion_maximizer as cm  # type: ignore
        last = ""
        for m in reversed(history or []):
            if m.get("role") == "user": last = str(m.get("content") or ""); break
        tac = cm.close_block(user_text=last, brief=brief or None, chat_id=None)
    except Exception: tac = ""
    who = (name or "misafir").strip()[:40] or "misafir"
    if (lang or "tr") == "tr":
        tone = score.get("tone")
        rule = "VIP KAPANIS: tek net CTA, kayip cercevesi, sozlesme+odeme zincirine kapat." if tone == "vip_close" else ("EGITICI TON: once ogret, kanit ver, tek kapsam sorusuyla bitir; odeme acma." if tone == "educate" else "DANISMAN TON: kisa teshis + kanit + tek sonraki adim.")
        return (f"Sen DevSolve Teknik Ekip satis muhendisisin. Musteri: {who}. Dil: Turkce. {rule}\nKESIN: ucretsiz deneme/indirim ASLA; uydurma metrik yok; tek CTA; kendini bot/yapay zeka diye tanitma; STOPa saygi.\n[DIS KAYNAK/RAG]\n{rag}\n{tac}\n[FORM]\n{(brief or '-')[:800]}")
    rule_en = "VIP CLOSE: one CTA, loss framing, close to contract+payment." if score.get("tone") == "vip_close" else ("EDUCATOR: teach first, evidence, one scoping question." if score.get("tone") == "educate" else "CONSULTANT: diagnosis + evidence + one next step.")
    return (f"You are DevSolve senior sales engineer. Customer: {who}. English. {rule_en}\nHARD: never free trial/discount; no invented metrics; single CTA; honor STOP.\n[RAG]\n{rag}\n{tac}\n[FORM]\n{(brief or '-')[:800]}")
def audit_reply(text, *, lang="tr"):
    try:
        from nirvana import language_auditor as la  # type: ignore
        fixed, _ = la.audit(text or "", turkish=(lang == "tr"), limit=1200)
        return fixed
    except Exception:
        out = re.sub(r"https?://\S+", "", text or "").strip()
        return (re.sub(r"[ \t]{2,}", " ", out)[:1200] if out else ("Anladim, detaylandirayim." if lang == "tr" else "Understood."))
def fallback_reply(*, lang="tr", tone="consult", name=""):
    who = (name or "").strip().split()[0][:24] if (name or "").strip() else ""
    pre = (who + " " if who else "")
    if lang == "tr":
        if tone == "vip_close": return f"{pre}netlestireyim: kopuklugu tek teslimat + izleme ile kapatiyoruz. Uygunsa 'baslayalim' yazin, sozlesme+odeme adimini acayim."
        if tone == "educate": return f"{pre}sorun genelde kaynak-webhook/API-hedef zincirinde kopuyor; once olcum, sonra tek akista onarim. Hangi platformdasiniz?"
        return f"{pre}anladim: hangi platform ve odeme adimi? Tek cumle yazin, olcum planini cikaralim."
    if tone == "vip_close": return f"{pre}to be concrete: one deliverable + monitoring. Write 'let us start' and I open contract+payment."
    if tone == "educate": return "Break is usually source-webhook/API-destination; we measure first. Which platform?"
    return "Understood - which platform and payment step? One line please."
def drip_text(step, *, lang="tr", name=""):
    who = (name or "").strip().split()[0][:24]; hi = (who + ", " if who else "")
    if lang == "tr":
        return {"drip_15m": f"{hi}taslak burada - tek satir yazin, olcum planini cikaralim.", "drip_2h": f"{hi}kisa hatirlatma: kopukluk her hafta buyur; kapsami bugun netlestirelim mi?", "drip_24h": f"{hi}dunku bulgu gecerli - pilot slot icin bugun bir satir yeterli; degilse STOP."}.get(step, f"{hi}buradayim.")
    return {"drip_15m": f"{hi}still here - one line and I outline the plan.", "drip_2h": f"{hi}nudge: gap compounds weekly; scope today?", "drip_24h": f"{hi}finding stands - one line for pilot slot; STOP if not."}.get(step, f"{hi}here.")
# __PART3__ store
import threading as _th
_store_lock = _th.Lock(); _sessions: dict[str, dict[str, Any]] = {}; _loaded = False
def _load_disk():
    try:
        if SESSIONS_PATH.exists():
            d = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict): return d
    except Exception: logger.warning("webchat sessions unreadable - RAM ile devam")
    return {}
def _save_disk(data):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SESSIONS_PATH.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(SESSIONS_PATH)
    except Exception: logger.warning("webchat sessions yazilamadi", exc_info=True)
def _ensure_loaded():
    global _loaded
    if _loaded: return
    with _store_lock:
        if _loaded: return
        for sid, row in _load_disk().items():
            if isinstance(row, dict): _sessions[str(sid)] = row
        _loaded = True
def get_session(sid):
    _ensure_loaded()
    with _store_lock:
        r = _sessions.get(str(sid))
        return json.loads(json.dumps(r, ensure_ascii=False)) if isinstance(r, dict) else None
def put_session(sid, **fields):
    _ensure_loaded()
    with _store_lock:
        row = dict(_sessions.get(str(sid)) or {}); row.update(fields); row["sid"] = str(sid)
        row.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        _sessions[str(sid)] = row; _save_disk(_sessions)
        return json.loads(json.dumps(row, ensure_ascii=False))
def worker_for(sid):
    return f"w{(int(hashlib.md5(str(sid).encode()).hexdigest(), 16) % N_WORKERS) + 1}"
def create_session(*, name="", lang="tr", brief="", form=None, worker=""):
    sid = uuid.uuid4().hex[:16]
    return put_session(sid, name=(name or "")[:48], lang=(lang or "tr")[:8] or "tr", brief=(brief or "")[:2000], form=form or {}, worker=worker or worker_for(sid), history=[], inbox=[], drips_sent=[], score=20, tone="educate", last_at=time.time(), vip_notified=False, payment_notified=False)
def append_history(sid, role, content):
    _ensure_loaded()
    with _store_lock:
        row = dict(_sessions.get(str(sid)) or {}); h = list(row.get("history") or [])
        h.append({"role": role, "content": (content or "")[:1200]}); row["history"] = h[-MAX_HISTORY:]; row["last_at"] = time.time()
        _sessions[str(sid)] = row; _save_disk(_sessions)
def push_inbox(sid, text, *, kind="drip"):
    _ensure_loaded()
    with _store_lock:
        row = dict(_sessions.get(str(sid)) or {}); ib = list(row.get("inbox") or [])
        ib.append({"ts": time.time(), "kind": kind, "text": (text or "")[:1200]}); row["inbox"] = ib[-20:]
        _sessions[str(sid)] = row; _save_disk(_sessions)
def pop_inbox(sid):
    _ensure_loaded()
    with _store_lock:
        row = dict(_sessions.get(str(sid)) or {}); items = list(row.get("inbox") or []); row["inbox"] = []
        _sessions[str(sid)] = row; _save_disk(_sessions); return items
def mark_drip(sid, step):
    _ensure_loaded()
    with _store_lock:
        row = dict(_sessions.get(str(sid)) or {}); s = list(row.get("drips_sent") or [])
        if step not in s: s.append(step)
        row["drips_sent"] = s; _sessions[str(sid)] = row; _save_disk(_sessions)
def all_sessions():
    _ensure_loaded()
    with _store_lock: return [dict(v) for v in _sessions.values()]
# __PART4__ admin+brain+voice
def _admin_chat_id():
    try:
        raw = os.getenv("TELEGRAM_OWNER_CHAT_ID", "") or os.getenv("TELEGRAM_ADMIN_ID", "") if config is None else (str(getattr(config, "TELEGRAM_OWNER_CHAT_ID", "") or "") or str(getattr(config, "TELEGRAM_ADMIN_ID", "") or ""))
        raw = str(raw).strip()
        return int(raw) if raw.lstrip("-").isdigit() else None
    except Exception: return None
def notify_admin(text, *, high_priority=False):
    target = _admin_chat_id()
    if not target: return False
    body = text if text.startswith("[DevSolve") else f"[DevSolve Ops]\n{text}"
    try:
        import flood_guard  # type: ignore
        if not flood_guard.sync_acquire(int(target)): raise RuntimeError("flood")
    except Exception as exc:
        try:
            import task_queue  # type: ignore
            task_queue.enqueue("telegram_notify", {"chat_id": int(target), "text": body, "high_priority": bool(high_priority), "critical": True}, max_attempts=8, delay_s=60)
        except Exception: pass
        logger.warning("admin notify flood - kuyruk: %s", exc); return False
    try:
        import owner_notify  # type: ignore
        if owner_notify.send(body, chat_id=int(target), high_priority=high_priority): return True
        raise RuntimeError("send False")
    except Exception as exc:
        try:
            import task_queue  # type: ignore
            task_queue.enqueue("telegram_notify", {"chat_id": int(target), "text": body, "high_priority": bool(high_priority), "critical": True}, max_attempts=8, delay_s=60)
        except Exception: pass
        logger.warning("admin notify fail - kuyruk: %s", exc); return False
_ollama_sem = None
def _sem():
    global _ollama_sem
    if _ollama_sem is None: _ollama_sem = asyncio.Semaphore(1)
    return _ollama_sem
async def _brain_reply(session, user_text):
    sid = str(session.get("sid") or ""); name = str(session.get("name") or "")
    lang = str(session.get("lang") or detect_lang(user_text or ""))
    if not session.get("lang"):
        try: put_session(sid, lang=lang)
        except Exception: pass
    history = list(session.get("history") or []) + [{"role": "user", "content": (user_text or "")[:1200]}]
    history = history[-MAX_HISTORY:]
    scored = score_lead(user_text or "")
    system = build_system_prompt(name=name, lang=lang, score=scored, brief=str(session.get("brief") or ""), history=history)
    msgs = [{"role": "system", "content": system}] + history
    reply = ""
    try:
        import ollama_client  # type: ignore
        async with _sem():
            reply = await asyncio.wait_for(asyncio.to_thread(ollama_client.chat, msgs, temperature=0.6, max_tokens=280), timeout=120.0)
            reply = str(reply or "").strip()
    except Exception as exc: logger.warning("ollama down (sid=%s): %s - yedek", sid, exc); reply = ""
    if not reply: reply = fallback_reply(lang=lang, tone=str(scored.get("tone") or "educate"), name=name)
    return audit_reply(reply, lang=lang), scored
def _voice_path(sid): return VOICE_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '', str(sid))[:32]}.mp3"
async def ensure_voice(sid, text, *, lang="tr"):
    snippet = (text or "").strip()
    if not snippet: return None
    try: import edge_tts  # type: ignore
    except Exception: return None
    voice = "tr-TR-EmelNeural" if (lang or "tr") == "tr" else "en-US-AriaNeural"
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        files = sorted(VOICE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        while len(files) >= VOICE_MAX_FILES and files:
            try: files.pop(0).unlink()
            except OSError: break
    except Exception: pass
    path = _voice_path(sid)
    def _synth():
        async def _run():
            import edge_tts as _e  # type: ignore
            await _e.Communicate(snippet[:600], voice).save(str(path))
        try:
            asyncio.run(_run()); return path.exists() and path.stat().st_size > 0
        except RuntimeError:
            import concurrent.futures as cf
            with cf.ThreadPoolExecutor(max_workers=1) as ex:
                return bool(ex.submit(lambda: __import__("asyncio").run(_run())).result(timeout=VOICE_TIMEOUT_S))
        except Exception: return False
    try:
        ok = await asyncio.wait_for(asyncio.to_thread(_synth), timeout=VOICE_TIMEOUT_S + 5)
        if ok: return f"/voice/{path.name}"
    except Exception as exc: logger.warning("voice fail (sid=%s): %s", sid, exc)
    return None
# TEK DOGRULUK KAYNAGI (single source of truth): saf yardimcilar webchat_core'da
# yasar. Yukaridaki gecmis kopyalar geriye donuk uyumluluk icin durur; asagidaki
# import onlari GOLGELER — testler (tests/test_webchat.py) core'u dogruladigi icin
# test edilen davranis = canli davranis. Yeni kural buraya degil core'a yazilir.
from webchat_core import (  # noqa: E402
    DRIP_STEPS as _CORE_DRIP_STEPS,
    DRIP_TOL as DRIP_TOLERANCE,
    MAX_HISTORY as _CORE_MAX_HISTORY,
    N_WORKERS as _CORE_N_WORKERS,
    STATE_DIR as _CORE_STATE_DIR,
    SESSIONS_PATH as _CORE_SESSIONS_PATH,
    TEMPLATES_DIR as _CORE_TEMPLATES_DIR,
    VOICE_DIR as _CORE_VOICE_DIR,
    VOICE_MAX_FILES as _CORE_VOICE_MAX_FILES,
    VOICE_TIMEOUT_S as _CORE_VOICE_TIMEOUT_S,
    WEBCHAT_BIND as _CORE_WEBCHAT_BIND,
    WEBCHAT_PORT as _CORE_WEBCHAT_PORT,
    WEBCHAT_PUBLIC_URL as _CORE_WEBCHAT_PUBLIC_URL,
    _voice_path as _core_voice_path,
    all_sessions as _core_all_sessions,
    append_history as _core_append_history,
    audit_reply as _core_audit_reply,
    build_prompt as _core_build_prompt,
    create_session as _core_create_session,
    detect_lang as _core_detect_lang,
    drip_text as _core_drip_text,
    ensure_session as _core_ensure_session,
    fallback_reply as _core_fallback_reply,
    get_session as _core_get_session,
    greeting as _core_greeting,
    mark_drip as _core_mark_drip,
    notify_admin as _core_notify_admin,
    pop_inbox as _core_pop_inbox,
    push_inbox as _core_push_inbox,
    put_session as _core_put_session,
    score_lead as _core_score_lead,
    seed_from_handoff as _core_seed_from_handoff,
    wants_to_buy as _core_wants_to_buy,
    webchat_url as _core_webchat_url,
    worker_for as _core_worker_for,
    worker_index as _core_worker_index,
)

# Golgeleme (canli yol core'dan akar):
DRIP_STEPS = _CORE_DRIP_STEPS
MAX_HISTORY = _CORE_MAX_HISTORY
N_WORKERS = _CORE_N_WORKERS
STATE_DIR = _CORE_STATE_DIR
SESSIONS_PATH = _CORE_SESSIONS_PATH
TEMPLATES_DIR = _CORE_TEMPLATES_DIR
VOICE_DIR = _CORE_VOICE_DIR
VOICE_MAX_FILES = _CORE_VOICE_MAX_FILES
VOICE_TIMEOUT_S = _CORE_VOICE_TIMEOUT_S
WEBCHAT_BIND = _CORE_WEBCHAT_BIND
WEBCHAT_PORT = _CORE_WEBCHAT_PORT
WEBCHAT_PUBLIC_URL = _CORE_WEBCHAT_PUBLIC_URL
_voice_path = _core_voice_path
all_sessions = _core_all_sessions
append_history = _core_append_history
audit_reply = _core_audit_reply
build_system_prompt = _core_build_prompt
create_session = _core_create_session
detect_lang = _core_detect_lang
drip_text = _core_drip_text
ensure_session = _core_ensure_session
fallback_reply = _core_fallback_reply
get_session = _core_get_session
greeting = _core_greeting
mark_drip = _core_mark_drip
notify_admin = _core_notify_admin
pop_inbox = _core_pop_inbox
push_inbox = _core_push_inbox
put_session = _core_put_session
score_lead = _core_score_lead
seed_from_handoff = _core_seed_from_handoff
wants_to_buy = _core_wants_to_buy
webchat_url = _core_webchat_url
worker_for = _core_worker_for
worker_index = _core_worker_index

# __PART5A__ routes
app = None; _conns: dict[str, Any] = {}; _queues: list = []; _tasks: list = []
def _lazy_app():
    global app
    if app is not None: return app
    from fastapi import FastAPI, WebSocket  # type: ignore
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # type: ignore
    f = FastAPI(title="Nirvana Web Live Chat Engine", version="1.0.0")
    @f.get("/health")
    async def health():
        try:
            import flood_guard as _fg  # type: ignore
            fl = _fg.status()
        except Exception: fl = {}
        return JSONResponse({"ok": True, "engine": "webchat", "workers": N_WORKERS, "sessions": len(all_sessions()), "online": len(_conns), "public_url": WEBCHAT_PUBLIC_URL, "flood": fl, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    @f.get("/chat")
    async def chat_page(sid: str = "", name: str = ""):
        p = TEMPLATES_DIR / "chat.html"
        html = p.read_text(encoding="utf-8") if p.exists() else "<html><body><h1>DevSolve Live Chat</h1></body></html>"
        return HTMLResponse(html)
    @f.post("/api/session")
    async def api_session(payload: dict[str, Any]):
        data = payload or {}
        name = str(data.get("name") or "")[:48]; lang = str(data.get("lang") or "")[:8]
        brief = str(data.get("brief") or "")[:2000]
        form = data.get("form") if isinstance(data.get("form"), dict) else {}
        # Form linkindeki token (dsXXXXXXXX) veya hazir sid: oturum AYNI id ile acilir
        # ve handoff kaydindan (sirket/teshis/dil) tohumlanir.
        wanted = str(data.get("sid") or data.get("token") or "")[:64]
        row = ensure_session(wanted, name=name, lang=lang, brief=brief, form=form) if wanted \
            else create_session(name=name, lang=lang or "tr", brief=brief, form=form)
        sid = str(row.get("sid"))
        base = WEBCHAT_PUBLIC_URL or f"http://127.0.0.1:{WEBCHAT_PORT}"
        seeded = bool(row.get("brief"))
        greet = row.get("greeting") or greeting(name=str(row.get("name") or ""),
                                                lang=str(row.get("lang") or "tr"),
                                                seeded=seeded)
        if not row.get("greeting"):
            append_history(sid, "assistant", greet)
            put_session(sid, greeting=greet)
        try: asyncio.get_event_loop().create_task(_fire_voice(sid, greet, str(row.get("lang") or "tr")))
        except Exception: pass
        return JSONResponse({"ok": True, "sid": sid, "url": f"{base}/chat?sid={sid}",
                             "worker": row.get("worker"), "greeting": greet,
                             "seeded": seeded})
    @f.get("/voice/{fname}")
    async def voice_file(fname: str):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "", fname)[:64]; path = VOICE_DIR / safe
        if not path.exists() or path.suffix.lower() != ".mp3": return JSONResponse({"ok": False}, status_code=404)
        return FileResponse(str(path), media_type="audio/mpeg")
    @f.get("/api/admin/status")
    async def admin_status(code: str = ""):
        expect = str(getattr(config, "ADMIN_CODE", "") or "") if config else os.getenv("ADMIN_CODE", "")
        if not expect or code != expect: return JSONResponse({"ok": False}, status_code=401)
        rows = all_sessions()
        return JSONResponse({"ok": True, "sessions": len(rows), "online": len(_conns), "vips": len([r for r in rows if int(r.get("score") or 0) >= 70])})
    app = f; return f
# __PART5B__ ws
def _reg_ws(f):
    from fastapi.responses import JSONResponse as _J  # type: ignore
    @f.websocket("/ws/{sid}")
    async def ws_chat(ws, sid: str):  # type: ignore
        await ws.accept(); _conns[str(sid)] = ws
        try:
            # Form linkiyle gelen musteri (dsXXXXXXXX token) icin oturum BURADA acilir:
            # handoff kaydindan sirket/teshis/dil tohumlanir, karsilama gonderilir.
            row = get_session(sid)
            if row is None:
                row = ensure_session(sid)
                greet = greeting(name=str(row.get("name") or ""),
                                 lang=str(row.get("lang") or "tr"),
                                 seeded=bool(row.get("brief")))
                append_history(sid, "assistant", greet)
                put_session(sid, greeting=greet)
                await ws.send_json({"type": "agent", "text": greet, "kind": "greeting"})
                try: asyncio.get_event_loop().create_task(_fire_voice(sid, greet, str(row.get("lang") or "tr")))
                except Exception: pass
            for it in pop_inbox(sid): await ws.send_json({"type": "agent", "text": it.get("text"), "kind": it.get("kind")})
            vp = _voice_path(sid)
            await ws.send_json({"type": "ready", "worker": worker_for(sid), "voice_url": f"/voice/{vp.name}" if vp.exists() else None})
            try:
                from fastapi import WebSocketDisconnect as _D  # type: ignore
            except Exception: _D = Exception  # type: ignore
            while True:
                try: data = await ws.receive_json()
                except _D: break
                text = str((data or {}).get("text") or "")[:2000]
                if not text.strip(): continue
                if text.strip() == "/status":
                    await ws.send_json({"type": "agent", "kind": "status", "text": f"Oturumlar: {len(all_sessions())} | cevrimici: {len(_conns)}"}); continue
                append_history(sid, "user", text)
                fut = asyncio.get_event_loop().create_future()
                idx = int(hashlib.md5(str(sid).encode()).hexdigest(), 16) % max(1, N_WORKERS)
                await _queues[idx].put({"sid": sid, "text": text, "fut": fut})
                try: res = await asyncio.wait_for(fut, timeout=150.0)
                except asyncio.TimeoutError:
                    s0 = get_session(sid) or {}
                    res = {"reply": fallback_reply(lang=str(s0.get("lang") or "tr"), tone="consult", name=str(s0.get("name") or "")), "score": {"score": 20, "tone": "consult"}, "voice_url": None}
                await ws.send_json({"type": "agent", "text": res.get("reply"), "score": (res.get("score") or {}).get("score"), "tone": (res.get("score") or {}).get("tone"), "voice_url": res.get("voice_url")})
        except Exception as exc: logger.warning("ws kapandi %s: %s", sid, exc)
        finally: _conns.pop(str(sid), None)
    return f
_orig_lazy = _lazy_app
def _lazy_app():  # type: ignore
    f = _orig_lazy(); return _reg_ws(f)
async def _fire_voice(sid, text, lang):
    try:
        url = await ensure_voice(sid, text, lang=lang)
        if url:
            ws = _conns.get(str(sid))
            if ws is not None:
                try: await ws.send_json({"type": "voice", "voice_url": url}); return
                except Exception: pass
            push_inbox(sid, url, kind="voice")
    except Exception: pass
# __PART5C__ loops
async def _worker_loop(idx, queue):
    while True:
        job = await queue.get()
        try:
            sid = str(job.get("sid") or ""); text = str(job.get("text") or ""); fut = job.get("fut")
            sess = get_session(sid)
            if sess is None:
                if fut is not None and not fut.done(): fut.set_result({"reply": "Oturum yok.", "score": {"score": 0}, "voice_url": None})
                continue
            reply, scored = await _brain_reply(sess, text)
            append_history(sid, "assistant", reply)
            put_session(sid, score=int(scored.get("score") or 0), tone=str(scored.get("tone") or ""))
            try:
                s2 = get_session(sid) or {}; sc = int(scored.get("score") or 0); who = str(s2.get("name") or sid)[:40]
                if (sc >= 80 or wants_to_buy(text)) and not s2.get("vip_notified"):
                    notify_admin(f"VIP LEAD (webchat) {who} skor={sc} son={(text or '')[:200]} sid={sid}", high_priority=True)
                    put_session(sid, vip_notified=True)
                if wants_to_buy(text) and not s2.get("payment_notified"):
                    link = ""
                    try:
                        from nirvana import payment as pm  # type: ignore
                        link = pm.payment_link()
                    except Exception: link = ""
                    if link: reply = f"{reply}\n\nOdeme talebi: {link}"
                    notify_admin(f"ODEME ISTEGI (webchat) {who} sid={sid}", high_priority=True)
                    put_session(sid, payment_notified=True)
            except Exception: logger.warning("admin notify skip", exc_info=True)
            url = await ensure_voice(sid, reply, lang=str((get_session(sid) or {}).get("lang") or "tr"))
            if fut is not None and not fut.done(): fut.set_result({"reply": reply, "score": scored, "voice_url": url})
        except Exception as exc:
            logger.warning("worker err: %s", exc, exc_info=True)
            try:
                ff = job.get("fut")
                if ff is not None and not ff.done(): ff.set_result({"reply": fallback_reply(lang="tr", tone="consult", name=""), "score": {"score": 20}, "voice_url": None})
            except Exception: pass
        finally: queue.task_done()
async def _drip_loop():
    while True:
        try:
            now = time.time()
            for row in all_sessions():
                sid = str(row.get("sid") or "")
                if not sid: continue
                last = float(row.get("last_at") or 0)
                if last <= 0: continue
                age = now - last; sent = set(row.get("drips_sent") or [])
                lang = str(row.get("lang") or "tr"); name = str(row.get("name") or "")
                for step, at in DRIP_STEPS:
                    if step in sent: continue
                    if age >= at and age <= at + max(DRIP_TOLERANCE.get(step, 300.0), 600.0):
                        if step == "drip_24h" and int(row.get("score") or 0) >= 80: mark_drip(sid, step); continue
                        t = drip_text(step, lang=lang, name=name)
                        append_history(sid, "assistant", t); mark_drip(sid, step)
                        ws = _conns.get(sid)
                        if ws is not None:
                            try: await ws.send_json({"type": "agent", "kind": step, "text": t})
                            except Exception: push_inbox(sid, t, kind=step)
                        else: push_inbox(sid, t, kind=step)
            try:
                import task_queue as _tq  # type: ignore
                import owner_notify as _on  # type: ignore
                _tq.run_due("telegram_notify", _on.deliver_queued_notify, limit=5, lease_s=300.0, retry_in_s=60.0)
            except Exception: pass
        except Exception as exc: logger.warning("drip err: %s", exc)
        await asyncio.sleep(60.0)
async def _hb_loop():
    try:
        import heartbeat as _hb  # type: ignore
        _hb.ready()
    except Exception: pass
    while True:
        try:
            import heartbeat as _hb  # type: ignore
            _hb.pulse("webchat", {"online": len(_conns), "sessions": len(all_sessions())})
        except Exception: pass
        await asyncio.sleep(15.0)
def _ensure_runtime():
    global _queues
    try: loop = asyncio.get_event_loop()
    except RuntimeError: loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    if not _queues:
        _queues = [asyncio.Queue(maxsize=200) for _ in range(N_WORKERS)]
        for i, q in enumerate(_queues): _tasks.append(loop.create_task(_worker_loop(i, q)))
        _tasks.append(loop.create_task(_drip_loop())); _tasks.append(loop.create_task(_hb_loop()))
try:
    from fastapi import FastAPI as _FA  # type: ignore
    _lazy_app()
    @app.on_event("startup")  # type: ignore
    async def _on_startup():
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        _ensure_runtime()
except Exception: pass
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _lazy_app(); import uvicorn  # type: ignore
    uvicorn.run(app, host=WEBCHAT_BIND, port=WEBCHAT_PORT, workers=1, log_level="info")
    return 0
if __name__ == "__main__": raise SystemExit(main())






