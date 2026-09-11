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
    re.compile(r'opacity\s*:\s*0\b', re.I),
    re.compile(r'width\s*:\s*0\b[^;]*height\s*:\s*0\b', re.I),
    re.compile(r'aria-hidden\s*=\s*"true"', re.I),
]


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
        inner = m.group(2)
        if any(p.search(style) for p in HONEY_PATTERNS):
            for name in re.findall(
                r'<input[^>]*?(?:name|id)\s*=\s*["\']?(\w[\w-]*)["\']?',
                inner, re.I,
            ):
                if name not in seen and name not in {"csrf", "token", "_token", "form_key"}:
                    findings.append({"name": name, "reason": "hidden_container"})
                    seen.add(name)

    # 2. Input-level inline style hidden
    for m in re.finditer(
        r'<input[^>]*?style\s*=\s*["\']([^"\']*)["\'][^>]*?>', html, re.S | re.I,
    ):
        style = m.group(1)
        if any(p.search(style) for p in HONEY_PATTERNS):
            nm = re.search(r'(?:name|id)\s*=\s*["\']?(\w[\w-]*)', m.group(0), re.I)
            if nm:
                name = nm.group(1)
                if name not in seen:
                    findings.append({"name": name, "reason": "inline_hidden"})
                    seen.add(name)

    # 3. Field names commonly used as honeypots
    honeypot_names = {
        "email_confirm", "emailConfirm", "phone2", "address2", "company_name",
        "website_url", "fax", "middlename", "lastname2", "date_of_birth",
        "how_did_you_hear", "utm_source", "gclid", "fbclid",
    }
    for m in re.finditer(
        r'<input[^>]*?(?:name|id)\s*=\s*["\']?(\w[\w-]*)["\']?', html, re.I,
    ):
        if m.group(1).lower() in honeypot_names:
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
