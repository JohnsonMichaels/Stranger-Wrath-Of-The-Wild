"""List an XBE's non-kernel (xbdm.dll) imports: which ordinals it actually needs.

    python tools/xbe_nonkernel_imports.py <xbe> [<xbe> ...]

The XBE header's dwNonKernelImportDirAddr points at an array of
{ThunkAddr, LibNameAddr} pairs terminated by a zero entry. Each thunk table is a
list of 0x80000000|ordinal words terminated by 0. Cxbx rewrites each word in place
with a host function pointer taken from Cxbx_LibXbdmThunkTable[ordinal], so any
ordinal beyond the end of that table yields a garbage pointer the title then calls.
"""
import struct
import sys


def rd(d, base, va, n):
    # header structures live at their virtual addresses inside the raw header
    off = va - base
    return d[off:off + n]


def main():
    for p in sys.argv[1:]:
        d = open(p, 'rb').read()
        base = struct.unpack_from('<I', d, 0x104)[0]
        dir_va = struct.unpack_from('<I', d, 0x15C)[0]
        print("== %s" % p)
        if not dir_va:
            print("   (no non-kernel imports)")
            continue
        off = dir_va - base
        while True:
            thunk_va, name_va = struct.unpack_from('<II', d, off)
            if not thunk_va or not name_va:
                break
            no = name_va - base
            name = d[no:d.find(b'\0\0', no) + 1].decode('utf-16-le', 'replace')
            print("   library %-12s thunk table @ 0x%08x" % (name, thunk_va))
            # the thunk table lives in .rdata, so map through the section table
            n = struct.unpack_from('<I', d, 0x11C)[0]
            hdr = struct.unpack_from('<I', d, 0x120)[0] - base
            toff = None
            for i in range(n):
                o = hdr + i * 0x38
                _, sva, vsz, raw, _ = struct.unpack_from('<IIIII', d, o)
                if sva <= thunk_va < sva + vsz:
                    toff = raw + (thunk_va - sva)
                    break
            if toff is None:
                print("      <thunk table not in any section>")
            else:
                i = 0
                while True:
                    w = struct.unpack_from('<I', d, toff + i * 4)[0]
                    if w == 0:
                        break
                    print("      [%d] 0x%08x -> ordinal %d%s"
                          % (i, w, w & 0x7FFFFFFF,
                             "   <-- BEYOND Cxbx_LibXbdmThunkTable[1+72]"
                             if (w & 0x7FFFFFFF) > 72 else ""))
                    i += 1
            off += 8
        print()


if __name__ == '__main__':
    main()
