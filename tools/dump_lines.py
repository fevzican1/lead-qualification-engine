import sys

def dump(path, start, end, out):
    lines = open(path, encoding='utf-8').read().splitlines(keepends=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(''.join(lines[start - 1:end]))

if __name__ == '__main__':
    dump(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
