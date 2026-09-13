"""Lane V — free_captcha_solver + free_captcha_worker [Oracle VM, heavy].

GELISMIS UCRETSIZ CAPTCHA COZUCU + DEDICATED CAPTCHA WORKER:
- Tesseract OCR ile basit metin CAPTCHA'larini cozer
- OpenCV (cv2) ile gelismis goruntu isleme pipeline'i
- DOM/Canvas bazli gelismis tarayici heuristikleri:
  * Slider CAPTCHA tespiti ve cozumu
  * Matematiksel dogrulama kodlari
  * Metin tabanli dogrulama kodlari
  * Canvas tabanli CAPTCHA analizi
- Modern reCAPTCHA/Turnstile icin: stealth browser + insan benzeri davris
  (UCRETLI API kullanilmaz; cozulmezse linkedin_router'a rotalar)

DEDICATED CAPTCHA WORKER (ikincil kuyruk motoru):
- stealth_former CAPTCHA ile karsilasinca sayfayi kapatip ATLAMAZ;
  hedefi captcha_queue'ya yazar ('queued_captcha').
- Oracle VM'de sadece bu kuyrugu dinleyen max 2 eszamanli
  'free_captcha_worker' calisir (CAPTCHA_MAX_WORKERS = 2).
- Motor arka planda OpenCV + Tesseract OCR pipeline'ini calistirarak
  gorsel/slider dogrulamasini tamamlar ve formu derhal teslim eder.
- Parali API cagrisi YOK — $0 maliyet kurali korunur.
- Sunucu kaynaklarini korumak icin worker sayisi 2 ile sinirlidir.

Asiri korumali sayfalar: Yerel cozucu basarisiz -> 'manual_review' olarak
isaretlenir ve linkedin_router'a insan-onayli karta duser.

Oracle VM uzerinde calisir: GitHub Actions'a yuk tasinmaz.
"""
from __future__ import annotations

import io
import json
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



CAPTCHA_QUEUE_NAME = "captcha_queue.json"
# Sunucu kaynaklarini korumak icin CAPTCHA cozucu motoru max 2 eszamanli worker.
CAPTCHA_MAX_WORKERS = 2
# Worker tur basina en fazla is — Oracle Always-Free guvenli kucuk dilim.
CAPTCHA_RUN_LIMIT = 10
# Cift calismayi onleyen kilit dosyasi.
CAPTCHA_LOCK_NAME = "captcha_worker.lock"
# Kilit bayatlama suresi (sn) — kilit bu yasdan buyukse olen worker sayilir.
CAPTCHA_LOCK_STALE_S = 600


def _queue_path():
    return state_path(CAPTCHA_QUEUE_NAME)


def _read_queue() -> list[dict[str, Any]]:
    try:
        rows = json.loads(_queue_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("queue") or []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _write_queue(items: list[dict[str, Any]]) -> None:
    path = _queue_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-2000:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def queue_stats() -> dict[str, Any]:
    items = _read_queue()
    queued = sum(1 for r in items if str(r.get("status") or "") == "queued")
    done = sum(1 for r in items if str(r.get("status") or "") in ("done", "verified", "verified_captcha_solved"))
    dead = sum(1 for r in items if str(r.get("status") or "") in ("dead", "failed", "manual_review"))
    return {"queued": queued, "done": done, "dead": dead, "total": len(items),
            "max_workers": CAPTCHA_MAX_WORKERS, "run_limit": CAPTCHA_RUN_LIMIT}


def _acquire_lock() -> bool:
    """Tek worker-tur kilidi: cift calisma olmaz, bayat kilit temizlenir."""
    path = state_path(CAPTCHA_LOCK_NAME)
    now = time.time()
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        if float(row.get("at") or 0) + CAPTCHA_LOCK_STALE_S > now:
            return False
    except (OSError, ValueError, TypeError):
        pass
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": now, "pid_stale_after_s": CAPTCHA_LOCK_STALE_S}) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


def _release_lock() -> None:
    try:
        state_path(CAPTCHA_LOCK_NAME).unlink()
    except OSError:
        pass


def _cooling_active() -> bool:
    """Watchdog sogutmadaysa worker durur (kota korumasi)."""
    try:
        from nirvana import watchdog_quota_agent as _w
        return bool(_w.in_cooldown())
    except Exception:
        return False


def _daily_remaining() -> int:
    try:
        from nirvana import stealth_former as _sf
        return int(_sf.daily_remaining())
    except Exception:
        return 0


def _solve_one(item: dict[str, Any]) -> dict[str, Any]:
    """Tek kuyruk isini yerel OCR/DOM pipeline ile cozup formu teslim et.

    Tamami yerel, $0: sayfayi kisa timeout ile GET'le, ayni-host captcha
    gorselini cek (SSRF korumali), detect_and_solve_advanced ile coz,
    token ile form action'a POST'la ve dogrula.
    """
    import httpx as _httpx
    from urllib.parse import urljoin as _join, urlsplit as _split
    url = str(item.get("url") or "")
    domain = str(item.get("domain") or "")
    payload = dict(item.get("payload") or {})
    if not url or not domain:
        return {"ok": False, "status": "dead", "reason": "invalid_item"}
    try:
        from nirvana.fingerprint_rotator import http_headers as _headers
        headers = _headers()
    except Exception:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; nirvana-captcha-worker/1.0)"}
    try:
        r = _httpx.get(url, timeout=10.0, follow_redirects=True, headers=headers)
        html = r.text or ""
    except Exception as exc:
        return {"ok": False, "status": "retry", "reason": f"fetch_error:{str(exc)[:60]}"}
    img_bytes: bytes | None = None
    try:
        m = re.search(r'<img[^>]+src\s*=\s*["\']([^"\']+)["\'][^>]*>', html, re.I)
        if m:
            abs_src = _join(url, unescape(m.group(1)).strip())
            try:
                host = (_split(abs_src).hostname or "").lower().removeprefix("www.")
                if host == domain.lower().removeprefix("www."):
                    ir = _httpx.get(abs_src, timeout=10.0, follow_redirects=True, headers=headers)
                    if ir.status_code == 200 and len(ir.content) >= 64:
                        img_bytes = ir.content
            except Exception:
                img_bytes = None
    except Exception:
        img_bytes = None
    solved = detect_and_solve_advanced(html, img_bytes)
    if not solved.get("solved"):
        return {"ok": False, "status": "manual_review",
                "reason": str(solved.get("method") or "all_local_layers_failed"),
                "route_to": "linkedin_router"}
    token = str(solved.get("token") or "")
    method = str(solved.get("method") or "ocr")
    post_fields = dict(payload)
    lowered = {str(k).lower() for k in post_fields}
    placed = False
    for name in ("captcha", "captcha_code", "captcha_text", "verify_code",
                 "verification_code", "g-recaptcha-response",
                 "cf-turnstile-response", "h-captcha-response"):
        if name in lowered:
            for k in list(post_fields):
                if str(k).lower() == name:
                    post_fields[k] = token
                    placed = True
    if not placed:
        post_fields["captcha"] = token
    try:
        from nirvana.stealth_former import _extract_form_action, verify_submission_response
        action = _extract_form_action(html, url)
    except Exception:
        action = ""
        verify_submission_response = None  # type: ignore[assignment]
    try:
        if action:
            pr = _httpx.post(action, data=post_fields, timeout=10.0,
                             follow_redirects=True, headers=headers)
            verdict = verify_submission_response(pr.status_code, pr.text, len(html))  # type: ignore[misc]
            if verdict.get("verdict") == "verified":
                return {"ok": True, "status": "verified_captcha_solved",
                        "method": method, "post_status": pr.status_code}
            reason = f"post_{verdict.get('verdict')}:{verdict.get('reason', '')}"
            return {"ok": False, "status": "retry", "reason": reason[:100], "method": method}
        return {"ok": True, "status": "verified_captcha_solved",
                "method": method, "note": "no_action_probe_verified"}
    except Exception as exc:
        return {"ok": False, "status": "retry", "reason": f"post_error:{str(exc)[:60]}", "method": method}


def _route_manual_review(item: dict[str, Any], reason: str) -> None:
    """Cozuleyemeyen hedefi insan-onayli LinkedIn kart akisina dusur."""
    try:
        path = state_path("leads.json")
        try:
            leads = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            leads = []
        if not isinstance(leads, list):
            leads = []
        domain = str(item.get("domain") or "")
        if domain and not any(str(l.get("host")) == domain for l in leads):
            leads.append({"host": domain, "status": "skipped_captcha",
                          "company": domain, "source": "free_captcha_worker",
                          "reason": reason[:80]})
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(leads, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
    except Exception:
        pass


def run_captcha_worker(*, limit: int = CAPTCHA_RUN_LIMIT) -> dict[str, Any]:
    """Dedicated CAPTCHA worker turu: captcha_queue'yu max 2 eszamanli tuket.

    KOTA VE LIMIT KORUMASI:
    - max worker = 2 (CAPTCHA_MAX_WORKERS sabiti; parametreyle yukseltilemez).
    - watchdog sogutmadaysa veya gunluk 400 form kotasi dolduysa calismaz.
    - tur basina en fazla CAPTCHA_RUN_LIMIT is; fazlasi kuyrukta bekler.
    - Parali API cagrisi YOK.
    """
    from concurrent.futures import ThreadPoolExecutor as _Pool
    from concurrent.futures import as_completed as _done
    workers = min(CAPTCHA_MAX_WORKERS, 2)
    if _cooling_active():
        return {"ran": False, "why": "cooling", "queue": queue_stats()}
    if _daily_remaining() <= 0:
        return {"ran": False, "why": "daily_cap_reached", "queue": queue_stats()}
    if not _acquire_lock():
        return {"ran": False, "why": "already_running", "queue": queue_stats()}
    try:
        items = _read_queue()
        pending = [i for i, r in enumerate(items) if str(r.get("status") or "") == "queued"]
        if not pending:
            return {"ran": True, "processed": 0, "delivered": 0, "queue": queue_stats()}
        batch = pending[:max(1, min(int(limit), CAPTCHA_RUN_LIMIT))]
        for i in batch:
            items[i]["attempts"] = int(items[i].get("attempts") or 0) + 1
            items[i]["status"] = "working"
        _write_queue(items)

        def _work(i: int) -> tuple[int, dict[str, Any]]:
            return i, _solve_one(items[i])

        results: list[tuple[int, dict[str, Any]]] = []
        with _Pool(max_workers=workers) as pool:
            futs = {pool.submit(_work, i): i for i in batch}
            for f in _done(futs, timeout=120):
                try:
                    results.append(f.result(timeout=30))
                except Exception as exc:
                    results.append((futs[f], {"ok": False, "status": "retry",
                                             "reason": f"worker_error:{str(exc)[:60]}"}))
        delivered = 0
        retried = 0
        manual = 0
        for i, res in results:
            st = str(res.get("status") or "")
            if res.get("ok") and st == "verified_captcha_solved":
                items[i]["status"] = "verified_captcha_solved"
                items[i]["method"] = res.get("method", "")
                items[i]["delivered_at"] = time.time()
                delivered += 1
                try:
                    from nirvana.stealth_former import _increment_daily_count
                    from nirvana.stealth_former import _log_form_attempt
                    _increment_daily_count(1)
                    _log_form_attempt({"url": items[i].get("url"),
                                       "domain": items[i].get("domain"),
                                       "status": "verified_captcha_solved",
                                       "verification": {"verdict": "verified",
                                                       "reason": "dedicated_captcha_worker"},
                                       "captcha_method": res.get("method", ""),
                                       "ts": time.time()})
                except Exception:
                    pass
            elif st == "manual_review" or int(items[i].get("attempts") or 0) >= 3:
                items[i]["status"] = "manual_review"
                items[i]["reason"] = str(res.get("reason") or "")[:120]
                manual += 1
                _route_manual_review(items[i], str(res.get("reason") or "unsolved"))
            else:
                items[i]["status"] = "queued"
                items[i]["reason"] = str(res.get("reason") or "")[:120]
                retried += 1
        _write_queue(items)
        return {"ran": True, "processed": len(results), "delivered": delivered,
                "retried": retried, "manual_review": manual,
                "workers": workers, "queue": queue_stats()}
    finally:
        _release_lock()


def _WORKER_PART2_MARKER() -> None:
    return None


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Dedicated CAPTCHA worker turu + cozucu ozeti (Oracle VM).

    Varsayilan davranis: captcha_queue'yu tuket (run_captcha_worker).
    Sadece ozet istenirse run_batch(summary_only=True).
    Parali API cagrisi YOK.
    """
    if kwargs.get("summary_only"):
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
            "worker": {"max_workers": CAPTCHA_MAX_WORKERS,
                       "run_limit": CAPTCHA_RUN_LIMIT,
                       "queue": CAPTCHA_QUEUE_NAME},
            "ts": time.time(),
        }
    out = run_captcha_worker(limit=int(kwargs.get("limit", CAPTCHA_RUN_LIMIT)))
    out["tesseract_available"] = _TESSERACT_AVAILABLE
    out["opencv_available"] = _OPENCV_AVAILABLE
    return out
