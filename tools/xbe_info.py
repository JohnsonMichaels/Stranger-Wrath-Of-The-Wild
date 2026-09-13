#!/usr/bin/env python3
"""Dump XBE header + section table, and the PE header of the paired .exe.

Usage: python xbe_info.py <file.xbe> [file.exe]
"""
import struct
import sys


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def xbe_info(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:4] == b"XBEH", "not an XBE"
    base = u32(data, 0x104)
    size_headers = u32(data, 0x108)
    size_image = u32(data, 0x10C)
    cert_addr = u32(data, 0x118)
    nsec = u32(data, 0x11C)
    sec_hdr_addr = u32(data, 0x120)
    entry_enc = u32(data, 0x128)
    kthunk_enc = u32(data, 0x158)

    print(f"XBE            : {path}")
    print(f"  base addr    : 0x{base:08x}")
    print(f"  size headers : 0x{size_headers:x}")
    print(f"  size image   : 0x{size_image:x}")
    print(f"  sections     : {nsec}")
    print(f"  sec hdr addr : 0x{sec_hdr_addr:08x}")
    # Entry point / kernel thunk are XOR-encoded; try both debug and retail keys
    for name, key in (("DEBUG", 0x94859D4B), ("RETAIL", 0xA8FC57AB)):
        ep = entry_enc ^ key
        if base <= ep < base + size_image:
            print(f"  entry point  : 0x{ep:08x}  (XOR key {name})")
    for name, key in (("DEBUG", 0xEFB1F152), ("RETAIL", 0x5B6D40B6)):
        kt = kthunk_enc ^ key
        if 0x80000000 <= kt <= 0x80100000 or (base <= kt < base + size_image):
            print(f"  kernel thunk : 0x{kt:08x}  (XOR key {name})")

    # certificate
    co = cert_addr - base
    title_id = u32(data, co + 0x08)
    name_utf16 = data[co + 0x0C:co + 0x0C + 80].decode("utf-16-le", "replace").rstrip("\x00")
    print(f"  TitleID      : 0x{title_id:08x}")
    print(f"  Title name   : {name_utf16!r}")

    print()
    print(f"  {'name':<20} {'virt addr':>10} {'virt size':>10} {'raw addr':>10} {'raw size':>10}  flags")
    secs = []
    for i in range(nsec):
        o = (sec_hdr_addr - base) + i * 0x38
        flags = u32(data, o + 0x00)
        va = u32(data, o + 0x04)
        vsz = u32(data, o + 0x08)
        ra = u32(data, o + 0x0C)
        rsz = u32(data, o + 0x10)
        naddr = u32(data, o + 0x14)
        no = naddr - base
        end = data.index(b"\x00", no)
        nm = data[no:end].decode("ascii", "replace")
        secs.append((nm, va, vsz, ra, rsz, flags))
        exe = "X" if flags & 4 else "-"
        wr = "W" if flags & 1 else "-"
        print(f"  {nm:<20} 0x{va:08x} 0x{vsz:08x} 0x{ra:08x} 0x{rsz:08x}  {wr}{exe} 0x{flags:x}")
    return base, secs


def pe_info(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:2] == b"MZ"
    pe = u32(data, 0x3C)
    assert data[pe:pe + 4] == b"PE\x00\x00", "no PE sig"
    machine = struct.unpack_from("<H", data, pe + 4)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    image_base = u32(data, opt + 28)
    size_image = u32(data, opt + 56)
    print()
    print(f"PE             : {path}")
    print(f"  machine      : 0x{machine:04x}")
    print(f"  opt magic    : 0x{magic:04x}")
    print(f"  image base   : 0x{image_base:08x}")
    print(f"  size of image: 0x{size_image:08x}")
    print(f"  sections     : {nsec}")
    sh = opt + opt_size
    print(f"  {'name':<12} {'virt addr':>10} {'virt size':>10} {'raw ptr':>10} {'raw size':>10}")
    for i in range(nsec):
        o = sh + i * 40
        nm = data[o:o + 8].rstrip(b"\x00").decode("ascii", "replace")
        vsz = u32(data, o + 8)
        va = u32(data, o + 12)
        rsz = u32(data, o + 16)
        ra = u32(data, o + 20)
        print(f"  {nm:<12} 0x{va:08x} 0x{vsz:08x} 0x{ra:08x} 0x{rsz:08x}")
    # debug directory -> PDB path / GUID / age
    dd = opt + (96 if magic == 0x10b else 112)
    dbg_rva = u32(data, dd + 6 * 8)
    dbg_size = u32(data, dd + 6 * 8 + 4)
    if dbg_rva:
        # map rva -> file offset
        def rva2off(rva):
            for i in range(nsec):
                o = sh + i * 40
                vsz = u32(data, o + 8)
                va = u32(data, o + 12)
                ra = u32(data, o + 20)
                if va <= rva < va + max(vsz, u32(data, o + 16)):
                    return ra + (rva - va)
            return None
        off = rva2off(dbg_rva)
        n = dbg_size // 28
        for i in range(n):
            e = off + i * 28
            dtype = u32(data, e + 12)
            dsz = u32(data, e + 16)
            draw = u32(data, e + 24)
            if dtype == 2:  # CODEVIEW
                cv = data[draw:draw + dsz]
                if cv[:4] == b"RSDS":
                    g = cv[4:20]
                    guid = "%08X%04X%04X%s" % (
                        struct.unpack_from("<I", g, 0)[0],
                        struct.unpack_from("<H", g, 4)[0],
                        struct.unpack_from("<H", g, 6)[0],
                        g[8:16].hex().upper())
                    age = u32(cv, 20)
                    pdb = cv[24:].split(b"\x00")[0].decode("ascii", "replace")
                    print(f"  CodeView     : RSDS {guid} age={age}")
                    print(f"  PDB path     : {pdb}")
                elif cv[:4] == b"NB10":
                    sig = u32(cv, 8)
                    age = u32(cv, 12)
                    pdb = cv[16:].split(b"\x00")[0].decode("ascii", "replace")
                    print(f"  CodeView     : NB10 sig={sig:08X} age={age}")
                    print(f"  PDB path     : {pdb}")


if __name__ == "__main__":
    xbe_info(sys.argv[1])
    if len(sys.argv) > 2:
        pe_info(sys.argv[2])
