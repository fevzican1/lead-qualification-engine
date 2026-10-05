"""Lane HS — honeypot_human_sim [GitHub Actions, heavy].

Anti-spam bypass: hidden bot-trap fields (display:none, visibility:hidden,
off-screen position) are detected and left empty. Mouse-move and key-press
events are simulated with randomized jitter delays to mimic human interaction.

Deliverables:
- detect_honeypot_fields(html) -> list of input names to leave empty
- simulate_interaction_delay() -> randomized ms sleep with jitter profile
- render_human_events(n) -> deterministic-ish mouse/key event sequence
"""
from __future__ import annotations

import random
import re
import time
from typing import Any

import config

# Jitter profiles: (mean_ms, std_dev_ms, min_ms, max_ms) per interaction phase
JITTER_PROFILE = {
    "mouse_enter":   (420, 180, 120, 1100),
    "mouse_move":    (95, 45, 20, 320),
    "mouse_pause":   (700, 350, 150, 2200),
    "key_press":     (110, 55, 30, 380),
    "field_focus":   (260, 130, 80, 750),
    "form_submit":   (1400, 600, 400, 3800),
}

HONEY_PATTERNS = [
    re.compile(r'display\s*:\s*none', re.I),
    re.compile(r'visibility\s*:\s*hidden', re.I),
    re.compile(r'position\s*:\s*absolute[^;]*left\s*:\s*-\d{3,}', re.I),
    re.compile(r'position\s*:\s*(absolute|fixed)[^;]*top\s*:\s*-\d{3,}', re.I),
    re.compile(r'opacity\s*:\s*0\b', re.I),
    re.compile(r'width\s*:\s*0\b[^;]*height\s*:\s*0\b', re.I),
    re.compile(r'height\s*:\s*0\b[^;]*width\s*:\s*0\b', re.I),
    re.compile(r'clip(-path)?\s*:\s*(inset\(\s*(50|100)%|rect\(\s*0)', re.I),
    re.compile(r'font-size\s*:\s*0\b', re.I),
    re.compile(r'aria-hidden\s*=\s*"true"', re.I),
    re.compile(r'aria-hidden\s*=\s*\'true\'', re.I),
    re.compile(r'\btabindex\s*=\s*["\']?-1', re.I),
    re.compile(r'\bhidden\b(?=[^>]*>)', re.I),
    re.compile(r'class\s*=\s*["\'][^"\']*\b(sr-only|visually-hidden|screen-reader|hidden-field|hp-field)\b', re.I),
]

# Bot tuzağı olarak en sık kullanılan alan adları (rapor: honeypot kaçınma).
HONEYPOT_NAMES = {
    "website", "website_url", "url", "homepage", "site", "your_website",
    "email_confirm", "emailconfirm", "confirm_email", "email2",
    "phone2", "tel2", "address2", "zip2", "middlename", "middle_name",
    "lastname2", "company_url", "company_website", "fax", "fax_number",
    "date_of_birth", "how_did_you_hear", "utm_source", "gclid", "fbclid",
    "leave_blank", "leaveblank", "leave_empty", "leaveempty", "bot_field",
    "botcheck", "bot_check", "honeypot", "hp", "_gotcha", "gotcha",
    "_honey", "confirmemail", "company_name", "first_name_2",
}

# Canlı DOM (JS evaluate) çıktısı için CSS gizleme denetimi.
HIDDEN_STYLE_RE = re.compile(
    r"(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(\.0+)?\b|"
    r"clip(-path)?\s*:\s*inset\(\s*(50|100)%|width\s*:\s*0(px)?\b|height\s*:\s*0(px)?\b)",
    re.I,
)
ATTR_PAIR_RE = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*["']([^"']*)["']""", re.S)
HIDDEN_ATTR_RE = re.compile(r"(?<![-\w])hidden(?![=\w\"'])", re.I)


def is_bot_field(attrs: dict[str, Any] | None, *, box: tuple[float, ...] | None = None) -> str | None:
    """Tek alan denetimi -> tuzak gerekçesi veya None (doldurulabilir).

    `attrs`: type/name/id/class/style/aria-hidden/tabindex/hidden.
    `box`: tarayıcıdan gelen (x, y, width, height); görünmez/kapalı alanı yakalar.
    """
    row = {str(k).lower(): v for k, v in (attrs or {}).items()}
    name = str(row.get("name") or row.get("id") or "").strip().lower()
    if name in HONEYPOT_NAMES:
        return "honeypot_name"
    if str(row.get("hidden", "")).lower() in {"true", "1", "hidden"} or row.get("hidden") is True:
        return "hidden_attribute"
    if str(row.get("aria-hidden", "")).lower() == "true":
        return "aria_hidden"
    if str(row.get("tabindex", "")).strip() == "-1" and "text" in str(row.get("type", "text")).lower():
        if not name:
            return "tabindex_minus_one"
    style = str(row.get("style") or "")
    if style and HIDDEN_STYLE_RE.search(style):
        return "hidden_style"
    cls = str(row.get("class") or "")
    if re.search(r"\b(sr-only|visually-hidden|screen-reader|hidden-field|hp-field)\b", cls, re.I):
        return "hidden_class"
    if box is not None and len(box) >= 4:
        x, y, width, height = (float(v or 0) for v in box[:4])
        if width <= 1 or height <= 1 or x < -1000 or y < -1000:
            return "offscreen_box"
    return None



def _rand_bounded(mean: float, std: float, lo: float, hi: float) -> int:
    for _ in range(8):
        v = random.gauss(mean, std)
        if lo <= v <= hi:
            return int(v)
    return int(max(lo, min(hi, mean)))


def detect_honeypot_fields(html: str) -> list[dict[str, Any]]:
    """Return hidden input names that bots should NOT fill."""
    if not html:
        return []
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1. Style-based hidden containers wrapping <input>
    for m in re.finditer(
        r'<\w+[^>]*style\s*=\s*["\']([^"\']*)["\'][^>]*>(.*?)</\w+>',
        html, re.S | re.I,
    ):
        style = m.group(1)
        if any(p.search(style) for p in HONEY_PATTERNS):
            for name in re.findall(
                r'<input[^>]*?(?:name|id)\s*=\s*["\']?(\w[\w-]*)["\']?',
                m.group(2), re.I,
            ):
                if name not in seen and name not in {"csrf", "token", "_token", "form_key"}:
                    findings.append({"name": name, "reason": "hidden_container"})
                    seen.add(name)

    # 2. Input-level gizleme: inline style, hidden attr, aria-hidden, tabindex, class
    for tag in re.finditer(r"<input\b[^>]*>", html, re.I):
        chunk = tag.group(0)
        attrs = {m.group(1).lower(): m.group(2) for m in ATTR_PAIR_RE.finditer(chunk)}
        # type="hidden" alanlar (CSRF/doğrulama jetonları) honeypot DEĞİLDİR:
        # bot bunları doldurmaz, formla birlikte taşınmaları gerekir.
        if str(attrs.get("type", "")).strip().lower() == "hidden":
            continue
        name = str(attrs.get("name") or attrs.get("id") or "").strip()
        reason: str | None = None
        if any(p.search(str(attrs.get("style") or "")) for p in HONEY_PATTERNS):
            reason = "inline_hidden"
        elif HIDDEN_ATTR_RE.search(chunk):
            reason = "hidden_attribute"
        elif str(attrs.get("aria-hidden", "")).lower() == "true":
            reason = "aria_hidden"
        elif str(attrs.get("tabindex", "")).strip() == "-1":
            reason = "tabindex_minus_one"
        elif re.search(r"\b(sr-only|visually-hidden|screen-reader|hidden-field|hp-field)\b",
                       str(attrs.get("class") or ""), re.I):
            reason = "hidden_class"
        elif name.lower() in HONEYPOT_NAMES:
            reason = "honeypot_name"
        if reason and name and name not in seen:
            findings.append({"name": name, "reason": reason})
            seen.add(name)

    # 3. Field names commonly used as honeypots
    for m in re.finditer(
        r'<input[^>]*?(?:name|id)\s*=\s*["\']?(\w[\w-]*)["\']?', html, re.I,
    ):
        if m.group(1).lower() in HONEYPOT_NAMES and m.group(1) not in seen:
            findings.append({"name": m.group(1), "reason": "honeypot_name"})
            seen.add(m.group(1))

    return findings


def simulate_interaction_delay(phase: str = "key_press") -> int:
    """Sleep for a randomized human-like duration. Returns ms elapsed."""
    mean, std, lo, hi = JITTER_PROFILE.get(phase, JITTER_PROFILE["key_press"])
    ms = _rand_bounded(mean, std, lo, hi)
    time.sleep(ms / 1000.0)
    return ms


def render_human_events(n_fields: int = 6) -> list[dict[str, Any]]:
    """Build a deterministic-but-varied event sequence for a headless run."""
    events: list[dict[str, Any]] = []
    t = 0.0
    for i in range(n_fields):
        # Mouse approach
        dt = random.gauss(95, 45) / 1000.0
        t += max(0.02, min(0.32, dt))
        events.append({"t": round(t, 3), "type": "mouse_move"})
        # Focus field
        ft = random.gauss(260, 130) / 1000.0
        t += max(0.08, min(0.75, ft))
        events.append({"t": round(t, 3), "type": "focus", "field": i})
        # Keystrokes per field (~8 chars)
        for _ in range(random.randint(4, 12)):
            kt = random.gauss(110, 55) / 1000.0
            t += max(0.03, min(0.38, kt))
            events.append({"t": round(t, 3), "type": "key_press"})
        # Micro-pause between fields
        pt = random.gauss(700, 350) / 1000.0
        t += max(0.15, min(2.2, pt))
        events.append({"t": round(t, 3), "type": "pause"})
    # Submit hesitation
    st = random.gauss(1400, 600) / 1000.0
    t += max(0.4, min(3.8, st))
    events.append({"t": round(t, 3), "type": "submit"})
    return events


def run_batch(**kwargs: Any) -> dict[str, Any]:
    """Standalone lane entry; returns current simulation profile."""
    return {
        "jitter_profile": {k: list(v) for k, v in JITTER_PROFILE.items()},
        "honeypot_patterns": len(HONEY_PATTERNS),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
