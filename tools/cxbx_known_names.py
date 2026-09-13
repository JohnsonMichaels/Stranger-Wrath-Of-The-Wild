#!/usr/bin/env python3
"""Scrape the symbol names Cxbx-Reloaded's XbSymbolDatabase knows about out of
cxbxr-emu.dll's string table, so the generated cache only carries names the
emulator will actually look up.

Usage: python cxbx_known_names.py <cxbxr-emu.dll> <out.txt>
"""
import re
import sys

# Names in the symbol database look like C identifiers; the interesting ones are
# the Xbox library entry points Cxbx patches.
PREFIXES = (
    "D3D", "IDirect3D", "Direct3D", "Xb", "CDevice", "CMiniport", "CTexture",
    "IDirectSound", "CDirectSound", "CMcpx", "DirectSound", "XAudio", "XAC",
    "IXACT", "XACT", "XGraphics", "XG", "XFont", "Xapi", "X", "CHRTF",
    "CFullHRTF", "CLightHRTF", "CStream", "Get", "Set", "Lock", "Unlock",
    "PSGP", "CMemory", "CModule", "DSound", "g_", "main", "_",
)


def strings(data, minlen=4):
    out = []
    cur = bytearray()
    start = 0
    for i, b in enumerate(data):
        if 0x20 <= b < 0x7F:
            if not cur:
                start = i
            cur.append(b)
        else:
            if len(cur) >= minlen:
                out.append((start, cur.decode("ascii")))
            cur = bytearray()
    if len(cur) >= minlen:
        out.append((start, cur.decode("ascii")))
    return out


IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

if __name__ == "__main__":
    with open(sys.argv[1], "rb") as f:
        data = f.read()
    names = set()
    for _, s in strings(data):
        if IDENT.match(s) and len(s) >= 5 and s.startswith(PREFIXES):
            names.add(s)
    with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as f:
        for n in sorted(names):
            f.write(n + "\n")
    print(f"{len(names)} candidate symbol names -> {sys.argv[2]}")
    d3d = sorted(n for n in names if n.startswith(("D3DDevice_", "D3D_", "D3DResource_")))
    print(f"  of which D3DDevice_/D3D_/D3DResource_: {len(d3d)}")
    for n in d3d[:15]:
        print("   ", n)
