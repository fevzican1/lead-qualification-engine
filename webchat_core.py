"""Nirvana Web Live Chat Engine core — saf yardimcilar (fastapi SART DEGIL).

Telegram musteriye KAPALI: trafik WEBCHAT_PUBLIC_URL uzerinden akar.
Telegram SADECE pasif admin hattidir (VIP/odeme/status + flood_gate).
"""
from __future__ import annotations
import hashlib, json, logging, os, re, time, uuid
from pathlib import Path
from typing import Any
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
DRIP_TOL = {"drip_15m": 750.0, "drip_2h": 1500.0, "drip_24h": 3600.0}
_BUDGET_RE = re.compile(r"(\u20ac|\$|\u00a3|eur[oa]?|usd|dolar|tl|b\u00fct\u00e7e|butce|budget|fiyat|\u00fccret|ucret|\b\d[\d.\s]{2,}\b)", re.I)
_HIGH_RE = re.compile(r"(sat\u0131n al|satin al|ba\u015flayal\u0131m|baslayal|kabul|onayl|\u00f6deme|odeme|anla\u015ft\u0131k|anlastik|fatura|teklif|demo|toplant\u0131|toplanti|\bbuy\b|\bpay\b|ready to (buy|start|pay)|let'?s (start|proceed)|go ahead|send (the )?(payment|invoice|link)|approve|hire you)", re.I)
_VIP_RE = re.compile(r"(\u20ac|\$|\u00a3|\b\d\s?000\b|\b[5-9]\d{3}\b)", re.I)
# DIKKAT: bu karakter sinifi re.IGNORECASE ile DERLENMEZ. Unicode case-folding
# yuzunden 'i' harfi 'İ' sinifina eslesiyor ve Ingilizce metin "tr" oluyordu
# (canli bug: "Hello, what is the price?" -> tr).
_TR_RE = re.compile(r"[\u00e7\u011f\u0131\u00f6\u015f\u00fc\u00c7\u011e\u0130\u00d6\u015e\u00dc]")
# Diakritiksiz yazilmis Turkce icin anahtar kelime kapisi ("Tesekkurler" gibi).
_TR_WORD_RE = re.compile(
    r"\b(merhaba|selam|fiyat|nedir|nasil|nas\u0131l|tesekkur|te\u015fekk\u00fcr|"
    r"ucret|\u00fccret|odeme|\u00f6deme|satin|sat\u0131n|sozlesme|s\u00f6zle\u015fme|"
    r"lutfen|l\u00fctfen|kabul|basla|ba\u015fla|evet|hayir|hay\u0131r)\b",
    re.I,
)
_BUY_RE = re.compile(r"sat\u0131n\s*al|ba\u015flayabilir|baslayabilir|haydi\s+ba\u015fla|hadi\s+ba\u015fla|anla\u015ft\u0131k|anlastik|\u00f6deme\s*link|odeme\s*link|kabul\s+ediyoruz|onayl\u0131yor|how\s+(do\s+i|can\s+i|to)\s+(buy|pay|start)|ready\s+to\s+(buy|start|pay)|i\s+want\s+to\s+(buy|start)|proceed\s+to\s+pay|send\s+(the\s+)?(invoice|payment|link)|let'?s\s+(proceed|start)|go\s+ahead|hire\s+you\b", re.I)
_NEG_RE = re.compile(r"\b(not|no|never|don't|do not|can't|cannot|won't|if|whether)\b|hay\u0131r|hayir|istemiyorum|de\u011fil|degil|\?", re.I)
def wants_to_buy(t: str) -> bool:
    c = (t or "").strip()
    return bool(_BUY_RE.search(c) and not _NEG_RE.search(c))
def detect_lang(text: str) -> str:
    try:
        from nirvana import language_auditor as la  # type: ignore
        code = la.detect_locale(f"<html><body>{text}</body></html>", fallback="")
        if code in ("tr","en","de","fr","es","it","pt","nl","ar","ru"):
            return code
    except Exception:
        pass
    b = f" {text or ''} "
    if _TR_RE.search(b) or _TR_WORD_RE.search(b):
        return "tr"
    if re.search(r"\b(der|die|das|und|kontakt)\b", b, re.I):
        return "de"
    if re.search(r"\b(bonjour|merci|contactez)\b", b, re.I):
        return "fr"
    if re.search(r"\b(hola|gracias|contacto)\b", b, re.I):
        return "es"
    return "en"
def score_lead(text):
    t = text or ""; s = 20; sig = []
    if _HIGH_RE.search(t):
        s += 40; sig.append("intent:high")
    elif re.search(r"(fiyat|price|how much|ne kadar|bilgi|info|nas\u0131l|nasil|how)", t, re.I):
        s += 15; sig.append("intent:mid")
    else:
        sig.append("intent:low")
    if _VIP_RE.search(t):
        s += 25; sig.append("budget:vip")
    elif _BUDGET_RE.search(t):
        s += 10; sig.append("budget:mentioned")
    if wants_to_buy(t):
        s += 10; sig.append("buy:explicit")
    s = max(0, min(100, s))
    tone = "vip_close" if s >= 70 else ("educate" if s < 40 else "consult")
    return {"score": s, "tone": tone, "signals": sig}
def _rag_block():
    try:
        import knowledge  # type: ignore
        return knowledge.assistant_context(limit=10)
    except Exception:
        return "Hizmet: e-ticaret/CRM/ERP entegrasyonu, odeme webhook onarimi."
def _tactic_block(user_text="", brief=""):
    try:
        from nirvana import conversion_maximizer as cm  # type: ignore
        return cm.close_block(user_text=user_text or "", brief=brief or None, chat_id=None)
    except Exception:
        return ""
def build_prompt(*, name="", lang="tr", score=None, brief="", history=None):
    score = score or {"tone": "educate"}
    rag = _rag_block(); last = ""
    for m in reversed(history or []):
        if (m or {}).get("role") == "user":
            last = str(m.get("content") or ""); break
    tac = _tactic_block(last, brief)
    who = (name or "misafir").strip()[:40] or "misafir"
    ctx = (brief or "-")[:800]
    if (lang or "tr") == "tr":
        tone = score.get("tone")
        rule = ("VIP KAPANIS: tek net CTA, kayip cercevesi, sozlesme+odeme zincirine kapat."
            if tone == "vip_close" else
            "EGITICI TON: once sorunu ogret, kanit ver, tek kapsam sorusuyla bitir; odeme acma."
            if tone == "educate" else
            "DANISMAN TON: kisa teshis + kanit + tek sonraki adim sorusu.")
        return (f"Sen DevSolve Teknik Ekip kidemli satis muhendisisin. Musteri: {who}. "
            f"Dil: Turkce (mukemmel dil bilgisi). {rule}\n"
            f"KURALLAR: ucretsiz deneme/indirim ASLA; uydurma metrik yok; tek CTA; "
            f"kendini bot/yapay zeka diye tanitma (sorulmadikca); STOP'a saygi.\n"
            f"[DIS KAYNAK/RAG]\n{rag}\n{tac}\n[FORM BAGLAMI]\n{ctx}")
    rule_en = ("VIP CLOSE: one clear CTA, loss framing, close to contract+payment."
        if score.get("tone") == "vip_close" else
        "EDUCATOR TONE: teach first, evidence, one scoping question; never open payment."
        if score.get("tone") == "educate" else
        "CONSULTANT TONE: short diagnosis + evidence + one next step.")
    return (f"You are DevSolve senior sales engineer. Customer: {who}. {rule_en}\n"
        f"RULES: never free trial/discount; no invented metrics; single CTA; honor STOP.\n"
        f"[RAG]\n{rag}\n{tac}\n[CONTEXT]\n{ctx}")
def audit_reply(text, *, lang="tr"):
    try:
        from nirvana import language_auditor as la  # type: ignore
        fixed, _ = la.audit(text or "", turkish=(lang == "tr"), limit=1200)
        return fixed
    except Exception:
        out = re.sub(r"https?://\S+", "", text or "").strip()
        out = re.sub(r"[ \t]{2,}", " ", out)
        d = "Anladim, detaylandirayim." if lang == "tr" else "Understood."
        return (out[:1200] or d)
def fallback_reply(*, lang="tr", tone="consult", name=""):
    who = (name or "").strip().split()[0][:24] if (name or "").strip() else ""
    pre = (who + " " if who else "")
    if lang == "tr":
        if tone == "vip_close":
            return (f"{pre}netlestireyim: akistaki kopuklugu tek teslimat + izleme "
                f"kapsamiyla kapatiyoruz. Uygunsa 'baslayalim' yazin, sozlesme + odeme adimini acayim.")
        if tone == "educate":
            return (f"{pre}sorun genelde kaynak -> webhook/API -> hedef zincirinde kopuyor; "
                f"once olcum, sonra tek akista onarim yapiyoruz. Hangi platformu kullaniyorsunuz?")
        return (f"{pre}anladim — hangi platform ve odeme adimi? Tek cumleyle yazin, olcum planini cikaralim.")
    if tone == "vip_close":
        return f"{pre}to be concrete: one deliverable + monitoring. Write 'let\\'s start' to open contract + payment."
    if tone == "educate":
        return "The break is usually source -> webhook/API -> destination; we measure first. Which platform?"
    return "Understood — which platform and payment step? One line and I outline the plan."
def drip_text(step, *, lang="tr", name=""):
    who = (name or "").strip().split()[0][:24]; hi = (who + ", " if who else "")
    if lang == "tr":
        return {"drip_15m": f"{hi}taslak burada — tek satir yazmaniz yeterli, olcum planini cikaralim.",
            "drip_2h": f"{hi}kisa hatirlatma: kopukluk her hafta buyur; kapsami bugun netlestirelim mi?",
            "drip_24h": f"{hi}dunku bulgu gecerli — pilot slot icin bugun bir satir yeterli; degilse STOP.",
            }.get(step, f"{hi}buradayim — tek satirla devam edelim.")
    return {"drip_15m": f"{hi}still here — one line and I outline the plan.",
        "drip_2h": f"{hi}quick nudge: the gap compounds weekly; scope it today?",
        "drip_24h": f"{hi}yesterday's finding stands — one line today; STOP if not.",
        }.get(step, f"{hi}still here.")
def worker_for(sid):
    h = int(hashlib.md5(str(sid).encode()).hexdigest(), 16)
    return f"w{(h % N_WORKERS) + 1}"
def worker_index(sid):
    h = int(hashlib.md5(str(sid).encode()).hexdigest(), 16)
    return h % max(1, N_WORKERS)
def webchat_url(sid=""):
    base = WEBCHAT_PUBLIC_URL or f"http://127.0.0.1:{WEBCHAT_PORT}"
    return f"{base}/chat?sid={sid}" if sid else f"{base}/chat"
import threading as _th
_store_lock = _th.Lock(); _sessions = {}; _loaded = False
def _load_disk():
    try:
        if SESSIONS_PATH.exists():
            d = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:
        logger.warning("webchat sessions unreadable — RAM ile devam")
    return {}
def _save_disk(data):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SESSIONS_PATH.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(SESSIONS_PATH)
    except Exception:
        logger.warning("webchat sessions yazilamadi", exc_info=True)
def _ensure_loaded():
    global _loaded
    if _loaded:
        return
    with _store_lock:
        if _loaded:
            return
        for sid, row in _load_disk().items():
            if isinstance(row, dict):
                _sessions[str(sid)] = row
        _loaded = True
def _clone(row):
    return json.loads(json.dumps(row, ensure_ascii=False))
def get_session(sid):
    _ensure_loaded()
    with _store_lock:
        r = _sessions.get(str(sid))
        return _clone(r) if isinstance(r, dict) else None
def put_session(sid, **f):
    _ensure_loaded()
    with _store_lock:
        r = dict(_sessions.get(str(sid)) or {}); r.update(f); r["sid"] = str(sid)
        r.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        _sessions[str(sid)] = r; _save_disk(_sessions)
        return _clone(r)
def create_session(*, name="", lang="tr", brief="", form=None, worker="", sid=""):
    """Oturum ac (form token'i verilirse AYNI sid kullanilir — link kimligi sabit)."""
    clean = re.sub(r"[^A-Za-z0-9_-]", "", str(sid or ""))[:32]
    sid = clean or uuid.uuid4().hex[:16]
    return put_session(sid, name=(name or "")[:48], lang=(lang or "tr")[:8] or "tr",
        brief=(brief or "")[:2000], form=form or {}, worker=worker or worker_for(sid),
        history=[], inbox=[], drips_sent=[], score=20, tone="educate",
        last_at=time.time(), vip_notified=False, payment_notified=False)
def seed_from_handoff(token):
    """Form token'ini (dsXXXXXXXX) web sohbet oturumuna bagla: sirket + teshis + dil.

    Form dolduran musteriye giden link zaten bu token'i tasir
    (/chat?sid=dsXXXX). Boylece musteri ilk mesajini yazmadan once AI form
    verisini (sirket, tespit edilen akis, dil) bilir; karsilama kisisellesir.
    """
    tok = re.sub(r"[^A-Za-z0-9_-]", "", str(token or ""))[:64]
    if not tok:
        return {}
    try:
        import telegram_handoff  # type: ignore
        row = telegram_handoff.lookup(tok) or {}
        brief = telegram_handoff.brief_block(row) or ""
    except Exception:
        return {}
    if not row:
        return {}
    company = str(row.get("company") or row.get("host") or row.get("target_domain") or "")
    return {"name": company[:48], "brief": brief[:2000],
            "lang": "tr" if row.get("turkish") else "en", "token": tok}
def ensure_session(sid, *, name="", lang="", brief="", form=None):
    """Var olan oturumu doner; yoksa verilen sid ile ACAR (form linki → oturum).

    Form linkindeki token (dsXXXXXXXX) icin oturum, handoff kaydindan
    (sirket/teshis/dil) tohumlanir — musteri tek satir yazmadan baglam hazir olur.
    """
    existing = get_session(sid)
    if existing is not None:
        return existing
    seed = seed_from_handoff(sid) if (not name and not brief) else {}
    return create_session(
        sid=str(sid),
        name=name or seed.get("name") or "",
        lang=lang or seed.get("lang") or "tr",
        brief=brief or seed.get("brief") or "",
        form=form or ({"token": seed.get("token")} if seed.get("token") else {}),
    )
def greeting(*, name="", lang="tr", seeded=False):
    """Karsilama metni: tohumlanmis oturumda sirket adi ve teshis gecirilir."""
    first = (name or "").strip().split()[0][:24] if (name or "").strip() else ""
    if lang == "tr":
        if seeded:
            ref = f"{first} ekibi, " if first else ""
            return (f"Merhaba {ref}ben DevSolve Teknik Ekip. Formunuzdaki akis notunu "
                    "inceledim; tek soru: hangi adımda tıkanıyor (sipariş → CRM, ödeme callback)?")
        who = f"{first} " if first else ""
        return (f"Merhaba {who}— ben DevSolve Teknik Ekip. Akışınızı tek akışta kapatmak için "
                "buradayım; platformunuzu tek satırla yazın.")
    if seeded:
        ref = f"{first} team, " if first else ""
        return (f"Hello {ref}DevSolve technical team here. I read the flow note from your form — "
                "one question: where does it break (order → CRM, payment callback)?")
    who = f"{first} — " if first else ""
    return (f"Hello {who}DevSolve technical team here. Write your platform in one line "
            "and I'll outline the measurement plan.")

def append_history(sid, role, content):
    _ensure_loaded()
    with _store_lock:
        r = dict(_sessions.get(str(sid)) or {}); h = list(r.get("history") or [])
        h.append({"role": role, "content": (content or "")[:1200]})
        r["history"] = h[-MAX_HISTORY:]; r["last_at"] = time.time()
        _sessions[str(sid)] = r; _save_disk(_sessions)
def push_inbox(sid, text, *, kind="drip"):
    _ensure_loaded()
    with _store_lock:
        r = dict(_sessions.get(str(sid)) or {}); b = list(r.get("inbox") or [])
        b.append({"ts": time.time(), "kind": kind, "text": (text or "")[:1200]})
        r["inbox"] = b[-20:]; _sessions[str(sid)] = r; _save_disk(_sessions)
def pop_inbox(sid):
    _ensure_loaded()
    with _store_lock:
        r = dict(_sessions.get(str(sid)) or {}); items = list(r.get("inbox") or [])
        r["inbox"] = []; _sessions[str(sid)] = r; _save_disk(_sessions)
        return items
def mark_drip(sid, step):
    _ensure_loaded()
    with _store_lock:
        r = dict(_sessions.get(str(sid)) or {}); s = list(r.get("drips_sent") or [])
        if step not in s:
            s.append(step)
        r["drips_sent"] = s; _sessions[str(sid)] = r; _save_disk(_sessions)
def all_sessions():
    _ensure_loaded()
    with _store_lock:
        return [dict(v) for v in _sessions.values()]
def _admin_chat_id():
    try:
        if config is None:
            raw = os.getenv("TELEGRAM_OWNER_CHAT_ID", "") or os.getenv("TELEGRAM_ADMIN_ID", "")
        else:
            raw = str(getattr(config, "TELEGRAM_OWNER_CHAT_ID", "") or "") or str(getattr(config, "TELEGRAM_ADMIN_ID", "") or "")
        raw = raw.strip()
        return int(raw) if raw.lstrip("-").isdigit() else None
    except Exception:
        return None
def notify_admin(text, *, high_priority=False):
    target = _admin_chat_id()
    if not target:
        return False
    body = text if text.startswith("[DevSolve") else f"[DevSolve Ops]\n{text}"
    try:
        import flood_guard  # type: ignore
        if not flood_guard.sync_acquire(int(target)):
            raise RuntimeError("flood-gate closed")
    except Exception as exc:
        try:
            import task_queue  # type: ignore
            task_queue.enqueue("telegram_notify", {"chat_id": int(target), "text": body,
                "high_priority": bool(high_priority), "critical": True}, max_attempts=8, delay_s=60)
        except Exception:
            pass
        logger.warning("admin notify flood kapisinda — kuyruk: %s", exc)
        return False
    try:
        import owner_notify  # type: ignore
        if owner_notify.send(body, chat_id=int(target), high_priority=high_priority):
            return True
        raise RuntimeError("owner_notify.send False")
    except Exception as exc:
        try:
            import task_queue  # type: ignore
            task_queue.enqueue("telegram_notify", {"chat_id": int(target), "text": body,
                "high_priority": bool(high_priority), "critical": True}, max_attempts=8, delay_s=60)
        except Exception:
            pass
        logger.warning("admin notify kuyruk: %s", exc)
        return False
def _voice_path(sid):
    return VOICE_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '', sid)[:32]}.mp3"
