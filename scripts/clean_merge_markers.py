"""Strip git merge-conflict markers from JSON shard files and validate them.

The harvest-shard commit step used `git pull --rebase --autostash`. When the
same fleet family re-runs (every 30 min) the stash-pop can conflict with the
previously committed shards, baking `<<<<<<<` / `=======` / `>>>>>>>` markers
into the JSON. merge_feed_shards then rejects those shards, silently starving
that family out of the feed.

Use:
    python scripts/clean_merge_markers.py feeds/shards/*.json
        remove markers in place; exit 1 if any file is unusable afterwards
    python scripts/clean_merge_markers.py --check feeds/shards/*.json
        read-only: exit 0 only if every file parses with no markers left
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

_MARKER = re.compile(r"^(?:<<<<<<<|=======|>>>>>>>)", re.MULTILINE)


def _valid_text(text: str) -> bool:
    """JSON-parseable, no markers, and internally consistent (count==urls)."""
    if _MARKER.search(text):
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    rows = data.get("urls")
    if not isinstance(rows, list):
        return False
    if "count" in data:
        try:
            if int(data["count"]) != len(rows):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _score_side(side: str) -> tuple[int, str, int]:
    """Score a conflict side: (row count, updated_at, fresh-preferring bonus)."""
    m_count = re.search(r'"count"\s*:\s*(\d+)', side)
    count = int(m_count.group(1)) if m_count else 0
    m_upd = re.search(r'"updated_at"\s*:\s*"([^"]+)"', side)
    updated = m_upd.group(1) if m_upd else ""
    # In stash-pop conflicts the LAST side is the freshly harvested data
    # (the "Stashed changes"); prefer it on ties.
    return count, updated, 1


def _split_blocks(lines: list[str]) -> tuple[list[list[str]], list[list[list[str]]]]:
    """Split lines into (non-conflict chunks, conflict block sides).

    Each block is `[side_a_lines, side_b_lines, ...]`. Choosing a side per
    block picks which sibling lines to keep; the non-conflict chunks surround
    them unchanged.
    """
    chunks: list[list[str]] = []
    blocks: list[list[list[str]]] = []
    current_chunk: list[str] = []
    i = 0
    while i < len(lines):
        m = _MARKER.match(lines[i].strip())
        if m is not None and m.group(0) == "<<<<<<<":
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
            j = i + 1
            sides: list[list[str]] = [[]]
            while j < len(lines):
                me = _MARKER.match(lines[j].strip())
                if me is not None and me.group(0) == "=======":
                    sides.append([])
                elif me is not None and me.group(0) == ">>>>>>>":
                    j += 1
                    break
                else:
                    sides[-1].append(lines[j])
                j += 1
            blocks.append(sides)
            i = j
        else:
            if m is not None and m.group(0) in ("=======", ">>>>>>>"):
                # Stray marker outside a block: drop it.
                i += 1
                continue
            current_chunk.append(lines[i])
            i += 1
    if current_chunk:
        chunks.append(current_chunk)
    return chunks, blocks


def _build(chunks: list[list[str]], blocks: list[list[list[str]]], choices: list[int]) -> str:
    """Assemble a candidate document from per-block side choices."""
    out: list[str] = []
    for idx in range(len(blocks)):
        out.extend(chunks[idx])
        side = blocks[idx][choices[idx]]
        out.extend(side)
    out.extend(chunks[len(blocks)])
    return "".join(out)


def _resolve(text: str) -> str:
    """Replace each conflict block with its best side.

    Prefer the LAST side (the stashed/fresh harvest on stash-pop conflicts)
    everywhere, then fall back per-file to the first side when the assembled
    document is inconsistent, then greedy-flip blocks until it validates.
    """
    lines = text.splitlines(keepends=True)
    if not _MARKER.search(text):
        return text
    chunks, blocks = _split_blocks(lines)
    n = len(blocks)
    if n == 0:
        return text

    def try_choices(prio: int) -> str | None:
        choices = [len(b) - 1 if (len(b) > 1 and prio == 0) else 0 for b in blocks]
        candidate = _build(chunks, blocks, choices)
        if _valid_text(candidate):
            return candidate
        return None

    # Try all-last, then all-first.
    for prio in (0, 1):
        candidate = try_choices(prio)
        if candidate is not None:
            return candidate
    # Greedy: start from all-last and flip one block at a time if it helps.
    choices = [len(b) - 1 if len(b) > 1 else 0 for b in blocks]
    for idx in range(n):
        if choices[idx] == 0 or len(blocks[idx]) < 2:
            continue
        saved = choices[idx]
        choices[idx] = 0
        candidate = _build(chunks, blocks, choices)
        if _valid_text(candidate):
            return candidate
        choices[idx] = saved
    # Last resort: strip marker lines and keep whichever side survives.
    return "\n".join(ln for ln in text.splitlines() if not _MARKER.match(ln))


def _strip_markers(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return True
    if not _MARKER.search(text):
        return False
    cleaned = _resolve(text)
    path.write_text(cleaned, encoding="utf-8")
    print(f"cleaned {path}")
    return True


def _validate(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID {path}: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return False
    if _MARKER.search(path.read_text(encoding="utf-8")):
        print(f"MARKERS {path}", file=sys.stderr)
        return False
    rows = data.get("urls") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        print(f"NO URLS {path}", file=sys.stderr)
        return False
    return True


def _expand(patterns: list[Path]) -> list[Path]:
    """Expand shell-style globs (PowerShell/Windows do not expand them)."""
    out: list[Path] = []
    for pattern in patterns:
        raw = str(pattern)
        if glob.has_magic(raw):
            matches = sorted(Path(p) for p in glob.glob(raw))
            out.extend(matches)
        elif pattern.exists():
            out.append(pattern)
        else:
            out.append(pattern)  # let validate report it as missing
    seen: set[str] = set()
    unique: list[Path] = []
    for path in out:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate only")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    paths = _expand(args.paths)
    if args.check:
        bad = [p for p in paths if not _validate(p)]
        return 1 if bad else 0

    for path in paths:
        _strip_markers(path)
    bad = [p for p in paths if not _validate(p)]
    if bad:
        print(f"{len(bad)} file(s) still unusable after cleanup", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())