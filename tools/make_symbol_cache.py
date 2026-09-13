#!/usr/bin/env python3
"""Build a Cxbx-Reloaded SymbolCache INI from a DbgHelp PDB dump.

  python make_symbol_cache.py <xbe> <exe> <syms.tsv> <known-names.txt>
                              <template.ini> <out.ini> [--verify <truth.ini>]

Address model (proven, see report):
    XBE_VA = PDB_RVA + delta,  delta = XBE_section_va - PE_section_rva
delta is asserted constant across every section present in both files.

Name model: Cxbx-Reloaded's XbSymbolDatabase uses flat C-ish names.  The PDB
holds the same entry points under a few spellings, so each PDB symbol yields a
set of candidate Cxbx names and only those in the known-name list survive:
  D3DDevice_SetTexture                      -> itself
  _XAudioCreatePcmFormat@12                 -> XAudioCreatePcmFormat
  DirectSound::CDirectSoundBuffer::Play     -> CDirectSoundBuffer_Play
                                            -> DSound_CDirectSoundBuffer_Play
  D3D::g_Stream                             -> D3D_g_Stream
  D3D__pDevice                              -> D3D_g_pDevice
"""
import re
import struct
import sys
from collections import defaultdict

from pdb_to_cxbx import (compute_delta, load_tsv, parse_ini_symbols,  # noqa
                         read_xbe_sections, undecorate)

SYMTAG_FUNCTION = 5
SYMTAG_DATA = 7
SYMTAG_PUBLIC = 10

# section name -> extra Cxbx name prefixes used for symbols living there
SECTION_PREFIX = {
    "D3D":     ["D3D"],
    "D3DX":    ["D3DX", "D3D"],
    "DSOUND":  ["DSound", "DirectSound"],
    "XACTENG": ["XACT"],
    "XGRPH":   ["XGraphics", "XG"],
    "XPP":     [],
    "WMADEC":  [],
}
LIB_SECTIONS = set(SECTION_PREFIX)

# name prefix -> section the real implementation is expected to live in.
NAME_HOME = [
    (("D3DX",), ["D3DX", "D3D"]),
    (("D3DDevice_", "D3DResource_", "D3DTexture_", "D3DSurface_", "D3DPalette_",
      "D3DVertexBuffer_", "D3DCubeTexture_", "D3DVolume", "D3D_", "D3D8",
      "Direct3D", "IDirect3D", "D3DBaseTexture_", "D3DIndexBuffer_",
      "D3DPushBuffer_", "D3DFixup_", "Get2DSurfaceDesc", "XMETAL", "CDevice",
      "CMiniport", "CTexture", "PSGP"), ["D3D", "D3DX"]),
    (("DirectSound", "CDirectSound", "IDirectSound", "CMcpx", "DSound",
      "XAudio", "CHRTF", "CFullHRTF", "CLightHRTF", "CStream", "CMemoryManager",
      "CRefCount"), ["DSOUND"]),
    (("XACT", "IXACT", "CEngine", "CSoundBank", "CWaveBank"), ["XACTENG"]),
    (("XGraphics", "XFont", "XG"), ["XGRPH"]),
]


def homes_for(name):
    for prefixes, secs in NAME_HOME:
        if name.startswith(prefixes):
            return secs
    return []


def load_xbe_image(path):
    with open(path, "rb") as f:
        data = f.read()
    base = struct.unpack_from("<I", data, 0x104)[0]
    nsec = struct.unpack_from("<I", data, 0x11C)[0]
    sha = struct.unpack_from("<I", data, 0x120)[0]
    secs = []
    for i in range(nsec):
        o = (sha - base) + i * 0x38
        flags = struct.unpack_from("<I", data, o + 0x00)[0]
        va = struct.unpack_from("<I", data, o + 0x04)[0]
        vsz = struct.unpack_from("<I", data, o + 0x08)[0]
        ra = struct.unpack_from("<I", data, o + 0x0C)[0]
        rsz = struct.unpack_from("<I", data, o + 0x10)[0]
        naddr = struct.unpack_from("<I", data, o + 0x14)[0]
        no = naddr - base
        nm = data[no:data.index(b"\x00", no)].decode("ascii", "replace")
        secs.append({"name": nm, "va": va, "vsize": vsz, "raw": ra,
                     "rawsize": rsz, "flags": flags, "exec": bool(flags & 4)})
    return data, secs


def sec_of(secs, va):
    for s in secs:
        if s["va"] <= va < s["va"] + s["vsize"]:
            return s
    return None


def read_va(data, secs, va, n):
    s = sec_of(secs, va)
    if not s:
        return None, None
    off = va - s["va"]
    if off >= s["rawsize"]:
        return s, None
    return s, data[s["raw"] + off: s["raw"] + off + n]


def resolve_thunk(data, secs, va, depth=0):
    """Follow `jmp rel32` stubs (the linker's incremental-link / COMDAT
    forwarders).  Cxbx's pattern scanner reports the final body, so we must
    too - proven by XAudioCreatePcmFormat / XInitDevices in the Release build."""
    seen = set()
    while depth < 4:
        s, b = read_va(data, secs, va, 5)
        if not b or len(b) < 5 or b[0] != 0xE9 or va in seen:
            return va
        seen.add(va)
        rel = struct.unpack_from("<i", b, 1)[0]
        tgt = (va + 5 + rel) & 0xFFFFFFFF
        if sec_of(secs, tgt) is None:
            return va
        va = tgt
        depth += 1
    return va


def candidate_names(raw, section):
    u = undecorate(raw)
    if not u:
        return ()
    out = {u}
    if "::" in u:
        parts = u.split("::")
        out.add("_".join(parts[-2:]))
        out.add("_".join(parts))
    # D3D__pDevice -> D3D_g_pDevice  (lib globals spelled with a double _)
    m = re.match(r"^([A-Za-z0-9]+)__([A-Za-z_][A-Za-z0-9_]*)$", u)
    if m:
        out.add(f"{m.group(1)}_g_{m.group(2)}")
        out.add(f"{m.group(1)}_{m.group(2)}")
    for p in SECTION_PREFIX.get(section, []):
        for c in list(out):
            out.add(f"{p}_{c}")
    return out


PROLOGUES = (b"\x55\x8b\xec", b"\x55\x89\xe5", b"\x83\xec", b"\x81\xec",
             b"\x53", b"\x56", b"\x57", b"\x51", b"\x52", b"\x50", b"\x8b\xff",
             b"\x6a", b"\x8b\x44\x24", b"\x8b\x4c\x24", b"\x8b\x54\x24",
             b"\xa1", b"\xb8", b"\x33\xc0", b"\xc3", b"\xe9", b"\x8b\x0d",
             b"\x8b\x15", b"\x66\x8b", b"\xf3\x0f", b"\xd9", b"\xdd", b"\x0f",
             b"\x8d", b"\x31\xc0", b"\x85", b"\xff\x74", b"\xff\x15", b"\x68",
             b"\x8a", b"\x89", b"\xc7", b"\x83\x7c\x24", b"\x83\x3d", b"\x8b\x35")


def main():
    xbe, exe, tsv, names_file, template, outp = sys.argv[1:7]
    truth_ini = None
    if "--verify" in sys.argv:
        truth_ini = sys.argv[sys.argv.index("--verify") + 1]

    base, delta, _xs, imgbase = compute_delta(xbe, exe, quiet=True)
    data, secs = load_xbe_image(xbe)
    syms = load_tsv(tsv)
    known = set()
    with open(names_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                known.add(line)

    print(f"XBE base 0x{base:08x}   PE image base 0x{imgbase:08x}")
    print(f"XBE_VA = PDB_RVA + 0x{delta:x}")
    print(f"{len(syms)} PDB symbols, {len(known)} names known to Cxbx")

    # ---- pass 1: raw address -> Cxbx-known names, no thunk following
    raw_hits = []            # (addr, size, tag, section, [names])
    named_at = defaultdict(set)
    for rva, size, tag, raw in syms:
        if tag not in (SYMTAG_FUNCTION, SYMTAG_DATA, SYMTAG_PUBLIC):
            continue
        addr = rva + delta
        s = sec_of(secs, addr)
        if s is None:
            continue
        names = [n for n in candidate_names(raw, s["name"]) if n in known]
        if not names:
            continue
        raw_hits.append((addr, size, tag, s, names))
        named_at[addr].update(names)

    # ---- pass 2: a 5-byte `jmp rel32` stub is only worth following when the
    # target does not already carry a Cxbx-known name of its own.  DirectSound
    # exposes its public API as a contiguous table of such stubs and Cxbx
    # reports the stub (IDirectSoundStream_SetPitch), while a fold-thunk whose
    # body is anonymous to Cxbx must be followed (XAudioCreatePcmFormat).
    cand = defaultdict(list)
    thunks = 0
    for addr, size, tag, s, names in raw_hits:
        final = addr
        if tag != SYMTAG_DATA and s["exec"] and size <= 5:
            tgt = resolve_thunk(data, secs, addr)
            if tgt != addr and not (named_at[tgt] - set(names)):
                final = tgt
                thunks += 1
                s = sec_of(secs, final) or s
        for n in names:
            cand[n].append((final, size, tag, s["name"]))

    chosen = {}
    ambiguous = []
    for name, lst in cand.items():
        homes = homes_for(name)

        def rank(t):
            addr, size, tag, sec = t
            if homes:
                sr = homes.index(sec) if sec in homes else (
                    len(homes) if sec in LIB_SECTIONS else len(homes) + 1)
            else:
                sr = 0 if sec in LIB_SECTIONS else 1
            tr = 0 if tag == SYMTAG_FUNCTION else (1 if tag == SYMTAG_DATA else 2)
            return (sr, tr, -size, addr)

        uniq = sorted(set(lst))
        addrs = {a for a, _, _, _ in uniq}
        best = sorted(uniq, key=rank)[0]
        chosen[name] = best
        if len(addrs) > 1:
            ambiguous.append((name, best, uniq))

    # ---- sanity: does each address look like code / live in a sane section?
    bad_pro, in_text, by_sec = [], 0, defaultdict(int)
    for name, (addr, size, tag, sec) in sorted(chosen.items()):
        by_sec[sec] += 1
        if sec not in LIB_SECTIONS:
            in_text += 1
        if tag != SYMTAG_DATA:
            s, b = read_va(data, secs, addr, 4)
            if b and not b.startswith(PROLOGUES):
                bad_pro.append((name, addr, b.hex(" ")))

    with open(template, "r", encoding="utf-8", errors="replace") as f:
        tpl = f.read()
    head = tpl.split("[Symbols]")[0].rstrip("\r\n")

    out_syms = {k: v[0] for k, v in chosen.items()}
    kept = 0
    if "--merge" in sys.argv:
        # keep whatever Cxbx's own scanner already found and we could not
        # reproduce, so the cache is a superset of what worked before
        for k, v in parse_ini_symbols(template).items():
            if k not in out_syms:
                out_syms[k] = v
                kept += 1
        print(f"merged from template: {kept} pre-existing symbols kept")

    lines = [head, "", "", "[Symbols]"]
    for k in sorted(out_syms):
        lines.append(f"{k} = 0x{out_syms[k]:x}")
    lines.append("")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    print(f"thunks followed  : {thunks}")
    print(f"symbols emitted  : {len(chosen)} -> {outp}")
    print(f"  per section    : " + ", ".join(f"{k}={v}" for k, v in
                                             sorted(by_sec.items(), key=lambda kv: -kv[1])))
    print(f"  outside libs   : {in_text}")
    print(f"  D3DDevice_*    : {len([k for k in chosen if k.startswith('D3DDevice_')])}")
    print(f"  odd prologues  : {len(bad_pro)}")
    for n, a, b in bad_pro[:10]:
        print(f"      ? {n} @0x{a:x}: {b}")
    print(f"ambiguous names  : {len(ambiguous)}")
    for n, best, lst in ambiguous[:8]:
        print(f"      ~ {n} -> 0x{best[0]:x}({best[3]}) of " +
              ", ".join(f"0x{a:x}({sec},{sz}b)" for a, sz, t, sec in lst))

    if truth_ini:
        truth = parse_ini_symbols(truth_ini)
        hit = miss = absent = 0
        bad = []
        for k, v in sorted(truth.items()):
            if k not in chosen:
                absent += 1
            elif chosen[k][0] == v:
                hit += 1
            else:
                miss += 1
                bad.append((k, v, chosen[k]))
        print(f"VERIFY vs {truth_ini}")
        print(f"  exact    : {hit}")
        print(f"  mismatch : {miss}")
        print(f"  absent   : {absent}")
        for k, v, c in bad[:25]:
            print(f"    ! {k}: cxbx=0x{v:x} mine=0x{c[0]:x} ({c[3]}, {c[1]}b)")
        gone = [k for k in sorted(truth) if k not in chosen]
        print("  absent names: " + ", ".join(gone[:40]))


if __name__ == "__main__":
    main()
