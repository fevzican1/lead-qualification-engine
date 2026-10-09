"""BOLUM 3+4 kar recetesi regresyon kapilari: demo -> webchat -> bildirim ayni rakam."""
from __future__ import annotations
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent


def test_compute_default_recipe_is_22000_band():
    from core import profit_recipe as p
    r = p.compute()
    assert r["headline_eur"] == 22000
    assert r["first_month_eur"] == 17800.0
    assert r["band_ok"] is True
    assert 15000 <= 22000 <= 30000
    assert "12 musteri" in r["formula"] and "22.000" in r["formula"]


def test_recipe_rows_have_sources_and_no_forced_band():
    from core import profit_recipe as p
    # Banda ZORLAMA YOK: kucuk portfoyde rakam bandin disinda kalir, oldugu gibi gosterilir.
    r = p.compute(clients=3, clients_source="test")
    assert r["headline_eur"] < 15000
    assert r["band_ok"] is False
    assert all(row.get("source") for row in r["proof"])


def test_resolve_wins_over_url_profit_and_persists(tmp_path, monkeypatch):
    from core import profit_recipe as p
    store = tmp_path / "recipes.json"
    monkeypatch.setattr(p, "PATH", store)
    rec = p.resolve("Nova Medya", clients="12", requested_profit="99999")
    assert rec["headline_eur"] == 22000          # URL'deki 99999 KAYBEDER
    assert rec["profit_mismatch"] is True
    again = p.recall("Nova Medya")
    assert again and again["headline_eur"] == 22000   # farkli istemci ayni rakami gorur
    rec2 = p.resolve("Nova Medya", requested_profit="22000")
    assert rec2["profit_mismatch"] is False


def test_demo_greeting_and_context_block_carry_the_number():
    from core import profit_recipe as p
    rec = p.resolve("Nova Medya", clients="12")
    ctx = p.context_payload(rec)
    g = p.demo_greeting(ctx, lang="tr")
    assert "Nova Medya" in g and "22.000" in g and "Operasyon bizde" in g
    b = p.context_block(ctx, lang="tr")
    assert "[KÂR REÇETESİ" in b and "ASLA" in b and "22.000" in b
    url = p.chat_url(ctx)
    assert "agency=Nova+Medya" in url and "profit=22000" in url


def test_demo_page_renders_bolum3_layout():
    from core import profit_recipe as p
    rec = p.resolve("Nova Medya", clients="12")
    tpl = (ROOT / "templates" / "demo.html").read_text(encoding="utf-8")
    html = p.render_page(rec, tpl)
    assert "Nova Medya İçin Hazırlanan Özel Whitelabel Kâr Reçetesi" in html
    assert "22.000" in html and "12 Aktif Müşteri" in html
    assert "Operasyon bizden" in html
    assert "Whitelabel Lisansını Aktif Et" in html
    assert "ctaBtn" in html and "/chat?" in html
    assert "${" not in html  # yer tutucusuz render


def test_demo_payoneer_trio_renders():
    """Payoneer uclusu: sabit CTA + 35 saat bandi + webhook tedarik hatti."""
    from core import profit_recipe as p
    rec = p.resolve("Nova Medya", clients="12")
    tpl = (ROOT / "templates" / "demo.html").read_text(encoding="utf-8")
    html = p.render_page(rec, tpl)
    assert "paybar" in html and "payTimer" in html          # 1) sayacli banner
    assert "sticky-cta" in html and "Paneli Kalici Yap" in html  # 2) sabit buton
    assert "link.payoneer.com" in html                      # canli odeme linki
    assert "${" not in html
    assert p.demo_expires_ts() > int(__import__("time").time()) + 34 * 3600
    src = (ROOT / "ingest_api.py").read_text(encoding="utf-8")
    assert "provision_paid_chat" in src                     # 3) webhook->tedarik


def test_demo_widget_hits_live_brain():
    """Satis widget'i CANLI beyne bagli: once /api/demo-ask, dusus yerel."""
    html = (ROOT / "templates" / "demo.html").read_text(encoding="utf-8")
    assert "saleWidget" in html and "/api/demo-ask" in html
    src = (ROOT / "webchat_server.py").read_text(encoding="utf-8")
    assert '"/api/demo-ask"' in src and "_brain_reply" in src
    assert "demo-widget" in src and "demo_context" in src


def test_router_appends_recipe_and_prompt_has_closing():
    from core import llm_router as r
    src = (ROOT / "core" / "llm_router.py").read_text(encoding="utf-8")
    assert "context_block" in src and "demo_context" in src
    txt = (ROOT / "config" / "prompts" / "agency_partner.txt").read_text(encoding="utf-8")
    assert "KAPANIS" in txt and "Payoneer" in txt and "en gec 2. mesajda" in txt
    assert r.AGENCY_MODEL == "qwen2.5:3b"


def test_webchat_seeds_demo_and_closes():
    src = (ROOT / "webchat_server.py").read_text(encoding="utf-8")
    assert '"/demo"' in src and "demo_context" in src and "agency_msg_count" in src
    assert "hesaplanan kar=" in src
    html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    assert 'q.get("agency")' in html and "agency:agency" in html


def test_notifier_profit_line_keeps_default_shape():
    from services import notifier as n
    msg = n.partner_message("Nova", "info@x.com", "https://pay.link/1")
    assert "YENI B2B IS ORTAGI" in msg and "Whitelabel Retainer" in msg
    msg2 = n.partner_message("Nova", "info@x.com", "https://pay.link/1", profit_eur="22000")
    assert "22000" in msg2
