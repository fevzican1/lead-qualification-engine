"""Kök neden regresyon testleri — 2026-10-14 kesinti/yakıt/demo arızası.

Üç canli ariza bu dosyada kilitlenir:
1. yakıt sayaci 400'lük sabit tarama limitine takiliyordu (hedef 500 — asla
   ulasilamiyordu) → her tur "yakıt ince" + gereksiz feed senkronu;
2. 600s duvar-saati SIGKILL turu ortadan kesiyordu → "0 onayli form + KESINTI";
3. gonderilen form metninde/copy'da canli demo linki YOKTU.
"""
from __future__ import annotations

import json
from pathlib import Path

import config
import domain_store

ROOT = Path(__file__).resolve().parents[1]
LIVE = "http://141.253.144.36:8765"


def _isolate_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(domain_store, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(domain_store, "PROCESSED_PATH", tmp_path / "processed.json")
    monkeypatch.setattr(domain_store, "BUDGET_PATH", tmp_path / "budget.json")


def _write_queue(path: Path, n: int, *, easy: int = 70) -> None:
    rows = []
    for i in range(n):
        rows.append(
            {
                "url": f"https://fuelhost{i}.example.com/contact",
                "source": "feed",
                "authorized_contact": True,
                "queued_at": "2026-10-14T00:00:00+00:00",
                "fails": 0,
                "easy_score": easy,
                "form_verified": True,
            }
        )
    path.write_text(
        json.dumps({"urls": rows, "updated_at": "2026-10-14T00:00:00+00:00"},
                   ensure_ascii=False),
        encoding="utf-8",
    )


# --- 1) Yakıt sayaci: hedef ulasilabilir, 400 tavanı YOK ----------------------

def test_fuel_count_reaches_target_above_old_400_cap(tmp_path, monkeypatch):
    """550 hazir form_verified satirda sayac >= hedef (>=500) olmali.

    Eski davranis: pending_rows(limit=400) → sayac TAKSIM 400'de kalmisti,
    FUEL_TARGET (500) hicbir zaman dolmazdi → auto_runner her tur "yakıt ince"
    deyip feed senkronu yapiyordu.
    """
    _isolate_queue(tmp_path, monkeypatch)
    _write_queue(tmp_path / "queue.json", 550)
    target = domain_store.chromium_fuel_target()
    assert target >= 500, "fuel hedefi operator seviyesi (500) altina dusmemeli"
    count = domain_store.chromium_fuel_count()
    assert count >= target, f"fuel {count} < target {target} — tarama limiti hedefi kirpiyor"
    assert count > 400, "eski 400'lük tapani geri geldi (kok neden 1)"


def test_fuel_count_counts_only_form_verified_rows(tmp_path, monkeypatch):
    """Sayaç yalnizca form_verified / authorized_contact satirlari sayar."""
    _isolate_queue(tmp_path, monkeypatch)
    _write_queue(tmp_path / "queue.json", 100, easy=70)
    assert domain_store.chromium_fuel_count() == 100
    _write_queue(tmp_path / "queue.json", 50, easy=30)
    assert domain_store.chromium_fuel_count() == 0



# --- 2) Yumusak tur butcesi: SIGKILL yerine temiz kapanis ---------------------

def test_pipeline_soft_deadline_is_wired_before_hard_kill():
    """pipeline.py her lead oncesinde sure kontrol eder; 4 cagri noktasina
    deadline_at iletilir; butce hard-kill'in 60s ALTINDA biter."""
    src = (ROOT / "pipeline.py").read_text(encoding="utf-8")
    assert "deadline_at: float | None = None" in src
    assert src.count("deadline_at=deadline_at") >= 4, (
        "tum _run_browser_pipeline cagri noktalarina deadline iletilmeli"
    )
    assert "Soft round budget" in src, "tur dongusu yumusak butcede temiz kapanmali"
    assert "Soft round deadline reached" in src, (
        "dilim icinde sonraki lead'e baslamadan once sure kontrolu yok"
    )
    assert 'PIPELINE_RUN_TIMEOUT_SECONDS", 600) or 600) - 60' in src, (
        "yumusak butce hard-kill'den en az 60s once bitmeli"
    )


def test_auto_runner_hard_timeout_still_backstops():
    """Hard-kill SON savunma olarak kalir (asilmayi yakalar) ama 600s'yi asmaz."""
    src = (ROOT / "auto_runner.py").read_text(encoding="utf-8")
    assert 'timeout=int(getattr(config, "PIPELINE_RUN_TIMEOUT_SECONDS"' in src
    assert int(config.PIPELINE_RUN_TIMEOUT_SECONDS) <= 600, (
        "600s kurali (config clamp) korunmali — asim hattini yeniden acmaz"
    )


def test_job_watchdog_age_margin_not_breached():
    """Yumusak kapanis (<=540s) job_watchdog kacak-yas sinirinin (2700s) cok altinda."""
    assert int(config.PIPELINE_RUN_TIMEOUT_SECONDS) - 60 < 2700


# --- 3) Canli demo linki: gonderilen form metninde + CTA'da --------------------

def test_config_customer_demo_link_single_source(monkeypatch):
    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", LIVE)
    assert config.customer_demo_link() == f"{LIVE}/demo"
    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", "")
    assert config.customer_demo_link() == ""


def test_form_message_carries_demo_line(monkeypatch):
    """Gonderilen form METNINE demo satiri kendi satirinda eklenir (idempotent)."""
    import form_submitter

    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", LIVE)
    out = form_submitter.with_demo_line("Merhaba, checkout akisinda kopukluk var. STOP")
    assert f"{LIVE}/demo" in out
    assert out.splitlines()[-1].startswith("Canlı demo sayfası:")
    # ikinci kez uygulanmaz (form_cta zaten eklediyse tekrar eklenmez)
    assert form_submitter.with_demo_line(out).count("/demo") == 1
    # bos mesaj bos kalir — zorunlu alan tamamlama yolu (_sender_values("", "")) bozulmaz
    assert form_submitter.with_demo_line("") == ""
    # WEBCHAT_PUBLIC_URL yoksa kopya degismez (gecis donemi uyumlulugu)
    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", "")
    assert form_submitter.with_demo_line("Pitch STOP") == "Pitch STOP"


def test_sender_values_message_carries_demo(monkeypatch):
    """Tum gonderim yollari (fill + ajax + mailto) gecen paylasilan map."""
    import form_submitter

    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", LIVE)
    vals = form_submitter._sender_values("Pitch STOP", "Konu")
    assert f"{LIVE}/demo" in vals["message"]
    assert form_submitter._sender_values("", "")["message"] == ""


def test_form_cta_carries_demo_in_customer_variants(monkeypatch):
    import telegram_handoff as th

    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", LIVE)
    chat = f"{LIVE}/chat?sid=abc"
    tr = th.form_cta(link=chat, turkish=True, domain="ornek.com")
    en = th.form_cta(link=chat, turkish=False, domain="ornek.com")
    assert f"{LIVE}/demo" in tr
    assert f"{LIVE}/demo" in en
    # demo linki ayri satirda (form UI'larinda tiklanabilir)
    assert any(line.startswith("Canlı demo sayfası:") for line in tr.splitlines())
    # kurumsal basvuru (variant X) satis CTA'si degil — demo satiri yok
    x = th.form_cta(link=chat, turkish=True, domain="ornek.com", variant="X")
    assert "/demo" not in x


def test_form_copy_body_carries_demo_and_survives_auditor(monkeypatch):
    """form_copy govdesi (Dil Bekçisi'nden SONRA) demo satirini korumali."""
    import telegram_handoff as th

    monkeypatch.setattr(config, "WEBCHAT_PUBLIC_URL", LIVE)
    subject, body = th.form_copy(
        host="ornek.com",
        hints=["woocommerce", "php"],
        link=f"{LIVE}/chat?sid=abc",
        turkish=True,
    )
    assert subject
    assert f"{LIVE}/demo" in body, "Dil Bekçisi demo linkini silmemeli (_webchat_allowed)"


def test_chat_template_has_demo_bridge():
    """Sohbet ekranindan demo sayfasina tek tik koprusu."""
    html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    assert "demoBtn" in html
    assert 'location.href="/demo"' in html

