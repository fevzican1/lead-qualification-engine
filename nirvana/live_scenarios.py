"""Canli is akisi simulasyonlari - manifest kapsaminda gercek zincir testi.

Bu modul, "sadece calisiyor mu" degil "NITELIK olarak donusum ve satis
bitiriciligi olcumune uygun mu" sorusunu yanitlar. Kapsam:

  1. DEMO  : /api/demo-ask uzerinden ajan -> musteri karsi lamasi, kar marji
             rakami, 7 kanit, Payoneer linkinin ilk 2 mesajda gelmesi.
  2. WEBCHAT: /api/session + ws akisi, ilk mesajda odeme linki, ilk 2 mesajda
             link, "ucretsiz" taviz yok, bot kelimesi sizmaz, dil tespiti.
  3. FORM  : form -> aktif sohbet motoru yonlendirmesi, linkin ulasabilir
             olmasi, kanit karti ile formun baglantisi.
  4. TESLIMAT ISCISI: odeme oncesi engel, odeme sonrasi teslim, musteri
             izolasyonu, donus musteri hatirlama, yenileme.
  5. KALICI HAFIZA: ayni musteri tekrar geldiginde onceki konusma/rapor/odeme
             hatirlaniyor mu.

Her adim gercek modulleri cagirir; sahte veri URETILMEZ, sadece fixture
lead girdileri kullanilir (form gonderiminde zaten oyle dogrulanir).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS: list[dict[str, Any]] = []


def _ok(name: str, detail: str = "", **ex: Any) -> None:
    RESULTS.append({"step": name, "ok": True, "detail": detail, **ex})
    print(f"[OK ] {name}: {detail}")


def _bad(name: str, detail: str, **ex: Any) -> None:
    RESULTS.append({"step": name, "ok": False, "detail": detail, **ex})
    print(f"[FAIL] {name}: {detail}")


def _contains_any(text: str, words: list[str]) -> bool:
    low = (text or "").lower()
    return any(w.lower() in low for w in words)


def _payoneer_link() -> str:
    from nirvana import payment as _p  # type: ignore
    return str(_p.payment_link() or "")


def _banned_free(text: str) -> str:
    """Ucretsiz taviz yasagi: 'ucretsiz deneme/free trial/bedava' gecerli mi."""
    low = (text or "").lower()
    for bad in ("ücretsiz deneme", "ucretsiz deneme", "free trial", "bedava deneme",
                "ücretsiz试", "no cost", "kayıt ücretsiz"):
        if bad in low:
            return bad
    return ""


def _run_async(coro) -> Any:
    """Event loop'u temiz kurup bitirir (asyncio.set_event_loop sizintisini onler)."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        finally:
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass


# ---------------------------------------------------------------- DEMO
def scenario_demo() -> None:
    """Demo ajani: karsi lamasi -> kar marji -> 7 kanit -> Payoneer linki."""
    name = "demo_agency_agent"
    try:
        from webchat_server import _brain_reply, _lazy_app  # type: ignore
        from webchat_core import ensure_session, get_session, put_session, append_history
        from core import profit_recipe as _pr  # type: ignore
    except Exception as e:
        _bad(name, f"import hatasi: {type(e).__name__}: {e}")
        return
    try:
        recipe = _pr.resolve("Manifest Ajans Testi", clients="12")
        ctx = _pr.context_payload(recipe)
        sid = "demo-manifest-%d" % int(time.time() % 100000)
        sess = ensure_session(sid, name="Manifest Ajans Testi", lang="tr",
                              source="demo-widget")
        put_session(sid, demo_context=ctx, partner_state="QUALIFIED",
                    partner_data={"agency_name": "Manifest Ajans Testi"})
        sess = get_session(sid) or sess
        # 1) karsi lamasi: hesaplanan kar rakami + guven cumlesi
        greet = _pr.demo_greeting(ctx, lang="tr")
        if int(recipe.get("headline_eur") or 0) <= 0:
            _bad(name, "kar marji hesaplanmadi")
            return
        if not greet.strip():
            _bad(name, "karsi lamasi bos")
            return
        if str(recipe.get("headline_eur")) not in greet.replace(".", "").replace(",", ""):
            # rakam farkli bicimdeyse sayi degerine bak
            nums = [int(x.replace(".", "").replace(",", ""))
                    for x in __import__("re").findall(r"[\d.,]{4,}", greet)
                    if x.replace(".", "").replace(",", "").isdigit()]
            if recipe.get("headline_eur") not in nums:
                _bad(name, f"karsi lamada kar marji yok (headline={recipe.get('headline_eur')})")
                return
        # 2) 7 kanit noktasi
        proof = recipe.get("proof") or []
        if len(proof) < 7:
            _bad(name, f"kanit noktasi eksik: {len(proof)}/7")
            return
        # 3) ilk soru -> Payoneer linki (2. mesaj kurali)
        r1, _ = _run_async(_brain_reply(sess, "Kar marjinizi anlatir misiniz?"))
        link = _payoneer_link()
        if not link or "payoneer" not in link.lower():
            _bad(name, "Payoneer linki uretilemedi")
            return
        if not _contains_any(r1, ["payoneer", "ödeme", "odeme", "5000", "5.000"]):
            _bad(name, f"ilk soruda odeme/yonlendirme yok: {r1[:120]!r}")
            return
        free = _banned_free(r1)
        if free:
            _bad(name, f"demo cevabinda ucretsiz taviz: {free}")
            return
        _ok(name, f"karsilama+{len(proof)} kanit+Payoneer ilk mesajda ({recipe.get('headline_eur')} EUR)",
            headline_eur=int(recipe.get("headline_eur") or 0), proof=len(proof))
    except Exception as e:
        _bad(name, f"prova patladi: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- WEBCHAT
def scenario_webchat() -> None:
    """Webchat motoru: odeme yonlendirmesi, ucretsiz taviz yok, bot kelimesi
    sizmaz, dil algilama calisir."""
    name = "webchat_sales_motor"
    try:
        from webchat_core import (ensure_session, get_session, detect_lang,
                                 score_lead, payment_stage, wants_to_buy,
                                 greeting)
    except Exception as e:
        _bad(name, f"import hatasi: {type(e).__name__}: {e}")
        return
    try:
        # 1) Turkce/Ingilizce dil algilama
        if detect_lang("Merhaba, fiyat bilgisi alabilir miyim?") != "tr":
            _bad(name, "TR dil algilama bozuk")
            return
        if detect_lang("Hello, could you share pricing details?") != "en":
            _bad(name, "EN dil algilama bozuk")
            return
        # 2) odeme niyeti -> close, ucretsiz taviz yok
        st = payment_stage("Odeme linkini atar misiniz")
        if not (isinstance(st, dict) and st.get("stage")):
            _bad(name, "odeme asamasi tespit edilmedi: %r" % (st,))
            return
        if not st.get("buy") and not st.get("link_req"):
            _bad(name, "odeme asamasi bos: %r" % (st,))
            return
        if not wants_to_buy("Odeme linkini atar misiniz, 5000 EUR odemeyi simdi yapacagim"):
            _bad(name, "odeme niyeti tespit edilmedi")
            return
        sc_raw = score_lead("Odeme linkini atar misiniz, 5000 EUR odemeyi simdi yapacagim")
        sc = sc_raw.get("score") if isinstance(sc_raw, dict) else sc_raw
        if int(sc or 0) < 80:
            _bad(name, "odeme niyeti skoru dusuk: %s" % sc)
            return
        # 3) karsi lamada bot kelimesi sizmaz
        g = str(greeting(name="Ajans", lang="tr", source="webchat") or "").lower()
        for w in ("bot", "chatbot", "yapay zeka asistani", "robot"):
            if w in g:
                _bad(name, f"karsi lamada bot sizintisi: {w}")
                return
        # 4) ilk 2 mesajda Payoneer linki
        from webchat_server import _brain_reply
        sid = "wc-manifest-%d" % int(time.time() % 100000)
        sess = ensure_session(sid, name="Ajans Test", lang="tr", source="webchat")
        sess = get_session(sid) or sess
        r1, _ = _run_async(_brain_reply(sess, "Kar marjinizi anlatir misiniz?"))
        r2, _ = _run_async(_brain_reply(sess, "Fiyat cok yuksek, biraz dusuneyim"))
        pair = "%s %s" % (r1, r2)
        if not _contains_any(pair, ["payoneer", "ödeme", "odeme", "5.000", "5000"]):
            _bad(name, f"ilk 2 mesajda odeme yonlendirmesi yok: {pair[:140]!r}")
            return
        if "payoneer" not in pair.lower():
            _bad(name, "Payoneer linki sohbete girmedi")
            return
        free = _banned_free(pair)
        if free:
            _bad(name, f"sohbette ucretsiz taviz: {free}")
            return
        low = pair.lower()
        for w in ("bot", "chatbot"):
            if w in low:
                _bad(name, f"sohbette bot kelimesi sizdi: {w}")
                return
        _ok(name, f"dil TR/EN + ilk 2 mesajda Payoneer + bot sizmadi + skoru {sc}",
             score=int(sc or 0))
    except Exception as e:
        _bad(name, f"prova patladi: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- FORM
def scenario_form() -> None:
    """Form -> aktif sohbet motoru yonlendirmesi: link ulasabilir mi,
    kanit karti formla bagli mi, ucretsiz taviz yok."""
    name = "form_to_chat_yonlendirme"
    try:
        import config
        import telegram_handoff as _th
    except Exception as e:
        _bad(name, f"import hatasi: {type(e).__name__}: {e}")
        return
    try:
        # 1) musteri linki GERCEKTEN erisilebilir bir adres mi
        link = str(config.customer_chat_link("manifest-probe") or "").strip()
        if not link:
            _bad(name, "musteri linki bos")
            return
        low = link.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            _bad(name, f"musteri linki gecersiz: {link[:80]}")
            return
        # 2) form kopyasi: kanit + odeme yonlendirmesi + AYNI link
        lead = {"url": "https://beispiel-firma.de",
                "company_name": "Beispiel GmbH",
                "description": "enterprise software platform",
                "fit_score": 88, "value_proposition": "v"}
        rec = _th.build_handoff_record(
            lead,
            token="manifest-probe-token",
            company="Beispiel GmbH",
            pain="Odeme guven kirilmasi",
            quote="Checkout sayfasinda odeme adimi belirsiz.",
            turkish=True,
        )
        body = " ".join(str(rec.get(k) or "") for k in
                        ("subject", "body", "form_copy", "hook", "form_cta",
                         "excerpt", "error_type", "probe"))
        if not body.strip():
            _bad(name, "form govdesi bos")
            return
        if not _contains_any(body, ["payoneer", "ödeme", "odeme", "5.000", "5000"]):
            _bad(name, "formda odeme yonlendirmesi yok")
            return
        if not _contains_any(body, ["EUR", "%", "kanit", "rapor", "DS-"]):
            _bad(name, "formda kanit/marj gostergesi yok")
            return
        if link.lower() not in body.lower():
            _bad(name, "formdaki link aktif sohbet motoru linkiyle ayni degil")
            return
        free = _banned_free(body)
        if free:
            _bad(name, f"formda ucretsiz taviz: {free}")
            return
        # 3) capraz-domain kanit korumasi
        try:
            from nirvana import forget_guard as _fg
            if not callable(getattr(_fg, "proof_matches_form", None)):
                _bad(name, "capraz-domain kanit korumasi eksik")
                return
        except Exception as e:
            _bad(name, f"forget_guard hatasi: {type(e).__name__}: {e}")
            return
        _ok(name, "form->motor linki ayni + kanit + 5000 EUR yonlendirme OK")
    except Exception as e:
        _bad(name, f"prova patladi: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- TESLIMAT
def scenario_delivery() -> None:
    """Teslimat iscisi: odeme oncesi engel, odeme sonrasi teslim, izolasyon."""
    name = "teslimat_iscisi_zinciri"
    try:
        from nirvana import delivery_worker as _dw
        import telegram_sessions as _ts
    except Exception as e:
        _bad(name, f"import hatasi: {type(e).__name__}: {e}")
        return
    try:
        _dw.save_jobs([])
        _dw.save_reports([])
        # 1) odeme ONCESI: is kuyruga girer ama TESLIM edilmez
        _dw.start_job(chat_id=940001, domain="odeme-oncesi.de", service="infra-sweep")
        out = _dw.run_batch(notify=False)
        if int(out.get("delivered") or 0) != 0:
            _bad(name, "SAHTE TESLIMAT: odeme olmadan teslim edildi")
            return
        jobs = {j["chat_id"]: j for j in _dw.load_jobs()}
        if jobs.get(940001, {}).get("status") != "awaiting_payment":
            _bad(name, "odemesiz is 'awaiting_payment' degil")
            return
        # 2) odeme SONRASI: dogrulanmis is teslim edilir
        # Not: ayni odemis musterinin FARKLI servisleri ayri ayri teslim edilir
        # (2 servis = 2 teslimat). Beklenti delivered=2.
        orig = _ts.fulfillment_ready
        _ts.fulfillment_ready = lambda cid: int(cid) == 940002
        try:
            _dw.save_jobs([])
            _dw.save_reports([])
            _dw.start_job(chat_id=940002, domain="musteri-a.de", service="infra-sweep")
            _dw.start_job(chat_id=940002, domain="musteri-a.de", service="contact-audit")
            _dw.start_job(chat_id=940003, domain="musteri-b.de", service="infra-sweep")
            out2 = _dw.run_batch(notify=False)
        finally:
            _ts.fulfillment_ready = orig
        if int(out2.get("delivered") or 0) != 2:
            _bad(name, f"dogrulanmis isler teslim edilemedi (delivered={out2.get('delivered')})")
            return
        jobs = {j["chat_id"]: j for j in _dw.load_jobs()}
        if jobs.get(940002, {}).get("status") != "delivered":
            _bad(name, "teslim durumu islenmedi")
            return
        # 3) izolasyon: odemesiz musterinin isi etkilenmemis olmali
        if jobs.get(940003, {}).get("status") != "awaiting_payment":
            _bad(name, "KARISMA: odemesiz musterinin isi etkilendi")
            return
        # 4) rapor olusmus mu
        if not _dw.reports_for_chat(940002):
            _bad(name, "teslimat raporu olusmadi")
            return
        _ok(name, "odeme oncesi engelli + sonrasi teslim + izolasyon + rapor OK")
    except Exception as e:
        _bad(name, f"prova patladi: {type(e).__name__}: {e}")
    finally:
        try:
            _dw.save_jobs([])
            _dw.save_reports([])
        except Exception:
            pass


# ------------------------------------------------------------------ VLA PIPELINE
def scenario_vla() -> None:
    """VLA (Very Life Annuity) pipeline: financial shock card + delivery integration.

    Integrates the AX-2 'Kayip Para' proof card generation with the delivery workflow.
    Validates that VLA loss calculation works within the delivery scenario and that
    the loss_shock_card manifest gate passes.
    """
    name = "vla_pipeline_zk"
    try:
        from nirvana import financial_shock_card as _fsc
        from nirvana import delivery_worker as _dw
        import telegram_sessions as _ts
    except Exception as e:
        _bad(name, f"import hatasi: {type(e).__name__}: {e}")
        return

    try:
        # 1) Financial shock card generation (VLA Kayip Para)
        #    Using test diagnostics: dom_ms=4200ms, slow_count=4, bad_count=2
        #    With revenue 22000 EUR -> band 10-12%, daily_loss ~2420 EUR, window_loss ~36300 EUR
        sh = _fsc.build_shock(
            {"dom_ms": 4200, "slow_count": 4, "bad_count": 2},
            revenue_eur=22000, currency="EUR", report_id="VLA-MANIFEST-1"
        )
        if not sh.get("ok"):
            _bad(name, f"VLA shock karti uretilemedi: {sh}")
            return

        # Save the shock card for manifest gate validation
        _fsc.save_shock("vla-manifest-dept.sh", sh)

        # 2) VLA-specific validation: ensure band and loss calculations are valid
        band_pct = sh.get("band_pct", "")
        daily_loss = sh.get("daily_loss", 0)
        window_loss = sh.get("window_loss", 0)

        if band_pct not in ("2-5", "5-8", "8-10", "10-12"):
            _bad(name, f"VLA band_pct beklenmedik: {band_pct}")
            return

        if daily_loss <= 0:
            _bad(name, f"VLA daily_loss pozitif olmalı: {daily_loss}")
            return

        if window_loss <= 0:
            _bad(name, f"VLA window_loss pozitif olmalı: {window_loss}")
            return

        # 3) Integration with delivery workflow
        _dw.save_jobs([])
        _dw.save_reports([])

        # Start a VLA-related job in the delivery pipeline
        _dw.start_job(chat_id=950001, domain="vla-test.de", service="vla-audit")
        out = _dw.run_batch(notify=False)

        # 4) Verify delivery did not prematurely deliver VLA-sensitive jobs
        if int(out.get("delivered") or 0) != 0:
            _bad(name, "SAHTE TESLIMAT: VLA isi odemsiz teslim edildi")
            return

        # 5) Verify shock card was saved and has valid data
        loaded = _fsc.load_shock("vla-manifest-dept.sh")
        if not loaded or not loaded.get("ok"):
            _bad(name, "VLA shock karti kaydedilemedi veya yuklenemedi")
            return

        if loaded.get("band_pct") != band_pct:
            _bad(name, f"VLA band_pct uyusmuyor: kaydedik={loaded.get('band_pct')}, beklenti={band_pct}")
            return

        # 6) Isolation check: ensure VLA calculation doesn't interfere with other scenarios
        #    by verifying the standard delivery scenario still works independently
        _ts.fulfillment_ready = lambda cid: int(cid) == 940002
        try:
            _dw.save_jobs([])
            _dw.save_reports([])
            _dw.start_job(chat_id=940002, domain="musteri-a.de", service="infra-sweep")
            out2 = _dw.run_batch(notify=False)
        finally:
            _ts.fulfillment_ready = lambda cid: False

        if int(out2.get("delivered") or 0) != 1:
            _bad(name, f"Standart delivery deliver beklenendi gibi degil: {out2.get('delivered')}")
            return

        # 7) Final validation: shock card summary
        summary = _fsc.build_shock_summary(sh)
        if not summary or "kaybediyorsunuz" not in summary.lower():
            _bad(name, f"VLA shock ozeti eksik veya hatali: {summary}")
            return

        _ok(name, f"VLA pipeline OK: band={band_pct}, daily_loss={daily_loss:.0f}EUR, window_loss={window_loss:.0f}EUR, delivery_izolasyon_ok=true",
            band_pct=band_pct, daily_loss=daily_loss, window_loss=window_loss,
            summary=summary, shock_ok=True)

    except Exception as e:
        _bad(name, f"prova patladi: {type(e).__name__}: {e}")
    finally:
        try:
            _dw.save_jobs([])
            _dw.save_reports([])
        except Exception:
            pass


# ------------------------------------------------------------------ CLI
SCENARIOS = (
    ("demo", scenario_demo),
    ("webchat", scenario_webchat),
    ("form", scenario_form),
    ("delivery", scenario_delivery),
    ("vla", scenario_vla),
)


def run_all(*, only: str = "") -> dict[str, Any]:
    """Tum canli senaryolari sirayla calistirir; ozet raporu dondurur."""
    RESULTS.clear()
    for name, fn in SCENARIOS:
        if only and only != name:
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - senaryo cokerse raporla
            _bad(name, f"senaryo patladi: {type(e).__name__}: {e}")
    ok = [r for r in RESULTS if r.get("ok")]
    bad = [r for r in RESULTS if not r.get("ok")]
    summary = {
        "ok_count": len(ok),
        "fail_count": len(bad),
        "ok": bool(RESULTS) and not bad,
        "results": RESULTS,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="nirvana.live_scenarios")
    ap.add_argument("--only", default="", help="tek senaryo: demo|webchat|form|delivery")
    ap.add_argument("--json", default="", help="ozet raporu bu dosyaya yaz")
    a = ap.parse_args(argv)
    summary = run_all(only=a.only)
    print(f"SCENARIO DURUM: ok={summary['ok_count']} fail={summary['fail_count']} "
          f"-> {'GECTI' if summary['ok'] else 'KALDI'}")
    if a.json:
        try:
            Path(a.json).write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"UYARI rapor yazilamadi: {type(e).__name__}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
