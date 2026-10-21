"""Lane AP — sentinel [Oracle VM, light]: Birleşik Kendi Kendini Onarma Çekirdeği.

Master Sentinel — sistemin ÜST AKLI ("Unified Self-Healing Fabric"): her katmanın
kendi bağımsız bağışıklık sistemini korurken tek bir akıllı çekirdek 30 sn'de bir
sistemi puanlar ve yalnızca ARIZALI bileşeni izole edip ameliyat eder:

  * ``webchat`` : müşteri kapısı ``/health`` (25)     * ``pipeline``: auto_runner
    kalp atışı (30)                                   * ``db``: SQLite WAL kilidi (20)
  * ``workers`` : task_queue durum okuması (10)       * ``llm``: circuit_breaker şalter
    panosu — failover zaten kendi başına çözer, sentinel yalnız raporlar (10)
  * ``notify``  : Telegram ``probe_delivery`` (5; 5 dk throttle)

Sağlık skoru = ağırlıklı 0-100. İzolasyon çekirdeği (surgical checkpoint):

  * Kırmızı bileşen ``SENTINEL_STREAK`` (2) ardışık tur kırmızı olana ve bileşene
    özel ``SENTINEL_COOLDOWN_S`` (300 sn) dolana kadar BEKLENİR; tek tur
    dalgalanması asla restart tetiklemez.
  * Müdahale yalnızca o bileşene uygulanır (port temizle / WAL checkpoint / tek
    unit restart). SAĞLIKLI BİLEŞENLERE ASLA DOKUNULMAZ.
  * ``workers``/``llm``/``notify`` rapor-bileşenidir: kendi bağışıklık sistemleri
    (lease reclaim, failover şalteri, notify kuyruğu) devrede olduğundan sentinel
    burada onarım uygulamaz, kök nedeni raporlar.
  * REBOOT YOK — sunucu/servis komple asla yeniden başlatılmaz; onarım
    job_watchdog'un savaşta denenmiş primitifleriyle (clear_port, reset_wal,
    restart_unit) yalnızca arızalı bileşene uygulanır.

Ölçüm hataları fail-open'dır (probe patlarsa kırmızı sayılmaz — kör restart yok).
``dry_run=True`` hiçbir müdahale/yazım yapmaz. ``main`` tek örnekli (flock) ve
sert tavanlıdır; tur bitmeden yeni tur başlamaz. Hiçbir hata bekçiyi düşürmez.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

from nirvana.registry import state_path

logger = logging.getLogger(__name__)

STATE_NAME = "sentinel.json"

# Ağırlıklı sağlık skoru (toplam 100): işin taşıyıcısı pipeline > müşteri kapısı
# > veri > worker havuzu > LLM şalteri > bildirim.
WEIGHTS: dict[str, int] = {
    "pipeline": 30,
    "webchat": 25,
    "db": 20,
    "workers": 10,
    "llm": 10,
    "notify": 5,
}
COMPONENTS: tuple[str, ...] = tuple(WEIGHTS)
# Onarım uygulanabilen bileşenler (diğerleri rapor-bileşeni: kendi bağışıklık
# sistemi kendi başının çaresine bakar).
REPAIRABLE = frozenset({"webchat", "pipeline", "db"})

STREAK = int(os.getenv("SENTINEL_STREAK", "2") or 2)
COOLDOWN_S = float(os.getenv("SENTINEL_COOLDOWN_S", "300") or 300)
HEARTBEAT_MAX_AGE_S = float(os.getenv("SENTINEL_HEARTBEAT_MAX_AGE_S", "180") or 180)
NOTIFY_PROBE_TTL_S = float(os.getenv("SENTINEL_NOTIFY_PROBE_TTL_S", "300") or 300)
HARD_CAP_S = float(os.getenv("SENTINEL_HARD_CAP_S", "25") or 25)
WEBCHAT_PORT = int(os.getenv("WEBCHAT_PORT", "8765") or 8765)
WEBCHAT_UNIT = "nirvana-webchat.service"
PIPELINE_UNIT = "nirvana-pipeline.service"
HEARTBEAT_NAME = "auto_runner"


# --- durum (kalıcı; salt-okunur /sentinel ucu bu dosyayı okur) -----------------

def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path(STATE_NAME).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        tmp = state_path(STATE_NAME).with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str) + "\n",
                       encoding="utf-8")
        tmp.replace(state_path(STATE_NAME))
    except OSError as exc:  # noqa: BLE001 — durum yazımı bekçiyi düşürmez
        logger.warning("sentinel state yazılamadı: %s", str(exc)[:120])


# --- ölçüler (her sonda bağımsız korunur; hata fail-open) ---------------------

def probe_webchat(*, health_fn: Callable[[], bool] | None = None,
                  port: int | None = None) -> dict[str, Any]:
    """Müşteri kapısı ayakta mı? (``/health`` 200)."""
    try:
        if health_fn is None:
            from nirvana import job_watchdog

            ok = bool(job_watchdog.webchat_health(port=port or WEBCHAT_PORT, timeout=4.0))
        else:
            ok = bool(health_fn())
        return {"ok": ok, "detail": "/health 200" if ok else "/health yanitsız (kapı kapalı)"}
    except Exception as exc:  # noqa: BLE001 — sonda hatası kırmızı sayılmaz
        return {"ok": True, "detail": f"webchat sonda hatası (fail-open): {str(exc)[:80]}"}


def probe_pipeline(*, age_fn: Callable[[], float | None] | None = None) -> dict[str, Any]:
    """Pipeline kalp atışı: ``heartbeat_auto_runner.json`` yaşı sınırı içinde mi?"""
    try:
        if age_fn is None:
            import heartbeat

            age = heartbeat.age_seconds(HEARTBEAT_NAME)
        else:
            age = age_fn()
        if age is None:
            return {"ok": False, "detail": "kalp atisi YOK (auto_runner pulse etmiyor)"}
        ok = float(age) <= HEARTBEAT_MAX_AGE_S
        return {"ok": ok,
                "detail": (f"kalp atisi {float(age):.0f}s once" if ok else
                           f"kalp atisi GECIKMELI ({float(age):.0f}s > {HEARTBEAT_MAX_AGE_S:.0f}s)")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "detail": f"kalp atisi okunamadi (fail-open): {str(exc)[:80]}"}


def probe_db(*, dbs_fn: Callable[[], list[Any]] | None = None,
             locked_fn: Callable[[Any], bool] | None = None) -> dict[str, Any]:
    """SQLite WAL kilidi: kilitli dosya var mı (``BEGIN IMMEDIATE`` denemesi)?"""
    try:
        if dbs_fn is None or locked_fn is None:
            from nirvana import job_watchdog

            dbs = dbs_fn() if dbs_fn is not None else job_watchdog.sqlite_dbs()
            locked = locked_fn if locked_fn is not None else job_watchdog.db_locked
        else:
            dbs = dbs_fn()
            locked = locked_fn
        rows = list(dbs)
        hits = [str(getattr(p, "name", p)) for p in rows if locked(p)]
        if hits:
            return {"ok": False, "detail": "SQLite kilidi: " + ", ".join(hits)}
        return {"ok": True, "detail": "WAL temiz (%d dosya)" % len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "detail": f"db kilit denetimi atlandi (fail-open): {str(exc)[:80]}"}


def probe_workers(*, stats_fn: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Worker havuzu (task_queue): durum panosu okunuyor mu?

    Durgun iş kendi bağışıklık sistemindedir (lease reclaim + retry→dead):
    sentinel burada restart uygulamaz, yalnız havuzun ölmediğini doğrular.
    """
    try:
        if stats_fn is None:
            import task_queue

            stats = task_queue.stats()
        else:
            stats = dict(stats_fn())
        return {"ok": True,
                "detail": "havuz okunur: done=%s dead=%s" % (
                    stats.get("done", 0), stats.get("dead", 0))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"task_queue okunamadi: {str(exc)[:90]}"}


def probe_llm(*, status_fn: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    """LLM şalter panosu: failover ZATEN devrede (llm_router / circuit_breaker).

    Sentinel burada onarım uygulamaz — model/endpoint geçişi ve half-open prob
    şalterin kendi işidir. Yalnızca kök neden görünürlüğü için kapsam raporlanır.
    """
    try:
        if status_fn is None:
            import circuit_breaker

            pan = circuit_breaker.status()
        else:
            pan = dict(status_fn())
        panos = pan.get("breakers") if isinstance(pan.get("breakers"), dict) else pan
        open_names = [name for name, row in (panos or {}).items()
                      if isinstance(row, dict) and not row.get("closed", True)]
        if open_names:
            return {"ok": True, "detail": "failover devrede (acik salter: " +
                    ", ".join(sorted(open_names)[:4]) + ")"}
        return {"ok": True, "detail": "tum salterler kapali"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "detail": f"sealter panosu okunamadi (fail-open): {str(exc)[:80]}"}


def probe_notify(*, probe_fn: Callable[[], dict[str, Any]] | None = None,
                 state: dict[str, Any] | None = None,
                 now: float | None = None) -> dict[str, Any]:
    """Telegram bildirim kanalı — 5 dakikalık throttle (her turda net yok).

    ``probe_delivery`` mesaj GÖNDERMEDEN gerçek sağlığı ölçer (ZERO-TOUCH).
    """
    stamp = time.time() if now is None else float(now)
    cache = state.get("notify_probe") if isinstance(state, dict) else None
    if (isinstance(cache, dict) and cache.get("detail")
            and stamp - float(cache.get("ts") or 0) < NOTIFY_PROBE_TTL_S):
        return {"ok": bool(cache.get("ok", True)), "detail": str(cache.get("detail"))}
    try:
        if probe_fn is None:
            import owner_notify

            row = owner_notify.probe_delivery()
        else:
            row = dict(probe_fn())
        ok = bool(row.get("can_deliver"))
        detail = ("Telegram kanali acik" if ok else
                  "Telegram kanali KAPALI: " + str(row.get("target_detail") or "bilinmiyor")[:70])
        result: dict[str, Any] = {"ok": ok, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": True, "detail": f"kanal denetimi atlandi (fail-open): {str(exc)[:80]}"}
    if isinstance(state, dict):
        state["notify_probe"] = {"ts": stamp, "ok": result["ok"], "detail": result["detail"]}
    return result


# --- puan + izolasyon kararı --------------------------------------------------

def probe_all(**kw: Any) -> dict[str, dict[str, Any]]:
    """Altı bileşenin tamamını ölç (her sonda bağımsız korunur)."""
    state = kw.get("state") if isinstance(kw.get("state"), dict) else None
    return {
        "webchat": probe_webchat(health_fn=kw.get("health_fn"), port=kw.get("port")),
        "pipeline": probe_pipeline(age_fn=kw.get("age_fn")),
        "db": probe_db(dbs_fn=kw.get("dbs_fn"), locked_fn=kw.get("locked_fn")),
        "workers": probe_workers(stats_fn=kw.get("stats_fn")),
        "llm": probe_llm(status_fn=kw.get("status_fn")),
        "notify": probe_notify(probe_fn=kw.get("notify_probe_fn"), state=state,
                               now=kw.get("now")),
    }


def score(probes: dict[str, dict[str, Any]]) -> int:
    """Ağırlıklı sağlık skoru (0-100). Ölçülemeyen bileşen ağırlığı düşülür."""
    total = 0
    weight_seen = 0
    for name, weight in WEIGHTS.items():
        row = probes.get(name)
        if not isinstance(row, dict) or "ok" not in row:
            continue
        weight_seen += weight
        if row.get("ok"):
            total += weight
    if not weight_seen:
        return 100  # hiçbir sonda çalışmadı → kırmızı senaryo yok (fail-open)
    return int(round(total * 100.0 / weight_seen))


def decide(probes: dict[str, dict[str, Any]], state: dict[str, Any],
           *, now: float | None = None) -> list[dict[str, Any]]:
    """İzolasyon kararı: yalnızca kalıcı kırmızı + cooldown dolu bileşene müdahale.

    Her bileşen kendi hızlandırıcısında izole edilir (streak/cooldown bağımsız);
    tek bileşenin arızası asla diğerine sirayet etmez.
    """
    stamp = time.time() if now is None else float(now)
    streaks = state.get("streaks") if isinstance(state.get("streaks"), dict) else {}
    cooldowns = state.get("cooldowns") if isinstance(state.get("cooldowns"), dict) else {}
    actions: list[dict[str, Any]] = []
    for name, row in probes.items():
        ok = bool(isinstance(row, dict) and row.get("ok"))
        if ok:
            streaks[name] = 0
            continue
        streaks[name] = int(streaks.get(name) or 0) + 1
        if name not in REPAIRABLE:
            continue  # rapor-bileseni: kendi bağışıklık sistemi çözer
        if streaks[name] < STREAK:
            continue  # tek tur dalgalanması onarım tetiklemez (yanlış restart yok)
        last = float(cooldowns.get(name) or 0)
        if last and stamp - last < COOLDOWN_S:
            continue  # onarım cooldownda (restart kasırgası yok)
        actions.append({"component": name,
                        "streak": streaks[name],
                        "detail": str(row.get("detail") or "")})
    state["streaks"] = {str(k): int(v) for k, v in streaks.items()}
    state["cooldowns"] = {str(k): float(v) for k, v in cooldowns.items()}
    return actions


# --- cerrahi müdahale (SADECE arızalı bileşene) --------------------------------

def repair(component: str, *, state: dict[str, Any],
           now: float | None = None,
           cmd_fn: Any = None,
           wal_fn: Any = None,
           restart_fn: Any = None) -> dict[str, Any]:
    """Arızalı bileşeni YERİNDE onar — başka hiçbir bileşene dokunma.

    * ``webchat`` : port ``fuser -k -9`` + ``reset-failed`` + TEK unit restart.
    * ``pipeline``: kaçak süreç süpürmesi + TEK unit restart (sunucu değil).
    * ``db``      : kilitli SQLite'ta ``PRAGMA wal_checkpoint(TRUNCATE)`` +
                    ``-wal``/``-shm`` sıfırlama (milisaniyeler; servis yok).

    Her bileşenin kendi cooldown'ı yalnızca başarılı onarımda yazılır;
    ``restart_fn``/``wal_fn``/``cmd_fn`` enjeksiyonu test içindir (canlıda
    job_watchdog'un savaşta denenmiş primitifleri kullanılır). REBOOT YOK.
    """
    stamp = time.time() if now is None else float(now)
    out: dict[str, Any] = {"component": component, "ok": False, "applied": []}
    try:
        from nirvana import job_watchdog

        if component == "webchat":
            rfn = restart_fn or job_watchdog.restart_unit
            clear = job_watchdog.clear_port(WEBCHAT_PORT, cmd_fn=cmd_fn)
            reset = job_watchdog.reset_failed(WEBCHAT_UNIT, cmd_fn=cmd_fn)
            res = rfn(WEBCHAT_UNIT)
            out["applied"] = ["port temizlendi: %s" % bool(clear.get("ok")),
                              "reset-failed: %s" % bool(reset.get("ok")),
                              "restart: %s" % bool(res.get("ok"))]
            out["ok"] = bool(res.get("ok"))
        elif component == "pipeline":
            swept = job_watchdog.sweep_stuck_processes()
            rfn = restart_fn or job_watchdog.restart_unit
            res = rfn(PIPELINE_UNIT)
            out["applied"] = ["kacak supurge: %d" % len(swept),
                              "restart: %s" % bool(res.get("ok"))]
            out["ok"] = bool(res.get("ok"))
        elif component == "db":
            wfn = wal_fn or job_watchdog.reset_wal
            ok = True
            hits: list[str] = []
            for path in job_watchdog.sqlite_dbs():
                if not job_watchdog.db_locked(path):
                    continue
                res = wfn(path)
                ok = ok and bool(res.get("ok"))
                hits.append(str(getattr(path, "name", path)))
            out["applied"] = ["wal checkpoint: " + (", ".join(hits) if hits else "yok")]
            out["ok"] = ok if hits else True
        else:
            out["applied"] = ["rapor-bileseni (onarim yok)"]
            out["ok"] = True
    except Exception as exc:  # noqa: BLE001 — müdahale hatası bekçiyi düşürmez
        out["error"] = f"{type(exc).__name__}: {exc}"[:160]
    if out["ok"]:
        cds = state.get("cooldowns") if isinstance(state.get("cooldowns"), dict) else {}
        cds[component] = float(stamp)
        state["cooldowns"] = {str(k): float(v) for k, v in cds.items()}
    return out


def _notify(text: str) -> bool:
    try:
        import owner_notify

        return bool(owner_notify.send(text, high_priority=True))
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentinel notify atlandi: %s", str(exc)[:120])
        return False


def fix_notify_enabled() -> bool:
    """Varsayılan SESSIZ onarım; yalnız env ile açılırsa tek satır gider."""
    raw = str(os.getenv("SENTINEL_NOTIFY_ON_FIX",
                        os.getenv("JOB_WATCHDOG_NOTIFY_ON_FIX", "0"))).strip().lower()
    return raw in {"1", "true", "yes", "on"}


# --- tur (Oracle timer, 30 sn) ------------------------------------------------

def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Ölç (6 bileşen) → puanla → izole et (yalnızca arızalı) → tek satır rapor.

    ``dry_run=True`` hiçbir müdahale/yazım yapmaz. Sağlıklı sistemde tur
    milisaniyelerle biter ve TEK bileşen bile restart görmez (REBOOT YOK).
    """
    dry_run = bool(kwargs.get("dry_run", False))
    state = dict(kwargs["state"]) if isinstance(kwargs.get("state"), dict) else _load_state()
    now = float((kwargs.get("now_fn") or time.time)())
    probes = probe_all(state=state, now=now,
                       health_fn=kwargs.get("health_fn"),
                       age_fn=kwargs.get("age_fn"),
                       dbs_fn=kwargs.get("dbs_fn"),
                       locked_fn=kwargs.get("locked_fn"),
                       stats_fn=kwargs.get("stats_fn"),
                       status_fn=kwargs.get("status_fn"),
                       notify_probe_fn=kwargs.get("notify_probe_fn"),
                       port=kwargs.get("port"))
    total = score(probes)
    actions = decide(probes, state, now=now)
    repairs: list[dict[str, Any]] = []
    notes: list[str] = []
    if dry_run:
        for act in actions:
            notes.append(f"dry-run: {act['component']} mudahelesi atlandi "
                         f"(kesintisiz, state yazilmadi: {act['detail'][:60]})")
    else:
        for act in actions:
            res = repair(act["component"], state=state, now=now,
                         cmd_fn=kwargs.get("cmd_fn"),
                         wal_fn=kwargs.get("wal_fn"),
                         restart_fn=kwargs.get("restart_fn"))
            repairs.append(res)
            mark = "onarildi" if res.get("ok") else "MUDAHALE BASARISIZ"
            notes.append(f"{act['component']} {mark}: " + "; ".join(res.get("applied") or []))
            if res.get("error"):
                notes.append(f"{act['component']} hata: {res['error']}")
            # Lane AX: onarim BASARISIZSA siniflandir. GECICI -> sessiz izleme
            # (sonraki tur tekrar dener). MANTIKSAL -> manifest FALSE + nokta
            # atisi Telegram + guvenli mod (surec durdurulur).
            if not res.get("ok"):
                try:
                    from nirvana import error_classifier as _ec
                    _v = _ec.classify(
                        "%s onarim basarisiz: %s" % (
                            act["component"], "; ".join(res.get("applied") or [])[:200]
                            + " | " + str(res.get("error") or "")[:200]))
                    if _v.get("kind") == "logic" and _v.get("notify_now"):
                        from nirvana import manifest_state as _ms
                        if _ms.set_false("sentinel/%s" % act["component"],
                                         "%s onarimi cozemedi (x%s): %s"
                                         % (act["component"], _v.get("count"), _v.get("reason"))):
                            notes.append("MANIFEST FALSE: %s guvenli modda" % act["component"])
                except Exception:  # noqa: BLE001 — bekci siniflandirma yuzunden dusmez
                    pass
    # İş sonucu görünür kalsın (sentinel iş SEVİYESİNE karışmaz — job_watchdog
    # onun işi — ama panoda kök neden için sayılar raporlanır).
    try:
        import knowledge

        forms_today, forms_hour = knowledge.submit_counts()
        forms: dict[str, Any] = {"today": int(forms_today), "hour": int(forms_hour)}
    except Exception:  # noqa: BLE001
        forms = {"today": None, "hour": None}
    try:
        import domain_store

        queue_depth: int | None = int(domain_store.queue_depth())
    except Exception:  # noqa: BLE001
        queue_depth = None

    red = [name for name, row in probes.items() if not row.get("ok")]
    state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    state["last_score"] = total
    state["last_red"] = red
    state["last_detail"] = {name: str(row.get("detail") or "")
                            for name, row in probes.items()}
    state["runs"] = int(state.get("runs") or 0) + 1
    if repairs:
        state["repairs"] = int(state.get("repairs") or 0) + 1
    state["last_repairs"] = repairs
    if not dry_run:
        _save_state(state)
        try:
            import heartbeat

            heartbeat.pulse("sentinel", {"score": total, "red": red})
        except Exception:  # noqa: BLE001
            pass
        if repairs and fix_notify_enabled():
            fixed = [r["component"] for r in repairs if r.get("ok")]
            if fixed:
                _notify("[DevSolve Ops] Sentinel: %s izole edildi -> skor %d/100 "
                        "(reboot yok)" % ("/".join(fixed), total))
    return {
        "lane": "sentinel",
        "dry_run": dry_run,
        "ok": not red,
        "score": total,
        "red": red,
        "probes": probes,
        "repairs": repairs,
        "notes": notes,
        "cooldowns_s": {name: max(0, int(COOLDOWN_S - (now - float(v))))
                        for name, v in (state.get("cooldowns") or {}).items()},
        "forms": forms,
        "queue_depth": queue_depth,
        "state": str(state_path(STATE_NAME)),
        "note": ("Birlesik otonom saglik cekirdegi: 6 katman skorlanir; yalnizca "
                 "kirmizi + cooldown dolu bilesen izole edilir. REBOOT YOK."),
    }


def self_test() -> int:
    """Kuru çalışma: gerçek müdahale/yazım olmadan karar zincirini doğrular."""
    state: dict[str, Any] = {}
    probes = probe_all(state=state)
    total = score(probes)
    assert set(probes) == set(COMPONENTS)
    assert 0 <= total <= 100
    # Enjeksiyon: pipeline iki tur kirmizi + cooldown dolu → TEK aksiyon pipeline.
    red_probes = {name: {"ok": True, "detail": ""} for name in COMPONENTS}
    red_probes["pipeline"] = {"ok": False, "detail": "test"}
    assert decide(red_probes, state, now=1000.0) == []  # ilk tur: streak 1 → bekleme
    acts2 = decide(red_probes, state, now=1010.0)
    assert [a["component"] for a in acts2] == ["pipeline"]  # yalnizca pipeline
    # Basarili onarim cooldown yazar (restart kasirgasini keser).
    res = repair("pipeline", state=state, now=1010.0,
                 restart_fn=lambda unit: {"ok": True, "unit": unit})
    assert res["ok"] is True and float(state["cooldowns"]["pipeline"]) == 1010.0
    assert decide(red_probes, state, now=1020.0) == []  # cooldown: hemen tekrar yok
    assert set(state.get("streaks") or {}) <= set(COMPONENTS)
    print("SENTINEL SELF-TEST OK: skor=%s red=%s (eylem yok)" % (
        total, sorted(k for k, v in probes.items() if not v["ok"])))
    return 0


def _acquire_singleton() -> Any:
    """30 sn'lik tur için tek örnek kilidi (posix: ``flock``; süreç ölürse düşer)."""
    if os.name != "posix":
        return None
    try:
        import fcntl

        handle = open(state_path("sentinel.lock"), "w")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        return "busy"
    except Exception:  # noqa: BLE001 — kilit altyapısı yoksa engelleme yapma
        return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nirvana.sentinel")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    lock = _acquire_singleton()
    if lock == "busy":
        print(json.dumps({"lane": "sentinel", "skipped": "already_running"},
                         ensure_ascii=False))
        return 0
    result = run_batch(dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


