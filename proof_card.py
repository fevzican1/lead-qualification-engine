"""Local proof card for Telegram. No cloud image API, no extra Oracle shape."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config

CACHE = config.ROOT / "proof_cache"
W, H = 1200, 675
_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/freefont"),
    Path("C:/Windows/Fonts"),
)
_FONT_PAIRS = (
    ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"),
    ("LiberationSans-Bold.ttf", "LiberationSans-Regular.ttf"),
    ("FreeSansBold.ttf", "FreeSans.ttf"),
    ("arialbd.ttf", "arial.ttf"),
    ("segoeuib.ttf", "segoeui.ttf"),
)


def _fonts():
    from PIL import ImageFont

    for bold_name, regular_name in _FONT_PAIRS:
        bold_path = regular_path = None
        for folder in _FONT_DIRS:
            b = folder / bold_name
            r = folder / regular_name
            if b.exists():
                bold_path = b
            if r.exists():
                regular_path = r
        if bold_path and regular_path:
            try:
                return (
                    ImageFont.truetype(str(bold_path), 36),
                    ImageFont.truetype(str(regular_path), 26),
                    ImageFont.truetype(str(regular_path), 20),
                )
            except OSError:
                continue
    fallback = ImageFont.load_default()
    return fallback, fallback, fallback


def _fit(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


# Variant -> the four flow boxes drawn on the card. Index 2 is the break.
_FLOWS_TR: dict[str, tuple[tuple[str, str], ...]] = {
    "A": (
        ("1  Checkout", "wc-ajax POST"),
        ("2  Ödeme", "gateway OK"),
        ("3  Kopuk", "webhook retry gecikmesi"),
        ("4  Sipariş", "Pending'de kalıyor"),
    ),
    "B": (
        ("1  Sepet", "cart attribute"),
        ("2  Storefront", "API senkron"),
        ("3  Kopuk", "attribute uyuşmazlığı"),
        ("4  Checkout", "dönüşüm kaybı"),
    ),
    "D": (
        ("1  Ödeme", "onay callback"),
        ("2  Panel", "sipariş kaydı"),
        ("3  Kopuk", "durum senkron gecikmesi"),
        ("4  Sevkiyat", "elle takip"),
    ),
    "E": (
        ("1  Quote", "checkout POST"),
        ("2  Order", "quote-to-order"),
        ("3  Kopuk", "duplicate payload"),
        ("4  Sipariş", "sessiz kayıp"),
    ),
    "C": (
        ("1  Form/POST", "checkout isteği"),
        ("2  Uygulama", "işleme alma"),
        ("3  Kopuk", "session timeout"),
        ("4  Sipariş/CRM", "kayıt düşmüyor"),
    ),
}
_FLOWS_EN: dict[str, tuple[tuple[str, str], ...]] = {
    "A": (
        ("1  Checkout", "wc-ajax POST"),
        ("2  Payment", "gateway OK"),
        ("3  Break", "webhook retry delay"),
        ("4  Order", "stuck Pending"),
    ),
    "B": (
        ("1  Cart", "cart attribute"),
        ("2  Storefront", "API sync"),
        ("3  Break", "attribute mismatch"),
        ("4  Checkout", "conversion leak"),
    ),
    "D": (
        ("1  Payment", "approval callback"),
        ("2  Panel", "order record"),
        ("3  Break", "status sync delay"),
        ("4  Fulfilment", "manual chase"),
    ),
    "E": (
        ("1  Quote", "checkout POST"),
        ("2  Order", "quote-to-order"),
        ("3  Break", "duplicate payload"),
        ("4  Order", "silent loss"),
    ),
    "C": (
        ("1  Form/POST", "checkout request"),
        ("2  App", "processing"),
        ("3  Break", "session timeout"),
        ("4  Order/CRM", "record missing"),
    ),
}


def _flow(row: dict[str, Any] | None, *, turkish: bool) -> tuple[tuple[str, str], ...]:
    variant = str((row or {}).get("variant") or "C").upper()
    table = _FLOWS_TR if turkish else _FLOWS_EN
    return table.get(variant, table["C"])


def render(row: dict[str, Any] | None, *, turkish: bool = True) -> Path | None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    who = _fit(str((row or {}).get("company") or (row or {}).get("host") or "Sizin panel"), 36)
    confirmed = bool((row or {}).get("platform_confirmed"))
    platform = str((row or {}).get("platform") or (row or {}).get("stack") or "").strip()
    pay = ", ".join(str(p) for p in ((row or {}).get("payment_stack") or []) if p)
    evidence = ", ".join(str(e) for e in ((row or {}).get("platform_evidence") or []) if e)
    if confirmed and platform:
        stack_line = f"{platform}{' + ' + pay if pay else ''}"
    else:
        stack_line = "checkout / ödeme akışı" if turkish else "checkout / payment flow"
    pain = _fit(
        str(
            (row or {}).get("error_type")
            or (row or {}).get("pain")
            or "ödeme onayı ile sipariş satırı aynı id'de kilitlenmiyor"
        ),
        62,
    )
    price = config.price_label()
    CACHE.mkdir(exist_ok=True)
    host = _fit(str((row or {}).get("host") or "lead"), 40).replace("/", "_")
    out = CACHE / f"proof-{host}.png"

    img = Image.new("RGB", (W, H), "#0B1220")
    draw = ImageDraw.Draw(img)
    title_font, body, small = _fonts()

    draw.rectangle((0, 0, W, 8), fill="#22C55E")
    title = "DevSolve  ·  akış analizi" if turkish else "DevSolve  ·  flow analysis"
    draw.text((48, 36), title, font=title_font, fill="#F8FAFC")
    draw.text((48, 92), who, font=body, fill="#86EFAC")
    draw.text((48, 132), _fit(stack_line, 52), font=small, fill="#94A3B8")

    x = 48
    boxes = _flow(row, turkish=turkish)
    for i, (label, sub) in enumerate(boxes):
        color = "#EF4444" if i == 2 else "#1E293B"
        border = "#F97316" if i == 2 else "#334155"
        draw.rounded_rectangle((x, 200, x + 250, 360), radius=18, fill=color, outline=border, width=3)
        draw.text((x + 18, 224), _fit(label, 16), font=body, fill="#F8FAFC")
        draw.text((x + 18, 280), _fit(sub, 26), font=small, fill="#CBD5E1")
        if i < 3:
            draw.polygon([(x + 262, 270), (x + 286, 280), (x + 262, 290)], fill="#64748B")
        x += 286

    draw.rounded_rectangle((48, 400, W - 48, 520), radius=16, fill="#111827", outline="#334155", width=2)
    draw.text((72, 424), "Tespit" if turkish else "Finding", font=small, fill="#94A3B8")
    draw.text((72, 456), pain, font=body, fill="#F8FAFC")

    if turkish:
        foot = f"Kapsam: tek köprü, panel durur. İş {price} flat — Payoneer yalnız net evet sonrası."
    else:
        foot = f"Scope: one bridge, panel stays. Job {price} flat — Payoneer only after a clear yes."
    draw.text((48, 560), foot, font=small, fill="#A7F3D0")

    if confirmed and evidence:
        note = (
            f"Altyapı kaynak kodundan doğrulandı ({evidence}). Şablon akış kartı — canlı ekran görüntüsü değil."
            if turkish
            else f"Stack confirmed from page source ({evidence}). Schematic flow card — not a live screenshot."
        )
    else:
        note = (
            "Genel checkout akışı şablonu — canlı ekran görüntüsü değil."
            if turkish
            else "Generic checkout flow schematic — not a live screenshot."
        )
    draw.text((48, 610), _fit(note, 108), font=small, fill="#64748B")

    tmp = out.with_suffix(".tmp.png")
    img.save(tmp, "PNG", optimize=True)
    tmp.replace(out)
    return out


def caption(row: dict[str, Any] | None, *, turkish: bool = True) -> str:
    who = str((row or {}).get("company") or (row or {}).get("host") or "").strip()
    confirmed = bool((row or {}).get("platform_confirmed"))
    platform = str((row or {}).get("platform") or (row or {}).get("stack") or "").strip()
    err = str((row or {}).get("error_type") or (row or {}).get("pain") or "").strip().rstrip(".")
    if turkish:
        lead = f"{who}: " if who else ""
        head = (
            f"{lead}{platform} akışınızda {err} — kırmızı kutu kilitlenmesi gereken halka."
            if confirmed and platform and err
            else f"{lead}checkout / ödeme kopuğu bu kartta. Kırmızı kutu kilitlenmesi gereken halka."
        )
        return (
            f"{head} İş {config.price_label()}, panel durur. "
            "Bu akışı bugün 2 saatlik bir uygulama slotunda kalıcı olarak kapatabiliriz — "
            "randevu oluşturalım mı? Uymuyorsa zorlamam."
        )
    lead = f"{who}: " if who else ""
    head = (
        f"{lead}{err} on your {platform} flow — the red box is the link that should lock on one id."
        if confirmed and platform and err
        else f"{lead}this is the checkout/payment break. The red box is the link that should lock on one id."
    )
    return (
        f"{head} Job {config.price_label()}, panel stays. "
        "We can close this permanently in a 2-hour implementation slot today — "
        "shall I book it? If it does not fit, I will not push."
    )
