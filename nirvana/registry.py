"""Nirvana registry loader (nirvana/nirvana.yaml)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

import config

REGISTRY_PATH = config.ROOT / "nirvana" / "nirvana.yaml"
STATE_DIR = config.ROOT / "nirvana" / "state"


def load_registry(path: Path | None = None) -> dict[str, Any]:
    source = path or REGISTRY_PATH
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("nirvana"), dict):
        raise RuntimeError("nirvana.yaml is missing the top-level 'nirvana' mapping")
    return data["nirvana"]


@lru_cache(maxsize=1)
def MODULES() -> dict[str, dict[str, Any]]:
    return dict(load_registry()["modules"])


def module(name: str) -> dict[str, Any]:
    try:
        return MODULES()[name]
    except KeyError as exc:
        raise KeyError(f"Unknown nirvana module: {name}") from exc


def state_path(name: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / name
