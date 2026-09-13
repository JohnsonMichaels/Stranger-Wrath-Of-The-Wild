#!/usr/bin/env python3
"""ds3d_disasm.py - disassembler / cross-reference helper for the DS3D call-path audit.

The other xbe_*.py tools want the 5-column pdb_dump.ps1 TSV; this one reads the
2-column TSV that pdb_syms.ps1 emits (rva<TAB>name, hex, no 0x) and, on top of the
listing, annotates every call target with the Cxbx patch status recorded in the
live diagnostics.txt ("HLE: X Patched" / "HLE: X: No patch registered (symbol
found at 0x..., running unpatched)").  A function that never appears in the
diagnostics was not detected by Cxbx's symbol database at all, which also means
it runs natively; it is marked NATIVE(undetected).

    python tools/ds3d_disasm.py [--xbe X] [--syms S] [--delta 0x10920] [--diag D]
        disasm   <name|va> [--len N]      annotated listing of one function
        callees  <name|va> [--len N]      only the outgoing call/jmp lines
        callers  <name|va> [...]          every E8/E9 site whose target is the function
        seccalls <section> [--to REGEX]   every call/jmp leaving a section (e.g. XACTENG)
                                          whose target name matches REGEX, with status
        ptr      <name|va>                dword references (vtables, pointer tables)
        status   [REGEX]                  PDB symbol -> Cxbx patch status table
        name     <va> [...]               symbolize addresses

<name> is an exact PDB name (namespace included, e.g. "DirectSound::CDirectSoundVoice::SetVolume");
if a name is ambiguous the candidates are listed and a VA must be used instead.
Function extent = distance to the next PDB symbol in the same section, capped by --len.

Defaults point at the May-2004 Final beta (default.xbe, swsyms.tsv, delta 0x10920,
oddbeta/diagnostics.txt).
"""
import argparse
import bisect
import re
import struct
import sys

import capstone

DEF_XBE = r"C:\Users\<you>\SWBeta\Game\default.xbe"
DEF_SYMS = r"C:\Users\<you>\AppData\Local\Temp\swsyms.tsv"
DEF_DIAG = r"C:\Users\<you>\SWBeta\oddbeta\diagnostics.txt"
DEF_DELTA = 0x10920

MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
MD.detail = True


# ---------------------------------------------------------------- XBE image
class Xbe:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        d = self.d
        if d[:4] != b"XBEH":
            raise SystemExit("not an XBE: %s" % path)
        self.base = struct.unpack_from("<I", d, 0x104)[0]
        n = struct.unpack_from("<I", d, 0x11C)[0]
        hdr = struct.unpack_from("<I", d, 0x120)[0] - self.base
        self.secs = []
        for i in range(n):
            o = hdr + i * 0x38
            flags, va, vsz, raw, rsz = struct.unpack_from("<IIIII", d, o)
            na = struct.unpack_from("<I", d, o + 0x14)[0] - self.base
            nm = d[na:d.find(b"\0", na)].decode("latin1")
            self.secs.append(dict(name=nm, va=va, vsz=vsz, raw=raw, rsz=rsz,
                                  isexec=bool(flags & 4)))

    def sec_of(self, va):
        for s in self.secs:
            if s["va"] <= va < s["va"] + s["vsz"]:
                return s
        return None

    def read(self, va, n):
        s = self.sec_of(va)
        if s is None:
            return b""
        off = va - s["va"]
        n = max(0, min(n, s["rsz"] - off))
        return self.d[s["raw"] + off: s["raw"] + off + n]

    def sec_by_name(self, name):
        for s in self.secs:
            if s["name"].lower() == name.lower():
                return s
        return None


# ---------------------------------------------------------------- symbols
class Syms:
    def __init__(self, path, delta):
        self.byva = {}
        self.byname = {}
        for line in open(path, encoding="utf-8", errors="replace"):
            p = line.rstrip("\n").split("\t")
            if len(p) < 2:
                continue
            try:
                rva = int(p[0], 16)
            except ValueError:
                continue
            name = p[-1] if len(p) == 2 else p[4]
            va = rva + delta
            self.byva.setdefault(va, []).append(name)
            self.byname.setdefault(name, []).append(va)
        self.starts = sorted(self.byva)

    def name(self, va, limit=0x4000):
        i = bisect.bisect_right(self.starts, va) - 1
        if i < 0:
            return "", None
        start = self.starts[i]
        if va - start > limit:
            return "", None
        return self.byva[start][0], va - start

    def fmt(self, va, limit=0x4000):
        n, off = self.name(va, limit)
        if not n:
            return "?"
        return n if off == 0 else "%s+0x%x" % (n, off)

    def extent(self, va, xbe, cap):
        i = bisect.bisect_right(self.starts, va)
        s = xbe.sec_of(va)
        end = va + cap
        if i < len(self.starts):
            end = min(end, self.starts[i])
        if s is not None:
            end = min(end, s["va"] + s["rsz"])
        return end - va

    def resolve(self, tok):
        """name or hex VA -> [va]"""
        if re.fullmatch(r"(0x)?[0-9a-fA-F]+", tok) and tok not in self.byname:
            return [int(tok, 16)]
        return sorted(set(self.byname.get(tok, [])))


# ---------------------------------------------------------------- patch status
class Diag:
    """Cxbx patch-binding status from diagnostics.txt."""
    P_PATCHED = re.compile(r"^HLE: (\S+) Patched")
    P_UNPATCHED = re.compile(
        r"^HLE: (\S+): No patch registered \(symbol found at 0x([0-9A-Fa-f]+), running unpatched\)")

    def __init__(self, path):
        self.patched = set()
        self.unpatched = {}   # cxbx name -> va
        if not path:
            return
        try:
            f = open(path, encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in f:
            m = self.P_PATCHED.match(line)
            if m:
                self.patched.add(m.group(1))
                continue
            m = self.P_UNPATCHED.match(line)
            if m:
                self.unpatched[m.group(1)] = int(m.group(2), 16)
        self.unpatched_by_va = {v: k for k, v in self.unpatched.items()}

    @staticmethod
    def cxbx_name(pdb):
        n = pdb
        if n.startswith("DirectSound::"):
            n = n[len("DirectSound::"):]
        return n.replace("::", "_")

    def _match(self, names, base):
        for k in names:
            if k == base or re.fullmatch(re.escape(base) + r"(_\d+|__r\d+|_\d+_\d+)?", k):
                return k
        return None

    def status(self, va, pdbname):
        """-> (tag, cxbxname)"""
        if va in self.unpatched_by_va:
            return "UNPATCHED-NATIVE", self.unpatched_by_va[va]
        if not pdbname:
            return "", ""
        base = self.cxbx_name(pdbname)
        k = self._match(self.patched, base)
        if k:
            return "PATCHED", k
        k = self._match(self.unpatched, base)
        if k:
            return "UNPATCHED-NATIVE", k
        return "NATIVE(undetected)", ""


# ---------------------------------------------------------------- helpers
def aligned(blob, secva, hit_i, backs=(16, 32, 48, 64, 96, 160, 256, 384)):
    for back in backs:
        start = max(0, hit_i - back)
        for ins in MD.disasm(blob[start:hit_i + 16], secva + start):
            i = ins.address - secva
            if i == hit_i:
                return True
            if i > hit_i:
                break
    return False


def target_note(ctx, tva):
    xbe, syms, diag = ctx
    n, off = syms.name(tva)
    sec = xbe.sec_of(tva)
    secn = sec["name"] if sec else "?"
    tag, cx = diag.status(tva, n if off == 0 else "")
    lbl = syms.fmt(tva)
    if off == 0 and tag:
        return "%s [%s%s] sec=%s" % (lbl, tag, (" as " + cx) if cx and cx != Diag.cxbx_name(n) else "", secn)
    return "%s sec=%s" % (lbl, secn)


def annotate(ctx, ins):
    xbe, syms, diag = ctx
    notes = []
    for op in ins.operands:
        if op.type == capstone.x86.X86_OP_IMM:
            v = op.imm & 0xFFFFFFFF
            if ins.mnemonic in ("call", "jmp") or ins.mnemonic.startswith("j"):
                notes.append(target_note(ctx, v))
            elif xbe.sec_of(v) is not None and v >= 0x10000:
                n, off = syms.name(v, 0x100)
                if n:
                    notes.append("-> " + syms.fmt(v, 0x100))
        elif op.type == capstone.x86.X86_OP_MEM:
            disp = op.mem.disp & 0xFFFFFFFF
            if op.mem.base == 0 and op.mem.index == 0 and xbe.sec_of(disp) is not None:
                n, off = syms.name(disp, 0x100)
                if n:
                    notes.append("[%s]" % syms.fmt(disp, 0x100))
    return "  ; " + " | ".join(notes) if notes else ""


def disasm_range(ctx, va, length, only_calls=False):
    xbe, syms, diag = ctx
    blob = xbe.read(va, length)
    for ins in MD.disasm(blob, va):
        is_call = ins.mnemonic in ("call", "jmp") or ins.mnemonic.startswith("j")
        if only_calls and not is_call:
            continue
        print("%08x  %-16s %-6s %s%s" % (ins.address, ins.bytes.hex(), ins.mnemonic,
                                          ins.op_str, annotate(ctx, ins)))


def resolve_one(syms, tok):
    vas = syms.resolve(tok)
    if not vas:
        raise SystemExit("no symbol named %r" % tok)
    if len(vas) > 1:
        print("ambiguous %r: %s" % (tok, ", ".join("0x%08x" % v for v in vas)))
        raise SystemExit(2)
    return vas[0]


# ---------------------------------------------------------------- commands
def cmd_disasm(ctx, args, only_calls):
    xbe, syms, diag = ctx
    va = resolve_one(syms, args.target)
    length = args.len or syms.extent(va, xbe, 0x3000)
    n, off = syms.name(va)
    tag, cx = diag.status(va, n if off == 0 else "")
    print("; %s @ 0x%08x len=0x%x sec=%s status=%s %s" % (
        syms.fmt(va), va, length, (xbe.sec_of(va) or {}).get("name"), tag or "-", cx))
    disasm_range(ctx, va, length, only_calls)


def cmd_callers(ctx, args):
    xbe, syms, diag = ctx
    targets = {}
    for t in args.target:
        for va in syms.resolve(t):
            targets[va] = syms.fmt(va)
    if not targets:
        raise SystemExit("no targets")
    for sec in xbe.secs:
        if not sec["rsz"] or not sec["isexec"]:
            continue
        blob = xbe.d[sec["raw"]: sec["raw"] + sec["rsz"]]
        for i in range(len(blob) - 5):
            op = blob[i]
            if op in (0xE8, 0xE9):
                ilen, rel = 5, struct.unpack_from("<i", blob, i + 1)[0]
            elif op == 0x0F and 0x80 <= blob[i + 1] <= 0x8F:
                ilen, rel = 6, struct.unpack_from("<i", blob, i + 2)[0]
            else:
                continue
            va = sec["va"] + i
            tgt = (va + ilen + rel) & 0xFFFFFFFF
            if tgt not in targets:
                continue
            kind = {0xE8: "call", 0xE9: "jmp"}.get(op, "jcc")
            print("  %-4s 0x%08x -> 0x%08x %-48s sec=%-8s in %-56s %s" % (
                kind, va, tgt, targets[tgt], sec["name"], syms.fmt(va),
                "ALIGNED" if aligned(blob, sec["va"], i) else "*UNVERIFIED*"))


def cmd_seccalls(ctx, args):
    xbe, syms, diag = ctx
    sec = xbe.sec_by_name(args.section)
    if sec is None:
        raise SystemExit("no section %r" % args.section)
    pat = re.compile(args.to) if args.to else None
    lo, hi = sec["va"], sec["va"] + sec["rsz"]
    starts = [s for s in syms.starts if lo <= s < hi]
    # sweep per function so the linear decode stays aligned
    for k, fva in enumerate(starts):
        fend = starts[k + 1] if k + 1 < len(starts) else hi
        blob = xbe.read(fva, fend - fva)
        for ins in MD.disasm(blob, fva):
            if not (ins.mnemonic in ("call", "jmp") or ins.mnemonic.startswith("j")):
                continue
            if not ins.operands or ins.operands[0].type != capstone.x86.X86_OP_IMM:
                continue
            tva = ins.operands[0].imm & 0xFFFFFFFF
            if lo <= tva < hi and not args.internal:
                continue  # stays inside the section
            n, off = syms.name(tva)
            label = syms.fmt(tva)
            if pat and not pat.search(label):
                continue
            tsec = xbe.sec_of(tva)
            tag, cx = diag.status(tva, n if off == 0 else "")
            if args.status and (not tag or not re.search(args.status, tag)):
                continue
            print("  %-4s 0x%08x in %-52s -> 0x%08x %-52s [%s] sec=%s" % (
                ins.mnemonic, ins.address, syms.fmt(ins.address), tva, label,
                tag or "-", tsec["name"] if tsec else "?"))


def cmd_ptr(ctx, args):
    xbe, syms, diag = ctx
    va = resolve_one(syms, args.target)
    pat = struct.pack("<I", va)
    for sec in xbe.secs:
        if not sec["rsz"]:
            continue
        blob = xbe.d[sec["raw"]: sec["raw"] + sec["rsz"]]
        i = blob.find(pat)
        while i != -1:
            here = sec["va"] + i
            print("  dword 0x%08x @ 0x%08x sec=%-8s in %s  ctx=%s" % (
                va, here, sec["name"], syms.fmt(here), blob[max(0, i - 8): i + 8].hex()))
            i = blob.find(pat, i + 1)


def cmd_status(ctx, args):
    xbe, syms, diag = ctx
    pat = re.compile(args.regex) if args.regex else None
    for va in syms.starts:
        for n in syms.byva[va]:
            if pat and not pat.search(n):
                continue
            sec = xbe.sec_of(va)
            tag, cx = diag.status(va, n)
            print("0x%08x  %-8s %-20s %-48s %s" % (va, sec["name"] if sec else "?", tag, cx, n))


def cmd_name(ctx, args):
    xbe, syms, diag = ctx
    for t in args.target:
        va = int(t, 16)
        n, off = syms.name(va)
        tag, cx = diag.status(va, n if off == 0 else "")
        print("0x%08x  %s  %s %s" % (va, syms.fmt(va), tag, cx))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xbe", default=DEF_XBE)
    ap.add_argument("--syms", default=DEF_SYMS)
    ap.add_argument("--delta", default="0x%x" % DEF_DELTA)
    ap.add_argument("--diag", default=DEF_DIAG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("disasm"); p.add_argument("target"); p.add_argument("--len", type=lambda s: int(s, 0), default=0)
    p = sub.add_parser("callees"); p.add_argument("target"); p.add_argument("--len", type=lambda s: int(s, 0), default=0)
    p = sub.add_parser("callers"); p.add_argument("target", nargs="+")
    p = sub.add_parser("seccalls"); p.add_argument("section"); p.add_argument("--to", default=None)
    p.add_argument("--status", default=None, help="regex on status tag, e.g. UNPATCHED|NATIVE")
    p.add_argument("--internal", action="store_true", help="also list calls that stay inside the section")
    p = sub.add_parser("ptr"); p.add_argument("target")
    p = sub.add_parser("status"); p.add_argument("regex", nargs="?", default=None)
    p = sub.add_parser("name"); p.add_argument("target", nargs="+")
    args = ap.parse_args()

    delta = int(args.delta, 16)
    ctx = (Xbe(args.xbe), Syms(args.syms, delta), Diag(args.diag))
    if args.cmd == "disasm":
        cmd_disasm(ctx, args, False)
    elif args.cmd == "callees":
        cmd_disasm(ctx, args, True)
    elif args.cmd == "callers":
        cmd_callers(ctx, args)
    elif args.cmd == "seccalls":
        cmd_seccalls(ctx, args)
    elif args.cmd == "ptr":
        cmd_ptr(ctx, args)
    elif args.cmd == "status":
        cmd_status(ctx, args)
    elif args.cmd == "name":
        cmd_name(ctx, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
