#!/usr/bin/env python
import sys
import os
sys.path.insert(0, r'C:\Users\Lenovo\lead-qualification-engine-2')

ROOT = os.path.join(os.getcwd(), r'C:\Users\Lenovo\lead-qualification-engine-2')

demo_path = os.path.join(ROOT, "nirvana", "templates", "demo.html")
print(f"demo.html exists: {os.path.exists(demo_path)}")

if os.path.exists(demo_path):
    with open(demo_path, "r", encoding="utf-8") as f:
        demo = f.read()
    print(f"\ndemo.html content (first 500 chars): {demo[:500]}")
    
    required_keys = ["ctaBtn", "demo_expires_ts", "chat_url", "pay_url"]
    miss = [k for k in required_keys if k not in demo]
    print(f"\nRequired keys check: {required_keys}")
    print(f"Missing keys: {miss}")
else:
    print("demo.html NOT found!")