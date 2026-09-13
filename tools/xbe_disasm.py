"""Disassemble a range of Xbox virtual addresses straight out of an XBE.

    python tools/xbe_disasm.py <xbe> <va_hex> <length> [<syms.tsv> <delta_hex>]

Cxbx crash dumps and the PDB both speak XBE virtual addresses, but the file on disk
is laid out by raw offset, so this walks the XBE section table to map VA -> file
offset and disassembles from there. Pass the pdb_dump.ps1 TSV and the build's delta
(see tools/xbe_symbolize.py) to have call targets named.
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
        name_addr = struct.unpack_from('<I', d, o + 0x14)[0] - base
        name = b''
        if 0 <= name_addr < len(d):
            end = d.find(b'\0', name_addr)
            name = d[name_addr:end]
        out.append((va, vsz, raw, rsz, name.decode('latin1')))
    return out


def va_to_off(secs, va):
    for sva, vsz, raw, rsz, name in secs:
        if sva <= va < sva + vsz:
            return raw + (va - sva), name
    return None, None


def load_syms(path, delta):
    funcs = []
    for line in open(path, encoding='utf-8', errors='replace'):
        p = line.rstrip('\n').split('\t')
        if len(p) < 5:
            continue
        try:
            funcs.append((int(p[0], 16) + delta, int(p[1]), p[4]))
        except ValueError:
            pass
    funcs.sort()
    return funcs


def name_of(funcs, va):
    if not funcs:
        return ''
    lo, hi, best = 0, len(funcs) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if funcs[mid][0] <= va:
            best = funcs[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return ''
    off = va - best[0]
    if best[1] and off >= best[1]:
        return ''
    return '  ; %s+0x%x' % (best[2], off) if off else '  ; %s' % best[2]


def main():
    d = open(sys.argv[1], 'rb').read()
    va = int(sys.argv[2], 16)
    ln = int(sys.argv[3], 0)
    funcs = []
    if len(sys.argv) > 5:
        funcs = load_syms(sys.argv[4], int(sys.argv[5], 16))
    secs = sections(d)
    off, sec = va_to_off(secs, va)
    if off is None:
        print('VA 0x%08x not in any section' % va)
        return 1
    print('# section %s  file offset 0x%x' % (sec, off))
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    for ins in md.disasm(d[off:off + ln], va):
        extra = ''
        if ins.mnemonic in ('call', 'jmp') and ins.op_str.startswith('0x'):
            extra = name_of(funcs, int(ins.op_str, 16))
        print('%08x  %-22s %s %s%s' % (
            ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str, extra))
    return 0


if __name__ == '__main__':
    sys.exit(main())
