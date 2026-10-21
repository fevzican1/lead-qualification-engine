#!/usr/bin/env python
import sys
import os
import json

# Add the workspace to path
sys.path.insert(0, r'C:\Users\Lenovo\lead-qualification-engine-2')

from manifest_check import run

# Run with strict mode
print("Running manifest_check with --strict...")
r = run(strict=True)

# Write results to file for capture
output_path = os.path.join(os.getcwd(), "manifest_results.json")
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(r, f, indent=2, ensure_ascii=False)

# Print structured results
print("\n=== STRUCTURED RESULTS ===")
print(json.dumps(r, indent=2, ensure_ascii=False))

# Check each gate
print("\n=== GATE STATUS ===")
for item in r.get("results", []):
    gate_id = item.get("gate", "unknown")
    ok = item.get("ok", False)
    detail = item.get("detail", "")
    print(f"  {gate_id}: {'PASS' if ok else 'FAIL'} - {detail[:100]}")

# Summary
ok_count = sum(1 for item in r.get("results", []) if item.get("ok"))
bad_count = sum(1 for item in r.get("results", []) if not item.get("ok"))
print(f"\n=== SUMMARY ===")
print(f"OK: {ok_count}, FAIL: {bad_count}")
print(f"Overall: {'ALL PASSED' if ok_count == len(r.get('results', [])) else 'SOME FAILED'}")

# Save summary
summary = {
    "ok_count": ok_count,
    "fail_count": bad_count,
    "overall": "ALL PASSED" if ok_count == len(r.get("results", [])) else "SOME FAILED"
}
with open("manifest_summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)

print(f"\nResults written to {output_path} and manifest_summary.json")