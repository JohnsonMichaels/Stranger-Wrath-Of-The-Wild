#!/usr/bin/env python3
"""Turn a DbgHelp symbol dump (see pdb_dump.ps1) into a Cxbx-Reloaded SymbolCache INI.

The PDB records RVAs relative to the host PE's image base (0x400000).  The XBE
converter re-lays the same sections out at a different offset, but the shift is
uniform across every section, so:

    XBE_VA = PDB_RVA + delta      where delta = XBE_section_va - PE_section_rva

`delta` is computed from the section tables and asserted to be identical for
every section that exists in both files.

Sub-commands:
  verify  <xbe> <exe> <tsv> [ini]   compute delta, cross-check against an INI
  emit    <xbe> <exe> <tsv> <template-ini> <out-ini> [--names <file>]
"""
import os
import re
import struct
import sys
from collections import defaultdict


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def read_xbe_sections(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:4] == b"XBEH"
    base = u32(data, 0x104)
    nsec = u32(data, 0x11C)
    sha = u32(data, 0x120)
    out = []
    for i in range(nsec):
        o = (sha - base) + i * 0x38
        flags = u32(data, o + 0x00)
        va = u32(data, o + 0x04)
        vsz = u32(data, o + 0x08)
        naddr = u32(data, o + 0x14)
        no = naddr - base
        nm = data[no:data.index(b"\x00", no)].decode("ascii", "replace")
        out.append({"name": nm, "va": va, "vsize": vsz, "flags": flags,
                    "exec": bool(flags & 4)})
    return base, out, data


def read_pe_sections(path):
    with open(path, "rb") as f:
        data = f.read()
    pe = u32(data, 0x3C)
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    sh = pe + 24 + opt_size
    image_base = u32(data, pe + 24 + 28)
    out = []
    for i in range(nsec):
        o = sh + i * 40
        nm = data[o:o + 8].rstrip(b"\x00").decode("ascii", "replace")
        vsz = u32(data, o + 8)
        va = u32(data, o + 12)
        out.append({"name": nm, "rva": va, "vsize": vsz})
    return image_base, out


def compute_delta(xbe_path, exe_path, quiet=False):
    """Match XBE sections to PE sections positionally by name and prove the
    virtual-address shift is a single constant."""
    base, xsecs, _ = read_xbe_sections(xbe_path)
    imgbase, psecs = read_pe_sections(exe_path)

    # Names repeat (BINKYUY2 x4), so consume PE sections in order.
    pe_by_name = defaultdict(list)
    for s in psecs:
        pe_by_name[s["name"]].append(s)
    idx = defaultdict(int)

    deltas = defaultdict(list)
    for xs in xsecs:
        lst = pe_by_name.get(xs["name"])
        if not lst:
            continue
        i = idx[xs["name"]]
        if i >= len(lst):
            continue
        idx[xs["name"]] += 1
        ps = lst[i]
        deltas[xs["va"] - ps["rva"]].append(xs["name"])

    if not quiet:
        for d, names in sorted(deltas.items(), key=lambda kv: -len(kv[1])):
            print(f"  delta 0x{d:x}  ({len(names)} sections): {' '.join(names[:8])}"
                  + (" ..." if len(names) > 8 else ""))
    assert len(deltas) == 1, f"non-uniform section shift: {sorted(deltas)}"
    delta = next(iter(deltas))
    return base, delta, xsecs, imgbase


def load_tsv(path):
    """rva, size, tag, flags, name"""
    syms = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            rva = int(parts[0], 16)
            size = int(parts[1])
            tag = int(parts[2])
            name = "\t".join(parts[4:])
            syms.append((rva, size, tag, name))
    return syms


def parse_ini_symbols(path):
    out = {}
    insec = False
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("["):
                insec = (s == "[Symbols]")
                continue
            if insec and "=" in s:
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip()
                if v.startswith("0x"):
                    out[k] = int(v, 16)
    return out


def undecorate(name):
    """_Foo@8 / @Foo@8 / _Foo -> Foo.  Leaves C++ mangled (?...) names alone."""
    if name.startswith("?"):
        return None
    n = name
    if n[:1] in ("_", "@"):
        n = n[1:]
    m = re.match(r"^(.*)@\d+$", n)
    if m:
        n = m.group(1)
    return n


SYMTAG_FUNCTION = 5
SYMTAG_PUBLIC = 10


def cmd_verify(argv):
    xbe, exe, tsv = argv[0], argv[1], argv[2]
    ini = argv[3] if len(argv) > 3 else None
    print(f"XBE {xbe}\nEXE {exe}")
    base, delta, xsecs, imgbase = compute_delta(xbe, exe)
    print(f"  XBE base 0x{base:08x}  PE image base 0x{imgbase:08x}")
    print(f"  => XBE_VA = PDB_RVA + 0x{delta:x}")

    syms = load_tsv(tsv)
    print(f"  {len(syms)} symbols in {tsv}")

    if not ini:
        return
    truth = parse_ini_symbols(ini)
    print(f"  {len(truth)} ground-truth symbols in {os.path.basename(ini)}")

    # build name -> set of computed addresses
    byname = defaultdict(set)
    for rva, size, tag, name in syms:
        u = undecorate(name)
        if u:
            byname[u].add(rva + delta)

    hit = miss = absent = 0
    misses = []
    for k, v in sorted(truth.items()):
        cand = byname.get(k)
        if not cand:
            absent += 1
            continue
        if v in cand:
            hit += 1
        else:
            miss += 1
            misses.append((k, v, sorted(cand)))
    print(f"  EXACT MATCH  : {hit}")
    print(f"  MISMATCH     : {miss}")
    print(f"  not in PDB   : {absent}")
    for k, v, c in misses[:15]:
        print(f"    ! {k}: cxbx=0x{v:x} pdb={[hex(x) for x in c]}")


def section_of(addr, xsecs):
    for s in xsecs:
        if s["va"] <= addr < s["va"] + s["vsize"]:
            return s
    return None


def cmd_emit(argv):
    xbe, exe, tsv, template, outp = argv[:5]
    names_file = None
    libs_only = True
    if "--names" in argv:
        names_file = argv[argv.index("--names") + 1]
    if "--all-sections" in argv:
        libs_only = False

    base, delta, xsecs, _ = compute_delta(xbe, exe, quiet=True)
    syms = load_tsv(tsv)

    # Sections that hold statically-linked Xbox libraries.  Cxbx only ever
    # patches code that lives in these; a same-named symbol inside .text is an
    # inline expansion compiled into the game and must not be reported.
    LIB_SECTIONS = {"D3D", "D3DX", "DSOUND", "XACTENG", "XGRPH", "XPP",
                    "XONLINE", "XNET", "WMADEC", "XGRAPHC", "XGRAPHCD"}

    wanted = None
    if names_file:
        wanted = set()
        with open(names_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    wanted.add(line)

    # name -> list of (addr, size, tag, section)
    cand = defaultdict(list)
    for rva, size, tag, name in syms:
        if tag not in (SYMTAG_FUNCTION, SYMTAG_PUBLIC):
            continue
        u = undecorate(name)
        if not u:
            continue
        if wanted is not None and u not in wanted:
            continue
        addr = rva + delta
        sec = section_of(addr, xsecs)
        if sec is None or not sec["exec"]:
            continue
        if libs_only and sec["name"] not in LIB_SECTIONS:
            continue
        cand[u].append((addr, size, tag, sec["name"]))

    # Pick one address per name.  Prefer a SymTagFunction with a real body;
    # among those prefer the largest (thunks/stubs are tiny), then lowest addr.
    chosen = {}
    ambiguous = []
    for name, lst in cand.items():
        uniq = sorted(set(lst))
        if len({a for a, _, _, _ in uniq}) > 1:
            ambiguous.append((name, uniq))
        best = sorted(uniq, key=lambda t: (0 if t[2] == SYMTAG_FUNCTION else 1,
                                           -t[1], t[0]))[0]
        chosen[name] = best[0]

    # ---- write INI: keep template's [Info]/[Certificate]/[Libs], new [Symbols]
    with open(template, "r", encoding="utf-8", errors="replace") as f:
        tpl = f.read()
    head = tpl.split("[Symbols]")[0].rstrip("\r\n")
    lines = [head, "", "", "[Symbols]"]
    for k in sorted(chosen):
        lines.append(f"{k} = 0x{chosen[k]:x}")
    lines.append("")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    print(f"delta          : 0x{delta:x}")
    print(f"candidates     : {len(cand)} distinct names")
    print(f"ambiguous      : {len(ambiguous)} names had >1 address")
    print(f"written        : {len(chosen)} symbols -> {outp}")
    for name, lst in ambiguous[:12]:
        print(f"  ~ {name}: " + ", ".join(f"0x{a:x}({s}b,{sec})" for a, s, t, sec in lst))


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"verify": cmd_verify, "emit": cmd_emit}[cmd](sys.argv[2:])
