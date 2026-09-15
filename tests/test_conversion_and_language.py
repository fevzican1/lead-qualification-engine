"""
Dönüşüm Maksimize Edici + Dil Bekçisi testleri.
"""

from __future__ import annotations

import pytest


# --- Dönüşüm Maksimize Edici -------------------------------------------------

def test_detect_stage_tr_en():
    from nirvana.conversion_maximizer import detect_stage

    assert detect_stage("kabul ediyorum, ödemeyi yapalım") == "close"
    assert detect_stage("I want to pay now") == "close"
    assert detect_stage("fiyat biraz pahalı bence") == "objection_price"
    assert detect_stage("daha sonra dönüş yaparım") == "objection_delay"
    assert detect_stage("fiyat ne kadar?") == "value"
    assert detect_stage("kanıt var mı, raporu göster") == "proof"
    assert detect_stage("acil, bugün görüşebilir miyiz?") == "urgency"
    assert detect_stage("merhaba") == "curiosity"


def test_intent_pacing_low_never_offers_payment():
    from nirvana.conversion_maximizer import close_block, intent_level

    assert intent_level("kendinizi tanıtır mısınız?") == "low"
    assert intent_level("fiyat nedir?") == "mid"
    assert intent_level("kabul ediyorum") == "high"
    block = close_block(user_text="kendinizden bahsedin", brief=None)
    assert "ASLA" in block  # düşük niyette ödeme sözü yasak
    assert "tek CTA" in block
    block_high = close_block(user_text="kabul ediyorum, başlayalım", brief=None)
    assert "YÜKSEK NİYET" in block_high


def test_loss_framing_never_invents_and_uses_live_price():
    from nirvana.conversion_maximizer import loss_framing

    line = loss_framing(None, turkish=True)
    assert "2500" in line  # tek kaynak: nirvana.payment
    assert "amorti" in line


def test_tactics_fed_from_external_overlay():
    import knowledge
    from nirvana.conversion_maximizer import _pick_tactics

    tactics = knowledge.live_tactics()
    assert any(str(t.get("weight")) == "3" for t in tactics)  # conversion.json yüklü
    top = _pick_tactics("close")
    assert top and str(top[0].get("weight")) in ("4", "3")  # ağırlığa göre sıralı (en yüksek önce)


def test_run_batch_reports(tmp_path, monkeypatch):
    from nirvana import conversion_maximizer as cm
    from nirvana import registry

    monkeypatch.setattr(registry, "state_path", lambda name: tmp_path / name)
    cm.record(stage="close", intent="high", segment="smb", chat_id=1, n_tactics=2)
    cm.record(stage="curiosity", intent="low", segment="smb", chat_id=2, n_tactics=3)
    summary = cm.run_batch()
    assert summary["events"] == 2
    assert summary["tactic_bank"] > 0
    assert (tmp_path / "conversion_summary.json").exists()


# --- Dil Bekçisi --------------------------------------------------------------

def test_bot_leak_sentences_removed():
    from nirvana.language_auditor import audit

    reply, issues = audit(
        "Ben bir yapay zeka modeliyim. Sitenizde ölçülen darboğaz checkout hunisinde "
        "kayıp üretiyor. Sistem otomasyonla tespit etti.",
        turkish=True,
    )
    assert "yapay zeka" not in reply.lower()
    assert "otomasyonla" not in reply.lower()
    assert "darboğaz" in reply  # temiz cümle korunur
    assert "bot-leak:sentence-removed" in issues


def test_free_offer_never_passes():
    from nirvana.language_auditor import audit

    reply, issues = audit(
        "Ücretsiz deneme başlatabiliriz. Ölçülen kayıp aylık ciro riski taşıyor.",
        turkish=True,
    )
    assert "ücretsiz" not in reply.lower()
    assert "indirim" not in reply.lower()
    assert "free-offer:sentence-removed" in issues


def test_spelling_and_tone_fixes():
    from nirvana.language_auditor import audit

    reply, issues = audit("  yalnış ölçüm bilgileriniz gecmemis  ")
    assert "anlış" in reply  # yalnış→yanlış (ilk harf büyütülmüş olabilir)
    assert reply[0].isupper()
    assert reply.endswith(".")
    assert any(i.startswith("spelling:") for i in issues)


def test_unallowed_urls_stripped_payoneer_kept():
    from nirvana.language_auditor import audit

    reply, issues = audit(
        "Rapor hazır: https://link.payoneer.com/Token?t=ABC ve https://evil.example/x",
    )
    assert "payoneer" in reply
    assert "evil.example" not in reply
    assert "url:unallowed-removed" in issues


def test_locale_detection_from_html_and_words():
    from nirvana.language_auditor import detect_locale, greeting, locale_for_lead

    assert detect_locale('<html lang="de-DE">') == "de"
    assert detect_locale("Impressum Kontakt Wir über uns") == "de"
    assert detect_locale("İletişim Hakkımızda Kurumsal") == "tr"
    assert detect_locale("", fallback="en") == "en"
    assert greeting("de") == "Guten Tag,"
    assert locale_for_lead({"html": '<html lang="fr">bonjour'}) == "fr"


def test_language_guard_run_batch(tmp_path, monkeypatch):
    from nirvana import language_auditor as la
    from nirvana import registry

    monkeypatch.setattr(registry, "state_path", lambda name: tmp_path / name)
    report = la.run_batch()
    assert report["checked"] == 4
    assert report["issues"]  # örnek korpus kasıtlı hatalı
