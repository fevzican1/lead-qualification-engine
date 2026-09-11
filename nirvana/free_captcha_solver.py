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
import re
import time
from html import unescape
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


def solve_math_captcha(html: str) -> dict[str, Any]:
    """Sıfır maliyetli yerel matematik çözücü: '5 + 12 = ?' türü bulmacalar.

    ast tabanlı güvenli değerlendirici kullanır (eval YOK) — yalnızca
    sayı + - * / ve parantez kabul eder, harici API yok.
    """
    import ast

    text = re.sub(r"<[^>]+>", " ", html or "")
    text = text.replace("×", "*").replace("x", "*").replace("X", "*")
    text = unescape(text)
    expr = re.search(
        r"(?:what\s+is|sonucu|kaçt\w*|kaçt\w*|result|sum|solve|captcha|hesapla)?"
        r"\s*(\d+(?:\.\d+)?\s*[\+\-\*/\s]+\s*\d+(?:\s*[\+\-\*/\s]+\s*\d+)*)\s*(?:=|\?|'?)", text, re.I)
    if not expr:
        return {"ok": False}

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right if right else None
            if isinstance(node.op, ast.FloorDiv):
                return left // right if right else None
            return left % right if right else None
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            v = _eval(node.operand)
            return -v if v is not None else None
        return None

    try:
        tree = ast.parse(expr.group(1).strip(), mode="eval")
        answer = _eval(tree)
    except Exception:
        return {"ok": False}
    if answer is None or not isinstance(answer, (int, float)) or answer < 0 or answer > 1_000_000:
        return {"ok": False}
    token = str(int(answer)) if float(answer).is_integer() else f"{answer:.2f}"
    return {"ok": True, "token": token, "method": "math"}


WORD_PATTERNS = (
    re.compile(r"(?:type|enter|write|yaz(?:ın|in)?)\s+(?:the\s+)?word\s*[:\s]*[\"']?([A-Za-z0-9]{2,})[\"']?", re.I),
    re.compile(r"(?:kelimeyi|sözcüğü|yazın)\s*[:\s]*[\"']?([A-Za-z0-9]{2,})[\"']?", re.I),
    re.compile(r"(?:enter|type|yaz(?:ın|in)?)\s+[\"']?([A-Za-z0-9]{2,})[\"']?\s+(?:below|aşağıda)", re.I),
    re.compile(r"(?:type|enter|yaz(?:ın|in)?)\s+(?:the\s+)?(?:text|kelime)\s*[:\s]*([A-Za-z0-9]{2,})", re.I),
)


def solve_word_captcha(html: str) -> dict[str, Any]:
    """Kelime tabanlı CAPTCHA: "type the word apple" -> apple."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    for pat in WORD_PATTERNS:
        m = pat.search(text)
        if m:
            return {"ok": True, "token": m.group(1).strip(), "method": "word"}
    # Reversed form: "type the word: tsrif" -> "first"
    rev = re.search(r"(?:reversed|reverse|tersten)\s*(?:word|kelime)?\s*[:\s]*([A-Za-z0-9]{2,})", text, re.I)
    if rev:
        return {"ok": True, "token": rev.group(1)[::-1], "method": "word_reverse"}
    return {"ok": False}


def solve_hidden_input_captcha(html: str) -> dict[str, Any]:
    """Gizli/honeypot input bulmacaları: yakındaki metin ile name captcha/calc ise.

    adı solitaire'dan korumak için yalnızca tam sayı içeren gizli input'lara yanıt
    verir; eklenen başka alan doldurulmaz (honeypot boş bırakılır).
    """
    for m in re.finditer(
        r'<input[^>]*?(?:name|id)\s*=\s*["\']([^"\']*(?:captcha|calc|math|test|confirm)[^"\']*)["\'][^>]*value\s*=\s*["\']?([^"\'>]*)["\']?',
        html or "", re.I,
    ):
        name, val = m.group(1), m.group(2).strip()
        if name and val and val.isdigit():
            return {"ok": True, "token": val, "method": "hidden_input"}
    return {"ok": False}


def solve_dom_captcha(html: str) -> dict[str, Any]:
    """Herhangi yerel çözücü: math -> word -> hidden sırasıyla dener."""
    for solver in (solve_math_captcha, solve_word_captcha, solve_hidden_input_captcha):
        res = solver(html)
        if res.get("ok"):
            return res
    return {"ok": False}


def detect_and_solve(html: str, screenshot_bytes: bytes | None = None) -> dict[str, Any]:
    """HTML'de CAPTCHA tespit et ve sıfır maliyetli yerel katmanla çözmeye çal.

    Sırası: (1) modern reCAPTCHA/Turnstile -> stealth rotalama (linkedin_router),
    (2) yerel DOM çözücüleri (matematik / kelime / gizli-input) -> %100 otonom,
    (3) görsel OCR (Tesseract) en sonda. Harici API kullanılmaz — maliyet 0.
    """
    low = html.lower()
    captcha_markers = ["g-recaptcha", "cf-turnstile", "h-captcha", "data-sitekey"]
    has_modern = any(m.lower() in low for m in captcha_markers)

    if has_modern:
        # Modern engeller: local çözücü iddiası YOK — stealth + rotalama.
        if screenshot_bytes:
            result = solve_text_captcha(screenshot_bytes)
            if result["ok"]:
                return {"has_captcha": True, "solved": True, "token": result["text"], "method": "ocr"}
            return {"has_captcha": True, "solved": False, "route_to": "linkedin_router", "method": "ocr_failed"}
        return {"has_captcha": True, "solved": False, "route_to": "linkedin_router", "method": "no_screenshot"}

    # Yerel çözücü katmanı: matematik / kelime / gizli-input — 0 EUR.
    dom = solve_dom_captcha(html)
    if dom.get("ok"):
        return {"has_captcha": True, "solved": True, "token": dom["token"], "method": dom["method"]}

    # "captcha" yapisal baglamda (tag adi/attribute) geciyor ama cozulemedi ->
    # rotalama (harcanmaz). Duz metindeki kelime CAPTCHA degildir.
    if re.search(r"<[^>]*captcha", low):
        return {"has_captcha": True, "solved": False, "route_to": "linkedin_router", "method": "unsupported"}

    if screenshot_bytes:
        result = solve_text_captcha(screenshot_bytes)
        if result["ok"]:
            return {"has_captcha": True, "solved": True, "token": result["text"], "method": "ocr"}

    return {"has_captcha": False, "action": "proceed"}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """GitHub Actions'ta tetiklenir."""
    return {
        "tesseract_available": _TESSERACT_AVAILABLE,
        "local_solvers": ["math", "word", "word_reverse", "hidden_input", "ocr"],
        "ts": time.time(),
    }
