"""Check all nirvana modules and their host assignments."""
import sys
sys.path.insert(0, r"c:\Users\Lenovo\lead-qualification-engine-2")
from nirvana.registry import MODULES
m = MODULES()
print(f"Count: {len(m)}")
for k, v in sorted(m.items()):
    print(f"  {k:40s} host={v['host']:10s} letter={v['letter']}")
