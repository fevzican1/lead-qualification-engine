#!/usr/bin/env python
import subprocess
import sys
import os

# Use workspace directory for output
output_path = os.path.join(os.getcwd(), "manifest_output.txt")

# Run manifest_check.py --strict and capture all output
result = subprocess.run(
    [sys.executable, "manifest_check.py", "--strict"],
    cwd="C:\\Users\\Lenovo\\lead-qualification-engine-2",
    capture_output=True,
    text=True,
    timeout=120
)

# Write both stdout and stderr to output file
with open(output_path, "w", encoding="utf-8") as f:
    f.write("STDOUT:\n")
    f.write(result.stdout)
    f.write("\n\nSTDERR:\n")
    f.write(result.stderr)

print(f"Output written to {output_path}")
print(f"Return code: {result.returncode}")
print(f"Stdout length: {len(result.stdout)}")
print(f"Stderr length: {len(result.stderr)}")