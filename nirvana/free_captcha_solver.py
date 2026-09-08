"""Lane V — free_captcha_solver [GitHub Actions, heavy].

Ucretsiz CAPTCHA cozucu:
- Tesseract OCR ile basit metin CAPTCHA'larini cozer
- Gorsel isleme (Pillow) ile gurultu temizleme
- Modern reCAPTCHA/Turnstile icin: stealth browser + insan benzeri davris
  (UCRETLI API kullanilmaz; captcha varsa linkedin_router'a rotalar)

Oracle kotasini asmaz: islem GitHub Actions'ta yapilir, sadece sonuc Oracle'ye iletilir.
"""
from __future__ import annotations

import io
import time
from typing import Any

from nirvana.registry import state_path

# Tesseract kurulu mu kontrol et
_TESSERACT_AVAILABLE = False
try:
    import pytesseract
    from PIL import Image, ImageFilter, ImageOps
    _TESSERACT_AVAILABLE = True
except ImportError:
    pass


def solve_text_captcha(image_bytes: bytes) -> dict[str, Any]:
    """Tesseract OCR ile metin CAPTCHA coz."""
    if not _TESSERACT_AVAILABLE:
        return {"ok": False, "reason": "tesseract_not_installed"}
    
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Gurultu temizleme
        img = img.convert("L")  # Gri tonlama
        img = ImageOps.autocontrast(img)
        img = img.filter(ImageFilter.MedianFilter(size=3))
        # Binarization
        threshold = 128
        img = img.point(lambda p: 255 if p > threshold else 0)
        
        # OCR
        text = pytesseract.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
        cleaned = text.strip().replace(" ", "").replace("\n", "")
        
        return {"ok": len(cleaned) >= 4, "text": cleaned, "confidence": "medium"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


def solve_image_captcha(image_path: str) -> dict[str, Any]:
    """Dosyadan CAPTCHA coz."""
    try:
        with open(image_path, "rb") as f:
            return solve_text_captcha(f.read())
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


def detect_and_solve(html: str, screenshot_bytes: bytes | None = None) -> dict[str, Any]:
    """HTML'de CAPTCHA tespit et ve cozmeye calis."""
    captcha_markers = ["g-recaptcha", "cf-turnstile", "h-captcha", "data-sitekey"]
    has_captcha = any(m.lower() in html.lower() for m in captcha_markers)
    
    if not has_captcha:
        return {"has_captcha": False, "action": "proceed"}
    
    # CAPTCHA var - screenshot varsa OCR dene
    if screenshot_bytes:
        result = solve_text_captcha(screenshot_bytes)
        if result["ok"]:
            return {"has_captcha": True, "solved": True, "token": result["text"], "method": "ocr"}
    
        # OCR basarisiz - rotalama
        return {"has_captcha": True, "solved": False, "route_to": "linkedin_router", "method": "ocr_failed"}
    
    # Screenshot yok - direkt rotalama
    return {"has_captcha": True, "solved": False, "route_to": "linkedin_router", "method": "no_screenshot"}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """GitHub Actions'ta tetiklenir."""
    return {"tesseract_available": _TESSERACT_AVAILABLE, "ts": time.time()}
