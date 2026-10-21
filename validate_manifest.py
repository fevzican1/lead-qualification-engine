#!/usr/bin/env python
import sys
import os
import json

sys.path.insert(0, r'C:\Users\Lenovo\lead-qualification-engine-2')

from manifest_check import run

print("Running manifest_check with strict mode...")
r = run(strict=True)

# Write results
with open('manifest_results_final.json', 'w', encoding='utf-8') as f:
    json.dump(r, f, indent=2, ensure_ascii=False)

# Print summary
ok_count = sum(1 for item in r.get('results', []) if item.get('ok'))
bad_count = sum(1 for item in r.get('results', []) if not item.get('ok'))

print(f"\n=== SUMMARY ===")
print(f"OK: {ok_count}, FAIL: {bad_count}")
print(f"Total: {len(r.get('results', []))}")
print(f"Overall: {'ALL PASSED ✅' if ok_count == len(r.get('results', [])) else 'SOME FAILED ❌'}")

# Print gate details
print(f"\n=== GATE DETAILS ===")
for item in r.get('results', []):
    gate = item.get('gate', 'unknown')
    ok = item.get('ok', False)
    detail = item.get('detail', '')[:80]
    status = 'PASS' if ok else 'FAIL'
    print(f"  {gate}: {status} - {detail}")

# Save summary
summary = {
    "ok_count": ok_count,
    "fail_count": bad_count,
    "overall": "ALL PASSED" if ok_count == len(r.get('results', [])) else "SOME FAILED"
}
with open('manifest_summary_final.json', 'w', encoding='utf-8') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)

print(f"\nResults saved to manifest_results_final.json and manifest_summary_final.json")