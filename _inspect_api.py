"""Inspect pact Pact object API and config functions."""
import sys
sys.path.insert(0, r"c:\Users\Lenovo\lead-qualification-engine-2")

# 1. Pact object attributes
from pact import Pact
p = Pact("Consumer", "Provider")
print("=== Pact public attributes ===")
for x in sorted([a for a in dir(p) if not a.startswith("_")]):
    print(f"  {x}")
print()

# 2. config functions
import config
print("=== config functions ===")
for name in ["require_live_telegram_link", "next_bot_username", "set_bot_pool", "bot_pool_usernames", "bot_tokens", "TELEGRAM_BOT_USERNAME"]:
    obj = getattr(config, name, None)
    if obj is not None:
        print(f"  {name}: {type(obj).__name__}")
    else:
        print(f"  {name}: NOT FOUND")

print()
print("=== require_live_telegram_link source ===")
import inspect
print(inspect.getsource(config.require_live_telegram_link))

print()
print("=== next_bot_username source ===")
print(inspect.getsource(config.next_bot_username))

print()
print("=== llm_router _steps ===")
from core import llm_router as r
print(inspect.getsource(r._steps))
print(f"PRIMARY_MODEL={r.PRIMARY_MODEL}")
print(f"FAST_MODEL={r.FAST_MODEL}")
print(f"EXTERNAL_ENABLED={r.EXTERNAL_ENABLED}")
print(f"OLLAMA_BASE={r.OLLAMA_BASE}")
print(f"model_chain()={r.model_chain()}")
steps = r._steps()
print(f"_steps()={steps}")
for m, _ in steps:
    print(f"  step model starts with ollama/: {m.startswith('ollama/')}")

print()
print("=== MODULES count ===")
from nirvana.registry import MODULES
modules = MODULES()
print(f"Module count: {len(modules)}")
