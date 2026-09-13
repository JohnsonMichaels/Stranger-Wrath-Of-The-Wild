#!/usr/bin/env python3
"""Dump a range of XBE virtual addresses as raw dwords decoded as float32 and int32.

    python tools/ds3d_dump.py <xbe> <va_hex> <ndwords> [<label>]

Companion to ds3d_disasm.py: the 3D calculator's tables (rolloff, centre-channel
blend, HRTF filter pairs) and its default DS3DLISTENER / DS3DBUFFER structs sit
in the DSOUND data area; this prints them with the section-table VA->offset map
so each constant can be cited by address.
"""
import struct
import sys

from xbe_disasm import sections, va_to_off


def main():
    d = open(sys.argv[1], 'rb').read()
    va = int(sys.argv[2], 16)
    n = int(sys.argv[3], 0)
    label = sys.argv[4] if len(sys.argv) > 4 else ''
    secs = sections(d)
    off, sec = va_to_off(secs, va)
    if off is None:
        print('VA 0x%08x not in any section' % va)
        return 1
    print('; ===== %s  va %08x  section %s  file 0x%x  (%d dwords)' % (label, va, sec, off, n))
    for i in range(n):
        o = off + i * 4
        raw = d[o:o + 4]
        f = struct.unpack('<f', raw)[0]
        u = struct.unpack('<I', raw)[0]
        s = struct.unpack('<i', raw)[0]
        print('%08x  %s  u32=0x%08x  i32=%-11d f32=%r' % (va + i * 4, raw.hex(), u, s, f))
    return 0


if __name__ == '__main__':
    sys.exit(main())
