"""Enlarge the beta's small-object allocator arena.

    python tools/patch_smallalloc.py <xbe> [--pages N] [--revert] [--check]

WHY
---
Mongo Valley (region_03) does not load. It is not a crash, a hang, or missing data:
the engine runs out of small-allocator pages while deserialising the level, asserts,
and parks in its halt handler drawing the message to the screen forever. The user
sees a black screen; the engine is actually saying

    d:\\Exoddus\\Code\\Engine\\Core\\SmallAllocator_FixedRestoring.cpp:711
    "Good Lord! Out of Pages in SmallAllocator_FixedRestoring!"

SmallAllocator_FixedRestoring serves every allocation of 256 bytes or less out of a
FIXED 4 MB arena carved into 1024 pages of 4 KB. It never grows. A page returns to
the free list only when every object in it has been freed, so once all 1024 pages
hold at least one live object the next small allocation fails outright. Measured
live from guest memory, Mongo Valley takes the arena from 104 pages in use to
1024/1024 and pins it there.

Gizzard Gulch (region_02) is larger than Mongo Valley on every axis - 191 zonebundles
to 139, 130 MB to 111 MB, 19,822 level objects to 12,826 - and loads fine, so this is
not about level size. It is about how many small objects are live *simultaneously*
during deserialisation, which depends on the shape of the object graph rather than
its size. This beta simply sized the arena too small for region_03.

WHAT THIS CHANGES
-----------------
Four constants, all inside the initialiser at VA 0x00142EBF, which is the only code
that defines the arena:

    push 0x400000            arena byte size
    lea ecx, [eax+0x400000]  arena end pointer
    cmp ecx, 0x3FF           index of the last page (its next pointer stays NULL)
    cmp ecx, 0x400           number of pages to build

They must stay consistent: size == pages * 0x1000, last == pages - 1. The script
enforces that rather than trusting the caller.

Nothing else in the allocator depends on the page count. The size-to-bucket table at
0x2BD968 and the 15 bucket heads at 0x2BDA6C are indexed by allocation size, not by
page, and the free list is built by this same loop.

The XBE's section SHA hashes will no longer match, which Cxbx reports as a warning
and ignores; the fork already runs with IgnoreInvalidXbeSig.
"""
import argparse
import os
import shutil
import struct
import sys

# The initialiser's four constants: (VA of the immediate, current value, description).
# VAs are of the immediate itself, not of the instruction.
SIZE_SITES = [
    (0x00142EC8, "push <size>          arena byte size"),
    (0x00142ED6, "lea ecx,[eax+<size>] arena end pointer"),
]
LAST_SITE = (0x00142EF3, "cmp ecx, <pages-1>   last page index")
COUNT_SITE = (0x00142F12, "cmp ecx, <pages>     page count")

DEFAULT_PAGES = 4096          # 16 MB, four times the stock arena
STOCK_PAGES = 1024            # 4 MB as shipped
PAGE_SIZE = 0x1000


def sections(d):
    base = struct.unpack_from("<I", d, 0x104)[0]
    n = struct.unpack_from("<I", d, 0x11C)[0]
    hdr = struct.unpack_from("<I", d, 0x120)[0] - base
    out = []
    for i in range(n):
        o = hdr + i * 0x38
        _flags, va, vsz, raw, rsz = struct.unpack_from("<IIIII", d, o)
        out.append((va, vsz, raw, rsz))
    return out


def va2off(secs, va):
    for sva, vsz, raw, _rsz in secs:
        if sva <= va < sva + vsz:
            return raw + (va - sva)
    return None


def read_u32(d, secs, va):
    off = va2off(secs, va)
    if off is None or off + 4 > len(d):
        raise SystemExit("VA 0x%08X is not inside any XBE section" % va)
    return off, struct.unpack_from("<I", d, off)[0]


def describe(d, secs):
    """Report the arena the XBE currently declares, and check it is self-consistent."""
    vals = []
    for va, what in SIZE_SITES:
        off, v = read_u32(d, secs, va)
        vals.append((va, off, v, what))
    off, last = read_u32(d, secs, LAST_SITE[0])
    vals.append((LAST_SITE[0], off, last, LAST_SITE[1]))
    off, count = read_u32(d, secs, COUNT_SITE[0])
    vals.append((COUNT_SITE[0], off, count, COUNT_SITE[1]))

    for va, off, v, what in vals:
        print("  va=0x%08X file=0x%06X  0x%-8X  %s" % (va, off, v, what))

    size_a, size_b = vals[0][2], vals[1][2]
    consistent = (size_a == size_b
                  and count * PAGE_SIZE == size_a
                  and last == count - 1)
    print()
    print("  arena: %u pages of %u bytes = %.1f MB  [%s]"
          % (count, PAGE_SIZE, size_a / (1024.0 * 1024.0),
             "consistent" if consistent else "INCONSISTENT"))
    return count, consistent


def patch(path, pages, revert, check_only):
    d = bytearray(open(path, "rb").read())
    if struct.unpack_from("<I", d, 0)[0] != 0x48454258:  # 'XBEH'
        raise SystemExit("%s is not an XBE" % path)
    secs = sections(d)

    print("current arena in %s:" % os.path.basename(path))
    current, consistent = describe(d, secs)
    if check_only:
        return
    if not consistent:
        raise SystemExit(
            "\nRefusing to patch: the four constants do not agree with each other, so\n"
            "this file's layout is not what the disassembly showed. Re-check the\n"
            "initialiser at VA 0x00142EBF before touching it.")

    target = STOCK_PAGES if revert else pages
    if target == current:
        print("\nalready %u pages - nothing to do" % target)
        return
    if target < STOCK_PAGES:
        raise SystemExit("refusing to shrink below the stock %u pages" % STOCK_PAGES)

    size = target * PAGE_SIZE
    backup = path + ".presmallalloc"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
        print("\nbacked up original to %s" % os.path.basename(backup))
    else:
        print("\nbackup already exists: %s" % os.path.basename(backup))

    for va, _what in SIZE_SITES:
        off = va2off(secs, va)
        struct.pack_into("<I", d, off, size)
    struct.pack_into("<I", d, va2off(secs, LAST_SITE[0]), target - 1)
    struct.pack_into("<I", d, va2off(secs, COUNT_SITE[0]), target)

    open(path, "wb").write(bytes(d))
    print("\npatched arena:")
    describe(bytearray(open(path, "rb").read()), secs)
    print("\nThe section SHA hashes no longer match; Cxbx warns and continues.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbe")
    ap.add_argument("--pages", type=int, default=DEFAULT_PAGES,
                    help="page count to build (default %d = %d MB)"
                         % (DEFAULT_PAGES, DEFAULT_PAGES * PAGE_SIZE // (1024 * 1024)))
    ap.add_argument("--revert", action="store_true", help="restore the stock 1024 pages")
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    a = ap.parse_args()
    patch(a.xbe, a.pages, a.revert, a.check)


if __name__ == "__main__":
    main()
