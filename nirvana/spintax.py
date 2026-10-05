"""Gelişmiş Spintax motoru — her gönderimde benzersiz form metni.

Rapor (Micro-Batching + Spintax): aynı şablonun birebir tekrarı spam filtrelerini
tetikler. `{Merhaba|Selamlar|İyi çalışmalar}` biçimindeki gruplar her gönderimde
farklı bir kombinasyon üretir; içerik insan yazımı gibi varyasyon gösterir.

Sözdizimi:
- `{a|b{c|d}}`  -> iç içe gruplar (rastgele seçim)
- `\\{` / `\\}`  -> literal süslü parantez
- `[[degisken]]` -> değişken yer tutucu (render(values={...}))

Saf Python, deterministik seed desteği (test edilebilirlik), $0 maliyet.
"""
from __future__ import annotations

import random
import re
from typing import Any, Iterable

MAX_DEPTH = 12
_VAR_RE = re.compile(r"\[\[([A-Za-z0-9_]+)\]\]")


def _split_group(inner: str) -> list[str]:
    """Üst seviye '|' ayırıcısına göre böl (iç gruplar ve kaçışlar korunur)."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner):
            buf.append(inner[i:i + 2])
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        if ch == "|" and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _render(template: str, rng: random.Random, depth: int = 0) -> str:
    """Özyinelemeli ayrıştırıcı: en dıştaki grubu bulur, içeriği seçer, derine iner."""
    out: list[str] = []
    i = 0
    size = len(template)
    while i < size:
        ch = template[i]
        if ch == "\\" and i + 1 < size:
            out.append(template[i + 1])
            i += 2
            continue
        if ch == "{":
            level = 1
            j = i + 1
            while j < size and level:
                if template[j] == "\\":
                    j += 2
                    continue
                if template[j] == "{":
                    level += 1
                elif template[j] == "}":
                    level -= 1
                j += 1
            if level:  # dengesiz: literal bırak
                out.append(ch)
                i += 1
                continue
            inner = template[i + 1:j - 1]
            options = _split_group(inner)
            pick = options[0] if len(options) == 1 else rng.choice(options)
            out.append(_render(pick, rng, depth + 1) if depth < MAX_DEPTH else pick)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _substitute(text: str, values: dict[str, Any] | None) -> str:
    if not values:
        return text
    def repl(match: re.Match[str]) -> str:
        return str(values.get(match.group(1), match.group(0)))
    return _VAR_RE.sub(repl, text)


def render(template: str, *, seed: int | float | None = None,
           rng: random.Random | None = None, values: dict[str, Any] | None = None) -> str:
    """Şablonu tek bir varyasyona indir; değişkenleri yerleştir."""
    src = rng or random.Random(seed)
    out = _render(template or "", src)
    out = _substitute(out, values)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def variants(template: str, n: int = 3, *, seed: int | None = None,
             values: dict[str, Any] | None = None) -> list[str]:
    """Aynı şablondan n farklı metin (tekrar edenler ayıklanır)."""
    rng = random.Random(seed)
    out: list[str] = []
    for _ in range(max(1, int(n)) * 3):
        if len(out) >= max(1, int(n)):
            break
        candidate = render(template, rng=rng, values=values)
        if candidate not in out:
            out.append(candidate)
    return out


def uniqueness_ratio(samples: Iterable[str]) -> float:
    """Benzersizlik oranı — spam filtresi riski için sağlık metriği."""
    rows = [str(s or "") for s in samples]
    if not rows:
        return 0.0
    return round(len(set(rows)) / len(rows), 3)
