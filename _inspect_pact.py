"""Check Pact v4 serve() return value and URI access."""
import sys
sys.path.insert(0, r"c:\Users\Lenovo\lead-qualification-engine-2")

from pact import Pact
p = Pact("Consumer", "Provider")
result = p.serve()
print(f"serve() returned: {result!r} (type: {type(result).__name__})")
print(f"Pact attributes after serve():")
for x in sorted([a for a in dir(p) if not a.startswith("_")]):
    val = getattr(p, x)
    if not callable(val):
        print(f"  {x} = {val!r}")
    else:
        print(f"  {x} = <method>")
# Check mock service
if hasattr(p, '_mock_service'):
    ms = p._mock_service
    print(f"\n_mock_service: {ms!r}")
    for x in sorted([a for a in dir(ms) if not a.startswith("_")]):
        print(f"  _mock_service.{x} = {getattr(ms, x, 'N/A')!r}")
p.stop()
