"""Extract full failure tracebacks and warnings from pytest output."""
import re

with open(r"c:\Users\Lenovo\lead-qualification-engine-2\_pytest_full.txt", "r", encoding="utf-16", errors="ignore") as f:
    content = f.read()

lines = content.split("\n")

print("=" * 80)
print("ALL FAILURES AND ERRORS WITH FULL TRACEBACKS")
print("=" * 80)

i = 0
fail_count = 0
while i < len(lines):
    line = lines[i]
    m = re.match(r"\s*(tests/[^\s:]+::[^\s]+)\s+(FAILED|ERROR)", line)
    if m:
        test_name = m.group(1)
        status = m.group(2)
        fail_count += 1
        print(f"\n{'#'*80}")
        print(f"#{fail_count} FAILURE: {test_name} [{status}]")
        print(f"{'#'*80}")
        # Capture following lines until next test result or summary section
        j = i + 1
        cap = 0
        while j < len(lines):
            l = lines[j]
            if re.match(r"\s*tests/[^\s:]+::[^\s]+\s+(FAILED|ERROR|PASSED|SKIPPED)", l):
                break
            if l.strip().startswith("short test summary") or l.strip().startswith("===") and "passed" in l:
                break
            if l.strip():
                print(l[:500])
                cap += 1
            if cap > 30:
                print("...(truncated)...")
                break
            j += 1
        i = j
    else:
        i += 1

print(f"\n{'='*80}")
print("WARNINGS")
print(f"{'='*80}")
for i, line in enumerate(lines):
    if "UserWarning" in line or "DeprecationWarning" in line or "StarletteDeprecationWarning" in line:
        print(f"Line {i+1}: {line.strip()[:500]}")

print(f"\n{'='*80}")
print("SUMMARY")
print(f"{'='*80}")
