"""Dinamik CSRF / Turnstile jeton çıkarımı (pre-flight -> POST zinciri).

Rapor (Dinamik CSRF, Turnstile ve Parametre Ekstraksiyonu): Pre-flight bir formu
onayladığında POST'tan ÖNCE gizli girdilerdeki CSRF jetonları Regex ile çekilir;
Turnstile/reCAPTCHA tespit edilen formlarda jeton ücretsiz yerel çözücüden
(`nirvana/free_captcha_solver`) alınır, alınamazsa hedef CAPTCHA kuyruğuna yazılır
— hedef asla sessizce atılmaz.

Paralı API, harici captcha servisi veya proxy YOK ($0 kuralı).
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

HIDDEN_INPUT_RE = re.compile(r"<input\b[^>]*>", re.I | re.S)
ATTR_RE = re.compile(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*[\"']([^\"']*)[\"']", re.S)
TOKEN_NAME_RE = re.compile(
    r"(csrf|xsrf|_token|authenticity|nonce|wpnonce|form[-_]?key|"
    r"__requestverificationtoken|verification[-_]?token|security[-_]?token|"
    r"recaptcha[-_]?response|cf[-_]?turnstile[-_]?response|h[-_]?captcha[-_]?response)",
    re.I,
)
CAPTCHA_KINDS = (
    ("turnstile", re.compile(r"cf-turnstile|challenges\.cloudflare\.com/turnstile", re.I)),
    ("recaptcha_v3", re.compile(r"recaptcha/api\.js\?render|grecaptcha\.execute", re.I)),
    ("recaptcha_v2", re.compile(r"g-recaptcha|google\.com/recaptcha/api\.js", re.I)),
    ("hcaptcha", re.compile(r"h-captcha|hcaptcha\.com", re.I)),
)
SITEKEY_RE = re.compile(r"data-sitekey\s*=\s*[\"']([^\"']+)[\"']", re.I)
FORM_RE = re.compile(r"<form\b[^>]*>.*?</form>", re.I | re.S)


def hidden_inputs(html: str) -> list[dict[str, str]]:
    """Tüm `<input type="hidden">` alanları: name/value/attrs."""
    out: list[dict[str, str]] = []
    for tag in HIDDEN_INPUT_RE.findall(html or ""):
        attrs = {m.group(1).lower(): m.group(2) for m in ATTR_RE.finditer(tag)}
        if str(attrs.get("type", "")).strip().lower() != "hidden":
            continue
        name = str(attrs.get("name") or attrs.get("id") or "").strip()
        if not name:
            continue
        out.append({"name": name, "value": str(attrs.get("value") or ""), "tag": tag[:400]})
    return out


def token_payload(html: str) -> dict[str, str]:
    """CSRF/doğrulama jetonları (POST gövdesine eklenecek) — isim -> değer."""
    payload: dict[str, str] = {}
    for row in hidden_inputs(html):
        if TOKEN_NAME_RE.search(row["name"]):
            payload[row["name"]] = row["value"]
    # Turnstile/reCAPTCHA görünmez alanları: çözücü jetonu ayrıca ekler.
    return payload


def captcha_kind(html: str) -> str:
    """Sayfadaki captcha türü: turnstile / recaptcha_v2 / recaptcha_v3 / hcaptcha / none."""
    body = html or ""
    for name, pattern in CAPTCHA_KINDS:
        if pattern.search(body):
            return name
    return "none"


def turnstile_sitekey(html: str) -> str:
    """Turnstile/reCAPTCHA site anahtarı (data-sitekey) — yoksa boş."""
    match = SITEKEY_RE.search(html or "")
    return match.group(1) if match else ""


def form_action(html: str) -> str:
    """İlk formun action niteliği (göreli yollar korunur)."""
    match = FORM_RE.search(html or "")
    if not match:
        return ""
    attrs = {m.group(1).lower(): m.group(2) for m in ATTR_RE.finditer(match.group(0)[:400])}
    return str(attrs.get("action") or "").strip()


def solve_token(kind: str, html: str, *, screenshot_bytes: bytes | None = None) -> dict[str, Any]:
    """Ücretsiz yerel çözücüden captcha jetonu al (yoksa captcha_queue rotası)."""
    if kind == "none":
        return {"solved": False, "reason": "no_captcha", "route": ""}
    try:
        from nirvana import free_captcha_solver as solver  # type: ignore

        result = solver.detect_and_solve_advanced(html, screenshot_bytes)
        if isinstance(result, dict) and result.get("solved"):
            token = str(result.get("token") or result.get("value") or "")
            if token:
                return {"solved": True, "token": token, "reason": str(result.get("method") or "local")}
        return {"solved": False, "reason": str((result or {}).get("reason") or "unsolved"),
                "route": "captcha_queue"}
    except Exception as exc:  # noqa: BLE001 — çözücü yoksa rota bilgisi yeterli
        logger.info("Yerel captcha çözücü kullanılamadı (%s) — kuyruk rotası", exc)
        return {"solved": False, "reason": "solver_unavailable", "route": "captcha_queue"}


def prepare_post(html: str, *, extra: dict[str, str] | None = None,
                 screenshot_bytes: bytes | None = None) -> dict[str, Any]:
    """POST gövdesi + captcha kararı: jetonlar, action, captcha türü/sitekey."""
    kind = captcha_kind(html)
    payload = token_payload(html)
    if extra:
        payload.update({str(k): str(v) for k, v in extra.items()})
    out: dict[str, Any] = {
        "action": form_action(html),
        "captcha": kind,
        "sitekey": turnstile_sitekey(html),
        "tokens": payload,
        "token_names": sorted(token_payload(html)),
    }
    if kind != "none":
        solved = solve_token(kind, html, screenshot_bytes=screenshot_bytes)
        out["captcha_solved"] = bool(solved.get("solved"))
        out["captcha_route"] = str(solved.get("route") or "")
        if solved.get("solved"):
            field = "cf-turnstile-response" if kind == "turnstile" else "g-recaptcha-response"
            payload[field] = str(solved.get("token") or "")
    return out


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Lane çıktısı: jeton türleri ve captcha türleri özeti (dry-run)."""
    return {
        "token_patterns": TOKEN_NAME_RE.pattern[:120],
        "captcha_kinds": [name for name, _ in CAPTCHA_KINDS],
        "form_action_supported": True,
    }
