"""
Playwright site collector.

Visits each target URL, extracts a company description from public page content,
and records whether a contact form (or a dedicated contact page that contains
one) is available.

This module only *reads* public pages. It does not submit forms, solve CAPTCHAs,
or bypass access controls. Sites that block automation are recorded as failed
and skipped.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

import browser
import config
from site_signals import extract_stack_hints

logger = logging.getLogger(__name__)

CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/contactus",
    "/get-in-touch",
    "/getintouch",
    "/connect",
    "/iletisim",
    "/iletişim",
    "/bize-ulasin",
    "/bize-ulasın",
    "/about",
    "/about-us",
    "/support",
    "/help",
)

CONTACT_HREF_RE = re.compile(
    r"contact|get[-_ ]?in[-_ ]?touch|enquire|inquire|talk[-_ ]?to|book[-_ ]?demo|"
    r"iletisim|iletişim|bize[-_ ]?ula[sş]|satis|satış",
    re.I,
)

FALLBACK_CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/iletisim",
    "/bize-ulasin",
)

CAPTCHA_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    ".g-recaptcha",
    "[data-sitekey]",
)

COOKIE_OK_RE = re.compile(
    r"accept|agree|allow all|allow cookies|kabul|izin ver|anlad[ıi]m|"
    r"got it|continue|tümünü kabul|tumunu kabul",
    re.I,
)
COOKIE_NO_RE = re.compile(
    r"reject|decline|deny|refuse|reddet|sadece gerekli|necessary only|"
    r"essential only|reject all|customize|tercih",
    re.I,
)

SKIP_FRAME_RE = re.compile(
    r"recaptcha|hcaptcha|turnstile|youtube|vimeo|doubleclick|"
    r"facebook\.com/plugins|intercom|crisp\.chat|tidio\.|drift\.com|"
    r"tawk\.to|hotjar|googletagmanager",
    re.I,
)

WIDGET_IFRAME_RE = re.compile(
    r"hsforms\.com|formspree\.io|jotform\.|forms\.gle|docs\.google\.com/forms|"
    r"wufoo\.com|cognitoforms\.com|123formbuilder|forms\.office\.com|"
    r"web3forms\.com|getform\.io|usebasin\.com|formcarry\.com",
    re.I,
)

NEWSLETTER_RE = re.compile(
    r"newsletter|subscribe|bülten|bulten|mailing.?list|ebulten|kampanya.?mail",
    re.I,
)


def scan_urls(
    urls: Iterable[str],
    *,
    headless: Optional[bool] = None,
    timeout_ms: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Scan each URL and return a list of lead dicts."""
    unique = _dedupe_urls(urls)
    if not unique:
        return []

    headless = config.HEADLESS if headless is None else headless
    timeout_ms = config.NAV_TIMEOUT_MS if timeout_ms is None else timeout_ms
    results: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        chromium = browser.launch_browser(playwright, headless=headless)
        context = browser.collect_context(chromium)
        page = browser.new_page(context, timeout_ms=timeout_ms)
        try:
            for url in unique:
                logger.info("Collecting %s", url)
                try:
                    results.append(scan_one(page, url, timeout_ms=timeout_ms))
                except Exception as exc:  # noqa: BLE001 — record and continue
                    logger.exception("Collector failed for %s", url)
                    results.append(_failed_lead(url, str(exc)))
        finally:
            context.close()
            chromium.close()

    return results


def page_has_open_form(page: Page, *, timeout_ms: int | None = None) -> bool:
    """True if a fillable form/email control is in the DOM within the fingerprint window."""
    wait = int(timeout_ms if timeout_ms is not None else getattr(config, "DOM_FINGERPRINT_MS", 2000) or 2000)
    selector = (
        "form input[type='email'], form input[name*='mail' i], form textarea, "
        "input[type='email'], iframe[src*='hsforms'], iframe[src*='formspree'], "
        "iframe[src*='jotform']"
    )
    try:
        page.wait_for_selector(selector, timeout=max(400, wait))
        return True
    except Exception:  # noqa: BLE001
        return False


def scan_one(page: Page, url: str, *, timeout_ms: int) -> dict[str, Any]:
    """Collect description + contact-form metadata for a single URL."""
    url = _normalize_url(url)
    cap = max(8_000, min(int(timeout_ms), 25_000))
    try:
        page.set_default_timeout(cap)
        page.set_default_navigation_timeout(cap)
    except Exception:  # noqa: BLE001
        pass
    try:
        return _scan_one_body(page, url, timeout_ms=cap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Collect aborted %s: %s", url, exc)
        return _failed_lead(url, str(exc)[:300])


def _scan_one_body(page: Page, url: str, *, timeout_ms: int) -> dict[str, Any]:
    _goto(page, url, timeout_ms)
    _dismiss_cookie_banner(page)
    if not page_has_open_form(page):
        found = False
        for contact_url in _contact_candidates(page, url)[:1]:
            try:
                _goto(page, contact_url, min(timeout_ms, 8_000))
                _dismiss_cookie_banner(page)
                found = page_has_open_form(page)
            except Exception:  # noqa: BLE001
                found = False
            break
        if not found:
            logger.info("DOM fingerprint miss %s — skip without fill", url)
            return {
                "url": url,
                "final_url": getattr(page, "url", url),
                "company_name": "",
                "description": "",
                "page_excerpt": "",
                "stack_hints": [],
                "contact_form": {"found": False, "page_url": getattr(page, "url", url), "fields": []},
                "captcha_detected": False,
                "easy_score": 10,
                "status": "skipped_no_open_form",
                "error": "DOM fingerprint: no form/email in 2s",
            }

    company_name = _extract_company_name(page, url)
    description = _extract_description(page)
    page_text = _visible_text(page)

    form = _inspect_forms(page, page.url)
    captcha = _captcha_present(page) or _challenge_page(page)

    if captcha:
        lead = _lead_payload(
            url,
            page,
            company_name,
            description,
            page_text,
            form={"found": False, "page_url": page.url, "fields": []},
            captcha=True,
        )
        lead["status"] = "skipped_captcha"
        logger.info("Collected %s — skipped_captcha (no solve attempt)", url)
        return lead

    if not form["found"]:
        followed = 0
        for contact_url in _contact_candidates(page, url):
            if followed >= 2:
                break
            followed += 1
            logger.info("Following contact page %s", contact_url)
            try:
                _goto(page, contact_url, timeout_ms)
                _dismiss_cookie_banner(page)
                form = _inspect_forms(page, page.url)
                captcha = _captcha_present(page) or _challenge_page(page)
                if captcha:
                    lead = _lead_payload(
                        url,
                        page,
                        company_name,
                        description,
                        page_text,
                        form={"found": False, "page_url": page.url, "fields": []},
                        captcha=True,
                    )
                    lead["status"] = "skipped_captcha"
                    logger.info("Collected %s — skipped_captcha on contact page", url)
                    return lead
                if not description:
                    description = _extract_description(page)
                page_text = f"{page_text}\n{_visible_text(page)}"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not open contact page %s: %s", contact_url, exc)
                continue
            if form["found"]:
                break

    lead = _lead_payload(url, page, company_name, description, page_text, form, captcha)
    logger.info(
        "Collected %s (form=%s captcha=%s)",
        url,
        form.get("found"),
        captcha,
    )
    return lead


def _failed_lead(url: str, error: str) -> dict[str, Any]:
    return {
        "url": _normalize_url(url),
        "final_url": None,
        "company_name": _hostname(url),
        "description": "",
        "page_excerpt": "",
        "stack_hints": [],
        "contact_form": {"found": False, "page_url": None, "fields": []},
        "captcha_detected": False,
        "status": "failed",
        "error": error,
    }


def _lead_payload(
    url: str,
    page: Page,
    company_name: str,
    description: str,
    page_text: str,
    form: dict[str, Any],
    captcha: bool,
) -> dict[str, Any]:
    return {
        "url": url,
        "final_url": page.url,
        "company_name": company_name,
        "description": description,
        "page_excerpt": page_text[:3000],
        "stack_hints": extract_stack_hints(company_name, description, page_text),
        "contact_form": form,
        "captcha_detected": captcha,
        "status": "skipped_captcha" if captcha else "collected",
        "error": None,
    }


_CHALLENGE_RE = re.compile(
    r"just a moment|attention required|cf-browser-verification|checking your browser",
    re.I,
)


def _challenge_page(page: Page) -> bool:
    try:
        blob = page.evaluate(
            """() => ((document.title || '') + '\\n' + ((document.body && document.body.innerText) || '')).slice(0, 1800)"""
        )
    except Exception:  # noqa: BLE001
        return False
    return bool(_CHALLENGE_RE.search(str(blob or "")))


def _goto(page: Page, url: str, timeout_ms: int) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(800)
    except PlaywrightTimeout as exc:
        raise RuntimeError(f"Timed out loading {url}") from exc


def _dismiss_cookie_banner(page: Page) -> None:
    """Click an obvious consent button so the real page content is visible."""
    try:
        buttons = page.get_by_role("button")
        count = min(buttons.count(), 16)
        for i in range(count):
            btn = buttons.nth(i)
            if not btn.is_visible():
                continue
            label = (btn.inner_text() or "").strip()
            if not label or len(label) > 80:
                continue
            if COOKIE_NO_RE.search(label):
                continue
            if COOKIE_OK_RE.search(label):
                btn.click(timeout=1500)
                page.wait_for_timeout(300)
                return
    except Exception:  # noqa: BLE001
        return


def _extract_company_name(page: Page, url: str) -> str:
    og = _meta(page, "og:site_name") or _meta(page, "og:title")
    if og:
        return og.split("|")[0].split("–")[0].strip()[:120]

    ld_name = _json_ld_name(page)
    if ld_name:
        return ld_name[:120]

    try:
        title = page.title() or ""
        if title:
            return title.split("|")[0].split("–")[0].strip()[:120]
    except Exception:  # noqa: BLE001
        pass
    return _hostname(url)


def _extract_description(page: Page) -> str:
    for key in ("description", "og:description", "twitter:description"):
        value = _meta(page, key)
        if value and len(value) > 40:
            return value.strip()

    snippets: list[str] = []
    for selector in ("main p", "article p", ".about p", "#about p", "p"):
        try:
            loc = page.locator(selector)
            limit = min(loc.count(), 8)
            for i in range(limit):
                text = " ".join((loc.nth(i).inner_text() or "").split())
                if len(text) >= 60:
                    snippets.append(text)
                if sum(len(s) for s in snippets) >= 1500:
                    break
            if snippets:
                break
        except Exception:  # noqa: BLE001
            continue
    return " ".join(snippets).strip()[:2000]


def _visible_text(page: Page) -> str:
    try:
        text = page.evaluate("() => (document.body && document.body.innerText) || ''")
    except Exception:  # noqa: BLE001
        return ""
    return " ".join((text or "").split())[:8000]


def _meta(page: Page, name: str) -> str:
    try:
        if name.startswith("og:") or name.startswith("twitter:"):
            loc = page.locator(f'meta[property="{name}"]')
            if loc.count() == 0:
                loc = page.locator(f'meta[name="{name}"]')
        else:
            loc = page.locator(f'meta[name="{name}"]')
            if loc.count() == 0:
                loc = page.locator(f'meta[property="{name}"]')
        if loc.count() == 0:
            return ""
        return (loc.first.get_attribute("content") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _json_ld_name(page: Page) -> str:
    try:
        blobs = page.evaluate(
            """() => Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                .map(s => s.textContent || '')"""
        )
    except Exception:  # noqa: BLE001
        return ""

    import json

    for raw in blobs or []:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            types = item.get("@type")
            type_list = types if isinstance(types, list) else [types]
            if any(t in {"Organization", "LocalBusiness", "Corporation"} for t in type_list):
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
    return ""


def _iter_frames(page: Page):
    yield page.main_frame
    for frame in page.frames:
        url = (frame.url or "").lower()
        if frame == page.main_frame:
            continue
        if not url or SKIP_FRAME_RE.search(url):
            continue
        yield frame


def _inspect_forms(page: Page, page_url: str) -> dict[str, Any]:
    best: Optional[dict[str, Any]] = None
    best_score = -1
    for frame in _iter_frames(page):
        try:
            count = int(frame.evaluate("() => document.querySelectorAll('form').length") or 0)
        except Exception:  # noqa: BLE001
            continue
        forms = frame.locator("form")
        for i in range(min(count, 15)):
            form = forms.nth(i)
            try:
                if not form.is_visible(timeout=1_500):
                    continue
                info = _describe_form(form, page_url)
            except Exception:  # noqa: BLE001
                continue
            score = info.pop("_score")
            if score > best_score:
                best_score = score
                best = info

    if best and best_score >= 2:
        return best
    widget = _inspect_widget_iframe(page, page_url)
    if widget:
        return widget
    return _inspect_formless(page, page_url)


def _inspect_widget_iframe(page: Page, page_url: str) -> Optional[dict[str, Any]]:
    for frame in page.frames:
        src = frame.url or ""
        if not WIDGET_IFRAME_RE.search(src):
            continue
        logger.info("Form widget iframe %s", src[:120])
        return {
            "found": True,
            "page_url": page_url,
            "action": src,
            "method": "post",
            "fields": [
                {"name": "email", "type": "email", "purpose": "email", "label": "email"},
                {"name": "message", "type": "textarea", "purpose": "message", "label": "message"},
            ],
        }
    return None


def _inspect_formless(page: Page, page_url: str) -> dict[str, Any]:
    """Landing-page widgets with email + textarea and no <form> tag."""
    n_email = 0
    n_area = 0
    for frame in _iter_frames(page):
        try:
            e_n, a_n = frame.evaluate(
                """() => {
                    const vis = (e) => !!(e && e.getClientRects && e.getClientRects().length);
                    const emails = [...document.querySelectorAll('input[type="email"]')].filter(vis);
                    const areas = [...document.querySelectorAll('textarea')].filter(vis);
                    return [emails.length, areas.length];
                }"""
            )
            n_email += int(e_n or 0)
            n_area += int(a_n or 0)
        except Exception:  # noqa: BLE001
            continue
    if n_email < 1 or n_area < 1:
        return {"found": False, "page_url": page_url, "fields": []}
    fields: list[dict[str, str]] = []
    if n_email:
        fields.append({"name": "email", "type": "email", "purpose": "email", "label": "email"})
    if n_area:
        fields.append({"name": "message", "type": "textarea", "purpose": "message", "label": "message"})
    return {
        "found": True,
        "page_url": page_url,
        "action": page_url,
        "method": "post",
        "fields": fields,
    }


def _describe_form(form, page_url: str) -> dict[str, Any]:
    controls = form.locator("input, textarea, select")
    fields: list[dict[str, str]] = []
    score = 0
    n = min(controls.count(), 30)
    for i in range(n):
        el = controls.nth(i)
        try:
            el_type = (el.get_attribute("type") or "text").lower()
            if el_type in {"hidden", "submit", "button", "image", "file", "checkbox", "radio"}:
                continue
            name = el.get_attribute("name") or el.get_attribute("id") or ""
            placeholder = el.get_attribute("placeholder") or ""
            label = _nearby_label(el)
            purpose = classify_field(name, el_type, placeholder, label)
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            fields.append(
                {
                    "name": name,
                    "type": el_type if tag != "textarea" else "textarea",
                    "purpose": purpose or "unknown",
                    "label": (label or placeholder)[:80],
                }
            )
            if purpose in {"email", "message", "name", "first_name", "last_name"}:
                score += 2
            elif purpose and purpose != "unknown":
                score += 1
            if tag == "textarea":
                score += 2
        except Exception:  # noqa: BLE001
            continue

    action = form.get_attribute("action") or page_url
    method = (form.get_attribute("method") or "post").lower()
    form_id = (form.get_attribute("id") or "") + " " + (form.get_attribute("class") or "")
    purposes = {str(f.get("purpose") or "") for f in fields}
    if NEWSLETTER_RE.search(f"{action} {form_id}") and "message" not in purposes:
        score = 0
    return {
        "found": True,
        "page_url": page_url,
        "action": urljoin(page_url, action),
        "method": method,
        "fields": fields,
        "_score": score,
    }


def classify_field(name: str, el_type: str, placeholder: str, label: str) -> Optional[str]:
    """Map a form control onto a canonical purpose (name, email, message, ...)."""
    blob = " ".join([name, el_type, placeholder, label]).lower()
    blob = re.sub(r"[^\w]+", " ", blob, flags=re.UNICODE)

    if el_type == "email" or re.search(r"\b(e-?mail|eposta|e.?posta|youremail|useremail|mail adres)\b", blob):
        return "email"
    if el_type == "tel" or re.search(r"\b(phone|mobile|tel|whatsapp|telefon|cep|gsm)\b", blob):
        return "phone"
    if re.search(r"\b(last[-_ ]?name|lastname|surname|soyad(iniz|ınız)?|family.?name)\b", blob):
        return "last_name"
    if re.search(r"\b(first[-_ ]?name|firstname|given.?name|ad[ıi]n[ıi]z)\b", blob) and not re.search(
        r"soyad|last", blob
    ):
        return "first_name"
    if re.search(
        r"\b(full[-_ ]?name|your[-_ ]?name|\bname\b|"
        r"isim|ad soyad|fullname)\b",
        blob,
    ):
        return "name"
    if re.search(r"\b(company|organization|organisation|business|sirket|şirket|firma)\b", blob):
        return "company"
    if re.search(r"\b(website|url|site|web sitesi)\b", blob):
        return "website"
    if re.search(r"\b(subject|topic|konu|baslik|başlık)\b", blob):
        return "subject"
    if re.search(
        r"\b(message|comment|inquiry|enquiry|question|details|how can we|"
        r"mesaj|notunuz|aciklama|açıklama|talebiniz|your message)\b",
        blob,
    ):
        return "message"
    if el_type == "textarea":
        return "message"
    return None


def _nearby_label(el) -> str:
    try:
        return el.evaluate(
            """(e) => {
                if (e.id) {
                    const lab = document.querySelector(`label[for="${CSS.escape(e.id)}"]`);
                    if (lab) return lab.innerText || '';
                }
                const parent = e.closest('label');
                return parent ? (parent.innerText || '') : '';
            }"""
        ) or ""
    except Exception:  # noqa: BLE001
        return ""


def _captcha_present(page: Page) -> bool:
    for selector in CAPTCHA_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _contact_candidates(page: Page, base_url: str) -> list[str]:
    """Same-origin contact URLs: in-page links first, then common paths."""
    origin = _origin(base_url)
    found: list[str] = []
    seen: set[str] = set()

    def add(href: str) -> None:
        if _origin(href) != origin:
            return
        key = href.rstrip("/").lower()
        if key in seen:
            return
        seen.add(key)
        found.append(href)

    try:
        hrefs = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.href)
                .filter(Boolean)
                .slice(0, 80)"""
        )
    except Exception:  # noqa: BLE001
        hrefs = []

    for href in hrefs or []:
        if not isinstance(href, str):
            continue
        path = urlparse(href).path or ""
        if CONTACT_HREF_RE.search(href) or CONTACT_HREF_RE.search(path):
            add(href)
        if len(found) >= 2:
            break

    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    for path in FALLBACK_CONTACT_PATHS:
        add(urljoin(root, path))
    return found


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("Empty URL")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def _hostname(url: str) -> str:
    try:
        host = urlparse(_normalize_url(url)).hostname or url
        return host.removeprefix("www.")
    except Exception:  # noqa: BLE001
        return url


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".lower()


# Public aliases used by form_submitter so browser helpers live in one place.
goto_page = _goto
dismiss_cookie_banner = _dismiss_cookie_banner
captcha_present = _captcha_present


def _dedupe_urls(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls:
        raw = (raw or "").strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            url = _normalize_url(raw)
        except ValueError:
            continue
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    targets = sys.argv[1:] or ["https://example.com"]
    print(json.dumps(scan_urls(targets), indent=2))
