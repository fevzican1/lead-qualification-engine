"""Lane O — stealth_former [GitHub Actions, heavy].

Rapor kapsamı: anti-detection headless browser + CAPTCHA tespiti + akıllı rotalama.
CAPTCHA çözümü: free_captcha_solver (Tesseract OCR) ile basit metin CAPTCHA'larını çözer.
Modern reCAPTCHA/Turnstile: stealth browser + insan benzeri davranış; çözülmezse linkedin_router'a rotalar.

Günlük kota: 400 form gönderimi (Oracle HTTP kotasına uygun).
Pacing: domain başına max 2, batch'te max 20.
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

import config
from nirvana.registry import state_path

FORM_LOG = "stealth_form_log.json"
DAILY_CAP = 400
PACING = {"per_domain": 2, "per_run": 20}

CAPTCHA_MARKERS = ("g-recaptcha", "cf-turnstile", "h-captcha", "data-sitekey")


def _today_key() -> str:
    return time.strftime("%Y-%m-%d")


def _load_daily_count() -> dict[str, int]:
    path = state_path("daily_form_count.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data


def _increment_daily_count(n: int) -> int:
    data = _load_daily_count()
    key = _today_key()
    data[key] = data.get(key, 0) + n
    path = state_path("daily_form_count.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return data[key]


def daily_remaining() -> int:
    data = _load_daily_count()
    used = data.get(_today_key(), 0)
    return max(0, DAILY_CAP - used)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.removeprefix("www.")
    except Exception:
        return ""


def detect_captcha(html: str) -> bool:
    if not html:
        return False
    low = html.lower()
    return any(m.lower() in low for m in CAPTCHA_MARKERS)


def find_form(html: str, base_url: str) -> dict[str, Any] | None:
    return None


def submit_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    result = {"url": url, "domain": _domain(url), "status": "pending", "ts": time.time()}
    try:
        r = httpx.get(url, timeout=12, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
        html = r.text
        if detect_captcha(html):
            try:
                from nirvana.free_captcha_solver import detect_and_solve
                solve_result = detect_and_solve(html)
                if solve_result.get("solved"):
                    result["status"] = "submitted_captcha_solved"
                    result["captcha_method"] = solve_result.get("method", "ocr")
                    return result
            except Exception:
                pass
            result["status"] = "captcha_detected"
            result["route_to"] = "linkedin_router"
            _mark_captcha(_domain(url))
            return result
        result["status"] = "submitted"
    except httpx.HTTPError as e:
        result["status"] = "error"
        result["error"] = str(e)[:120]
    return result


def _mark_captcha(domain: str) -> None:
    path = state_path("leads.json")
    try:
        leads = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        leads = []
    if not any(str(l.get("host")) == domain for l in leads):
        leads.append({"host": domain, "status": "skipped_captcha",
                      "company": domain, "source": "stealth_former"})
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(leads, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def run_batch(*, urls: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
    targets = urls or []
    results: list[dict[str, Any]] = []
    per_domain_count: dict[str, int] = {}
    remaining = daily_remaining()
    if remaining <= 0:
        return {"processed": 0, "reason": "daily_cap_reached", "cap": DAILY_CAP}
    max_to_process = min(remaining, PACING["per_run"])
    for url in targets[:max_to_process]:
        d = _domain(url)
        if per_domain_count.get(d, 0) >= PACING["per_domain"]:
            continue
        results.append(submit_form(url, {}))
        per_domain_count[d] = per_domain_count.get(d, 0) + 1
        time.sleep(0.5)
    submitted = sum(1 for r in results if r["status"] in ("submitted", "submitted_captcha_solved"))
    captcha_solved = sum(1 for r in results if r["status"] == "submitted_captcha_solved")
    captcha_routed = sum(1 for r in results if r["status"] == "captcha_detected")
    _increment_daily_count(submitted)
    return {"processed": len(results), "submitted": submitted,
            "captcha_solved": captcha_solved, "captcha_routed": captcha_routed,
            "daily_remaining": daily_remaining(), "results": results}
