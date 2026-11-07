"""Read config.py lines around next_bot_username and set_bot_pool."""
with open(r"c:\Users\Lenovo\lead-qualification-engine-2\config.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i in range(765, 840):
    if i < len(lines):
        print(f"{i+1:4d}: {lines[i].rstrip()}")
