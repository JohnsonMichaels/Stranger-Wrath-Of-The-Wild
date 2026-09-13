"""Disassemble whole functions out of an XBE by PDB name, with call targets and
absolute data references named.

    python tools/ds3d_funcs.py <xbe> <syms.tsv> <delta_hex> [--out FILE] [--emit5 FILE] NAME|REGEX ...
    python tools/ds3d_funcs.py <xbe> <syms.tsv> <delta_hex> --data <va_hex> <ndwords>

<syms.tsv> is either pdb_syms.ps1 output (rva<TAB>name) or pdb_dump.ps1 output
(rva size tag flags name). A function's extent is taken as the distance to the next
symbol, which is exact for the LTCG DSOUND/XACT libraries (no padding symbols) and
at worst over-long elsewhere - read to the `ret`.

Why this exists: tools/xbe_disasm.py needs a VA and a length, and only names call
targets. Answering "what does this XDK function do" means disassembling a dozen
helpers by name and knowing which global / vtable slot each `call dword ptr [imm]`
goes through, so this names bracketed absolute addresses too (e.g. the
CHRTFSource::m_vtable slots that select Full vs Light HRTF).

--emit5 writes the symbol table back out as the 5-column TSV that xbe_xref.py and
xbe_callees.py expect (they keep only tag==5 rows, and pdb_syms.ps1 emits no tag).
--data dumps N dwords at a VA as hex / float / symbol, for vtables and default tables.
"""
import re
import struct
import sys

import capstone

sys.path.insert(0, __file__.rsplit('\\', 1)[0].rsplit('/', 1)[0])
from xbe_disasm import sections, va_to_off  # noqa: E402


def load_syms(path, delta):
    rows = []
    for line in open(path, encoding='utf-8', errors='replace'):
        p = line.rstrip('\n').split('\t')
        if len(p) < 2:
            continue
        try:
            rva = int(p[0], 16)
        except ValueError:
            continue
        name = p[4] if len(p) >= 5 else p[1]
        rows.append((rva + delta, name))
    rows.sort()
    funcs = []
    for i, (va, n) in enumerate(rows):
        nxt = va
        for vv, _ in rows[i + 1:]:
            if vv > va:
                nxt = vv
                break
        funcs.append((va, nxt - va, n))
    return funcs


def name_of(funcs, va):
    lo, hi, best = 0, len(funcs) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if funcs[mid][0] <= va:
            best = funcs[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None or (best[1] and va - best[0] >= best[1]):
        return ''
    off = va - best[0]
    return '%s+0x%x' % (best[2], off) if off else best[2]


BRACKET = re.compile(r'\[0x([0-9a-f]+)\]')


def disasm_func(d, secs, funcs, va, size, name, out):
    off, sec = va_to_off(secs, va)
    out.write('==== %s  guest=0x%x  size=0x%x  section=%s\n' % (name, va, size, sec))
    if off is None:
        out.write('   (not file-backed)\n\n')
        return
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    for ins in md.disasm(d[off:off + size], va):
        extra = ''
        if ins.mnemonic in ('call', 'jmp') and ins.op_str.startswith('0x'):
            extra = name_of(funcs, int(ins.op_str, 16))
        else:
            m = BRACKET.search(ins.op_str)
            if m:
                extra = name_of(funcs, int(m.group(1), 16))
        out.write('%08x  %-20s %s %s%s\n' % (ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str,
                                            ('  ; ' + extra) if extra else ''))
    out.write('\n')


def main():
    args = sys.argv[1:]
    if len(args) < 4:
        print(__doc__)
        return 1
    xbe, symtsv, delta = args[0], args[1], int(args[2], 16)
    rest = args[3:]
    out = sys.stdout
    emit5 = None
    data = None
    pats = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == '--out':
            out = open(rest[i + 1], 'w', encoding='utf-8')
            i += 2
        elif a == '--emit5':
            emit5 = rest[i + 1]
            i += 2
        elif a == '--data':
            data = (int(rest[i + 1], 16), int(rest[i + 2], 0))
            i += 3
        else:
            pats.append(a)
            i += 1
    d = open(xbe, 'rb').read()
    secs = sections(d)
    funcs = load_syms(symtsv, delta)
    if emit5:
        with open(emit5, 'w', encoding='utf-8') as f:
            for va, size, n in funcs:
                f.write('%x\t%d\t5\t0\t%s\n' % (va - delta, size, n))
        print('wrote %s (%d rows, all tag=5)' % (emit5, len(funcs)))
    if data:
        va, n = data
        off, sec = va_to_off(secs, va)
        for k in range(n):
            v = struct.unpack_from('<I', d, off + 4 * k)[0]
            fl = struct.unpack_from('<f', d, off + 4 * k)[0]
            out.write('%08x  %08x  %-14g  %s\n' % (va + 4 * k, v, fl, name_of(funcs, v)))
    for p in pats:
        hits = [f for f in funcs if f[2] == p]
        if not hits:
            rx = re.compile(p)
            hits = [f for f in funcs if rx.search(f[2])]
        if not hits:
            out.write('==== %s: no symbol\n\n' % p)
        for va, size, n in hits:
            disasm_func(d, secs, funcs, va, size, n, out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
