"""Extract failure details from pytest output file."""
import re

with open(r"c:\Users\Lenovo\lead-qualification-engine-2\_pytest_full.txt", "r", encoding="utf-16", errors="ignore") as f:
    content = f.read()

lines = content.split("\n")

# Find FAILED and ERROR lines
for i, line in enumerate(lines):
    if "FAILED" in line or "ERROR" in line:
        print(f"Line {i+1}: {line.strip()}")
        # Print next few lines for error details
        for j in range(i+1, min(i+8, len(lines))):
            if lines[j].strip() and not lines[j].startswith("tests/"):
                print(f"   detail {j+1}: {lines[j].strip()[:200]}")
        print()
