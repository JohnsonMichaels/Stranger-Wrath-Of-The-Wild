#!/usr/bin/env python3
"""List the outgoing calls of named functions in an XBE, in address order.

Companion to xbe_xref.py (which answers "who calls X?"); this answers
"what does X call?", which is how you read a startup path without a
disassembler.  Resolves MSVC incremental-link `E9` thunks.

  python xbe_callees.py --xbe SteefDebug.xbe --syms dbg.tsv --delta 0x118E0 \
                        --func "Renderer::AppInit" --func "Renderer::Swap"
"""
import argparse
import bisect
import re
import struct
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from xbe_xref import Xbe, load_syms, FuncIndex  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", required=True)
    ap.add_argument("--syms", required=True)
    ap.add_argument("--delta", default="0x118E0")
    ap.add_argument("--func", action="append", default=[],
                    help="exact or regex function name (repeatable)")
    ap.add_argument("--regex", action="store_true")
    args = ap.parse_args()

    delta = int(args.delta, 16)
    xbe = Xbe(args.xbe)
    funcs, byname = load_syms(args.syms, delta)
    idx = FuncIndex(funcs)

    # thunk map over executable sections
    thunk = {}
    for s in xbe.sections:
        if not s["exec"] or s["rsz"] == 0:
            continue
        blob = xbe.data[s["ra"]: s["ra"] + s["rsz"]]
        for i in range(len(blob) - 4):
            if blob[i] != 0xE9:
                continue
            va = s["va"] + i
            if idx.lookup(va)[0] is not None:
                continue
            thunk[va] = (va + 5 + struct.unpack_from("<i", blob, i + 1)[0]) & 0xFFFFFFFF

    def resolve(va, depth=8):
        while va in thunk and depth:
            va = thunk[va]
            depth -= 1
        return va

    wanted = []
    for f in args.func:
        if args.regex:
            pat = re.compile(f)
            for va, size, name in funcs:
                if pat.search(name) and size:
                    wanted.append((name, va, size))
        else:
            for va, size, tag in byname.get(f, []):
                if tag == 5 and size:
                    wanted.append((f, va, size))

    seen = set()
    for name, va, size in wanted:
        if (name, va) in seen:
            continue
        seen.add((name, va))
        body = xbe.read_va(va, size)
        print("\n=== %s  @0x%08x  (%d bytes) ===" % (name, va, size))
        if body is None:
            print("  <not file-backed>")
            continue
        i = 0
        n = 0
        while i < len(body) - 4:
            if body[i] == 0xE8:
                tgt = resolve((va + i + 5 +
                               struct.unpack_from("<i", body, i + 1)[0]) & 0xFFFFFFFF)
                tname, off = idx.lookup(tgt)
                sec = xbe.section_of(tgt)
                label = tname if tname else "sub_%08x" % tgt
                if off:
                    label += "+0x%x" % off
                n += 1
                print("  +0x%04x  call  [%s] %s" % (i, sec["name"] if sec else "?", label))
                i += 5
                continue
            i += 1
        if n == 0:
            print("  (no direct calls)")


if __name__ == "__main__":
    main()
