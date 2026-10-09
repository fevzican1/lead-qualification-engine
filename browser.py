"""
Playwright session helpers for Always Free Ampere.

Collect context blocks images, fonts, media, and CSS (HTML/DOM only).
Submit context keeps CSS so honeypots and visible fields stay accurate,
but still blocks images/fonts/media.
"""

from __future__ import annotations

import os
import subprocess
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
    # KOD ICI HARD-KILL (1 dakika kurali, katman 3): HICBIR sayfa yukleme veya
    # tarayici islemi 25 sn'den uzun suremez. Dis Nobetci (60s) + systemd
    # WatchdogSec(60s) + bu 25s tavan = kilitlenme en fazla 60 sn yasar.
    launch_timeout = int(float(getattr(config, "PLAYWRIGHT_LAUNCH_TIMEOUT_MS", 25000) or 25000))
    return playwright.chromium.launch(
        headless=headless,
        timeout=launch_timeout,
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


# --- kullan-at artık süpürmesi (kural 2) ------------------------------------

def _kill_pid(pid: int) -> bool:
    """PID'i zorla öldür (SIGKILL); hata halinde False (asla raise etmez)."""
    try:
        subprocess.run(
            ["kill", "-9", str(pid)], capture_output=True, timeout=10, check=False,
        )
        return True
    except Exception:  # noqa: BLE001 — öldürme hatası hattı düşürmez
        return False


def _ps_rows() -> list[tuple[int, int, int, str]]:
    """(pid, ppid, yaş_sn, args) — posix; hata halinde boş (fail-open)."""
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,etimes=,args="],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except Exception:  # noqa: BLE001
        return []
    rows: list[tuple[int, int, int, str]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid_s, ppid_s, age_s, args = parts
        if not (pid_s.isdigit() and ppid_s.isdigit() and age_s.isdigit()):
            continue
        rows.append((int(pid_s), int(ppid_s), int(age_s), args))
    return rows


def _is_chromium(args: str) -> bool:
    # Playwright'in paketlenmiş tarayıcısı `chromium-XXXX/chrome-linux/chrome`
    # yolunda durur; ikisi de "chrome" içerir → tek desen ikisini de kapsar.
    low = args.lower()
    return "chromium" in low or "chrome" in low


def _is_playwright_driver(args: str) -> bool:
    """Playwright sürücü süreci (node) — tarayıcı değil, ONUN SAHİBİ.

    Canlı arıza 2026-10-05: ``submit_lead`` 37 dk asılı kaldı ve ``purge_chromium``
    yalnızca chrome kromunu öldürdüğü için çağrı hiç açılmadı. Playwright'ın sync
    API'si Python tarafında değil **node sürücüsü** üzerinden konuşur; sürücü
    ölmezse takılan çağrı kendiliğinden dönmez. Kural 3'ün (30 sn duvar-saati)
    gerçekten işe yaraması için driver da imha edilmeli.
    """
    low = args.lower()
    return "playwright" in low and "driver" in low


def _is_browser_proc(args: str) -> bool:
    """Hard-kill hedefi: Chromium kromu + Playwright sürücüsü."""
    return _is_chromium(args) or _is_playwright_driver(args)


def purge_chromium(*, stale_after_s: float = 30.0) -> int:
    """Kullan-at artık süpürmesi (kural 2): kalan tarayıcı artıklarını imha et.

    1) **Kendi işlem ağacımızdaki** tüm Chromium + Playwright sürücüsü süreçleri
       (yaş sınırı yok): ``browser.close()`` sonrası bile kalan
       renderer/gpu/zygote artıklarını da kapsar — ``pkill -9 -f chromium``
       mantığı, tam olarak bu sürecin çocuklarına odaklı. Sürücü (node) dâhil:
       yalnızca chrome öldürülürse takılmış bir sync çağrısı asılı kalır.
    2) POSIX'te **sahipsiz** (ppid=1) ve ``stale_after_s``'den eski yetim
       chromium zombileri: çökmüş eski oturumlardan kalan RAM hırsızları.

    Eşzamanlı diğer hatların (enterprise lane, legacy runner) AKTİF
    tarayıcıları dokunulmaz: onların süreçleri yaşamlı kendi ağaçlarında
    kalır; yalnızca sahipsiz + yaşlı zombiler temizlenir.

    Fail-open: hiçbir zaman raise etmez; Windows'ta (geliştirme/test)
    no-op'dir. Sunucu reboot'u ASLA yapmaz.
    """
    if os.name != "posix":
        return 0
    rows = _ps_rows()
    if not rows:
        return 0
    children: dict[int, list[tuple[int, str]]] = {}
    for pid, ppid, _age, args in rows:
        children.setdefault(ppid, []).append((pid, args))
    killed = 0
    # 1) Kendi ağacımız (BFS): python -> playwright driver -> chromium -> renderer.
    me = os.getpid()
    seen = {me}
    stack = [me]
    while stack:
        cur = stack.pop()
        for pid, args in children.get(cur, ()):
            if pid in seen:
                continue
            seen.add(pid)
            if _is_browser_proc(args) and _kill_pid(pid):
                killed += 1
            stack.append(pid)
    # 2) Yetim + yaşlı zombiler (ppid=1): crash sonrası terk edilmiş artıklar.
    for pid, ppid, age, args in rows:
        if pid in seen or ppid != 1 or age < stale_after_s:
            continue
        if _is_browser_proc(args) and _kill_pid(pid):
            killed += 1
    return killed
