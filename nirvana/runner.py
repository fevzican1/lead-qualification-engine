"""Nirvana CLI — one entrypoint for all 9 lanes.

    python -m nirvana.runner <module_name> [--no-notify] [--chat-id N]

GitHub Actions and the Oracle systemd units both call this; the module's host
assignment lives in nirvana/nirvana.yaml.
"""
from __future__ import annotations

import argparse
import json
import sys

from nirvana.registry import MODULES, module

RUNNERS = {
    "discovery_agent": "nirvana.discovery_agent",
    "enrichment_agent": "nirvana.enrichment_agent",
    "audit_verifier_agent": "nirvana.audit_verifier_agent",
    "strategy_pivot_agent": "nirvana.strategy_pivot_agent",
    "objection_handler_agent": "nirvana.objection_handler_agent",
    "onboarding_agent": "nirvana.onboarding_agent",
    "delivery_runner": "nirvana.delivery_runner",
    "retention_agent": "nirvana.retention_agent",
    "watchdog_quota_agent": "nirvana.watchdog_quota_agent",
    "linkedin_router": "nirvana.linkedin_router",
    "meta_orchestrator": "nirvana.meta_orchestrator",
    "micro_audit_proof_agent": "nirvana.micro_audit_proof",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nirvana.runner")
    parser.add_argument("module", nargs="?", help="module name from nirvana.yaml")
    parser.add_argument("--list", action="store_true", help="list registered modules")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--self-test", action="store_true", help="micro_audit_proof: local-fixture dogrulama")
    args = parser.parse_args(argv)

    if args.list or not args.module:
        for name, meta in MODULES().items():
            print(f"{meta['letter']}  {name:26s} [{meta['host']:6s}] {meta['schedule']:12s} {meta['entrypoint']}")
        return 0

    if args.module not in RUNNERS:
        parser.error(f"unknown module {args.module!r}")
    module(args.module)  # validates registry entry exists
    import importlib
    runner = importlib.import_module(RUNNERS[args.module])

    # Only the Oracle-side lanes take notify flags; heavy GitHub lanes are pure.
    if args.module == "onboarding_agent":
        kwargs: dict = {"chat_id": args.chat_id}
    elif args.module in {"delivery_runner", "retention_agent"}:
        kwargs = {"notify": not args.no_notify}
    elif args.module == "watchdog_quota_agent":
        kwargs = {"dry_run": args.no_notify}  # watchdog hiçbir koşulda Telegram'a bildirim atmaz
    elif args.module == "linkedin_router":
        kwargs = {"notify": not args.no_notify}
    elif args.module == "meta_orchestrator":
        kwargs = {"notify": not args.no_notify}
    elif args.module == "micro_audit_proof_agent":
        kwargs = {"run_self_test": getattr(args, "self_test", False)}
    else:
        kwargs = {}
    result = runner.run_batch(**kwargs)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
