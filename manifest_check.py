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
    age = heartbeat.age_seconds("auto_runner")
    if age is None:
        return _r(False, "kalp atisi YOK - auto_runner tur atmadi")
    if age > 180:
        return _r(False, f"kalp atisi gecikmeli ({age:.0f}s>180s)", age_s=round(age, 1))
    return _r(True, f"kalp atisi {age:.0f}s once", age_s=round(age, 1))
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

DEV_WARN = {"env_sender", "env_webchat", "pipeline_live"}
def run(*, prime: bool = False, strict: bool = False) -> dict[str, Any]:
    if prime:
        try:
            from nirvana import hot_fuel
            hot_fuel.prime_from_files()
        except Exception as e:
            print(f"UYARI prime: {e}")
    rows: list[dict[str, Any]] = []
    for g in GATES:
        try:
            r = g["fn"]()
        except Exception as e:
            r = _r(False, f"{g['id']} olculemedi: {type(e).__name__}: {e}"[:200])
        st = "TRUE" if r.get("ok") else ("WARN" if ((not strict) and g["id"] in DEV_WARN) else "FALSE")
        rows.append({"gate": g["id"], "title": g["title"], "items": g["items"], "status": st, **r})
    fl = [x for x in rows if x["status"] == "FALSE"]
    return {"manifest": MANIFEST_VERSION, "strict": strict, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "deploy": "FALSE" if fl else "TRUE", "false_count": len(fl), "warn_count": sum(1 for x in rows if x["status"] == "WARN"), "gates": rows}

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
    # Dayaniklilik: kapiyi root ile kossak bile sonraki non-root kosu raporu
    # yazamazsa manifest patlamasin — yazim hatasi yalnizca UYARI.
    try:
        out.write_text(blob, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"UYARI manifest_report.json yazilamadi: {type(e).__name__} (root sahipli olabilir)")
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

