"""Local browser regression. All HTTP is intercepted; no third-party submissions."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import config
import enterprise_forms

URL = "https://jobs.example.org/apply"
HTML = '''<h1>Apply for Contract Python Engineer</h1>
<form method="post" action="/apply">
<label>Email<input type="email" name="email" required></label>
<label>Message<textarea name="message" required></textarea></label>
<button type="submit">Apply</button></form>'''


@pytest.fixture
def page():
    with sync_playwright() as pw:
        options = {"headless": True, "executable_path": pw.chromium.executable_path}
        if not Path(pw.chromium.executable_path).exists():
            edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
            if not edge.exists():
                pytest.skip("Chromium/Edge is not installed")
            options["executable_path"] = str(edge)
        browser = pw.chromium.launch(**options)
        page = browser.new_page()
        yield page
        browser.close()


def test_exact_form_single_post(page, monkeypatch):
    monkeypatch.setattr(config, "SENDER_EMAIL", "test@example.org")
    posts = []
    def route(req):
        if req.request.method == "POST":
            posts.append(req.request.post_data)
            req.fulfill(status=200, content_type="text/html", body="<h1>Thank you for applying</h1>")
        else:
            req.fulfill(status=200, content_type="text/html", body=HTML)
    page.route("**/*", route)
    row = {"url": URL, "value_proposition": "Test application STOP", "form_subject": "Test",
           "evidence": {"form_action": URL}}
    result = enterprise_forms.submit(page, row)
    assert result["status"] == "submitted_confirmed"
    assert len(posts) == 1


@pytest.mark.parametrize("extra", ['<input type="file" required>', '<input type="checkbox" required>',
                                  '<label>Work authorization<input name="eligible" required></label>'])
def test_unknown_required_questions_are_review_only(page, extra):
    page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html",
                                          body=HTML.replace("</form>", extra + "</form>")))
    page.goto(URL)
    assert enterprise_forms.application_form(page) is None


def test_sales_form_is_not_contractor_intake(page):
    page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html",
                                          body=HTML.replace("Apply for Contract Python Engineer", "Contact sales")))
    page.goto(URL)
    assert enterprise_forms.application_form(page) is None


def test_newsletter_does_not_receive_application(page, monkeypatch):
    monkeypatch.setattr(config, "SENDER_EMAIL", "test@example.org")
    posts = []
    html = '<form method="post" action="/newsletter"><input type="email"><button type="submit">Join</button></form>' + HTML
    def route(r):
        if r.request.method == "POST":
            posts.append(r.request.url)
            r.fulfill(status=200, content_type="text/html", body="Thank you for applying")
        else:
            r.fulfill(status=200, content_type="text/html", body=html)
    page.route("**/*", route)
    result = enterprise_forms.submit(page, {"url": URL, "value_proposition": "Example STOP",
                                           "evidence": {"form_action": URL}})
    assert result["status"] == "submitted_confirmed"
    assert posts == [URL]