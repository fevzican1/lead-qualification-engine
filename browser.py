"""
Playwright session helpers for Always Free Ampere.

Collect context blocks images, fonts, media, and CSS (HTML/DOM only).
Submit context keeps CSS so honeypots and visible fields stay accurate,
but still blocks images/fonts/media.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, Route

import config

_HEAVY = frozenset({"image", "media", "font", "stylesheet"})
_MEDIA = frozenset({"image", "media", "font"})
_HEAVY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".mp4", ".webm")
_CSS_EXT = (".css",)


def _abort_types(types: frozenset[str], extra_ext: tuple[str, ...]) -> Callable[[Route], Any]:
    def _handler(route: Route) -> None:
        request = route.request
        if request.resource_type in types:
            route.abort()
            return
        url = request.url.lower().split("?", 1)[0]
        if url.endswith(extra_ext):
            route.abort()
            return
        route.continue_()

    return _handler


def launch_browser(playwright: Playwright, *, headless: bool | None = None) -> Browser:
    if headless is None:
        headless = config.HEADLESS
    return playwright.chromium.launch(
        headless=headless,
        args=[
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-extensions",
        ],
    )


def collect_context(browser: Browser) -> BrowserContext:
    context = browser.new_context(
        locale="tr-TR",
        timezone_id="Europe/Istanbul",
        viewport={"width": 1280, "height": 720},
        java_script_enabled=True,
        extra_http_headers={
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    context.route("**/*", _abort_types(_HEAVY, _HEAVY_EXT + _CSS_EXT))
    return context


def submit_context(browser: Browser) -> BrowserContext:
    context = browser.new_context(
        locale="tr-TR",
        timezone_id="Europe/Istanbul",
        viewport={"width": 1280, "height": 720},
        java_script_enabled=True,
        extra_http_headers={
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    context.route("**/*", _abort_types(_MEDIA, _HEAVY_EXT))
    return context


def new_page(context: BrowserContext, *, timeout_ms: int | None = None) -> Page:
    page = context.new_page()
    page.set_default_timeout(timeout_ms or config.NAV_TIMEOUT_MS)
    return page
