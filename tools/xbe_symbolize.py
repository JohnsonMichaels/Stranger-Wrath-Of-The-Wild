"""Turn Xbox virtual addresses from a Cxbx crash dump into game function names.

    python tools/xbe_symbolize.py <syms.tsv> <delta_hex> <va> [<va> ...]

<syms.tsv> is the output of tools/pdb_dump.ps1 for the title's PDB. The PDB stores
RVAs relative to the linker's PE layout, which is NOT the layout the XBE gets loaded
at, so every address needs a fixed correction:

    XBE_VA = PDB_RVA + delta

Derive delta once per build by anchoring on a symbol Cxbx already resolved for you -
its kernel log prints "SymbolCache: <xbe va> -> mainCRTStartup", so
delta = that address - the PDB RVA of mainCRTStartup. Measured values:
Debug (2004-04-29) 0x118E0, Release (2004-05-22) 0x10900, Final (2004-05-22) 0x10920.
"""
import sys


def load(path):
    funcs = []
    for line in open(path, encoding='utf-8', errors='replace'):
        parts = line.rstrip('\n').split('\t')
        if len(parts) < 5:
            continue
        try:
            rva = int(parts[0], 16)
            size = int(parts[1])
        except ValueError:
            continue
        funcs.append((rva, size, parts[4]))
    funcs.sort()
    return funcs


def find(funcs, rva):
    lo, hi = 0, len(funcs) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if funcs[mid][0] <= rva:
            best = funcs[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    funcs = load(sys.argv[1])
    delta = int(sys.argv[2], 16)
    for a in sys.argv[3:]:
        va = int(a, 16)
        rva = va - delta
        hit = find(funcs, rva)
        if hit is None:
            print("0x%08x -> (before first symbol)" % va)
            continue
        off = rva - hit[0]
        inside = "" if (hit[1] and off < hit[1]) else "   [PAST END of symbol]"
        print("0x%08x  (pdb rva 0x%x)  ->  %s + 0x%x   [size 0x%x]%s"
              % (va, rva, hit[2], off, hit[1], inside))
    return 0


if __name__ == '__main__':
    sys.exit(main())
