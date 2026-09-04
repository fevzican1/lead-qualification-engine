import sys

sys.path.insert(0, r"C:\Users\Lenovo\lead-qualification-engine-2")
import config  # noqa: E402
import proof_card  # noqa: E402
import telegram_handoff  # noqa: E402

# 1) X (enterprise contractor) form metni — EN
subject, note = telegram_handoff.form_copy(
    host="shopifypartners.example",
    hints=[],
    link=f"https://t.me/{config.TELEGRAM_BOT_USERNAME or 'B2B_SalesAssistant_Bot'}?start=dsTESTX01",
    turkish=False,
    platform="Shopify",
    confidence=95,
    audience="enterprise",
)
ok_link_early = "t.me/" in note[:420]
ok_link_own = any(line.strip().startswith("https://t.me/") for line in note.splitlines())
ok_end = note.rstrip().endswith("dsTESTX01")
print("SUBJECT:", subject[:80])
print("LINK_EARLY:", ok_link_early, "| LINK_OWN_LINE:", ok_link_own, "| LINK_AT_END:", ok_end)
print("X_TALK:", ("contract" in note.lower() or "pilot" in note.lower() or "application" in note.lower()))

# 2) X proof card render (Pillow yoksa None döner, hata olmamalı)
row = {
    "company": "Shopify Partners",
    "host": "shopifypartners.example",
    "url": "https://shopifypartners.example",
    "turkish": False,
    "variant": "X",
    "audience": "enterprise",
    "pain": "contract integration-engineer need",
    "quote": "",
    "session_token": "dsTESTX01",
}
path = proof_card.render(row, turkish=False)
print("CARD_RENDER:", "OK" if (path and path.exists()) else "SKIPPED(no-pillow)")
cap = proof_card.caption(row, turkish=False)
print("CAPTION_X:", ("pilot" in cap.lower() or "contractor" in cap.lower()))

# 3) Brief block enterprise modu (DeepSeek'e giden kurallar)
brief = telegram_handoff.brief_block(row)
print("BRIEF_ENTERPRISE:", ("ENTERPRISE CONTRACTOR MODE" in brief or "contract" in brief.lower()))
