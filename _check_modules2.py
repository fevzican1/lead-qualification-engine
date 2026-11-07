"""Check module counts and host assignments."""
import sys
sys.path.insert(0, r"c:\Users\Lenovo\lead-qualification-engine-2")
from nirvana.registry import MODULES
m = MODULES()
print(f"Count: {len(m)}")
github = sorted([k for k,v in m.items() if v['host']=='github'])
oracle = sorted([k for k,v in m.items() if v['host']=='oracle'])
print(f"GITHUB ({len(github)}): {github}")
print(f"ORACLE ({len(oracle)}): {oracle}")
