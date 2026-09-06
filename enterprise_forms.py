"""Conservative contractor forms: one identified form, one native submit click.

Unknown questions, CV uploads, eligibility declarations and consent checkboxes
require a human. Never fill them with invented answers or use a submit cascade.
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urljoin

import enterprise_quality as quality

CAPTCHA = re.compile(r"recaptcha|hcaptcha|turnstile|cf-challenge", re.I)
FIELDS = {
    "email": r"email|e-mail",
    "first_name": r"first.?name|given.?name",
    "last_name": r"last.?name|surname|family.?name",
    "name": r"^(?:full |your )?name$",
    "company": r"^(?:your )?(?:company|organization)$",
    "phone": r"phone|telephone",
    "website": r"^(?:your )?website$",
    "subject": r"^subject$",
    "message": r"message|cover.?letter|additional information",
}


def field_kind(el: Any) -> str:
    kind = (el.get_attribute("type") or "text").lower()
    if kind in {"hidden", "file", "checkbox", "radio", "password"}:
        return ""
    if el.evaluate("e => e.tagName.toLowerCase()") == "select":
        return ""
    label = el.evaluate("e => Array.from(e.labels || []).map(l => l.textContent).join(' ')")
    # Explicit label wins over an uninformative/generated field name.
    text = str(label or el.get_attribute("placeholder") or el.get_attribute("name") or "").strip().lower()
    text = text.rstrip(" *:")
    for key, pattern in FIELDS.items():
        if re.search(pattern, text, re.I):
            return key
    return "email" if kind == "email" else ""


def application_form(page: Any) -> tuple[Any, dict[str, str]] | None:
    if not quality.public_https(page.url):
        return None
    text = " ".join(page.locator("body").inner_text(timeout=1500).split())[:80_000]
    if quality.CLOSED.search(text) or CAPTCHA.search(page.content()[:200_000]):
        return None
    heading = " ".join(page.locator("h1").all_text_contents())[:240]
    found = []
    for form in page.locator("form").all()[:8]:
        if not form.is_visible():
            continue
        channel = heading + " " + " ".join(form.inner_text(timeout=1500).split())[:4000]
        if (not quality.APPLY.search(channel) or quality.SALES.search(channel + " " + page.url)
                or (form.get_attribute("method") or "get").lower() != "post"):
            continue
        action = urljoin(page.url, form.get_attribute("action") or page.url)
        if not quality.public_https(action):
            continue
        purposes = set()
        unsupported = False
        for el in form.locator("input, textarea, select").all()[:40]:
            if not el.is_visible() or not el.is_enabled():
                continue
            kind = field_kind(el)
            if el.get_attribute("required") is not None and not kind:
                unsupported = True
            if kind:
                purposes.add(kind)
        if unsupported or not {"email", "message"}.issubset(purposes):
            continue
        button = form.locator("button[type='submit'], input[type='submit']")
        if button.count() != 1 or not button.first.is_visible() or not button.first.is_enabled():
            continue
        found.append((form, {"form_url": page.url, "form_action": action, "channel_quote": channel}))
    return found[0] if len(found) == 1 else None


def submit(page: Any, lead: dict[str, Any]) -> dict[str, Any]:
    result = dict(lead)
    result["status"] = "skipped_no_open_form"
    deadline = time.monotonic() + 30
    def left_ms():
        left = int((deadline - time.monotonic()) * 1000)
        if left <= 0:
            raise TimeoutError("enterprise site deadline")
        return left
    try:
        page.set_default_timeout(1500)
        page.goto(lead["url"], wait_until="domcontentloaded", timeout=min(12000, left_ms()))
        if page.url.split("#")[0] != lead["url"].split("#")[0]:
            return result  # changed destination needs a new GitHub scan
        page.locator("form input[type='email']").first.wait_for(state="visible", timeout=min(9000, left_ms()))
        selected = application_form(page)
        if not selected:
            return result
        form, evidence = selected
        if evidence["form_action"] != (lead.get("evidence") or {}).get("form_action"):
            return result
        from form_submitter import _sender_values
        values = _sender_values(lead["value_proposition"], lead.get("form_subject", ""))
        for el in form.locator("input, textarea, select").all()[:40]:
            if not el.is_visible() or not el.is_enabled():
                continue
            purpose = field_kind(el)
            value = values.get(purpose, "")
            if purpose and value:
                maximum = el.get_attribute("maxlength")
                if maximum and int(maximum) >= 0 and len(value) > int(maximum):
                    return result  # no truncating identity, evidence or opt-out
                el.fill(value, timeout=min(1500, left_ms()))
            elif el.get_attribute("required") is not None:
                return result
        if not form.evaluate("f => f.checkValidity()"):
            return result
        # Single native click; a timeout after click is ambiguous and NEVER auto-retried.
        result["status"] = "skipped_submit_failed"
        action = evidence["form_action"].split("#")[0]
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.split("#")[0] == action,
                                  timeout=min(5000, left_ms())) as pending:
            form.locator("button[type='submit'], input[type='submit']").click(timeout=min(2000, left_ms()))
        response = pending.value
        if 200 <= response.status < 300:
            body = page.locator("body").inner_text(timeout=min(1500, left_ms()))
            if re.search(r"application (?:has been )?(?:received|submitted)|thank you for (?:your application|applying)", body, re.I):
                result["status"] = "submitted_confirmed"
        result["submitted_url"] = page.url
        return result
    except Exception as exc:
        result["error"] = type(exc).__name__
        return result