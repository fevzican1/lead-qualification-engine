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


def render(row: dict[str, Any] | None, *, turkish: bool = True) -> Path | None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    who = _fit(str((row or {}).get("company") or (row or {}).get("host") or "Sizin panel"), 36)
    stack = _fit(str((row or {}).get("stack") or "checkout / ödeme"), 42)
    pain = _fit(str((row or {}).get("pain") or "ödeme onayı ile sipariş/ERP satırı aynı id'de kilitlenmiyor"), 62)
    price = config.price_label()
    CACHE.mkdir(exist_ok=True)
    host = _fit(str((row or {}).get("host") or "lead"), 40).replace("/", "_")
    out = CACHE / f"proof-{host}.png"

    img = Image.new("RGB", (W, H), "#0B1220")
    draw = ImageDraw.Draw(img)
    title_font, body, small = _fonts()

    draw.rectangle((0, 0, W, 8), fill="#22C55E")
    draw.text((48, 36), "DevSolve  ·  akış analizi", font=title_font, fill="#F8FAFC")
    draw.text((48, 92), who, font=body, fill="#86EFAC")
    draw.text((48, 132), stack, font=small, fill="#94A3B8")

    boxes = (
        ("1  Kaynak", "Sipariş / panel"),
        ("2  Ödeme", "iyzico / PayTR OK"),
        ("3  Kopuk", "gecikme / çift yazım"),
        ("4  Hedef", "ERP / stok / kargo"),
    )
    x = 48
    for i, (label, sub) in enumerate(boxes):
        color = "#EF4444" if i == 2 else "#1E293B"
        border = "#F97316" if i == 2 else "#334155"
        draw.rounded_rectangle((x, 200, x + 250, 360), radius=18, fill=color, outline=border, width=3)
        draw.text((x + 18, 224), label, font=body, fill="#F8FAFC")
        draw.text((x + 18, 280), sub, font=small, fill="#CBD5E1")
        if i < 3:
            draw.polygon([(x + 262, 270), (x + 286, 280), (x + 262, 290)], fill="#64748B")
        x += 286

    draw.rounded_rectangle((48, 400, W - 48, 520), radius=16, fill="#111827", outline="#334155", width=2)
    draw.text((72, 424), "Tespit", font=small, fill="#94A3B8")
    draw.text((72, 456), pain, font=body, fill="#F8FAFC")

    if turkish:
        foot = f"Kapsam: tek köprü, panel durur. İş {price} flat — Payoneer yalnız net evet sonrası."
    else:
        foot = f"Scope: one bridge, panel stays. Job {price} flat — Payoneer only after a clear yes."
    draw.text((48, 560), foot, font=small, fill="#A7F3D0")
    draw.text((48, 610), "Şablon analiz kartı — sitenizin canlı ekran görüntüsü değil.", font=small, fill="#64748B")

    tmp = out.with_suffix(".tmp.png")
    img.save(tmp, "PNG", optimize=True)
    tmp.replace(out)
    return out


def caption(row: dict[str, Any] | None, *, turkish: bool = True) -> str:
    who = str((row or {}).get("company") or (row or {}).get("host") or "").strip()
    if turkish:
        lead = f"{who}: " if who else ""
        return (
            f"{lead}checkout / ödeme kopuğu bu kartta. Kırmızı kutu kilitlenmesi gereken halka. "
            f"İş {config.price_label()}, panel durur. Uyuyorsa bu hafta elle kapanan sipariş sayısını yazın; "
            "uymuyorsa zorlamam."
        )
    lead = f"{who}: " if who else ""
    return (
        f"{lead}this is the checkout/payment break. The red box is the link that should lock on one id. "
        f"Job {config.price_label()}, panel stays. If it fits, send how many orders close by hand this week; "
        "if not, I will not push."
    )
