"""100ms Pre-Flight — ağır render'dan ÖNCE hafif HTTP eleme (rapor Aşama 1).

Rapor denklemi: T_toplam = N · ( P(Form)·T_render + (1-P(Form))·T_preflight )
- T_preflight ≈ 0.1 sn (HEAD + pencere ile sınırlı GET, TLS parmak izi taklitli)
- T_render ≈ 15 sn (Chromium)

%79'luk form barındırmayan/engelli hedef kümesi Chromium açılmadan elenir; kalan
form_ok / js_dynamic hedefleri paralel işçi havuzuna (Chromium) aktarılır.

Verdict'ler: form_ok, js_dynamic, no_form, captcha, waf, unreachable.
Fail-open: pre-flight hatası/HTTP bütçesi yoksa hedef Chromium'a bırakılır.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import config
from nirvana import form_tokens, net_stealth
from nirvana.honeypot_human_sim import detect_honeypot_fields

logger = logging.getLogger(__name__)

FORM_OK = "form_ok"
JS_DYNAMIC = "js_dynamic"
NO_FORM = "no_form"
CAPTCHA = "captcha"
WAF = "waf"
UNREACHABLE = "unreachable"

JS_FRAMEWORK_MARKERS = (
    "__next_data__", "data-reactroot", "id=\"root\"", "id='app'", "ng-app",
    "__nuxt__", "data-v-", "v-app", "gatsby", "svelte", "shadowroot",
    "wpcf7", "wpforms", "elementor-form", "gravity-forms", "hs-form",
)
FORM_HINT_WORDS = ("form", "contact", "iletisim", "iletişim", "kontakt", "contacto", "bize yaz")

WAF_HEADER_MARKERS = ("cf-mitigated", "cf-ray", "x-sucuri", "x-akamai", "incap_ses", "cf-chl")


def js_framework_hints(html: str) -> list[str]:
    body = (html or "").lower()
    return [marker for marker in JS_FRAMEWORK_MARKERS if marker in body][:6]


def verdict_for(page: net_stealth.Page, analysis: dict[str, Any]) -> str:
    """Tek hedef için karar: form_ok / js_dynamic / captcha / waf / no_form / unreachable."""
    if page.error:
        return UNREACHABLE
    if page.status in (401, 403, 429):
        return WAF if analysis.get("waf_strict") else CAPTCHA
    if page.status >= 400:
        return UNREACHABLE
    if analysis.get("captcha"):
        return CAPTCHA
    if analysis.get("waf_strict"):
        return WAF
    if analysis.get("form_verified"):
        return FORM_OK
    body = (page.text or "").lower()
    if (js_framework_hints(body)
            and any(word in body for word in FORM_HINT_WORDS)
            and "<form" not in body):
        return JS_DYNAMIC
    if "<form" in body and analysis.get("has_fields"):
        return JS_DYNAMIC
    return NO_FORM


def screen_url(url: str, *, timeout: float | None = None, budget_ms: int | None = None) -> dict[str, Any]:
    """Tek hedefi ~100ms bütçeyle tara (HEAD + pencere ile sınırlı GET)."""
    wait = float(timeout or getattr(config, "PREFLIGHT_TIMEOUT_SECONDS", 6.0) or 6.0)
    budget = float(budget_ms if budget_ms is not None else getattr(config, "PREFLIGHT_BUDGET_MS", 100) or 100)
    started = time.monotonic()
    import form_preflight  # döngüsel importu önlemek için burada

    page = net_stealth.head(url, timeout=min(wait, 3.0))
    if page.error or page.status == 405 or page.status == 501:
        page = net_stealth.get_html(url, timeout=wait)
    elif page.ok:
        page = net_stealth.get_html(url, timeout=wait)
    analysis = form_preflight.analyze_html(page.text or "", header_blob=page.header_blob())
    if any(marker in page.header_blob() for marker in WAF_HEADER_MARKERS) and not analysis["form_verified"]:
        analysis["waf_strict"] = True
    verdict = verdict_for(page, analysis)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    honeypots = detect_honeypot_fields(page.text or "") if verdict in (FORM_OK, JS_DYNAMIC) else []
    captcha_kind = form_tokens.captcha_kind(page.text or "") if verdict != UNREACHABLE else "none"
    return {
        "url": str(page.url or url),
        "requested_url": url,
        "verdict": verdict,
        "status": page.status,
        "elapsed_ms": elapsed_ms,
        "within_budget": elapsed_ms <= max(50.0, budget * 3),
        "backend": page.backend,
        "impersonate": page.impersonate,
        "captcha_kind": captcha_kind,
        "honeypots": [row.get("name") for row in honeypots][:8],
        "tokens": sorted(form_tokens.token_payload(page.text or "")),
        "js_hints": js_framework_hints(page.text or ""),
        "truncated": page.truncated,
        "error": page.error,
        "analysis": analysis,
    }



def screen_slice(jobs: list[dict[str, Any]], *, budget_ms: int | None = None,
                 workers: int = 6, deadline_s: float | None = None,
                 verify_gate: Any = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Paralel pre-flight: (geçen, elenen, istatistik).

    `verify_gate(url)` -> bool: ek HTTP bütçesi kapısı (domain_store.consume_http).
    False dönerse hedef elenmez, Chromium'a bırakılır (fail-open).
    """
    started = time.monotonic()
    keep: list[dict[str, Any]] = []
    drop: list[dict[str, Any]] = []
    stats = {"screened": 0, "form_ok": 0, "js_dynamic": 0, "no_form": 0,
             "captcha": 0, "waf": 0, "unreachable": 0, "gate_skipped": 0,
             "avg_ms": 0, "backend": net_stealth.backend()}
    total_ms = 0
    if not jobs:
        return keep, drop, stats
    pool_size = max(1, min(int(workers), 12))

    def _allowed(job: dict[str, Any]) -> bool:
        if verify_gate is None:
            return True
        try:
            return bool(verify_gate(str(job.get("url") or "")))
        except Exception:  # noqa: BLE001 — kapı hatası hattı durdurmaz
            return True

    allowed = [job for job in jobs if _allowed(job)]
    stats["gate_skipped"] = len(jobs) - len(allowed)
    keep.extend(jobs[len(allowed):])  # bütçe yoksa dokunma (fail-open)

    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futures = {
            pool.submit(screen_url, str(job.get("url") or ""), budget_ms=budget_ms): job
            for job in allowed
        }
        for future in as_completed(futures):
            job = futures[future]
            if deadline_s is not None and (time.monotonic() - started) > float(deadline_s):
                keep.append(job)
                continue
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001 — hata = dokunma
                logger.info("Pre-flight hata %s: %s", job.get("url"), exc)
                keep.append(job)
                continue
            stats["screened"] += 1
            total_ms += int(row.get("elapsed_ms") or 0)
            verdict = str(row.get("verdict") or NO_FORM)
            stats[verdict] = int(stats.get(verdict, 0)) + 1
            merged = {**job, "preflight": row}
            if verdict in (FORM_OK, JS_DYNAMIC):
                keep.append(merged)
            else:
                drop.append(merged)
    stats["avg_ms"] = int(total_ms / max(1, stats["screened"]))
    stats["elapsed_s"] = round(time.monotonic() - started, 2)
    logger.info(
        "Pre-flight: %s/%s form_ok=%s js=%s no_form=%s captcha=%s waf=%s ort=%sms (%s)",
        len(keep), len(jobs), stats["form_ok"], stats["js_dynamic"], stats["no_form"],
        stats["captcha"], stats["waf"], stats["avg_ms"], stats["backend"],
    )
    return keep, drop, stats


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Lane çıktısı: pre-flight motor durumu (ağ isteği YOK)."""
    return {
        "backend": net_stealth.backend(),
        "impersonate": net_stealth.DEFAULT_IMPERSONATE,
        "budget_ms": int(getattr(config, "PREFLIGHT_BUDGET_MS", 100) or 100),
        "js_fallback": bool(getattr(config, "JS_FALLBACK_ENABLED", True)),
        "verdicts": [FORM_OK, JS_DYNAMIC, NO_FORM, CAPTCHA, WAF, UNREACHABLE],
        "js_markers": len(JS_FRAMEWORK_MARKERS),
    }
