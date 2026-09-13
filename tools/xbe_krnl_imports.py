#!/usr/bin/env python3
"""List the xboxkrnl ordinals a title actually imports, plus its library records.

The XBE's kernel thunk table is a NULL-terminated array of DWORDs; each entry
is (0x80000000 | ordinal).  Reading it tells you exactly which kernel exports
the title can possibly call -- the right filter for "is this kernel stub
reachable?".

  python xbe_krnl_imports.py SteefDebug.xbe
"""
import struct
import sys

DEBUG_KT_KEY = 0xEFB1F152
RETAIL_KT_KEY = 0x5B6D40B6


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def main():
    path = sys.argv[1]
    d = open(path, "rb").read()
    base = u32(d, 0x104)
    size_image = u32(d, 0x10C)
    cert_addr = u32(d, 0x118)
    nsec = u32(d, 0x11C)
    sec_hdr = u32(d, 0x120)
    kt_enc = u32(d, 0x158)

    secs = []
    for i in range(nsec):
        o = (sec_hdr - base) + i * 0x38
        va, vsz, ra, rsz = u32(d, o + 4), u32(d, o + 8), u32(d, o + 12), u32(d, o + 16)
        naddr = u32(d, o + 0x14)
        nm = ""
        if naddr:
            p = naddr - base
            nm = d[p:d.find(b"\x00", p)].decode("ascii", "replace")
        secs.append((nm, va, vsz, ra, rsz))

    def off(va):
        for nm, sva, vsz, ra, rsz in secs:
            if sva <= va < sva + vsz and va - sva < rsz:
                return ra + (va - sva)
        return None

    kt = None
    for name, key in (("DEBUG", DEBUG_KT_KEY), ("RETAIL", RETAIL_KT_KEY)):
        v = kt_enc ^ key
        if base <= v < base + size_image:
            kt = v
            print("kernel thunk table @ 0x%08x  (XOR key %s)" % (v, name))
            break
    if kt is None:
        print("could not locate kernel thunk table")
        return

    p = off(kt)
    ords = []
    while True:
        v = u32(d, p)
        if v == 0:
            break
        if v & 0x80000000:
            ords.append(v & 0x7FFFFFFF)
        p += 4
        if len(ords) > 400:
            break
    print("imported kernel ordinals: %d" % len(ords))
    print(",".join(str(o) for o in sorted(ords)))

    # library version records from the certificate
    co = cert_addr - base
    nlib = u32(d, co + 0x9C)
    lva = u32(d, co + 0xA0)
    print("\nlibrary version records: %d" % nlib)
    lo = off(lva)
    for i in range(nlib):
        e = lo + i * 0x10
        nm = d[e:e + 8].decode("ascii", "replace").rstrip("\x00")
        maj, minr, bld, flags = struct.unpack_from("<HHHH", d, e + 8)
        print("  %-10s %d.%d.%d  flags=0x%04x%s"
              % (nm, maj, minr, bld, flags,
                 "  [DEBUG]" if flags & 0x8000 else ""))


if __name__ == "__main__":
    main()
