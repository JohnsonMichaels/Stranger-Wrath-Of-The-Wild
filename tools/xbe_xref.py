#!/usr/bin/env python3
"""Cross-reference an Xbox XBE against its paired PDB symbol dump.

Answers "does this title actually CALL function X?" by scanning every
executable section for direct `E8 rel32` / `E9 rel32` transfers and mapping
both the call site and the target back to PDB function names.

Inputs
------
  --xbe   the XBE image (read only)
  --syms  TSV produced by tools/pdb_dump.ps1  (rva TAB size TAB tag TAB flags TAB name)
  --delta XBE_VA - PDB_RVA   (0x118E0 for SteefDebug, 0x10900 for Steef release)

Usage
-----
  python xbe_xref.py --xbe SteefDebug.xbe --syms dbg.tsv --delta 0x118E0 \
                     --filter "^D3D" --out xrefs.tsv

Output columns: target, target_section, n_calls, callers(section:name xN, ...)
"""
import argparse
import bisect
import re
import struct
import sys
from collections import defaultdict


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


class Xbe:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        if self.data[:4] != b"XBEH":
            raise SystemExit("not an XBE: %s" % path)
        d = self.data
        self.base = u32(d, 0x104)
        nsec = u32(d, 0x11C)
        sec_hdr = u32(d, 0x120)
        self.sections = []
        for i in range(nsec):
            o = (sec_hdr - self.base) + i * 0x38
            flags = u32(d, o + 0x00)
            va = u32(d, o + 0x04)
            vsz = u32(d, o + 0x08)
            ra = u32(d, o + 0x0C)
            rsz = u32(d, o + 0x10)
            naddr = u32(d, o + 0x14)
            name = ""
            if naddr:
                p = naddr - self.base
                end = d.find(b"\x00", p)
                name = d[p:end].decode("ascii", "replace")
            self.sections.append(
                dict(name=name or "sec%d" % i, flags=flags, va=va, vsz=vsz,
                     ra=ra, rsz=rsz, exec=bool(flags & 0x00000004))
            )

    def section_of(self, va):
        for s in self.sections:
            if s["va"] <= va < s["va"] + s["vsz"]:
                return s
        return None

    def read_va(self, va, n):
        """Return n bytes at virtual address va, or None if not backed by file."""
        s = self.section_of(va)
        if s is None:
            return None
        off = va - s["va"]
        if off + n > s["rsz"]:
            return None
        return self.data[s["ra"] + off: s["ra"] + off + n]


def load_syms(path, delta):
    """-> (sorted list of (va, size, name) for functions, dict name->[va])"""
    funcs = []
    byname = defaultdict(list)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            try:
                rva = int(parts[0], 16)
                size = int(parts[1])
                tag = int(parts[2])
            except ValueError:
                continue
            name = parts[4]
            va = rva + delta
            if tag == 5:  # SymTagFunction
                funcs.append((va, size, name))
            byname[name].append((va, size, tag))
    funcs.sort(key=lambda t: (t[0], -t[1]))
    return funcs, byname


class FuncIndex:
    """Map an arbitrary VA to the function that contains it."""

    def __init__(self, funcs):
        # collapse duplicates at the same start VA, keeping the largest extent
        best = {}
        for va, size, name in funcs:
            cur = best.get(va)
            if cur is None or size > cur[0]:
                best[va] = (size, name)
        self.starts = sorted(best)
        self.info = [best[v] for v in self.starts]

    def lookup(self, va):
        i = bisect.bisect_right(self.starts, va) - 1
        if i < 0:
            return None, None
        start = self.starts[i]
        size, name = self.info[i]
        if size and va >= start + size:
            return None, None
        return name, va - start


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", required=True)
    ap.add_argument("--syms", required=True)
    ap.add_argument("--delta", default="0x118E0")
    ap.add_argument("--filter", default="^D3D", help="regex on target symbol name")
    ap.add_argument("--out", default=None)
    ap.add_argument("--imm", action="store_true",
                    help="also count 32-bit absolute immediates (indirect refs)")
    args = ap.parse_args()

    delta = int(args.delta, 16) if args.delta.lower().startswith("0x") else int(args.delta)
    xbe = Xbe(args.xbe)
    funcs, byname = load_syms(args.syms, delta)
    idx = FuncIndex(funcs)
    pat = re.compile(args.filter)

    targets = {}  # va -> name
    for va, size, name in funcs:
        if pat.search(name):
            targets.setdefault(va, name)

    # ---- pass 1: find MSVC incremental-link thunks (`E9 rel32`, 5 bytes, no
    # containing function symbol).  A debug build routes EVERY call through
    # one of these, so without resolving them every function shows exactly
    # one caller: its own thunk. ----
    thunk = {}  # thunk_va -> immediate target va
    for s in xbe.sections:
        if not s["exec"] or s["rsz"] == 0:
            continue
        blob = xbe.data[s["ra"]: s["ra"] + s["rsz"]]
        base = s["va"]
        for i in range(0, len(blob) - 4):
            if blob[i] != 0xE9:
                continue
            va = base + i
            if idx.lookup(va)[0] is not None:
                continue  # inside a real function -> an ordinary jmp
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            thunk[va] = (va + 5 + rel) & 0xFFFFFFFF

    def resolve(va, depth=8):
        seen = set()
        while va in thunk and depth > 0 and va not in seen:
            seen.add(va)
            va = thunk[va]
            depth -= 1
        return va

    # thunk_va -> final target, restricted to targets we care about
    tgt_set = set(targets)
    thunk_to_tgt = {}
    for tva in list(thunk):
        f = resolve(tva)
        if f in tgt_set:
            thunk_to_tgt[tva] = f

    # ---- pass 2: count real call sites, following thunks ----
    calls = defaultdict(lambda: defaultdict(int))   # target_va -> caller_name -> n
    imms = defaultdict(lambda: defaultdict(int))

    for s in xbe.sections:
        if not s["exec"] or s["rsz"] == 0:
            continue
        blob = xbe.data[s["ra"]: s["ra"] + s["rsz"]]
        base = s["va"]
        n = len(blob)
        for i in range(n - 4):
            op = blob[i]
            if op == 0xE8 or op == 0xE9:
                site = base + i
                cname, _ = idx.lookup(site)
                if cname is None and site in thunk:
                    continue  # this IS a thunk, not a call site
                rel = struct.unpack_from("<i", blob, i + 1)[0]
                raw = (site + 5 + rel) & 0xFFFFFFFF
                tgt = raw if raw in tgt_set else thunk_to_tgt.get(raw)
                if tgt is not None:
                    calls[tgt]["%s|%s" % (s["name"],
                                          cname or "sub_%08x" % site)] += 1
            if args.imm:
                val = struct.unpack_from("<I", blob, i)[0]
                v2 = val if val in tgt_set else thunk_to_tgt.get(val)
                if v2 is not None:
                    cname, _ = idx.lookup(base + i)
                    imms[v2][cname or "%s+0x%x" % (s["name"], i)] += 1

    rows = []
    for tva, tname in sorted(targets.items(), key=lambda kv: kv[1]):
        c = calls.get(tva, {})
        total = sum(c.values())
        # calls made by the TITLE's own code (.text / .textbss) vs by the
        # statically linked library section the target itself lives in.
        game = sum(n for k, n in c.items() if k.split("|", 1)[0].startswith(".text"))
        sec = xbe.section_of(tva)
        secname = sec["name"] if sec else "?"
        # game callers first: those are the evidence that the TITLE calls this
        callers = sorted(c.items(),
                         key=lambda kv: (not kv[0].startswith(".text"), -kv[1]))
        desc = ["%s x%d" % (cn, cnt) for cn, cnt in callers[:24]]
        row = [tname, "0x%08x" % tva, secname, str(total), str(game),
               "; ".join(desc)]
        if args.imm:
            im = imms.get(tva, {})
            row.insert(5, str(sum(im.values())))
        rows.append(row)

    hdr = ["target", "va", "section", "n_calls", "n_calls_from_title"]
    if args.imm:
        hdr.append("n_imm_refs")
    hdr.append("callers")

    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    out.write("\t".join(hdr) + "\n")
    for r in rows:
        out.write("\t".join(r) + "\n")
    if args.out:
        out.close()
        print("wrote %d rows -> %s" % (len(rows), args.out))


if __name__ == "__main__":
    main()
