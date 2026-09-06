"""Compatibility entry point for bounded, evidence-gated demand discovery."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def main() -> int:
    # Compatibility entry point: never publish the legacy unscanned fallback.
    from scripts.enterprise_demand_feed import main as build_demand_feed
    return build_demand_feed()


if __name__ == "__main__":
    raise SystemExit(main())
