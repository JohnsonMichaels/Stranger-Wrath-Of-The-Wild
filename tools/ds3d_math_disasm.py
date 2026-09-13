#!/usr/bin/env python3
"""Whole-function disassembly with constant resolution for the DSOUND 3D math audit.

    python tools/ds3d_math_disasm.py <xbe> <syms.tsv> <delta_hex> <name-or-va> [...]
    python tools/ds3d_math_disasm.py <xbe> <syms.tsv> <delta_hex> --range <va_hex> <len>
    python tools/ds3d_math_disasm.py <xbe> <syms.tsv> <delta_hex> --list <regex>

Reads the 2-column `rva<TAB>name` TSV (tools/pdb_syms.ps1).  Function extents come
from the next symbol in address order.  Every absolute memory operand is annotated
with the bytes found at that VA decoded as float32 / float64 / int32, so the
floating-point constants of the 3D calculator are read off the listing instead of
guessed; call/jmp targets and absolute immediates that hit a symbol are named.
(Sibling of ds3d_disasm.py, which is the call-path / patch-status tool.)
"""
import re
import struct
import sys

import capstone

from xbe_disasm import sections, va_to_off


def load_syms2(path, delta):
    syms = []
    for line in open(path, encoding='utf-8', errors='replace'):
        p = line.rstrip('\n').split('\t')
        if len(p) < 2:
            continue
        try:
            syms.append((int(p[0], 16) + delta, p[-1]))
        except ValueError:
            pass
    syms.sort()
    out = []
    for va, name in syms:
        if out and out[-1][0] == va:
            out[-1] = (va, out[-1][1] + ' / ' + name)
        else:
            out.append((va, name))
    return out


def sym_lookup(syms, va):
    lo, hi, best = 0, len(syms) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if syms[mid][0] <= va:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def name_at(syms, va):
    i = sym_lookup(syms, va)
    if i < 0:
        return ''
    off = va - syms[i][0]
    if off > 0x4000:
        return ''
    return syms[i][1] + ('+0x%x' % off if off else '')


def extent(syms, name_or_va):
    if re.fullmatch(r'(0x)?[0-9a-fA-F]+', name_or_va) and not any(
            s[1] == name_or_va for s in syms):
        va = int(name_or_va, 16)
        i = sym_lookup(syms, va)
    else:
        idx = [i for i, s in enumerate(syms) if s[1] == name_or_va or
               name_or_va in s[1].split(' / ')]
        if not idx:
            idx = [i for i, s in enumerate(syms) if name_or_va in s[1]]
        if not idx:
            raise SystemExit('no symbol matching %r' % name_or_va)
        i = idx[0]
        va = syms[i][0]
    end = syms[i + 1][0] if i + 1 < len(syms) else va + 0x400
    return va, end, syms[i][1]


def const_note(d, secs, va):
    off, sec = va_to_off(secs, va)
    if off is None or off + 8 > len(d):
        return ''
    f32 = struct.unpack_from('<f', d, off)[0]
    f64 = struct.unpack_from('<d', d, off)[0]
    i32 = struct.unpack_from('<i', d, off)[0]
    return '[%s @%08x: f32=%r f64=%r i32=%d/0x%x]' % (
        sec, va, f32, f64, i32, i32 & 0xffffffff)


def disasm(d, secs, syms, va, end, title=''):
    off, sec = va_to_off(secs, va)
    if off is None:
        print('VA 0x%08x not in any section' % va)
        return
    print('; ===== %s  va %08x..%08x (%d bytes)  section %s  file 0x%x' % (
        title, va, end, end - va, sec, off))
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    for ins in md.disasm(d[off:off + (end - va)], va):
        notes = []
        if ins.mnemonic in ('call', 'jmp') or ins.mnemonic.startswith('j'):
            if ins.op_str.startswith('0x'):
                t = int(ins.op_str, 16)
                n = name_at(syms, t)
                if n:
                    notes.append('-> ' + n)
        for op in ins.operands:
            if op.type == capstone.x86.X86_OP_MEM and op.mem.base == 0 \
                    and op.mem.index == 0 and op.mem.disp:
                n = name_at(syms, op.mem.disp)
                if n:
                    notes.append(n)
                c = const_note(d, secs, op.mem.disp)
                if c:
                    notes.append(c)
            elif op.type == capstone.x86.X86_OP_MEM and op.mem.base == 0 \
                    and op.mem.index != 0 and op.mem.disp:
                n = name_at(syms, op.mem.disp)
                if n:
                    notes.append('table ' + n)
            elif op.type == capstone.x86.X86_OP_IMM and ins.mnemonic in (
                    'push', 'mov', 'lea') and 0x10000 <= op.imm < 0x400000:
                n = name_at(syms, op.imm)
                if n:
                    notes.append('imm=' + n)
        i = sym_lookup(syms, ins.address)
        if i >= 0 and syms[i][0] == ins.address and ins.address != va:
            print('%s:' % syms[i][1])
        print('%08x  %-20s %-7s %-32s %s' % (
            ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str,
            '  ; '.join(notes)))


def main():
    d = open(sys.argv[1], 'rb').read()
    syms = load_syms2(sys.argv[2], int(sys.argv[3], 16))
    secs = sections(d)
    args = sys.argv[4:]
    if args and args[0] == '--list':
        rx = re.compile(args[1])
        for va, name in syms:
            if rx.search(name):
                print('%08x  %s' % (va, name))
        return 0
    if args and args[0] == '--range':
        va = int(args[1], 16)
        ln = int(args[2], 0)
        disasm(d, secs, syms, va, va + ln, 'range')
        return 0
    for a in args:
        va, end, name = extent(syms, a)
        disasm(d, secs, syms, va, end, name)
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
