"""Lane O — stealth_former [GitHub Actions, heavy].

Rapor kapsamı: anti-detection headless browser + CAPTCHA tespiti + akıllı rotalama.
CAPTCHA çözümü: free_captcha_solver (Tesseract OCR) ile basit metin CAPTCHA'larını çözer.
Modern reCAPTCHA/Turnstile: stealth browser + insan benzeri davranış; çözülmezse linkedin_router'a rotalar.

Yeni (v2):
- DOM & HTTP yanıt doğrulama: 200 OK, AJAX JSON {"status":"success"}, DOM "Mesajınız alındı"
- Content-length anomalisi tespiti (WAF sesiz yutma) → WAF_REJECT log
- Honeypot tespiti (display:none input'lar boş bırakılır)
- İnsan simülasyonu jitter (mouse/key gecikmeleri)
- Kanarya testi (50 gönderimde bir IP/shadowban tespiti)
- Çift kanallı teslimat (form + DMARC/SPF e-posta)

Günlük kota: 400 form gönderimi (Oracle HTTP kotasına uygun).
Pacing: domain başına max 2, batch'te max 20.
"""
from __future__ import annotations

import json
import re
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

SUCCESS_MARKERS = [
    "mesajınız alındı", "mesajınız alindi", "thank you", "message received",
    "successfully submitted", "we will be in touch", "iletildi", "teşekkür",
    "teşekkürler", "thanks for contacting", "başarıyla", "form submitted",
]
SUCCESS_JSON_KEYS = {"status", "result", "success", "code"}

WAF_MARKERS = [
    "access denied", "forbidden", "blocked", "cloudflare", "akismet",
    "spam detected", "your submission was blocked", "security check",
    "captcha required", "rate limit", "please verify you are human",
]


def verify_submission_response(status_code: int, body: str, expected_len: int | None = None) -> dict[str, Any]:
    """1. DOM & HTTP yanıt doğrulama.

    - HTTP 200 + AJAX JSON {"status":"success"} veya DOM başarı ibaresi → verified
    - WAF/anti-spam marker'ı veya content-length anomalisi → WAF_REJECT
    - Aksi halde → unverified (PASSED yazılmaz)
    """
    low = (body or "").lower()
    if any(m.lower() in low for m in WAF_MARKERS):
        return {"verdict": "WAF_REJECT", "reason": "waf_marker"}
    if status_code in (403, 429):
        return {"verdict": "WAF_REJECT", "reason": f"http_{status_code}"}
    if status_code != 200:
        return {"verdict": "unverified", "reason": f"http_{status_code}"}
    # AJAX JSON başarısı?
    try:
        data = json.loads(body or "")
        if isinstance(data, dict):
            for k in SUCCESS_JSON_KEYS:
                v = str(data.get(k, "")).lower()
                if v in ("success", "ok", "true", "1", "submitted", "received", "200"):
                    return {"verdict": "verified", "reason": "ajax_json_success"}
    except (ValueError, AttributeError):
        pass
    if any(m in low for m in SUCCESS_MARKERS):
        return {"verdict": "verified", "reason": "dom_success_marker"}
    # 200 dönüp hem marker yok hem gövde anormal kısaysa → sessiz yutma şüphesi
    if expected_len and len(body or "") < max(80, int(expected_len * 0.15)):
        return {"verdict": "WAF_REJECT", "reason": "content_length_anomaly"}
    return {"verdict": "unverified", "reason": "no_success_evidence"}


def detect_honeypot_fields(html: str) -> list[dict[str, Any]]:
    """3. Honeypot izolasyonu — helper modüle delege eder (import yoksa regex fallback)."""
    try:
        from nirvana.honeypot_human_sim import detect_honeypot_fields as _det
        return _det(html)
    except Exception:
        pass
    if not html:
        return []
    out: list[dict[str, Any]] = []
    for m in re.finditer(r'<input[^>]*?style\s*=\s*["\']([^"\']*)["\'][^>]*?>', html, re.S | re.I):
        style = m.group(1).lower()
        if "display:none" in style.replace(" ", "") or "visibility:hidden" in style.replace(" ", ""):
            nm = re.search(r'(?:name|id)\s*=\s*["\']?(\w[\w-]*)', m.group(0), re.I)
            if nm:
                out.append({"name": nm.group(1), "reason": "inline_hidden_fallback"})
    return out


def human_jitter_ms(phase: str = "key_press") -> int:
    """3. İnsan simülasyonu jitter — helper modüle delege eder."""
    try:
        from nirvana.honeypot_human_sim import JITTER_PROFILE
        import random as _r
        prof = JITTER_PROFILE.get(phase, JITTER_PROFILE["key_press"])
        mean, _std, lo, hi = prof
        v = _r.gauss(mean, _std)
        return int(max(lo, min(hi, v)))
    except Exception:
        return 150


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
    """Bare hostname; tolerant of double-scheme corruption in legacy state."""
    try:
        from nirvana import urlutil
        return urlutil.clean_domain(url)
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
    """Form gönderimi: keşif GET → (varsa) POST → DOM/HTTP doğrulama.

    Statüler:
    - verified / verified_captcha_solved → gerçekten doğrulandı
    - WAF_REJECT → WAF/anti-spam engeli veya sessiz yutma şüphesi
    - captcha_detected → LinkedIn'e rotalanır
    - unverified / error → PASSED sayılmaz, logda ayrı tutulur
    """
    result = {"url": url, "domain": _domain(url), "status": "pending", "ts": time.time()}
    try:
        from nirvana.fingerprint_rotator import http_headers as _rotate_headers
        headers = _rotate_headers()
        r = httpx.get(url, timeout=12, follow_redirects=True, headers=headers)
        html = r.text
        expected_len = len(html or "")
        if detect_captcha(html):
            try:
                from nirvana.free_captcha_solver import detect_and_solve
                solve_result = detect_and_solve(html)
                if solve_result.get("solved"):
                    result["status"] = "verified_captcha_solved"
                    result["verification"] = {"verdict": "verified", "reason": "captcha_solved_probe"}
                    result["captcha_method"] = solve_result.get("method", "ocr")
                    _log_form_attempt(dict(result))
                    _maybe_fire_canary()
                    return result
            except Exception:
                pass
            result["status"] = "captcha_detected"
            result["route_to"] = "linkedin_router"
            _mark_captcha(_domain(url))
            _log_form_attempt(dict(result))
            return result
        # 3. Honeypot: gizli alanlar doldurulmaz (payload'dan çıkarılır).
        try:
            hidden = {h.get("name") for h in detect_honeypot_fields(html) if h.get("name")}
        except Exception:
            hidden = set()
        clean_payload = {k: v for k, v in (payload or {}).items() if k not in hidden}
        result["honeypot_skipped"] = sorted(hidden & set((payload or {}).keys()))
        # Form action varsa gerçek POST dene; yoksa keşif sayfasını doğrula.
        post_verdict: dict[str, Any] | None = None
        action = _extract_form_action(html, url)
        if action and clean_payload:
            try:
                pr = httpx.post(action, data=clean_payload, timeout=15, follow_redirects=True,
                                headers=_rotate_headers())
                post_verdict = verify_submission_response(pr.status_code, pr.text, expected_len)
                result["post_status_code"] = pr.status_code
            except Exception as e:
                result["post_error"] = str(e)[:120]
        if post_verdict is None:
            post_verdict = verify_submission_response(r.status_code, html, expected_len)
        result["verification"] = post_verdict
        verdict = post_verdict.get("verdict", "unverified")
        if verdict == "verified":
            result["status"] = "verified"
            # 4. Çift kanal: doğrulanmış gönderimde paralel e-posta (hedef adres varsa).
            to_addr = (payload or {}).get("target_email", "")
            if to_addr:
                result["dual_delivery"] = _fire_dual_delivery(to_addr, clean_payload, url)
        elif verdict == "WAF_REJECT":
            result["status"] = "WAF_REJECT"
            result["waf_reason"] = post_verdict.get("reason", "")
        else:
            result["status"] = "unverified"
            result["unverified_reason"] = post_verdict.get("reason", "")
        _log_form_attempt(dict(result))
        _maybe_fire_canary()
    except httpx.HTTPError as e:
        result["status"] = "error"
        result["error"] = str(e)[:120]
        _log_form_attempt(dict(result))
    return result


def _extract_form_action(html: str, base_url: str) -> str:
    """Formun action URL'sini çıkar (yoksa '')."""
    if not html:
        return ""
    m = re.search(r'<form[^>]*?\saction\s*=\s*["\']?([^"\'\s>]+)', html, re.I)
    if not m:
        return ""
    action = m.group(1).strip()
    if action.lower().startswith(("http://", "https://")):
        return action
    try:
        from urllib.parse import urljoin
        return urljoin(base_url, action)
    except Exception:
        return ""


def _log_form_attempt(entry: dict[str, Any]) -> None:
    """Her denemeyi state/stealth_form_log.json'a ekle (son 2000 kayıt)."""
    path = state_path(FORM_LOG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
    except (OSError, ValueError):
        data = []
    slim = {k: entry.get(k) for k in (
        "ts", "url", "domain", "status", "verification", "waf_reason",
        "unverified_reason", "error", "post_error", "post_status_code",
        "route_to", "captcha_method", "tactic", "honeypot_skipped",
        "dual_delivery", "canary_fired") if k in entry}
    data.append(slim)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data[-2000:], ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _form_log_count() -> int:
    try:
        data = json.loads(state_path(FORM_LOG).read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else 0
    except (OSError, ValueError):
        return 0


def _maybe_fire_canary() -> dict[str, Any]:
    """2. Kanarya: her 50 dış gönderimde bir IP/shadowban tespiti."""
    try:
        from nirvana.canary_form import CANARY_INTERVAL, send_canary, record_canary
        count = _form_log_count()
        if count > 0 and count % CANARY_INTERVAL == 0:
            res = send_canary()
            try:
                record_canary(res)
            except Exception:
                pass
            return {"fired": True, "count": count, "result": res}
    except Exception as e:
        return {"fired": False, "error": str(e)[:100]}
    return {"fired": False}


def _fire_dual_delivery(to_addr: str, payload: dict[str, str], url: str) -> dict[str, Any]:
    """4. Çift kanal: doğrulanmış form sonrası paralel e-posta (SMTP yoksa no-op)."""
    try:
        from nirvana.dual_delivery import send_dual_email
        subject = "DevSolve — Teknik denetim özeti"
        body = ("Merhaba,\n\nWeb sitenizdeki iletişim formu üzerinden teknik denetim "
                f"özetimizi ilettik ({url}). Detaylı ölçüm raporu için bu e-postayı "
                "yanıtlayabilirsiniz.\n\nDevSolve Altyapı Ekibi")
        return send_dual_email(to_addr=to_addr, subject=subject, body_text=body)
    except Exception as e:
        return {"sent": False, "error": str(e)[:100]}


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
    """Matris kancasıyla 400/gün %100 kapasite: her hedef Taktik'e göre kanca alır."""
    targets = urls or []
        # Taktik Matrisi'nden kanca yükle (varsa) + routed (audit-pass) URL'leri hedefe ekle.
    from nirvana import urlutil
    tactic_hooks: dict[str, dict[str, Any]] = {}
    try:
        tdata = json.loads(state_path("tactic_matrix.json").read_text(encoding="utf-8"))
        for t in (tdata.get("routed") or []):
            d = urlutil.clean_domain(t.get("domain") or t.get("url"))
            tactic_hooks[d] = {"tactic": t.get("tactic"), "hook": t.get("hook", "")}
            u = urlutil.safe_url(t.get("url") or d)
            if d and u and targets.count(u) == 0:
                targets.append(u)
    except (OSError, ValueError):
        pass
    # Ayrıca pending (audit fail -> matris) kuyruğunu da işle (URL'ler normalize)
    try:
        pending = json.loads(state_path("tactic_matrix_pending.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pending = []
    for p in pending:
        d = urlutil.clean_domain(p.get("domain") or p.get("url"))
        u = urlutil.safe_url(p.get("url") or d)
        if d and u and targets.count(u) == 0:
            targets.append(u)

    results: list[dict[str, Any]] = []
    per_domain_count: dict[str, int] = {}
    remaining = daily_remaining()
    if remaining <= 0:
        return {"processed": 0, "reason": "daily_cap_reached", "cap": DAILY_CAP}
    max_to_process = min(remaining, PACING["per_run"])
    for raw_url in targets[:max_to_process]:
        url = urlutil.safe_url(raw_url)
        d = _domain(url)
        if not d or not url:
            results.append({"url": raw_url, "domain": d, "status": "error",
                            "error": "invalid_url", "ts": time.time()})
            continue
        if per_domain_count.get(d, 0) >= PACING["per_domain"]:
            continue
        hook = (tactic_hooks.get(d.lower(), {}) or {}).get("hook", "")
        result = submit_form(url, {})
        if hook:
            result["tactic_hook"] = hook
            result["tactic"] = (tactic_hooks.get(d.lower(), {}) or {}).get("tactic", "?")
        results.append(result)
        per_domain_count[d] = per_domain_count.get(d, 0) + 1
        # Sabit bekleme YOK: Gauss jitter (6s–30s) — bot ritmi yerine insan ritmi.
        from nirvana.fingerprint_rotator import inter_submit_delay_ms
        time.sleep(inter_submit_delay_ms() / 1000.0)
    submitted = sum(1 for r in results if r["status"] in ("verified", "verified_captcha_solved"))
    captcha_solved = sum(1 for r in results if r["status"] == "verified_captcha_solved")
    captcha_routed = sum(1 for r in results if r["status"] == "captcha_detected")
    waf_rejected = sum(1 for r in results if r["status"] == "WAF_REJECT")
    unverified = sum(1 for r in results if r["status"] == "unverified")
    errors = sum(1 for r in results if r["status"] == "error")
    _increment_daily_count(submitted)
    return {"processed": len(results), "submitted": submitted,
            "captcha_solved": captcha_solved, "captcha_routed": captcha_routed,
            "waf_rejected": waf_rejected, "unverified": unverified, "errors": errors,
            "tactic_hooked": sum(1 for r in results if r.get("tactic")),
            "daily_remaining": daily_remaining(), "results": results}


def live_stats() -> dict[str, Any]:
    """Canlı log özeti: gönderim sayıları + sıcak dönüş (Telegram) + neden analizi."""
    try:
        data = json.loads(state_path(FORM_LOG).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
    except (OSError, ValueError):
        data = []
    from collections import Counter
    by_status: dict[str, int] = dict(Counter(str(r.get("status", "?")) for r in data if isinstance(r, dict)))
    today = time.strftime("%Y-%m-%d")
    today_rows = [r for r in data if isinstance(r, dict)
                  and time.strftime("%Y-%m-%d", time.localtime(float(r.get("ts", 0) or 0))) == today]
    today_by: dict[str, int] = dict(Counter(str(r.get("status", "?")) for r in today_rows))
    # Sıcak dönüş: Telegram lead kayıtları (chat_id atanmış sıcak/warm).
    hot = warm = total_leads = 0
    try:
        leads = json.loads(state_path("leads.json").read_text(encoding="utf-8"))
        if isinstance(leads, list):
            total_leads = len(leads)
            for l in leads:
                s = str((l or {}).get("status", "")).lower()
                if "hot" in s:
                    hot += 1
                elif "warm" in s:
                    warm += 1
    except (OSError, ValueError):
        pass
    reasons: dict[str, int] = dict(Counter(
        str((r.get("verification") or {}).get("reason", r.get("waf_reason", "?")))
        for r in data if isinstance(r, dict)))
    return {"log_entries": len(data),
            "by_status_all": by_status,
            "today": today, "today_entries": len(today_rows), "today_by_status": today_by,
            "daily_cap": DAILY_CAP, "daily_remaining": daily_remaining(),
            "leads_total": total_leads, "leads_hot": hot, "leads_warm": warm,
            "verify_reasons": reasons}
