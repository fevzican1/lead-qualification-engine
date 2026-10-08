"""Low-latency local inference pipeline (target: core/llm_router.py).

YEREL-ONCELIKLI, TEK KAYNAKLI (2026-10-05):

  primary  : ollama/qwen2.5:7b   -> http://localhost:11434/v1 (Oracle yerel)
  hizli    : ollama/qwen2.5:3b   -> ultra hizli yanit (dense fallback)

Yapilandirma onceligi: config.yaml > ortam degiskeni > varsayilan.
Dis servisler (Groq / Cerebras / OpenRouter) TAMAMEN PASIFTIR:
LLM_EXTERNAL_ENABLED=0 (varsayilan) iken hicbir dis istek atilmaz; acilsa bile
dis kaynakli 404 / 500 / kota hatasi ASLA disari yansimaz (100% engel), zincir
her kosulda yerel Ollama'ya duser.

Yuksek eszamanlilik icin: num_ctx=1024 (dar baglam penceresi) ve num_thread=4
(CPU on isleme ~ms) -> ollama_client.chat() uzerinden gonderilir. Ollama servisi
keep_alive=-1 / num_parallel=4 ile RAM'de kilitlidir (deploy/oracle_setup.sh).

MUSTERIYE ASLA teknik mesaj gitmez (zero-notice): bozuk/JSON hata govdesi
suzulurse yanit SESSIZCE atlanir, akis kesintisiz devam eder.
"""
from __future__ import annotations
import asyncio, logging, os, re, time
from typing import Any
logger = logging.getLogger(__name__)
FAST_TIMEOUT_S = float(os.getenv("LLM_FAST_TIMEOUT_S", "1.5") or 1.5)
TOTAL_TIMEOUT_S = float(os.getenv("LLM_TOTAL_TIMEOUT_S", "6.0") or 6.0)
# Sunucuyu koruyan guvenli ayar: en fazla 5 eszamanli LLM cagrisi.
try:
    _cap = int(os.getenv("LLM_MAX_CONCURRENCY", "5") or 5)
except ValueError:
    _cap = 5
MAX_CONCURRENCY = max(1, min(5, _cap))
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "1024") or 1024)   # dar baglam penceresi
NUM_THREAD = int(os.getenv("OLLAMA_NUM_THREAD", "4") or 4)   # CPU is parcalari
EXTERNAL_ENABLED = os.getenv("LLM_EXTERNAL_ENABLED", "0").strip() == "1"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# --- B2B Whitelabel: system prompt izolasyonu (dinamik dosya okuma) -----------
AGENCY_PROMPT_PATH = os.getenv("AGENCY_PROMPT_PATH", "").strip() or os.path.join(_ROOT, "config", "prompts", "agency_partner.txt")
_AGENCY_PROMPT_CACHE: dict[str, Any] = {"mtime": 0.0, "text": ""}
AGENCY_MODEL = os.getenv("AGENCY_MODEL", "").strip() or "qwen2.5:3b"
def load_agency_prompt(*, refresh: bool = False) -> str:
    """config/prompts/agency_partner.txt dosyasindan oku (cache + fail-open)."""
    try:
        mtime = os.path.getmtime(AGENCY_PROMPT_PATH) if os.path.isfile(AGENCY_PROMPT_PATH) else 0.0
    except OSError:
        mtime = 0.0
    if not refresh and _AGENCY_PROMPT_CACHE.get("text") and _AGENCY_PROMPT_CACHE.get("mtime") == mtime:
        return str(_AGENCY_PROMPT_CACHE.get("text") or "")
    text = ""
    try:
        if os.path.isfile(AGENCY_PROMPT_PATH):
            with open(AGENCY_PROMPT_PATH, encoding="utf-8") as fh:
                text = str(fh.read() or "").strip()
    except Exception:
        logger.debug("agency prompt okunamadi", exc_info=True)
        text = ""
    if not text:
        text = "White-Label Altyapi Saglayicisi olarak davran. Ajanslari Whitelabel Retainer (5.000 EUR/ay) modeline dahil et. Kisa yaz, teknik hata yazma."
    _AGENCY_PROMPT_CACHE["mtime"] = mtime
    _AGENCY_PROMPT_CACHE["text"] = text
    return text
async def aload_agency_prompt() -> str:
    try:
        return await asyncio.to_thread(load_agency_prompt)
    except Exception:
        return load_agency_prompt()
def agency_system_prompt() -> str:
    return load_agency_prompt()
def default_payment_link() -> str:
    try:
        import config as _cfg  # type: ignore
        return str(getattr(_cfg, "PAYONEER_PAYMENT_URL", "") or "").strip()
    except Exception:
        return os.getenv("PAYONEER_PAYMENT_URL", "").strip()


def _load_yaml() -> dict:
    """config.yaml'daki [models] blogu (yoksa bos dict — fail-open)."""
    try:
        import yaml
        p = os.path.join(_ROOT, "config.yaml")
        if not os.path.isfile(p):
            return {}
        data = yaml.safe_load(open(p, encoding="utf-8")) or {}
        return dict(data.get("models") or {}) if isinstance(data, dict) else {}
    except Exception:
        return {}

_Y = _load_yaml()

def _pick(key: str, env: str, default: str) -> str:
    return os.getenv(env, "").strip() or str(_Y.get(key) or "").strip() or default

# Yerel Ollama tek/incil birincil kaynak (dis servisler asla birincil olamaz).
PRIMARY_MODEL = _pick("primary", "LLM_PRIMARY_MODEL", "ollama/qwen2.5:7b")
FAST_MODEL = _pick("fast", "LLM_FAST_MODEL", "ollama/qwen2.5:3b")
OLLAMA_BASE = _pick("base_url", "OLLAMA_BASE_URL", "http://localhost:11434/v1")
# Tanimsiz/yanlis model imzalari (ornek: 'stealth/space-bunny-alpha') REDDEDILIR.
_BANNED_MODEL_RE = re.compile(r"stealth|space[-_ ]?bunny|bunny[-_ ]?alpha|untitled|"
                              r"test-|experimental", re.I)
_EXTERNAL_PREFIXES = ("groq/", "cerebras/", "openrouter/", "openai/", "anthropic/",
                      "azure/", "vertexai/", "bedrock/")
# Dis API hata sinyalleri: disari YANSIMAZ, yalnizca yerel modele gecis tetikler.
_EXTERNAL_ERROR_RE = re.compile(
    r"\b(?:404|429|500|502|503|504)\b|quota|rate.?limit|billing|insufficient.?credit|"
    r"model.?not.?found|no.?such.?model|does.?not.?exist", re.I)
# Musteriye asla gosterilmeyen kesinti metni (zero-notice: kimse gormez).
DISRUPT_NOTICE = ""
JSON_BLOB_RE = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.S)
_ERROR_FIELD_RE = re.compile(r'"(?:error|detail|message|errors|code)"\s*:', re.I)

def is_cloud(model: str) -> bool:
    return str(model or "").startswith(_EXTERNAL_PREFIXES)

def is_model_not_found(exc: BaseException) -> bool:
    """Dis kaynakli 404 / 500 / kota hatasi — 100% engellenir, yuzeye cikmaz."""
    return bool(_EXTERNAL_ERROR_RE.search(str(exc)))

def local_name(model: str) -> str:
    """'ollama/qwen2.5:7b' -> 'qwen2.5:7b' (ollama_client yalnizca ad ister)."""
    m = str(model or "").strip()
    return m.split("/", 1)[1] if m.startswith("ollama/") else m

def _valid_model(model: str) -> bool:
    """Gecerli model mi? Yanlis imza ve DIS servis zincirden KIRPILIR."""
    m = str(model or "").strip()
    if not m or len(m) > 120 or _BANNED_MODEL_RE.search(m):
        return False
    if is_cloud(m):
        return False  # dis servisler pasif: zincire asla girmez
    # '/' izni: 'ollama/qwen2.5:7b' gibi yerel model imzalari da gecerli sayilir.
    return bool(re.match(r"^[A-Za-z0-9._:/-]{2,80}$", m))

def model_chain() -> list[str]:
    """Yerel Ollama zinciri (primary -> hizli); dis servis asla girmez."""
    out: list[str] = []
    for m in (PRIMARY_MODEL, FAST_MODEL):
        if m in out:
            continue
        if _valid_model(m):
            out.append(m)
        else:
            logger.warning("tanimsiz model zincirden cikarildi: %r", m)
    return out or ["ollama/qwen2.5:7b"]

def sanitize_reply(text: Any) -> tuple[str, bool]:
    """Yaniti chat-guvenli metne cevir. Dondurur: (guvenli_metin, temiz_mi).

    Temiz degilse ('', False) doner -> cagiran bu yaniti SESSIZCE atlar;
    musteriye hicbir hata/kesinti metni gosterilmez (zero-notice).
    """
    if text is None or isinstance(text, (dict, list)):
        return "", False
    s = str(text).strip()
    if not s:
        return "", False
    if "[object " in s:
        return "", False
    if JSON_BLOB_RE.match(s) or _ERROR_FIELD_RE.search(s):
        return "", False
    if s.startswith("{") and s.endswith("}"):
        return "", False
    if len(s) <= 60 and re.match(r"^(?:HTTP\s+)?(?:404|429|500|502|503|504)\b", s, re.I):
        return "", False
    return s, True

def litellm_proxy_config() -> dict:
    """Model yapilandirma ozeti (yerel Ollama = tek kaynak, dis pasif)."""
    return {"proxy": OLLAMA_BASE, "enabled": True,
            "external_enabled": EXTERNAL_ENABLED,
            "primary": PRIMARY_MODEL, "fallback": FAST_MODEL,
            "local_primary": PRIMARY_MODEL, "local_fallback": FAST_MODEL,
            "chain": model_chain(),
            "num_ctx": NUM_CTX, "num_thread": NUM_THREAD,
            "cache_ttl_s": 86400, "fast_timeout_s": FAST_TIMEOUT_S,
            "total_timeout_s": TOTAL_TIMEOUT_S, "max_concurrency": MAX_CONCURRENCY}
_sem_lock: asyncio.Semaphore | None = None
_sem_loop: asyncio.AbstractEventLoop | None = None

def _sem() -> asyncio.Semaphore:
    """Cokmesiz semafor: loop degisirse yeniden yaratilir, 5 tavan korunur."""
    global _sem_lock, _sem_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None  # type: ignore[assignment]
    if _sem_lock is None or (_sem_loop is not None and loop is not None and _sem_loop is not loop):
        try:
            _sem_lock = asyncio.Semaphore(MAX_CONCURRENCY)
        except Exception:
            # Asiri yukte bile yukselme: kilitsiz gecici semafor.
            _sem_lock = asyncio.Semaphore(1)
        _sem_loop = loop
    assert _sem_lock is not None
    return _sem_lock

def _steps() -> list[tuple[str, float]]:
    """(model, timeout) adimlari — yalnizca YEREL Ollama, dis servis yok."""
    out: list[tuple[str, float]] = []
    for m in model_chain():
        if is_cloud(m):
            continue  # dis servisler pasif: hicbir adimda gorunmez
        out.append((m, FAST_TIMEOUT_S if m == FAST_MODEL else TOTAL_TIMEOUT_S))
    return out or [(PRIMARY_MODEL, TOTAL_TIMEOUT_S)]

def _local_call(model: str, messages: list, *, temperature: float,
                max_tokens: int, timeout: float) -> str:
    """Yerel Ollama cagrisi: dar baglam (num_ctx) + 4 CPU parcasi."""
    import ollama_client
    return ollama_client.chat(messages, model=local_name(model),
                              temperature=temperature, max_tokens=max_tokens,
                              timeout=timeout)

async def aroute(messages: list, *, temperature=0.6, max_tokens=240, lang="en") -> dict:
    """Cache -> yerel Ollama (primary -> hizli) -> deterministik fallback.

    Dis servisler PASIFTIR: hicbir dis istek atilmaz. Dis kaynakli 404/500/kota
    hatasi yakalanirsa sadece loglanir ve zincir yerel Ollama'da devam eder;
    musteriye ASLA hata/kesinti metni gitmez (zero-notice).
    """
    t0 = time.monotonic()
    try:
        from nirvana import semantic_cache as sc
        hit = sc.get(messages)
        if hit:
            safe, ok = sanitize_reply(hit)
            if ok:
                return {"text": safe, "model": "cache", "latency_s": round(time.monotonic()-t0, 3)}
    except Exception: pass
    try: loop = asyncio.get_running_loop()
    except RuntimeError: loop = None  # type: ignore

    def _sync(model: str, timeout: float) -> str:
        return _local_call(model, messages, temperature=temperature,
                           max_tokens=max_tokens, timeout=timeout)

    async def _call(model: str, timeout: float) -> str:
        if loop is None:
            return _sync(model, timeout)
        async with _sem():
            return await loop.run_in_executor(None, lambda: _sync(model, timeout))

    last = ""
    for model, to in _steps():
        try:
            txt = await asyncio.wait_for(_call(model, to), timeout=to + 2.0)
        except Exception as e:
            last = str(e)[:160]
            # Dis kaynakli 404/500/kota hatasi: yalnizca log, musteriye hicbir
            # sey yansimaz; zincir yerel modelle devam eder.
            if is_model_not_found(e):
                logger.warning("dis kaynakli hata engellendi (gizlendi): %s", last)
            else:
                logger.warning("llm %s fail: %s", model, last)
            continue
        safe, ok = sanitize_reply(txt)
        if not ok:
            # JSON hata govdesi/'[object Object]' asla chat'e gecmez; SESSIZ atla.
            logger.warning("llm %s yaniti suzuldu (sessiz): %r", model, str(txt)[:80])
            last = "yanit suzuldu"
            continue
        try:
            from nirvana import semantic_cache as sc; sc.put(messages, safe)
        except Exception: pass
        return {"text": safe, "model": model, "latency_s": round(time.monotonic()-t0, 3)}
    try:
        import webchat_core as wc
        txt = wc.fallback_reply(lang=lang if lang in ("tr","en") else "en", tone="consult")
    except Exception: txt = "Teknik ekibimiz kisa surede donus yapacak."
    safe, ok = sanitize_reply(txt)
    return {"text": safe if ok else "", "model": "fallback",
            "latency_s": round(time.monotonic()-t0, 3), "error": last}
def route_sync(messages: list, **kw: Any) -> dict:
    try: asyncio.get_running_loop()
    except RuntimeError: return asyncio.run(aroute(messages, **kw))
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(1) as ex: return ex.submit(lambda: asyncio.run(aroute(messages, **kw))).result()

# --- B2B Whitelabel onboarding chati (Ollama qwen2.5:3b) ----------------------
async def aroute_agency(messages: list, *, session: dict | None = None, temperature: float = 0.6, max_tokens: int = 240, lang: str = "en") -> dict:
    """Ajans ortak sohbeti: prompt dosyadan, model qwen2.5:3b, hata-izole."""
    t0 = time.monotonic()
    try:
        system = await aload_agency_prompt()
    except Exception:
        system = load_agency_prompt()
    pay_link = default_payment_link()
    if pay_link and pay_link not in system:
        system = system + "\nGuncel Payoneer istek baglantisi: " + pay_link
    convo: list = [{"role": "system", "content": system}]
    try:
        convo.extend([m for m in (messages or []) if isinstance(m, dict)])
    except Exception:
        convo.append({"role": "user", "content": str(messages or "")[:1200]})
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None  # type: ignore[assignment]
    last = ""
    models = [AGENCY_MODEL, FAST_MODEL, PRIMARY_MODEL]
    seen: list[str] = []
    for m in models:
        if m and m not in seen:
            seen.append(m)
    for model in seen:
        try:
            if loop is None:
                txt = _local_call(model, convo, temperature=temperature, max_tokens=max_tokens, timeout=TOTAL_TIMEOUT_S)
            else:
                async with _sem():
                    txt = await asyncio.wait_for(loop.run_in_executor(None, lambda _m=model: _local_call(_m, convo, temperature=temperature, max_tokens=max_tokens, timeout=TOTAL_TIMEOUT_S)), timeout=TOTAL_TIMEOUT_S + 2.0)
        except Exception as e:
            last = str(e)[:160]
            logger.warning("agency llm %s fail: %s", model, last)
            continue
        safe, ok = sanitize_reply(txt)
        if not ok:
            last = "yanit suzuldu"
            continue
        return {"text": safe, "model": model, "latency_s": round(time.monotonic() - t0, 3)}
    try:
        import webchat_core as _wc
        fb = _wc.fallback_reply(lang=lang if lang in ("tr", "en") else "en", tone="consult")
    except Exception:
        fb = "Teknik ekibimiz kisa surede donus yapacak."
    safe, _ok = sanitize_reply(fb)
    return {"text": safe if _ok else "", "model": "fallback", "latency_s": round(time.monotonic() - t0, 3), "error": last}

def route_agency_sync(messages: list, **kw: Any) -> dict:
    try: asyncio.get_running_loop()
    except RuntimeError: return asyncio.run(aroute_agency(messages, **kw))
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(1) as ex: return ex.submit(lambda: asyncio.run(aroute_agency(messages, **kw))).result()
