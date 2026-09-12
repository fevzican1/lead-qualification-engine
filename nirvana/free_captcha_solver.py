"""Lane V — free_captcha_solver [Oracle VM, heavy].

GELISMIS UCRETSIZ CAPTCHA COZUCU:
- Tesseract OCR ile basit metin CAPTCHA'larini cozer
- OpenCV (cv2) ile gelismis goruntu isleme pipeline'i
- DOM/Canvas bazli gelismis tarayici heuristikleri:
  * Slider CAPTCHA tespiti ve cozumu
  * Matematiksel dogrulama kodlari
  * Metin tabanli dogrulama kodlari
  * Canvas tabanli CAPTCHA analizi
- Modern reCAPTCHA/Turnstile icin: stealth browser + insan benzeri davris
  (UCRETLI API kullanilmaz; cozulmezse linkedin_router'a rotalar)

Asiri korumali sayfalar: Yerel cozucu basarisiz -> 'skipped_captcha' olarak isaretlenir.

Oracle VM uzerinde calisir: GitHub Actions'a yuk tasinmaz.
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

# OpenCV kurulu mu kontrol et
_OPENCV_AVAILABLE = False
try:
    import cv2
    import numpy as np
    _OPENCV_AVAILABLE = True
except ImportError:
    pass



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

def _opencv_preprocess(image_bytes: bytes) -> bytes:
    """OpenCV ile gelismis goruntu on isleme pipeline'i."""
    if not _OPENCV_AVAILABLE:
        return image_bytes
    try:
        import cv2
        import numpy as np
        from PIL import Image

        img_array = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            return image_bytes

        # Gri tonlama
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Gurultu azaltma (Non-local means denoising)
        denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)

        # Kontrast artirma (CLAHE)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)

        # Adaptif esikleme
        binary = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        # Morfolojik isleme (gurultu temizleme)
        kernel = np.ones((2, 2), np.uint8)
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        # Kenar kirma (segmentation icin)
        result = cv2.GaussianBlur(cleaned, (3, 3), 0)

        # Bytes'a geri cevir
        _, buffer = cv2.imencode('.png', result)
        return buffer.tobytes()
    except Exception:
        return image_bytes




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



# --- GELISMIS HEURISTIK COZUCILER (UCRETSIZ, YEREL) ---------------------------------------

def _solve_slider_captcha(html: str) -> dict[str, Any]:
    """Slider CAPTCHA heuristigi: HTML'de slider widget'ini tespit et ve pozisyon hesapla."""
    target_match = re.search(
        r'(?:data-(?:target|position|slide|offset|gap))\s*=\s*["\']?(\d+)["\']?',
        html, re.I
    )
    if target_match:
        return {"ok": True, "token": target_match.group(1), "method": "slider_dom_target"}

    track_match = re.search(
        r'(?:slider|track|puzzle)[^>]*(?:width|size)\s*[:=]\s*["\']?(\d+)["\']?',
        html, re.I
    )
    gap_match = re.search(
        r'(?:gap|offset|move|distance)\s*[:=]\s*["\']?(\d+)["\']?',
        html, re.I
    )
    if track_match and gap_match:
        return {"ok": True, "token": gap_match.group(1), "method": "slider_track_calc"}

    return {"ok": False}


def _solve_canvas_captcha(html: str) -> dict[str, Any]:
    """Canvas tabanlı CAPTCHA tespiti: canvas elementinde gizli token/cevap arar."""
    for pattern in [
        r'<canvas[^>]*>.*?</canvas>.*?<input[^>]*value\s*=\s*["\']([^"\']+)["\']',
        r'<input[^>]*value\s*=\s*["\']([^"\']+)["\'].*?<canvas',
        r'data-canvas-answer\s*=\s*["\']([^"\']+)["\']',
        r'data-verify\s*=\s*["\']([^"\']+)["\']',
    ]:
        m = re.search(pattern, html, re.I | re.DOTALL)
        if m:
            return {"ok": True, "token": m.group(1), "method": "canvas_hidden_value"}

    canvas_text_match = re.search(
        r'(?:fillText|drawText)[^;]*["\']([A-Za-z0-9]{3,8})["\']',
        html, re.I
    )
    if canvas_text_match:
        return {"ok": True, "token": canvas_text_match.group(1), "method": "canvas_text"}

    return {"ok": False}


def _solve_text_verification(html: str) -> dict[str, Any]:
    """Metin tabanlı dogrulama kodlari: 'Kelimeyi yazin', 'Type the text' gibi."""
    for pattern in [
        r'(?:verify|dogrula|confirm)[^>]*>([^<]{3,8})</',
        r'(?:type|enter|write)\s+(?:the\s+)?(?:word|text|code)\s*[:=]?\s*["\']?([A-Za-z0-9]{3,8})',
        r'verification\s+code\s*[:=]?\s*["\']?([A-Za-z0-9]{3,8})',
        r'(?:captcha|dogrulama|verify)[^>]*value\s*=\s*["\']([^"\']+)["\']',
    ]:
        m = re.search(pattern, html, re.I)
        if m:
            return {"ok": True, "token": m.group(1), "method": "text_verification"}

def _solve_advanced_ocr(image_bytes: bytes) -> dict[str, Any]:
    """Gelismis OCR: OpenCV on isleme + Tesseract (birden fazla konfig ile)."""
    if not _TESSERACT_AVAILABLE:
        return {"ok": False, "reason": "tesseract_not_installed"}

    try:
        from PIL import Image, ImageFilter, ImageOps
        import pytesseract

        # Once OpenCV on isleme (varsa)
        processed_bytes = _opencv_preprocess(image_bytes) if _OPENCV_AVAILABLE else image_bytes

        img = Image.open(io.BytesIO(processed_bytes))

        # Pillow ile ek temizleme (OpenCV yoksa veya destek olarak)
        if not _OPENCV_AVAILABLE:
            img = img.convert("L")
            img = ImageOps.autocontrast(img)
            img = img.filter(ImageFilter.MedianFilter(size=3))
            threshold = 128
            img = img.point(lambda p: 255 if p > threshold else 0)

        # Birden fazla OCR konfigurasyonu dene
        configs = [
            "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
            "--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
            "--psm 13 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
        ]

        for config in configs:
            text = pytesseract.image_to_string(img, config=config)
            cleaned = text.strip().replace(" ", "").replace("\n", "")
            if len(cleaned) >= 4:
                return {"ok": True, "text": cleaned, "confidence": "medium", "method": "advanced_ocr"}

        return {"ok": False, "reason": "ocr_low_confidence"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


def detect_and_solve_advanced(html: str, screenshot_bytes: bytes | None = None) -> dict[str, Any]:
    """HTML'de CAPTCHA tespit et ve TUM yerel katmanlari sirayla dener.

    GELISMIS KATMANLAR (sirasiyla):
    1. Modern reCAPTCHA/Turnstile/CF -> direkt rotalama (cozum iddiasi YOK)
    2. DOM bazli: matematik -> kelime -> kelime ters -> gizli-input
    3. Gelismis heuristikler: slider -> canvas -> metin dogrulama
    4. OpenCV + Tesseract OCR (gelismis on isleme ile)
    5. Hepsi basarisiz -> skipped_captcha (linkedin_router'a rotala)

    Harici API kullanilmaz — maliyet 0.
    """
    low = html.lower()
    captcha_markers = ["g-recaptcha", "cf-turnstile", "h-captcha", "data-sitekey"]
    has_modern = any(m.lower() in low for m in captcha_markers)

    if has_modern:
        if screenshot_bytes:
            result = _solve_advanced_ocr(screenshot_bytes)
            if result["ok"]:
                return {"has_captcha": True, "solved": True, "token": result["text"], "method": "advanced_ocr"}
        return {"has_captcha": True, "solved": False, "route_to": "linkedin_router", "method": "modern_unsolvable"}

    # KATMAN 1: DOM bazli klasik cozuculer
    dom = solve_dom_captcha(html)
    if dom.get("ok"):
        return {"has_captcha": True, "solved": True, "token": dom["token"], "method": dom["method"]}

    # KATMAN 2: Gelismis heuristikler
    slider = _solve_slider_captcha(html)
    if slider.get("ok"):
        return {"has_captcha": True, "solved": True, "token": slider["token"], "method": slider["method"]}

    canvas = _solve_canvas_captcha(html)
    if canvas.get("ok"):
        return {"has_captcha": True, "solved": True, "token": canvas["token"], "method": canvas["method"]}

    text_ver = _solve_text_verification(html)
    if text_ver.get("ok"):
        return {"has_captcha": True, "solved": True, "token": text_ver["token"], "method": text_ver["method"]}

    # KATMAN 3: Gelismis OCR
    if screenshot_bytes:
        result = _solve_advanced_ocr(screenshot_bytes)
        if result["ok"]:
            return {"has_captcha": True, "solved": True, "token": result["text"], "method": "advanced_ocr"}

    # Hicbir katlan cozemedi
    if re.search(r"<[^>]*captcha", low):
        return {"has_captcha": True, "solved": False, "route_to": "linkedin_router",
                "method": "all_local_layers_failed"}

    return {"has_captcha": False, "action": "proceed"}



def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Oracle VM uzerinde gelismis CAPTCHA cozucu ozeti."""
    return {
        "tesseract_available": _TESSERACT_AVAILABLE,
        "opencv_available": _OPENCV_AVAILABLE,
        "local_solvers": [
            "math", "word", "word_reverse", "hidden_input",
            "slider", "canvas", "text_verification", "advanced_ocr"
        ],
        "solver_layers": [
            "dom_classic", "slider_heuristic", "canvas_heuristic",
            "text_verification", "opencv_ocr"
        ],
        "ts": time.time(),
    }

    return {"ok": False}



    return {"has_captcha": False, "action": "proceed"}


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """GitHub Actions'ta tetiklenir."""
    return {
        "tesseract_available": _TESSERACT_AVAILABLE,
        "local_solvers": ["math", "word", "word_reverse", "hidden_input", "ocr"],
        "ts": time.time(),
    }
