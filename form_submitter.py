"""
Playwright contact-form submitter.

Fills a previously discovered public contact form with the sender identity from
`.env` and the personalized value proposition from the qualifier.

Behaviour:
- Adaptive jitter before submit: 15–30s on ordinary sites, 45–60s on WAF/Cloudflare.
- Fills only visible input/textarea controls; skips hidden and honeypot fields.
- Types at 30–70ms per character. Never solves CAPTCHAs.
- Submit cascade: scroll → requestSubmit → MouseEvent click → Enter.
- Confirms via POST/AJAX 2xx/204, not only a "thank you" string.
- Does not bypass logins or access controls.

Only use this against properties you are authorized to contact.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any, Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

import browser
import config
import optout
from collector import captcha_present, classify_field, dismiss_cookie_banner, goto_page, page_has_open_form

logger = logging.getLogger(__name__)

SUCCESS_RE = re.compile(
    r"thank you|thanks for|message (has been )?sent|we('ll| will) (be in touch|contact)|"
    r"successfully submitted|your (inquiry|message|request).*(received|sent)|"
    r"form submitted|we'll get back|"
    r"teşekkür|tesekkur|mesajınız.*(iletil|ulaş)|mesajiniz.*(iletil|ulas)|"
    r"alınmıştır|alinmistir|gönderildi|gonderildi|iletilmiştir|iletilmistir",
    re.I,
)

HONEYPOT_NAME_RE = re.compile(
    r"honeypot|fax|leave[-_ ]?blank|bot[-_ ]?field|website2|url2|"
    r"confirmemail|middle.?name|fax_number|botcheck|leaveempty|hp_|"
    r"company_url|zip2|address2",
    re.I,
)

CONSENT_RE = re.compile(
    r"kvkk|aydınlatma|aydinlatma|gizlilik|privacy|terms of|kullanım şart|"
    r"kullanim sart|okudum|kabul ediyorum|i agree|i have read|açık rıza|acik riza",
    re.I,
)

SUBMIT_NAME_RE = re.compile(
    r"send|submit|contact|enquire|inquire|gönder|gonder|ilet|başvur|basvur|"
    r"teklif|quote|message|get in touch|bize yaz",
    re.I,
)

REVEAL_RE = re.compile(
    r"get in touch|contact us|bize yaz|teklif al|request (a )?quote|"
    r"leave a message|mesaj bırak|mesaj birak|talk to us|iletişime",
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

_NET_NOISE = (
    "google-analytics",
    "googletagmanager",
    "gtag/",
    "facebook.com/tr",
    "hotjar",
    "sentry.io",
    "clarity.ms",
    "doubleclick",
    "segment.io",
    "mixpanel",
    "amplitude",
    "newrelic",
    "bugsnag",
    "fullstory",
    "intercom.io",
    "intercomcdn",
    "hs-analytics",
    "hubspot.com/cs/",
    "cloudflareinsights",
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "google.com/ccm",
    "adsystem",
)

IS_HONEYPOT_JS = """(e) => {
  if (!e) return true;
  const type = (e.getAttribute('type') || '').toLowerCase();
  if (['hidden', 'submit', 'button', 'image', 'file'].includes(type)) return true;
  const style = window.getComputedStyle(e);
  if (style.display === 'none' || style.visibility === 'hidden') return true;
  if (parseFloat(style.opacity) === 0) return true;
  const rect = e.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return true;
  if (rect.bottom < 0 || rect.right < 0 || rect.left > (window.innerWidth + 80)) return true;
  if (e.getAttribute('aria-hidden') === 'true') return true;
  if (e.tabIndex < 0 && (style.position === 'absolute' || style.position === 'fixed')) {
    if (Math.abs(parseFloat(style.left) || 0) > 2000 || Math.abs(parseFloat(style.top) || 0) > 2000) {
      return true;
    }
  }
  return false;
}"""

REQUEST_SUBMIT_JS = """() => {
  const forms = Array.from(document.querySelectorAll('form'));
  for (const f of forms) {
    const rect = f.getBoundingClientRect();
    if (rect.width < 2 && rect.height < 2) continue;
    if (typeof f.requestSubmit === 'function') { f.requestSubmit(); return true; }
  }
  return false;
}"""

MOUSE_CLICK_JS = """() => {
  const re = /send|submit|contact|enquire|inquire|gönder|gonder|ilet|başvur|basvur|teklif|quote|message/i;
  const pick = [];
  const walk = (root) => {
    root.querySelectorAll('button, input[type="submit"], [role="button"], .hs-button, a.button').forEach((e) => pick.push(e));
    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });
  };
  walk(document);
  const btn = pick.find((e) => {
    const label = ((e.innerText || e.value || e.getAttribute('aria-label') || '') + '').trim();
    const type = (e.getAttribute('type') || '').toLowerCase();
    return type === 'submit' || re.test(label);
  });
  if (!btn) return false;
  btn.scrollIntoView({behavior: 'instant', block: 'center'});
  btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
  return true;
}"""


class FormNetWatcher:
    """Watch for a real form POST/AJAX — ignore analytics beacons."""

    def __init__(self, page: Page) -> None:
        self.hit = False
        self.status: int | None = None
        self.url = ""
        self._page = page
        page.on("response", self._on_response)

    def _on_response(self, response: Any) -> None:
        if self.hit:
            return
        try:
            request = response.request
            method = (request.method or "").upper()
            if method not in {"POST", "PUT", "PATCH"}:
                return
            url = (request.url or "").lower()
            if any(tok in url for tok in _NET_NOISE):
                return
            code = int(response.status or 0)
            if 200 <= code < 400:
                self.hit = True
                self.status = code
                self.url = request.url or ""
                logger.info("Form network confirm %s %s", code, self.url[:120])
        except Exception:  # noqa: BLE001
            return

    def wait(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.hit:
                return True
            try:
                self._page.wait_for_timeout(150)
            except Exception:  # noqa: BLE001
                break
        return self.hit

    def close(self) -> None:
        try:
            self._page.remove_listener("response", self._on_response)
        except Exception:  # noqa: BLE001
            pass


def submit_lead(
    lead: dict[str, Any],
    *,
    page: Optional[Page] = None,
    headless: Optional[bool] = None,
    during_delay: Optional[Any] = None,
) -> dict[str, Any]:
    """Submit one lead. Manages its own browser if `page` is not provided."""
    if page is not None:
        return _submit_with_page(page, lead, during_delay=during_delay)

    headless = config.HEADLESS if headless is None else headless
    with sync_playwright() as playwright:
        chromium = browser.launch_browser(playwright, headless=headless)
        context = browser.submit_context(chromium)
        owned = browser.new_page(context)
        try:
            return _submit_with_page(owned, lead, during_delay=during_delay)
        finally:
            context.close()
            chromium.close()


def submit_leads(
    leads: list[dict[str, Any]],
    *,
    headless: Optional[bool] = None,
) -> list[dict[str, Any]]:
    """Submit many leads, reusing one browser. Applies the inter-submit delay."""
    headless = config.HEADLESS if headless is None else headless
    updated: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        chromium = browser.launch_browser(playwright, headless=headless)
        context = browser.submit_context(chromium)
        page = browser.new_page(context)
        try:
            for index, lead in enumerate(leads):
                logger.info("Submitting %s (%s/%s)", lead.get("url"), index + 1, len(leads))
                updated.append(_submit_with_page(page, lead))
        finally:
            context.close()
            chromium.close()
    return updated


def _fast_fail(lead: dict[str, Any]) -> bool:
    attempts = int(lead.get("submit_attempts") or 0)
    status = str(lead.get("status") or "")
    return attempts >= 1 or status in {"failed", "skipped_submit_failed"}


def _site_budget(lead: dict[str, Any] | None = None) -> float:
    if lead is not None and _fast_fail(lead):
        return float(getattr(config, "SUBMIT_FAST_FAIL_SECONDS", 15) or 15)
    return float(getattr(config, "SITE_TIMEOUT_SECONDS", 90) or 90)


def _submit_with_page(
    page: Page,
    lead: dict[str, Any],
    *,
    during_delay: Optional[Any] = None,
) -> dict[str, Any]:
    result = dict(lead)
    form_meta = lead.get("contact_form") or {}
    form_url = form_meta.get("page_url") or lead.get("final_url") or lead.get("url")

    if optout.is_url_opted_out(str(lead.get("url") or form_url or "")):
        logger.info("Skipping submit for %s (unsubscribed)", lead.get("url"))
        result["status"] = "skipped_unsubscribed"
        return result

    if lead.get("captcha_detected") or form_meta.get("found") is not True:
        reason = "captcha_detected" if lead.get("captcha_detected") else "no_contact_form"
        logger.info("Skipping submit for %s (%s)", lead.get("url"), reason)
        result["status"] = "skipped_captcha" if lead.get("captcha_detected") else "skipped_no_contact_form"
        return result

    message = (lead.get("value_proposition") or "").strip()
    if not message:
        result["status"] = "skipped_no_pitch"
        result["error"] = "No value proposition to send"
        return result
    if "STOP" not in message and "Unsubscribe" not in message:
        message = f"{message} {optout.form_courtesy_line(turkish=False)}"
    subject = (lead.get("form_subject") or "").strip()

    busy_start = time.monotonic()
    paused = 0.0
    budget = _site_budget(lead)
    fast = _fast_fail(lead)

    def busy_used() -> float:
        return time.monotonic() - busy_start - paused

    def pause(fn: Any) -> None:
        nonlocal paused
        t0 = time.monotonic()
        try:
            fn()
        finally:
            paused += time.monotonic() - t0

    watcher: FormNetWatcher | None = None
    last_field: list[Any] = []
    try:
        if fast:
            logger.info("Fast-fail %.0fs window for %s", budget, lead.get("url"))
        cap_ms = max(4_000, min(int(budget * 1000), 25_000))
        try:
            page.set_default_timeout(cap_ms)
            page.set_default_navigation_timeout(cap_ms)
        except Exception:  # noqa: BLE001
            pass
        goto_page(page, form_url, cap_ms)
        dismiss_cookie_banner(page)
        if not page_has_open_form(page):
            result["status"] = "skipped_no_open_form"
            result["error"] = "DOM fingerprint: no form/email in 2s"
            logger.info("Fingerprint miss on submit %s — next site", lead.get("url"))
            return result
        _wait_widgets(page)
        if captcha_present(page):
            result["status"] = "skipped_captcha"
            result["captcha_detected"] = True
            logger.warning("CAPTCHA on %s — not submitting", form_url)
            return result
        if busy_used() > budget:
            raise TimeoutError("site budget before fill")

        filled = _fill_form(page, message, subject=subject, last_field=last_field)
        if filled < 2 and not fast:
            _reveal_widgets(page)
            dismiss_cookie_banner(page)
            filled = max(filled, _fill_form(page, message, subject=subject, last_field=last_field))
        if filled < 2:
            filled = max(filled, _fill_formless(page, message, subject=subject, last_field=last_field))
        _tick_consent(page)
        if filled == 0:
            result["status"] = "failed"
            result["error"] = "Could not map any visible form fields"
            return result

        needed = 0.0 if fast else delay_seconds_for(lead)
        jitter_started = time.monotonic()
        if during_delay and needed > 8:
            def _safe_prefetch() -> None:
                try:
                    during_delay()
                except Exception:
                    logger.exception("during_delay callback failed")

            pause(_safe_prefetch)
        remaining = needed - (time.monotonic() - jitter_started)
        if remaining > 0:
            logger.info("Waiting %.1fs more before submit", remaining)
            pause(lambda: time.sleep(remaining))

        if busy_used() > budget:
            raise TimeoutError("site budget before click")

        watcher = FormNetWatcher(page)
        clicked = _submit_cascade(page, watcher, last_field)
        success_dom = _looks_successful(page)
        net = bool(watcher.hit)
        if not net:
            watcher.wait(2.5)
            net = bool(watcher.hit)
            success_dom = success_dom or _looks_successful(page)

        result["submit_fields_filled"] = filled
        result["submitted_url"] = form_url
        result["error"] = None
        if net or success_dom:
            result["status"] = "submitted_confirmed" if net else "submitted"
            logger.info(
                "Form post finished for %s status=%s net=%s dom=%s",
                lead.get("url"),
                result["status"],
                net,
                success_dom,
            )
            return result
        if not clicked:
            result["status"] = "failed"
            result["error"] = "No visible submit control found"
            return result
        # Click happened but no POST and no thank-you — do not burn the hourly cap.
        result["status"] = "failed"
        result["error"] = "Submit click did not produce a form POST or thank-you"
        logger.info("Unconfirmed click for %s — not counting as submitted", lead.get("url"))
        return result
    except PlaywrightTimeout as exc:
        logger.warning("Timeout submitting %s: %s", lead.get("url"), exc)
        result["status"] = "skipped_submit_failed" if fast else "failed"
        result["error"] = f"timeout: {exc}"
        return result
    except TimeoutError as exc:
        logger.warning("Site budget submitting %s: %s", lead.get("url"), exc)
        result["status"] = "skipped_submit_failed" if fast else "failed"
        result["error"] = f"timeout: {exc}"
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("Submit failed for %s", lead.get("url"))
        result["status"] = "failed"
        result["error"] = str(exc)
        return result
    finally:
        if watcher is not None:
            watcher.close()
        _release_page(page)


def _release_page(page: Page) -> None:
    try:
        page.goto("about:blank", wait_until="domcontentloaded", timeout=5_000)
    except Exception:  # noqa: BLE001
        pass


def _wait_widgets(page: Page) -> None:
    try:
        page.wait_for_selector(
            "iframe[src*='hsforms'], iframe[src*='formspree'], iframe[src*='jotform'], "
            "form input, form textarea, textarea, input[type='email']",
            timeout=4_000,
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        page.wait_for_timeout(400)
    except Exception:  # noqa: BLE001
        pass


def _reveal_widgets(page: Page) -> None:
    """Open an in-page contact widget/tab — never follow a new navigation."""
    for scope in _scopes(page):
        try:
            loc = scope.get_by_role("button", name=REVEAL_RE)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=1_800)
                page.wait_for_timeout(700)
                logger.info("Opened in-page contact widget")
                return
        except Exception:  # noqa: BLE001
            continue
        try:
            loc = scope.get_by_role("tab", name=REVEAL_RE)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=1_800)
                page.wait_for_timeout(700)
                return
        except Exception:  # noqa: BLE001
            continue
        try:
            links = scope.get_by_role("link", name=REVEAL_RE)
            n = min(links.count(), 4)
            for i in range(n):
                href = (links.nth(i).get_attribute("href") or "").strip().lower()
                if href.startswith("#") or href.startswith("javascript:"):
                    links.nth(i).click(timeout=1_800)
                    page.wait_for_timeout(700)
                    return
        except Exception:  # noqa: BLE001
            continue


def _scopes(page: Page):
    widgets: list[Any] = []
    others: list[Any] = []
    for frame in page.frames:
        url = (frame.url or "").lower()
        if not url or SKIP_FRAME_RE.search(url):
            continue
        if frame == page.main_frame:
            continue
        if WIDGET_IFRAME_RE.search(url):
            widgets.append(frame)
        else:
            others.append(frame)
    for frame in widgets:
        yield frame
    yield page
    for frame in others:
        yield frame


def _type_value(page: Page, el: Any, value: str, last_field: list[Any] | None = None) -> bool:
    try:
        el.scroll_into_view_if_needed(timeout=1500)
        el.click(timeout=2000)
        page.wait_for_timeout(random.randint(120, 380))
        el.fill("")
        el.type(value, delay=random.randint(32, 68))
        if last_field is not None:
            last_field.clear()
            last_field.append(el)
        return True
    except Exception:  # noqa: BLE001
        try:
            el.evaluate(
                """(e, v) => {
                    e.focus();
                    e.value = v;
                    e.dispatchEvent(new Event('input', { bubbles: true }));
                    e.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                value,
            )
            if last_field is not None:
                last_field.clear()
                last_field.append(el)
            return True
        except Exception:  # noqa: BLE001
            return False


def _fill_form(
    page: Page,
    message: str,
    *,
    subject: str = "",
    last_field: list[Any] | None = None,
) -> int:
    filled = 0
    for scope in _scopes(page):
        n = _fill_controls(
            page,
            scope.locator("form input, form textarea, form select"),
            message,
            subject,
            last_field=last_field,
        )
        filled = max(filled, n)
        if filled >= 2:
            return filled
        n = _fill_controls(
            page,
            scope.locator("[role='form'] input, [role='form'] textarea, [role='textbox']"),
            message,
            subject,
            last_field=last_field,
        )
        filled = max(filled, n)
        if filled >= 2:
            return filled
    return filled


def _fill_formless(
    page: Page,
    message: str,
    *,
    subject: str = "",
    last_field: list[Any] | None = None,
) -> int:
    filled = 0
    for scope in _scopes(page):
        n = _fill_controls(
            page,
            scope.locator("input:visible, textarea:visible, [contenteditable='true']"),
            message,
            subject,
            last_field=last_field,
        )
        filled = max(filled, n)
        if filled >= 2:
            return filled
    return filled


def _sender_values(message: str, subject: str) -> dict[str, str]:
    sender = config.sender_payload()
    full = (sender.get("name") or "").strip()
    parts = full.split(None, 1)
    first = parts[0] if parts else full
    last = parts[1] if len(parts) > 1 else (parts[0] if parts else "")
    return {
        "name": full,
        "first_name": first,
        "last_name": last,
        "email": sender.get("email") or "",
        "company": sender.get("company") or "",
        "phone": sender.get("phone") or "",
        "website": sender.get("website") or "",
        "subject": subject or sender.get("subject") or "",
        "message": message,
    }


def _fill_controls(
    page: Page,
    controls: Any,
    message: str,
    subject: str,
    *,
    last_field: list[Any] | None = None,
) -> int:
    values = _sender_values(message, subject)

    try:
        count = min(controls.count(), 40)
    except Exception:  # noqa: BLE001
        return 0
    filled = 0
    used_purposes: set[str] = set()

    for i in range(count):
        el = controls.nth(i)
        try:
            if not el.is_visible():
                continue
            try:
                if el.evaluate(IS_HONEYPOT_JS):
                    continue
            except Exception:  # noqa: BLE001
                continue
            el_type = (el.get_attribute("type") or "text").lower()
            if el_type in {"hidden", "submit", "button", "image", "file", "checkbox", "radio"}:
                continue
            name = el.get_attribute("name") or el.get_attribute("id") or ""
            if HONEYPOT_NAME_RE.search(name):
                logger.info("Skipping honeypot field %s", name)
                continue
            autocomplete = el.get_attribute("autocomplete") or ""
            placeholder = el.get_attribute("placeholder") or ""
            contenteditable = (el.get_attribute("contenteditable") or "").lower()
            label = ""
            try:
                label = el.evaluate(
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
                label = ""

            tag = ""
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
            except Exception:  # noqa: BLE001
                tag = "input"
            if tag == "select":
                continue
            kind = "textarea" if tag == "textarea" else el_type
            if contenteditable == "true" and tag not in {"input", "textarea"}:
                kind = "textarea"
            purpose = classify_field(
                name,
                kind,
                placeholder,
                f"{label} {autocomplete}",
            )
            if tag not in {"input", "textarea", "select"} and contenteditable == "true":
                purpose = purpose or "message"
            if not purpose or purpose in used_purposes:
                continue
            value = values.get(purpose) or ""
            if not value:
                continue
            if contenteditable == "true" and tag not in {"input", "textarea"}:
                try:
                    el.click(timeout=1500)
                    el.evaluate(
                        """(e, v) => {
                            e.focus();
                            e.innerText = v;
                            e.dispatchEvent(new Event('input', { bubbles: true }));
                        }""",
                        value,
                    )
                    used_purposes.add(purpose)
                    filled += 1
                    if last_field is not None:
                        last_field.clear()
                        last_field.append(el)
                    continue
                except Exception:  # noqa: BLE001
                    continue
            if not _type_value(page, el, value, last_field=last_field):
                continue
            used_purposes.add(purpose)
            filled += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping field %s: %s", i, exc)
            continue

    if "message" not in used_purposes:
        for scope in _scopes(page):
            areas = scope.locator("textarea:visible")
            try:
                n = min(areas.count(), 5)
            except Exception:  # noqa: BLE001
                continue
            for i in range(n):
                area = areas.nth(i)
                try:
                    if not area.is_visible() or area.evaluate(IS_HONEYPOT_JS):
                        continue
                    if _type_value(page, area, message, last_field=last_field):
                        filled += 1
                        return filled
                except Exception:  # noqa: BLE001
                    continue
    return filled


def _tick_consent(page: Page) -> None:
    """Check visible KVKK/privacy boxes only — never captcha widgets."""
    for scope in _scopes(page):
        boxes = scope.locator("form input[type='checkbox'], input[type='checkbox']:visible")
        try:
            n = min(boxes.count(), 12)
        except Exception:  # noqa: BLE001
            continue
        for i in range(n):
            el = boxes.nth(i)
            try:
                if not el.is_visible():
                    continue
                name = el.get_attribute("name") or el.get_attribute("id") or ""
                if HONEYPOT_NAME_RE.search(name):
                    continue
                label = ""
                try:
                    label = el.evaluate(
                        """(e) => {
                            const id = e.id;
                            if (id) {
                              const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                              if (lab) return lab.innerText || '';
                            }
                            const parent = e.closest('label');
                            return parent ? (parent.innerText || '') : (e.getAttribute('aria-label') || '');
                        }"""
                    ) or ""
                except Exception:  # noqa: BLE001
                    label = ""
                blob = f"{name} {label}"
                if not CONSENT_RE.search(blob):
                    continue
                if el.is_checked():
                    continue
                el.check(timeout=1500)
                logger.info("Ticked consent checkbox %s", name or label[:40])
            except Exception:  # noqa: BLE001
                continue


def _find_submit_button(page: Page) -> Any | None:
    selectors = (
        "form button[type='submit']",
        "form input[type='submit']",
        "button[type='submit']",
        "input[type='submit']",
        ".hs-button, .hs-submit, [data-submit], [name='submit']",
    )
    for scope in _scopes(page):
        for sel in selectors:
            loc = scope.locator(sel)
            try:
                n = min(loc.count(), 6)
            except Exception:  # noqa: BLE001
                continue
            for i in range(n):
                btn = loc.nth(i)
                try:
                    if btn.is_visible():
                        return btn
                except Exception:  # noqa: BLE001
                    continue
        try:
            loc = scope.get_by_role("button", name=SUBMIT_NAME_RE)
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:  # noqa: BLE001
            continue
    return None


def _scroll_focus(page: Page, btn: Any) -> None:
    try:
        btn.evaluate("e => e.scrollIntoView({behavior: 'instant', block: 'center'})")
        page.wait_for_timeout(1500)
        btn.focus()
    except Exception:  # noqa: BLE001
        try:
            page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            pass


def _submit_cascade(page: Page, watcher: FormNetWatcher, last_field: list[Any]) -> bool:
    """A requestSubmit, B MouseEvent click, C Enter. Stop as soon as a POST is seen."""
    btn = _find_submit_button(page)
    if btn is not None:
        _scroll_focus(page, btn)

    # A — requestSubmit (does not skip React/Vue listeners the way form.submit() does)
    for scope in _scopes(page):
        try:
            if scope.evaluate(REQUEST_SUBMIT_JS):
                logger.info("Submit cascade A requestSubmit")
                if watcher.wait(1.8):
                    return True
                break
        except Exception:  # noqa: BLE001
            continue

    # B — MouseEvent click (and Playwright click as a sibling, not HTMLFormElement.submit)
    clicked = False
    if btn is not None:
        try:
            btn.evaluate(
                """(e) => {
                    e.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                }"""
            )
            clicked = True
            logger.info("Submit cascade B MouseEvent")
            if watcher.wait(1.8):
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            btn.click(timeout=3000, force=True)
            clicked = True
            logger.info("Submit cascade B force click")
            if watcher.wait(1.8):
                return True
        except Exception:  # noqa: BLE001
            pass
    for scope in _scopes(page):
        try:
            if scope.evaluate(MOUSE_CLICK_JS):
                clicked = True
                logger.info("Submit cascade B JS button click")
                if watcher.wait(1.8):
                    return True
                break
        except Exception:  # noqa: BLE001
            continue

    # C — Enter on the last filled control
    if last_field:
        try:
            last_field[0].focus()
            last_field[0].press("Enter")
            clicked = True
            logger.info("Submit cascade C Enter")
            if watcher.wait(1.8):
                return True
        except Exception:  # noqa: BLE001
            try:
                page.keyboard.press("Enter")
                clicked = True
                if watcher.wait(1.8):
                    return True
            except Exception:  # noqa: BLE001
                pass
    return clicked or watcher.hit


def _looks_successful(page: Page) -> bool:
    try:
        url = (page.url or "").lower()
        if any(tok in url for tok in ("thank", "thanks", "success", "tesekkur", "iletildi")):
            return True
        text = page.evaluate("() => (document.body && document.body.innerText) || ''")
    except Exception:  # noqa: BLE001
        return False
    return bool(SUCCESS_RE.search(str(text or "")))


def delay_seconds_for(lead: dict[str, Any] | None = None) -> float:
    strict = bool(lead and lead.get("waf_strict"))
    if strict:
        low = config.FORM_DELAY_STRICT_MIN_SECONDS
        high = config.FORM_DELAY_STRICT_MAX_SECONDS
        kind = "strict-WAF"
    else:
        low = config.FORM_DELAY_FAST_MIN_SECONDS
        high = config.FORM_DELAY_FAST_MAX_SECONDS
        kind = "fast"
    low, high = min(low, high), max(low, high)
    seconds = random.uniform(low, high)
    logger.info("Submit jitter %.1fs (%s)", seconds, kind)
    return seconds


def _courtesy_delay(lead: dict[str, Any] | None = None) -> None:
    time.sleep(delay_seconds_for(lead))
