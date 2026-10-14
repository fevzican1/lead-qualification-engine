import sys

KEYWORDS = ['Dış feed', 'Tank still thin', 'feed_stamp =', 'QUEUE_TARGET', 'if smb_early:', 'sync_github_feed', 'force_low =']

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'auto_runner.py'
    lines = open(path, encoding='utf-8').read().splitlines()
    for i, l in enumerate(lines, 1):
        if any(k in l for k in KEYWORDS):
            print(f"{i:4d} | {l.strip()[:110]}")

if __name__ == '__main__':
    main()


