"""CRO koprusu regresyon kapisi — v2 §8.5 (webchat ilk mesaj kisisellestirme) + v1 §4.1.

Kapsar: profil anahtari turetimi (form > brief > ad), onbellek-only icgoru
(AG YOK, fail-open), karsilama kisisellestirme, prompt sirket profili blogu,
oturumda profile_key/source saklama + eski oturumlara sessiz tamamlama,
chat.html CRO ogeleri (ROI hesaplayici, exit-intent, dwell, UTM) ve config bayragi.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import webchat_core as core

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """Oturum deposunu izole et + agsiz test icin zenginlestirme isitmayi kapat."""
    monkeypatch.setattr(core, "SESSIONS_PATH", tmp_path / "webchat_sessions.json")
    monkeypatch.setattr(core, "_loaded", False)
    monkeypatch.setattr(core, "_enrich_enabled", lambda: False)
    core._sessions.clear()
    yield tmp_path
    core._sessions.clear()
    monkeypatch.setattr(core, "_loaded", False)


def test_derive_profile_key_prefers_form_then_brief_then_name():
    assert core.derive_profile_key(form={"company": "Shop Example"}) == "shop example"
    assert core.derive_profile_key(form={"website": "https://Acme.io/about"}) == "acme.io"
    assert core.derive_profile_key(brief="Şirket: Beta Makine A.Ş.\nBulgu: crm").startswith("beta")
    assert core.derive_profile_key(brief="Url: https://gamma.example/path") == "gamma.example"
    assert core.derive_profile_key(name="") == ""


def test_profile_insight_reads_cache_without_network(tmp_path, monkeypatch):
    from nirvana import enrich_web

    cache = tmp_path / "enrich_cache.json"
    monkeypatch.setattr(enrich_web, "CACHE_PATH", cache)
    assert core.profile_insight("acme.io") == ""  # miss => "" (fail-open)
    cache.write_text(json.dumps({"acme.io": {
        "at": time.time(),
        "markdown": "# Acme\n\nAcme e-ticaret entegrasyonlari ve odeme akislari kurar.",
    }}), encoding="utf-8")
    ins = core.profile_insight("acme.io")
    assert "Acme e-ticaret" in ins and len(ins) <= 160
    # TTL gecmis kayit bos doner (eski profil kullanilmaz).
    cache.write_text(json.dumps({"acme.io": {
        "at": 1.0, "markdown": "Cok eski bir sirket profili satiri burada duruyor.",
    }}), encoding="utf-8")
    monkeypatch.setattr(enrich_web, "CACHE_TTL_S", 10.0)
    assert core.profile_insight("acme.io") == ""


def test_enrich_insight_skips_noise_lines():
    from nirvana import enrich_web

    md = "# Title\nhttps://noise.example\nMenu Home Contact\nAcme lojistik yazilimi gelistirir ve API entegrasyonu sunar."
    ins = enrich_web.insight(md)
    assert ins.startswith("Acme lojistik")
    assert enrich_web.insight("") == ""


def test_greeting_personalizes_with_insight_and_source():
    tr = core.greeting(name="Shop", lang="tr", seeded=True,
                       insight="Acme B2B entegrasyonlari kurar.", source="linkedin.com")
    assert "Merhaba" in tr and "linkedin.com" in tr and "Acme" in tr
    en = core.greeting(name="Shop", lang="en", seeded=False,
                       insight="Acme builds integrations.")
    assert "Hello" in en and "Acme" in en
    plain = core.greeting(name="", lang="tr", seeded=False)
    assert "Merhaba" in plain and "Sitenizi" not in plain


def test_create_session_stores_profile_key(isolated_store):
    row = core.create_session(name="Shop Example", lang="tr", brief="x",
                              form={"company": "Shop Example"})
    assert row["profile_key"] == "shop example" and "source" in row
    row2 = core.create_session(name="", brief="", form={"website": "https://Acme.io"})
    assert row2["profile_key"] == "acme.io"


def test_ensure_session_backfills_profile_key(isolated_store):
    core.create_session(sid="dsKEEP1", name="", lang="tr", brief="b")
    assert core.get_session("dsKEEP1")["profile_key"] == ""
    row = core.ensure_session("dsKEEP1", profile_key="newco.io", source="google")
    assert row["profile_key"] == "newco.io" and row["source"] == "google"
    # Mevcut kimlik uzerine yazilmaz.
    row2 = core.ensure_session("dsKEEP1", profile_key="other.io", source="bing")
    assert row2["profile_key"] == "newco.io" and row2["source"] == "google"


def test_build_prompt_includes_cached_company_profile(tmp_path, monkeypatch):
    from nirvana import enrich_web

    cache = tmp_path / "enrich_cache.json"
    monkeypatch.setattr(enrich_web, "CACHE_PATH", cache)
    cache.write_text(json.dumps({"acme.io": {
        "at": time.time(),
        "markdown": "Acme lojistik yazilimi gelistirir ve API entegrasyonu sunar.",
    }}), encoding="utf-8")
    prompt = core.build_prompt(name="Acme", lang="tr", profile_key="acme.io", source="linkedin.com")
    assert "[SIRKET PROFILI]" in prompt and "Acme lojistik" in prompt
    assert "[KAYNAK] linkedin.com" in prompt
    empty = core.build_prompt(name="Acme", lang="tr", profile_key="", source="")
    assert "[SIRKET PROFILI]" not in empty


def test_chat_html_has_cro_magnets():
    html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    for marker in ("roiBtn", "roiGain", "roiCost", "roiSend", "exitCard", "exitYes",
                   "utm_source", "mouseleave", "setTimeout", "sendText"):
        assert marker in html, marker
    assert "document.referrer" in html


def test_config_flag_default_on():
    import config

    assert config.ENRICH_WEBCHAT_ENABLED is True
