"""Extract ALL failures + warnings from pytest output with full traceback context."""
import re

with open(r"c:\Users\Lenovo\lead-qualification-engine-2\_pytest_full.txt", "r", encoding="utf-16", errors="ignore") as f:
    content = f.read()

lines = content.split("\n")

print("="*80)
print("FAILED / ERROR TESTS WITH FULL DETAILS")
print("="*80)

# Find sections: each failure has test name line, then traceback, then blank
i = 0
while i < len(lines):
    line = lines[i]
    if "FAILED" in line or "ERROR" in line:
        # print context from this line onward until next test name or end
        test_name = line.strip()
        print(f"\n{'#'*70}")
        print(f"TEST: {test_name}")
        print(f"{'#'*70}")
        # print following lines until we hit another tests/ line or blank section
        j = i + 1
        count = 0
        while j < len(lines) and count < 30:
            l = lines[j]
            # stop at next test nodeid line
            if re.match(r"tests/[a-z_]", l.strip()) and ("FAILED" in l or "ERROR" in l or "PASSED" in l or "SKIPPED" in l):
                break
            if l.strip():
                print(l[:500])
                count += 1
            j += 1
    i += 1

print("\n" + "="*80)
print("WARNINGS")
print("="*80)
for i, line in enumerate(lines):
    if "WARNING" in line.upper() or "warn" in line.lower() and len(line.strip()) > 20:
        print(f"Line {i+1}: {line.strip()[:400]}")
