"""Lane FR — fingerprint_rotator [GitHub Actions / Oracle submit contexts].

IP itibarını kısıtlamadan (hız düşürmeden) koruyan istek mimarisi:
- User-Agent / ekran çözünürlüğü / locale / timezone / Accept-Language rotasyonu
- Gaussian inter-submit jitter (sabit aralık YOK, tamamen insan davranışı)
- Her form gönderimi "tek bir merkezden" değil, farklı istemcilerden geliyor gibi görünür

Maliyet: 0 EUR (harici API yok). TLS-uç fingerprint'i gerçek anlamda döndürmek
yalnızca Playwright tarayıcı penceresi + sistem katmanıyla mümkündür; burada
HTTP/Header/DOM katmanı için dürüst bir rotasyon sağlanır.
"""
from __future__ import annotations

import random
from typing import Any

# Güncel UA havuzu (Chrome/Edge/Firefox, çeşitli platformlar)
UA_POOL = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
)

# (locale, timezone, accept-language)
LOCALE_POOL = (
    ("tr-TR", "Europe/Istanbul", "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"),
    ("en-US", "America/New_York", "en-US,en;q=0.9"),
    ("en-GB", "Europe/London", "en-GB,en;q=0.9,en-US;q=0.8"),
    ("de-DE", "Europe/Berlin", "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"),
    ("fr-FR", "Europe/Paris", "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7"),
    ("es-ES", "Europe/Madrid", "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7"),
)

# (genislik, yukseklik) ekran çözünürlükleri
VIEWPORT_POOL = (
    (1366, 768), (1280, 720), (1440, 900), (1536, 864), (1920, 1080), (1024, 768),
)

INTER_SUBMIT = (12_000, 5_000, 6_000, 30_000)  # (mean_ms, std_ms, lo_ms, hi_ms)


def rotate_ua() -> str:
    return random.choice(UA_POOL)


def rotate_viewport() -> tuple[int, int]:
    return random.choice(VIEWPORT_POOL)


def rotate_locale(turkish_site: bool = True) -> tuple[str, str, str]:
    """TR sitelerde ağırlık TR profiline ver, ama tekörnek olmasın."""
    if turkish_site and random.random() < 0.6:
        return LOCALE_POOL[0]
    return random.choice(LOCALE_POOL)


def http_headers(*, turkish_site: bool = True) -> dict[str, str]:
    """httpx tabanlı gönderimler için dönen header seti (her çağrıda farklı)."""
    ua = rotate_ua()
    _, _, accept_lang = rotate_locale(turkish_site)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": accept_lang,
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }


def browser_profile(*, turkish_site: bool = True) -> dict[str, Any]:
    """Playwright context kwargs: viewport, locale, timezone, headers rotasyonlu."""
    locale, tz, accept_lang = rotate_locale(turkish_site)
    width, height = rotate_viewport()
    return {
        "locale": locale,
        "timezone_id": tz,
        "viewport": {"width": width, "height": height},
        "java_script_enabled": True,
        "extra_http_headers": {
            "Accept-Language": accept_lang,
            "Upgrade-Insecure-Requests": "1",
        },
    }


def inter_submit_delay_ms() -> int:
    """İki gönderim arası Gauss jitter: sabit bekleme YOK (6s–30s bandı)."""
    for _ in range(10):
        v = random.gauss(INTER_SUBMIT[0], INTER_SUBMIT[1])
        if INTER_SUBMIT[2] <= v <= INTER_SUBMIT[3]:
            return int(v)
    return int(max(INTER_SUBMIT[2], min(INTER_SUBMIT[3], INTER_SUBMIT[0])))


def run_batch() -> dict[str, Any]:
    return {
        "uas": len(UA_POOL),
        "locales": len(LOCALE_POOL),
        "viewports": len(VIEWPORT_POOL),
        "inter_submit_ms": (INTER_SUBMIT[2], INTER_SUBMIT[3]),
        "sample_headers": http_headers(),
    }