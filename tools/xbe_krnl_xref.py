#!/usr/bin/env python3
"""Which kernel exports does the title actually CALL, and from where?

Kernel imports are indirect: `call dword ptr [thunk_table + n]` (FF 15 disp32)
or `jmp dword ptr [...]` (FF 25 disp32).  Scanning for those and mapping the
disp32 back to a thunk-table slot gives the ordinal, and the containing PDB
function gives the caller.

  python xbe_krnl_xref.py --xbe SteefDebug.xbe --syms dbg.tsv --delta 0x118E0
"""
import argparse
import struct
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from xbe_xref import Xbe, load_syms, FuncIndex  # noqa: E402

DEBUG_KT_KEY = 0xEFB1F152
RETAIL_KT_KEY = 0x5B6D40B6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", required=True)
    ap.add_argument("--syms", required=True)
    ap.add_argument("--delta", default="0x118E0")
    ap.add_argument("--ords", default="", help="comma list to restrict output")
    args = ap.parse_args()

    xbe = Xbe(args.xbe)
    funcs, _ = load_syms(args.syms, int(args.delta, 16))
    idx = FuncIndex(funcs)

    kt_enc = struct.unpack_from("<I", xbe.data, 0x158)[0]
    kt = None
    for key in (DEBUG_KT_KEY, RETAIL_KT_KEY):
        v = kt_enc ^ key
        if xbe.section_of(v):
            kt = v
            break
    if kt is None:
        raise SystemExit("kernel thunk table not found")

    # slot VA -> ordinal
    slot = {}
    va = kt
    while True:
        raw = xbe.read_va(va, 4)
        if raw is None:
            break
        v = struct.unpack("<I", raw)[0]
        if v == 0:
            break
        if v & 0x80000000:
            slot[va] = v & 0x7FFFFFFF
        va += 4

    want = set(int(x) for x in args.ords.split(",") if x.strip()) if args.ords else None
    hits = defaultdict(lambda: defaultdict(int))
    for s in xbe.sections:
        if not s["exec"] or s["rsz"] == 0:
            continue
        blob = xbe.data[s["ra"]: s["ra"] + s["rsz"]]
        for i in range(len(blob) - 6):
            if blob[i] != 0xFF or blob[i + 1] not in (0x15, 0x25):
                continue
            disp = struct.unpack_from("<I", blob, i + 2)[0]
            o = slot.get(disp)
            if o is None:
                continue
            if want and o not in want:
                continue
            cname, _ = idx.lookup(s["va"] + i)
            hits[o][cname or "%s+0x%x" % (s["name"], i)] += 1

    print("thunk table @ 0x%08x, %d imported ordinals, %d called"
          % (kt, len(slot), len(hits)))
    print("ordinal\tn_calls\tcallers")
    for o in sorted(hits):
        c = hits[o]
        tot = sum(c.values())
        top = sorted(c.items(), key=lambda kv: -kv[1])[:12]
        print("%d\t%d\t%s" % (o, tot, "; ".join("%s x%d" % t for t in top)))
    uncalled = sorted(set(slot.values()) - set(hits))
    print("\nimported but no direct indirect-call site found: %s"
          % ",".join(str(x) for x in uncalled))


if __name__ == "__main__":
    main()
