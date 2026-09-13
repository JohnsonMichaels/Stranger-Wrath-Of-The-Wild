#!/usr/bin/env python3
"""Reference finder for an XBE, keyed on raw addresses instead of PDB names.

tools/xbe_xref.py answers "who calls this *named* function", which needs the
target to exist in the title's PDB. The statically linked XDK libraries (D3D,
XGRPH, DSOUND ...) have no PDB entries, so for those you only ever have an
address - from Cxbx's SymbolCache. This tool works from the address.

    python tools/xbe_refs.py <xbe> calls   <va> [<va> ...]
    python tools/xbe_refs.py <xbe> ptr     <va> [<va> ...]
    python tools/xbe_refs.py <xbe> memdisp <disp_hex> [--reads]
    python tools/xbe_refs.py <xbe> sections
    (any subcommand also accepts --syms=<Cxbx SymbolCache .ini> to name sites)

calls    byte-scans every section holding raw bytes for `E8 rel32`, `E9 rel32`
         and `0F 8x rel32` whose computed target is a requested VA. Each hit is
         then verified by linear-sweeping from 96 bytes earlier and checking the
         candidate lands on a real instruction boundary; unverified hits are
         still printed but flagged, so a byte-pattern coincidence is visible
         rather than silently dropped or silently believed.
ptr      scans raw bytes of every section for the literal little-endian dword.
         Catches `mov reg, imm32` / `push imm32` and address-taken entries sat
         in a vtable or function-pointer table over in .data / .rdata.
memdisp  finds every instruction carrying the given disp32 in a memory operand
         and reports whether that operand is written or only read - i.e. "every
         place that stores to object+0x794". Candidates come from scanning for
         the raw displacement bytes, then re-aligning: for each start in
         [-12,-2] decode one instruction and keep the decode whose memory
         displacement matches and whose extent covers the displacement bytes.
"""
import struct
import sys

import capstone


def sections(d):
    base = struct.unpack_from('<I', d, 0x104)[0]
    n = struct.unpack_from('<I', d, 0x11C)[0]
    hdr = struct.unpack_from('<I', d, 0x120)[0] - base
    out = []
    for i in range(n):
        o = hdr + i * 0x38
        flags, va, vsz, raw, rsz = struct.unpack_from('<IIIII', d, o)
        na = struct.unpack_from('<I', d, o + 0x14)[0] - base
        nm = d[na:d.find(b'\0', na)].decode('latin1')
        out.append(dict(name=nm, va=va, vsz=vsz, raw=raw, rsz=rsz, flags=flags,
                        isexec=bool(flags & 4)))
    return out


def load_ini(path):
    """Cxbx SymbolCache .ini -> sorted [(va, name)]."""
    syms, in_s = [], False
    for line in open(path, encoding='utf-8', errors='replace'):
        s = line.strip()
        if s.startswith('['):
            in_s = (s.lower() == '[symbols]')
            continue
        if not in_s or '=' not in s:
            continue
        k, v = s.split('=', 1)
        try:
            syms.append((int(v.strip(), 16), k.strip()))
        except ValueError:
            pass
    syms.sort()
    return syms


def name_of(syms, va, limit=0x1000):
    if not syms:
        return ''
    lo, hi, best = 0, len(syms) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if syms[mid][0] <= va:
            best, lo = syms[mid], mid + 1
        else:
            hi = mid - 1
    if best is None or va - best[0] > limit:
        return ''
    off = va - best[0]
    return '%s+0x%x' % (best[1], off) if off else best[1]


MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
MD.detail = True


def aligned(blob, secva, hit_i, backs=(16, 32, 48, 64, 96, 160, 256, 384)):
    """True if a linear sweep from some earlier point lands exactly on hit_i.

    One fixed lookback is not enough: starting mid-instruction (or inside the
    int3 padding / jump tables between functions) desyncs the sweep and reports
    a real instruction as bogus, so try several and accept any agreement.
    """
    for back in backs:
        start = max(0, hit_i - back)
        for ins in MD.disasm(blob[start:hit_i + 16], secva + start):
            i = ins.address - secva
            if i == hit_i:
                return True
            if i > hit_i:
                break
    return False


def cmd_calls(d, secs, targets, syms):
    for sec in secs:
        if not sec['rsz']:
            continue
        blob = d[sec['raw']:sec['raw'] + sec['rsz']]
        for i in range(len(blob) - 5):
            op = blob[i]
            if op in (0xE8, 0xE9):
                ilen, rel = 5, struct.unpack_from('<i', blob, i + 1)[0]
            elif op == 0x0F and 0x80 <= blob[i + 1] <= 0x8F and i + 6 <= len(blob):
                ilen, rel = 6, struct.unpack_from('<i', blob, i + 2)[0]
            else:
                continue
            va = sec['va'] + i
            if ((va + ilen + rel) & 0xFFFFFFFF) not in targets:
                continue
            kind = {0xE8: 'call', 0xE9: 'jmp'}.get(op, 'jcc')
            print('  %-4s 0x%08x -> 0x%08x  sec=%-8s %-12s %s  %s'
                  % (kind, va, (va + ilen + rel) & 0xFFFFFFFF, sec['name'],
                     blob[i:i + ilen].hex(),
                     'ALIGNED' if aligned(blob, sec['va'], i) else '*UNVERIFIED*',
                     name_of(syms, va)))


def cmd_ptr(d, secs, targets, syms):
    for sec in secs:
        if not sec['rsz']:
            continue
        blob = d[sec['raw']:sec['raw'] + sec['rsz']]
        for t in sorted(targets):
            pat = struct.pack('<I', t)
            i = blob.find(pat)
            while i != -1:
                print('  dword 0x%08x @ 0x%08x  sec=%-8s ctx=%s  %s'
                      % (t, sec['va'] + i, sec['name'],
                         blob[max(0, i - 6):i + 4].hex(),
                         name_of(syms, sec['va'] + i)))
                i = blob.find(pat, i + 1)


def cmd_memdisp(d, secs, disp, syms, show_reads):
    pat = struct.pack('<I', disp)
    for sec in secs:
        if not sec['rsz'] or not sec['isexec']:
            continue
        blob = d[sec['raw']:sec['raw'] + sec['rsz']]
        i, seen = blob.find(pat), set()
        while i != -1:
            for back in range(2, 13):
                s = i - back
                if s < 0:
                    continue
                try:
                    ins = next(MD.disasm(blob[s:s + 16], sec['va'] + s, 1))
                except StopIteration:
                    continue
                if s + ins.size < i + 4:
                    continue
                hits = [o for o in ins.operands
                        if o.type == capstone.x86.X86_OP_MEM and o.mem.disp == disp]
                if not hits or ins.address in seen:
                    break
                seen.add(ins.address)
                wr = any(o.access & capstone.CS_AC_WRITE for o in hits)
                if wr or show_reads:
                    print('  %-5s 0x%08x  %-14s %-5s %-32s sec=%-8s %s%s'
                          % ('WRITE' if wr else 'read', ins.address,
                             ins.bytes.hex(), ins.mnemonic, ins.op_str,
                             sec['name'], name_of(syms, ins.address),
                             '' if aligned(blob, sec['va'], s) else '  *UNVERIFIED*'))
                break
            i = blob.find(pat, i + 1)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    d = open(sys.argv[1], 'rb').read()
    argv = list(sys.argv[2:])
    syms = []
    for a in list(argv):
        if a.startswith('--syms='):
            syms = load_ini(a.split('=', 1)[1])
            argv.remove(a)
    show_reads = '--reads' in argv
    if show_reads:
        argv.remove('--reads')
    secs = sections(d)
    cmd = argv[0]
    if cmd == 'sections':
        for s in secs:
            print('%-10s va=0x%08x vsz=0x%06x raw=0x%06x rsz=0x%06x %s'
                  % (s['name'], s['va'], s['vsz'], s['raw'], s['rsz'],
                     'EXEC' if s['isexec'] else ''))
    elif cmd == 'calls':
        cmd_calls(d, secs, {int(a, 16) for a in argv[1:]}, syms)
    elif cmd == 'ptr':
        cmd_ptr(d, secs, {int(a, 16) for a in argv[1:]}, syms)
    elif cmd == 'memdisp':
        cmd_memdisp(d, secs, int(argv[1], 16), syms, show_reads)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
