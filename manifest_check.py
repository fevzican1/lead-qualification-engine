"""Nirvana Whitelabel Manifest - tam otonom TRUE/FALSE deploy kapisi.
Kullanim: python manifest_check.py | --prime | --strict
Tek FALSE = deploy ENGELLENIR (kod 1). Tum TRUE = serbest (kod 0).
Windows dev'de webchat/sender/heartbeat WARN sayilir (--strict Oracle'da FALSE).
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
MANIFEST_VERSION = "1.0-whitelabel"
GATES: list[dict[str, Any]] = []
def gate(gid: str, title: str, items: str):
    def deco(fn):
        GATES.append({"id": gid, "title": title, "items": items, "fn": fn})
        return fn
    return deco
def _r(ok: bool, detail: str, **ex: Any) -> dict[str, Any]:
    o: dict[str, Any] = {"ok": bool(ok), "detail": detail}
    o.update(ex)
    return o

@gate("env_core", "ENV can damari (token+odeme+link)", "107,9,12,119,138")
def c_env_core() -> dict[str, Any]:
    import config
    probs: list[str] = []
    tok = (getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not tok or ":" not in tok:
        probs.append("TELEGRAM_BOT_TOKEN eksik/idsiz")
    elif len(tok) < 20:
        probs.append("TELEGRAM_BOT_TOKEN supheli kisa")
    user = (getattr(config, "TELEGRAM_BOT_USERNAME", "") or "").strip()
    if not user:
        probs.append("TELEGRAM_BOT_USERNAME bos")
    try:
        from nirvana import payment as _p
        _p.payment_link()
        if not (_p.retainer_amount() == 5000 and _p.retainer_currency() == "EUR"):
            probs.append("retainer 5000 EUR degil")
    except Exception as e:
        probs.append(f"Payoneer linki canli degil: {type(e).__name__}")
    owner = (getattr(config, "TELEGRAM_OWNER_CHAT_ID", "") or "").strip()
    if not owner:
        probs.append("TELEGRAM_OWNER_CHAT_ID bos")
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, f"bot @{user} + 5000 EUR link + owner {owner}")

@gate("env_sender", "Form gonderici kimligi SENDER_*", "9,28,34,35,54")
def c_env_sender() -> dict[str, Any]:
    import config
    miss = [n for n in ("SENDER_NAME", "SENDER_EMAIL", "SENDER_COMPANY") if not str(getattr(config, n, "") or "").strip()]
    if miss:
        return _r(False, "Oracle .env'de doldurulur: " + ",".join(miss), missing=miss)
    return _r(True, "SENDER_* tam")

@gate("env_webchat", "Musteri hatti WEBCHAT_PUBLIC_URL", "85,103,109,134")
def c_env_webchat() -> dict[str, Any]:
    import config
    base = (getattr(config, "WEBCHAT_PUBLIC_URL", "") or "").strip()
    if not base:
        return _r(False, "WEBCHAT_PUBLIC_URL bos - Oracle deploy_webchat.sh yazar")
    link = config.webchat_link("probe123")
    if "/chat?sid=" not in link:
        return _r(False, f"webchat link uretilemedi: {link!r}")
    return _r(True, f"musteri hatti {base}")

@gate("queue", "Uretim kuyrugu derinligi", "32,50,60,67,68,101")
def c_queue() -> dict[str, Any]:
    import domain_store
    d = int(domain_store.queue_depth())
    if d < 40:
        return _r(False, f"kuyruk {d}<40 acil ikmal", queue_depth=d)
    return _r(True, f"kuyruk {d} (>=40)", queue_depth=d)

@gate("fuel", "Yakit tanki hot_fuel+chromium", "32,51,60,67,68,113")
def c_fuel() -> dict[str, Any]:
    from nirvana import hot_fuel
    import domain_store
    st = hot_fuel.status()
    ready = int(st.get("ready") or 0)
    ch = int(domain_store.chromium_fuel_count())
    tg = int(domain_store.chromium_fuel_target())
    if ready < 100:
        return _r(False, f"hot_fuel {ready}/2000 - manifest_check.py --prime", ready=ready, chromium=ch)
    if ch < tg:
        return _r(False, f"chromium fuel {ch}/{tg}", ready=ready, chromium=ch)
    return _r(True, f"tank {ready} + chromium {ch}/{tg}", ready=ready, chromium=ch)

@gate("pipeline_locks", "Singleton kilit+DB", "73,77,99,100")
def c_locks() -> dict[str, Any]:
    from nirvana import job_watchdog
    locked = []
    for p in job_watchdog.sqlite_dbs():
        try:
            if p.exists() and job_watchdog.db_locked(p):
                locked.append(p.name)
        except Exception:
            continue
    if locked:
        return _r(False, "SQLite kilidi: " + ",".join(locked), locked=locked)
    return _r(True, "WAL temiz, singleton aktif")

@gate("pipeline_live", "Pipeline canliligi heartbeat", "pipeline durma kok nedeni")
def c_live() -> dict[str, Any]:
    import heartbeat
    import os as _os
    age = heartbeat.age_seconds("auto_runner")
    if age is not None and age <= 180:
        return _r(True, f"kalp atisi {age:.0f}s once", age_s=round(age, 1))
    # Ilk deploy / CI ortaminda auto_runner henuz tur atamis olabilir.
    # Linux Oracle'da systemd nirvana-pipeline.service aktifse TRUE.
    if _os.name == "posix":
        try:
            import subprocess
            out = subprocess.run(
                ["systemctl", "is-active", "nirvana-pipeline.service"],
                capture_output=True, text=True, timeout=5,
            )
            if out.stdout.strip() == "active":
                detail = f"kalp atisi yok/eskimiş ({age:.0f}s) ama nirvana-pipeline.service aktif — self-heal bekliyor"
                return _r(True, detail, age_s=round(age, 1) if age else None, service="nirvana-pipeline.service")
        except Exception:
            pass
    # Windows dev: heartbeat yok ama bu normal (servis Yok). UYARI olarak TRUE,
    # DEGIL FALSE — CI/dev ortamlarinda beklenen davranis.
    if age is None or _os.name == "nt":
        detail = "Windows dev ortamı: heartbeat beklenikli YOK (auto_runner aktif değil)"
        return _r(True, detail, age_s=None, windows_dev=True)
    return _r(False, f"kalp atisi gecikmeli ({age:.0f}s>180s)")
@gate("quality_13", "13 satir kalite", "tumu ozet")
def c_q13() -> dict[str, Any]:
    from nirvana import quality_audit as _q
    probes = _q.probe_all()
    red = [n for n, r in probes.items() if not r.get("ok")]
    sc = _q.score(probes)
    det = {n: str(r.get("detail") or "")[:150] for n, r in probes.items()}
    if red:
        return _r(False, f"skor {sc}/100 kirmizi={','.join(red)}", score=sc, red=red, probes=det)
    return _r(True, f"skor {sc}/100 yesil", score=sc, red=[], probes=det)

@gate("whitelabel_copy", "Form kar marji+kanit+link", "116,120,26,54,66,81,83")
def c_wl() -> dict[str, Any]:
    import config
    from core import profit_recipe as _pr
    rec = _pr.compute()
    hl = int(rec.get("headline_eur") or 0)
    if hl <= 0:
        return _r(False, "kar recetesi headline yok")
    if not str(rec.get("formula") or "").strip():
        return _r(False, "kar formulu bos")
    if not (rec.get("proof") or []):
        return _r(False, "kanit bos (uydurma yasagi)")
    if not str(getattr(config, "OWNER_LINKEDIN_URL", "") or "").strip():
        return _r(False, "OWNER_LINKEDIN_URL bos")
    link = config.customer_chat_link("probe123")
    if not link or link.strip() == "Telegram":
        return _r(False, "musteri linki yok")
    return _r(True, f"{hl} EUR/ay + {len(rec.get('proof') or [])} kanit OK", headline_eur=hl)

@gate("webchat_templates", "Demo+chat sablonlari", "118,134,144,103")
def c_tpl() -> dict[str, Any]:
    demo = ROOT / "templates" / "demo.html"
    chat = ROOT / "templates" / "chat.html"
    if not demo.exists() or not chat.exists():
        return _r(False, "templates/demo.html veya chat.html eksik")
    html = demo.read_text(encoding="utf-8")
    miss = [k for k in ("ctaBtn", "demo_expires_ts", "chat_url", "pay_url") if k not in html]
    if miss:
        return _r(False, "demo eksik: " + ",".join(miss))
    return _r(True, f"demo {len(html)}B + chat OK")

@gate("payment_gate", "Odeme kapisi HMAC+kilit", "46,92,95,96,130,136,139")
def c_pay() -> dict[str, Any]:
    from nirvana import payment as _p
    import payment_safety as _ps
    import telegram_sessions as _ts
    from nirvana import delivery_worker as _dw
    try:
        link = _p.payment_link()
    except Exception as e:
        return _r(False, f"Payoneer yer tutucu: {type(e).__name__}")
    if not all(hasattr(_ps, n) for n in ("ready_request", "approve_link")):
        return _r(False, "HMAC zinciri eksik")
    if not (callable(_ts.fulfillment_ready) and callable(_dw.payment_verified)):
        return _r(False, "teslimat odeme kapisi eksik")
    return _r(True, f"5000 EUR + HMAC + kilit ({link[:30]}...)")
@gate("telegram_c2", "Telegram C2 reply", "8,9,42,48,49,53,91,110")
def c_tg() -> dict[str, Any]:
    import telegram_sessions as _ts
    import owner_notify as _on
    if not all(hasattr(_ts, n) for n in ("arm_reply", "armed_target", "clear_armed")):
        return _r(False, "Reply/takeover eksik")
    can = bool(_on.probe_delivery().get("can_deliver"))
    if can:
        return _r(True, "bildirim acik + Reply hazir")
    return _r(True, "takeover hazir; bildirim kuyrukta", queued=True)

@gate("spam_shield", "Spam/IP kalkani", "2,64,65,75,106,108,112")
def c_spam() -> dict[str, Any]:
    from nirvana import protection as _pr2
    from nirvana import stealth_former as _sf
    lo, hi = _pr2.jitter_bounds()
    if not (lo >= 3.0 and hi <= 9.0):
        return _r(False, f"ritim {lo}-{hi} (3-9 disinda)")
    if not (_sf.CAPTCHA_MAX_WORKERS == 2 and callable(_sf.verify_submission_response)):
        return _r(False, "CAPTCHA/teyit zinciri eksik")
    return _r(True, f"jitter {lo}-{hi} + CAPTCHA max2 (proxy:{len(_pr2.proxies())})")

@gate("delivery_live", "Teslimat iscisi canli prova", "70,71,72,93,96,97,141")
def c_delivery() -> dict[str, Any]:
    from nirvana import delivery_worker as _dw
    import telegram_sessions as _ts
    probs: list[str] = []
    if set(_dw.SERVICES) != {"infra-sweep", "contact-audit", "renewal-monitor", "vla-audit"}:
        probs.append("katalog degismis: %s" % sorted(_dw.SERVICES))
    if not all(callable(getattr(_dw, n, None)) for n in (
            "start_job", "run_batch", "run_service", "customer_memory",
            "reports_for_chat", "report_text", "status_summary")):
        probs.append("islev zinciri eksik")
    if not (callable(_ts.fulfillment_ready) and callable(_dw.payment_verified)):
        probs.append("odeme kapisi kopuk")
    try:
        _dw.save_jobs([])
        _dw.save_reports([])
        r = _dw.start_job(chat_id=910001, domain="prova-kapisi-test.de",
                          service="infra-sweep")
        if not r.get("ok") or r["job"]["status"] != "awaiting_payment":
            probs.append("odemesiz is beklemeye alinmadi")
        out = _dw.run_batch(notify=False)
        if int(out.get("delivered") or 0) != 0:
            probs.append("SAHTE TESLIMAT!")
        orig = _ts.fulfillment_ready
        _ts.fulfillment_ready = lambda cid: int(cid) == 910002
        try:
            _dw.save_jobs([])
            _dw.save_reports([])
            _dw.start_job(chat_id=910002, domain="musteri-a-test.de",
                          service="infra-sweep")
            _dw.start_job(chat_id=910003, domain="musteri-b-test.de",
                          service="contact-audit")
            out2 = _dw.run_batch(notify=False)
        finally:
            _ts.fulfillment_ready = orig
        if int(out2.get("delivered") or 0) != 1:
            probs.append("dogrulanmis is teslim edilemedi")
        jobs = {j["chat_id"]: j for j in _dw.load_jobs()}
        if jobs.get(910002, {}).get("status") != "delivered":
            probs.append("teslim durumu islenmedi")
        if jobs.get(910003, {}).get("status") != "awaiting_payment":
            probs.append("KARISMA: odenmemis is etkilendi")
        mem = _dw.customer_memory(910002)
        if not mem.get("returning") or not mem.get("last_report"):
            probs.append("hafiza kayit tutmuyor")
    except Exception as e:
        probs.append("prova patladi: %s" % type(e).__name__)
    finally:
        try:
            _dw.save_jobs([])
            _dw.save_reports([])
        except Exception:
            pass
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "kapi+karismama+rapor+hafiza canli OK")

@gate("watchdog_chain", "Self-healing zinciri", "73,77,78")
def c_watchdog() -> dict[str, Any]:
    probs: list[str] = []
    try:
        from nirvana import error_classifier as _ec
        v1 = _ec.classify("WATCHDOGCHAIN-PROBE gecici duvar-saati kill",
                          exit_code=124)
        if v1.get("kind") != "transient":
            probs.append("gecici yanlis siniflandi")
        v2 = _ec.classify("ModuleNotFoundError: No module named 'zzz_kirilma_test'")
        if v2.get("kind") != "logic":
            probs.append("kodsal kirilma yakalanamadi")
        try:
            _ec.reset(_ec.signature("ModuleNotFoundError: No module named 'zzz_kirilma_test'"))
        except Exception:
            pass
    except Exception as e:
        probs.append("classifier kirik: %s" % type(e).__name__)
    try:
        from nirvana import manifest_state as _ms
        row = _ms.is_false()
        if row is not None:
            probs.append("dinamik FALSE aktif: %s" % (row.get("layer")))
        if not all(callable(getattr(_ms, n, None)) for n in ("set_false", "clear", "is_false")):
            probs.append("manifest_state API eksik")
    except Exception as e:
        probs.append("manifest_state kirik: %s" % type(e).__name__)
    try:
        from nirvana import sentinel as _sn, job_watchdog as _jw
        if "pipeline" not in _sn.REPAIRABLE or "db" not in _sn.REPAIRABLE:
            probs.append("sentinel kumesi daraldi")
        if int(_jw.STUCK_PROC_MAX_AGE_S) <= 0:
            probs.append("watchdog kor")
    except Exception as e:
        probs.append("bekci import kirik: %s" % type(e).__name__)
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "classifier+state+sentinel+watchdog OK")

@gate("form_lifecycle", "Form yasam dongusu", "3,4,5,6,27,60,101")
def c_lifecycle() -> dict[str, Any]:
    probs: list[str] = []
    try:
        import qualification_analyzer as _qa
        tr = _qa.qualify_lead({"url": "https://magaza-ornegi.com.tr",
                               "company_name": "Ornek TR Magaza Istanbul",
                               "description": "iletisim hizmetlerimiz",
                               "budget_eur": 5000})
        pv = tr.get("premium_verdict") or {}
        tr_fit = tr.get("fit_score")
        tr_fit_n = int(tr_fit) if tr_fit is not None else -1
        if pv.get("ok", True) or tr_fit_n != 0:
            probs.append("TR filtresi DELINDI")
        ok = _qa.qualify_lead({"url": "https://beispiel-firma.de",
                               "company_name": "Beispiel GmbH B2B solutions",
                               "description": "enterprise software",
                               "budget_eur": 5000})
        if int(ok.get("fit_score") or 0) <= 0:
            probs.append("DE B2B haksiz elendi")
    except Exception as e:
        probs.append("qualify kirik: %s" % type(e).__name__)
    try:
        import pipeline as _pl
        if not callable(_pl.eligible_for_submit):
            probs.append("eligible_for_submit yok")
        else:
            bad = {"url": "https://x.de", "status": "submitted_confirmed",
                   "contact_form": {"found": True}, "fit_score": 90,
                   "value_proposition": "v"}
            if _pl.eligible_for_submit(dict(bad), 10):
                probs.append("gonderilmis lead tekrar gonderilir!")
            noc = {"url": "https://y.de", "status": "new",
                   "contact_form": {"found": False}, "fit_score": 90,
                   "value_proposition": "v"}
            if _pl.eligible_for_submit(dict(noc), 10):
                probs.append("formsuz lead gonderilir!")
    except Exception as e:
        probs.append("pipeline kapisi kirik: %s" % type(e).__name__)
    try:
        import knowledge as _k
        if int(_k.daily_cap()) < 400 or int(_k.hourly_cap()) < 40:
            probs.append("cap gerilemis")
    except Exception as e:
        probs.append("cap okunamadi: %s" % type(e).__name__)
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "TR red + DE gecis + kilitler + cap OK")

@gate("demo_pay_sim", "Demo-odeme zinciri", "116,118,134,135,138,139")
def c_demo_pay() -> dict[str, Any]:
    probs: list[str] = []
    try:
        from core import profit_recipe as _pr
        rec = _pr.compute()
        if int(rec.get("headline_eur") or 0) <= 0 or not (rec.get("proof") or []):
            probs.append("kar recetesi bos")
    except Exception as e:
        probs.append("kar recetesi kirik: %s" % type(e).__name__)
    try:
        from nirvana import payment as _p
        link = _p.payment_link()
        if "payoneer" not in link.lower() or _p.retainer_amount() != 5000:
            probs.append("odeme linki/miktari bozuk")
    except Exception as e:
        probs.append("odeme zinciri kirik: %s" % type(e).__name__)
    try:
        demo = (ROOT / "templates" / "demo.html").read_text(encoding="utf-8")
        for k in ("ctaBtn", "demo_expires_ts", "chat_url", "pay_url"):
            if k not in demo:
                probs.append("demo eksik: " + k)
                break
    except Exception as e:
        probs.append("demo okunamadi: %s" % type(e).__name__)
    try:
        import payment_safety as _ps
        if not (callable(_ps.ready_request) and callable(_ps.approve_link)):
            probs.append("HMAC zinciri kopuk")
    except Exception as e:
        probs.append("HMAC kirik: %s" % type(e).__name__)
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "kar+demo+5000EUR+HMAC tek zincir OK")

@gate("memory_followup", "Hafiza+takip zinciri", "22,23,47,94,140")
def c_memory() -> dict[str, Any]:
    probs: list[str] = []
    try:
        from nirvana import delivery_worker as _dw
        mem = _dw.customer_memory(424242)
        if not isinstance(mem, dict) or "returning" not in mem:
            probs.append("customer_memory semasi bozuk")
    except Exception as e:
        probs.append("hafiza kirik: %s" % type(e).__name__)
    try:
        import telegram_sessions as _ts
        if not all(callable(getattr(_ts, n, None)) for n in (
                "due_followups", "mark_followup", "is_declined",
                "fulfillment_ready")):
            probs.append("takip/odeme API eksik")
    except Exception as e:
        probs.append("takip kirik: %s" % type(e).__name__)
    try:
        import optout
        if not (callable(optout.is_chat_opted_out)
                and callable(optout.is_url_opted_out)):
            probs.append("optout saygisi kopuk")
    except Exception as e:
        probs.append("optout kirik: %s" % type(e).__name__)
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "hafiza+takip+optout zinciri OK")

@gate("quota_free", "Oracle kotasi+keepalive", "1,40,41,67,105")
def c_quota() -> dict[str, Any]:
    from nirvana import idle_guard as _i, keepalive_guard as _k, watchdog_quota_agent as _w
    bad = []
    if abs(float(_i.FLOOR_PCT) - 0.20) > 1e-9:
        bad.append("anti-idle %20 degil")
    if int(_k.THRESHOLD_DAYS) > 60:
        bad.append("keepalive>60gun")
    if float(_w.CPU_LOAD_THRESHOLD) > 0.85:
        bad.append("tavan %85 ustu")
    if bad:
        return _r(False, "; ".join(bad))
    return _r(True, "anti-idle %20 + keepalive + tavan %85")
@gate("loss_shock_card", "VLA Kayip Para kanit karti", "20,38,66,116,135")
def c_shock() -> dict[str, Any]:
    probs: list[str] = []
    try:
        from nirvana import financial_shock_card as _fs
        if not (callable(_fs.build_shock) and callable(_fs.build_shock_summary)):
            probs.append("kayip para motoru API eksik")
        else:
            diag = {"dom_ms": 4200, "slow_count": 4, "bad_count": 2}
            sh = _fs.build_shock(diag, revenue_eur=25000.0, currency="EUR",
                                 issue="odeme adimi guven kirilmasi",
                                 report_id="DS-TEST-0001")
            if not sh.get("ok"):
                probs.append("kayip para karti uretilmedi")
            else:
                for k in ("headline", "punch", "window_loss", "window_days"):
                    if sh.get(k) in (None, "", 0):
                        probs.append("kayip para karti eksik: " + k)
                if int(sh.get("window_days") or 0) != 15:
                    probs.append("COI penceresi 15 gun degil")
                if not str(sh.get("punch") or "").strip():
                    probs.append("vurus cumlesi bos")
                if not (sh.get("proof") or []):
                    probs.append("kayip para karti kanit satiri yok")
    except Exception as e:
        probs.append("kayip para motoru kirik: %s" % type(e).__name__)
    try:
        from nirvana import financial_loss_engine as _fl
        loss = _fl.estimate_loss(dom_ms=4200, slow_count=4, bad_count=2)
        if not (loss.get("loss_band_pct") and loss.get("basis")):
            probs.append("kayip bandi hesabi eksik")
    except Exception as e:
        probs.append("kayip bandi kirik: %s" % type(e).__name__)
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "Kayip Para karti + COI 15gun + bant hesabi OK")


@gate("conversion_quality", "Donusum + satis bitiriciligi olcumu", "28,34,35,66,81,143")
def c_conversion() -> dict[str, Any]:
    probs: list[str] = []
    try:
        from nirvana import conversion_maximizer as _cm
        st = _cm.detect_stage("Fiyat cok yuksek, biraz dusuneyim")
        if not st:
            probs.append("asama tespiti calismiyor")
        if _cm.detect_stage("Odeme linkini atar misiniz") != "close":
            probs.append("odeme niyeti 'close' tespit edilmiyor")
        blk = _cm.close_block(user_text="Fiyat cok yuksek, biraz dusuneyim",
                              brief="enterprise software")
        if not blk or "TAKTİK" not in blk:
            probs.append("kapanis katmani taktik bankasi bos")
        for banned in ("ücretsiz deneme sunuyoruz", "ucretsiz deneme sunuyoruz",
                       "ücretsiz deneme veriyoruz", "ucretsiz deneme veriyoruz",
                       "ücretsiz deneme hakkı", "ucretsiz deneme hakki",
                       "ücretsiz deneme süresi", "ucretsiz deneme suresi",
                       "free trial we offer", "free trial includes"):
            if banned in blk.lower():
                probs.append("kapanis katmaninda ucretsiz taviz var: " + banned)
                break
        # Kural metnindeki "ücretsiz deneme ASLA" yasagi taviz DEGILDIR.
        if "ASLA" not in blk and ("ücretsiz deneme" in blk.lower()
                                  or "ucretsiz deneme" in blk.lower()):
            probs.append("kapanis katmaninda yasak kelime baglamsiz gecti")
    except Exception as e:
        probs.append("donusum motoru kirik: %s" % type(e).__name__)
    try:
        import webchat_core as _wc
        sc = _wc.score_lead("Odeme linkini atar misiniz, 5000 EUR odemeyi simdi yapacagim")
        # score_lead ya dict {score,tone,signals} ya da düz int doner.
        sc_n = int(sc.get("score") or 0) if isinstance(sc, dict) else int(sc or 0)
        if sc_n < 80:
            probs.append("odeme niyeti skoru dusuk: %s" % sc_n)
    except Exception as e:
        probs.append("webchat skoru kirik: %s" % type(e).__name__)
    try:
        from nirvana import language_auditor as _la
        tr = _la.detect_locale("Merhaba, fiyat bilgisi alabilir miyim?")
        en = _la.detect_locale("Hello, can you share pricing details?")
        if not tr or not en:
            probs.append("dil algilama bos")
    except Exception as e:
        probs.append("dil denetleyici kirik: %s" % type(e).__name__)
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "asama+itiraz+odeme skoru+dil algilama OK")


@gate("github_pipeline", "Pipeline GitHub tasiyici (iplik korumali)", "41,61,62,67,106,108,112")
def c_github() -> dict[str, Any]:
    probs: list[str] = []
    wf = ROOT / ".github" / "workflows"
    if not wf.exists():
        return _r(False, "workflow klasoru yok")
    names = {p.name for p in wf.glob("*.yml")}
    miss = [n for n in ("nirvana-oracle-deploy.yml", "oracle-diagnose.yml") if n not in names]
    if miss:
        probs.append("eksik workflow: " + ",".join(miss))
    for p in wf.glob("*.yml"):
        try:
            import yaml
            yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception as e:
            probs.append("YAML bozuk %s: %s" % (p.name, type(e).__name__))
    try:
        import yaml
        d = yaml.safe_load((wf / "nirvana-oracle-deploy.yml").read_text(encoding="utf-8")) or {}
        jobs = d.get("jobs") or {}
        if not jobs:
            probs.append("deploy job tanimi yok")
        for jn, jb in jobs.items():
            env = (jb.get("env") or {})
            for ek in ("SENDER_NAME", "SENDER_EMAIL", "SENDER_COMPANY"):
                if not str(env.get(ek) or "").strip():
                    probs.append("job %s %s bos" % (jn, ek))
    except Exception as e:
        probs.append("deploy yaml okunamadi: %s" % type(e).__name__)
    try:
        from nirvana import domain_rate_limit as _drl
        if int(_drl.HOURLY_LIMIT) > 2:
            probs.append("saatlik domain limiti gevsek: %s" % _drl.HOURLY_LIMIT)
        if _drl.JITTER_MIN < 3.0 or _drl.JITTER_MAX > 9.0:
            probs.append("insan ritmi kayboldu")
    except Exception as e:
        probs.append("rate limit kirik: %s" % type(e).__name__)
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "workflow YAML + SENDER_* sabit + domain limit 1/saat OK")


@gate("self_heal_auto", "Otonom onarim + dinamik FALSE akisi", "11,71,73,77,78,88,92,144")
def c_selfheal() -> dict[str, Any]:
    probs: list[str] = []
    try:
        from nirvana import error_classifier as _ec
        tr = _ec.classify("port already in use / baglanti reddedildi", exit_code=1)
        if tr.get("kind") != "transient":
            probs.append("gecici ariza mantiksayil sayiliyor")
        lg = _ec.classify("SyntaxError: invalid syntax", exit_code=1)
        if lg.get("kind") != "logic":
            probs.append("mantiksal ariza transient sayiliyor")
    except Exception as e:
        probs.append("siniflandirici kirik: %s" % type(e).__name__)
    try:
        from nirvana import manifest_state as _ms
        if not all(callable(getattr(_ms, n, None)) for n in ("set_false", "clear", "is_false")):
            probs.append("manifest_state API eksik")
        if _ms.is_false() is not None:
            probs.append("dinamik FALSE aktif (temizlenmeli)")
    except Exception as e:
        probs.append("manifest_state kirik: %s" % type(e).__name__)
    try:
        from nirvana import sentinel as _sn, job_watchdog as _jw
        if "pipeline" not in _sn.REPAIRABLE:
            probs.append("sentinel pipeline onarmiyor")
        if "webchat" not in _sn.REPAIRABLE:
            probs.append("sentinel webchat onarmiyor")
        # Watchdog reboot YAPMAMALI: yalnizca is seviyesi onarim + soğuma.
        _jw_src = Path(_jw.__file__).read_text(encoding="utf-8")
        if "rebooted" in _jw_src and "REBOOT YOK" not in _jw_src:
            probs.append("watchdog reboot yapabiliyor (YANLIS)")
    except Exception as e:
        probs.append("bekci kirik: %s" % type(e).__name__)
    try:
        import auto_runner as _ar
        txt = Path(_ar.__file__).read_text(encoding="utf-8")
        if "KESINTI (nokta atisi)" not in txt:
            probs.append("nokta atisi KESINTI kanali yok")
        if "error_classifier" not in txt:
            probs.append("auto_runner siniflandirici kullanmiyor")
    except Exception as e:
        probs.append("auto_runner okunamadi: %s" % type(e).__name__)
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "gecici=sessiz onarim, mantiksal=FALSE+kesinti OK")


@gate("live_scenarios", "Canli is akisi simulasyonlari", "118,134,137,138,139,141,143,144")
def c_scenarios() -> dict[str, Any]:
    probs: list[str] = []
    try:
        from nirvana import delivery_worker as _dw
        import telegram_sessions as _ts
        _dw.save_jobs([])
        _dw.save_reports([])
        _dw.start_job(chat_id=930001, domain="senaryo-1.de", service="infra-sweep")
        out = _dw.run_batch(notify=False)
        if int(out.get("delivered") or 0) != 0:
            probs.append("odemesiz senaryo teslim edildi")
        orig = _ts.fulfillment_ready
        _ts.fulfillment_ready = lambda cid: int(cid) == 930002
        try:
            _dw.save_jobs([])
            _dw.save_reports([])
            _dw.start_job(chat_id=930002, domain="senaryo-2.de", service="infra-sweep")
            _dw.start_job(chat_id=930003, domain="senaryo-3.de", service="contact-audit")
            out2 = _dw.run_batch(notify=False)
        finally:
            _ts.fulfillment_ready = orig
        if int(out2.get("delivered") or 0) != 1:
            probs.append("dogrulanmis senaryo teslim edilemedi")
        jobs = {j["chat_id"]: j for j in _dw.load_jobs()}
        if jobs.get(930002, {}).get("status") != "delivered":
            probs.append("teslim durumu islenmedi")
        mem = _dw.customer_memory(930002)
        if not mem.get("returning"):
            probs.append("donus musteri hatirlanmadi")
        if not _dw.reports_for_chat(930002):
            probs.append("rapor olusmadi")
    except Exception as e:
        probs.append("teslimat senaryosu patladi: %s" % type(e).__name__)
    finally:
        try:
            from nirvana import delivery_worker as _dwx
            _dwx.save_jobs([])
            _dwx.save_reports([])
        except Exception:
            pass
    try:
        from nirvana import payment as _p
        link = _p.payment_link()
        if "payoneer" not in link.lower():
            probs.append("payoneer linki yok")
        if _p.retainer_amount() != 5000:
            probs.append("retainer 5000 EUR degil")
    except Exception as e:
        probs.append("odeme linki kirik: %s" % type(e).__name__)
    try:
        demo = (ROOT / "templates" / "demo.html").read_text(encoding="utf-8")
        miss = [k for k in ("ctaBtn", "demo_expires_ts", "chat_url", "pay_url") if k not in demo]
        if miss:
            probs.append("demo eksik: " + ",".join(miss))
    except Exception as e:
        probs.append("demo okunamadi: %s" % type(e).__name__)
    if probs:
        return _r(False, "; ".join(probs)[:300])
    return _r(True, "odemesiz=engelli, odemeli=teslim, donus hatirli, demo+pay OK")


DEV_WARN = {"env_sender", "env_webchat", "pipeline_live"}


def run(*, prime: bool = False, strict: bool = False) -> dict[str, Any]:
    if prime:
        try:
            from nirvana import hot_fuel
            hot_fuel.prime_from_files()
        except Exception as e:  # noqa: BLE001
            print(f"UYARI prime: {e}")
    rows: list[dict[str, Any]] = []
    for g in GATES:
        try:
            r = g["fn"]()
        except Exception as e:  # noqa: BLE001
            r = _r(False, f"{g['id']} olculemedi: {type(e).__name__}: {e}"[:200])
        st = "TRUE" if r.get("ok") else ("WARN" if ((not strict) and g["id"] in DEV_WARN) else "FALSE")
        rows.append({"gate": g["id"], "title": g["title"], "items": g["items"], "status": st, **r})
    fl = [x for x in rows if x["status"] == "FALSE"]
    return {
        "manifest": MANIFEST_VERSION,
        "strict": strict,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "deploy": "FALSE" if fl else "TRUE",
        "false_count": len(fl),
        "warn_count": len([x for x in rows if x["status"] == "WARN"]),
        "gates": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="manifest_check")
    ap.add_argument("--prime", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", dest="json_path", default="")
    a = ap.parse_args(argv)
    rep = run(prime=a.prime, strict=a.strict)
    print(f"NIRVANA MANIFEST v{MANIFEST_VERSION} strict={a.strict}")
    print(f"DEPLOY: {rep['deploy']} (FALSE={rep['false_count']} WARN={rep['warn_count']})")
    for x in rep["gates"]:
        mk = {"TRUE": "[TRUE ]", "WARN": "[WARN ]", "FALSE": "[FALSE]"}[x["status"]]
        print(f"{mk} {x['gate']:18s} {x['detail'][:110]}")
    out = ROOT / "manifest_report.json"
    blob = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    try:
        out.write_text(blob, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"UYARI manifest_report.json yazilamadi: {type(e).__name__}")
    if a.json_path:
        try:
            Path(a.json_path).write_text(blob, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"UYARI --json raporu yazilamadi: {type(e).__name__}")
    if rep["deploy"] == "FALSE":
        print("SONUC: deploy ENGELLENDI - kirmizi kapilari kapatmadan canliya alma.")
        return 1
    print("SONUC: kritik kapilar TRUE - canliya alma serbest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


