#!/usr/bin/env python3
"""ds3d_xwb.py - list the waves inside the beta's XACT1 wave banks (WBND v3) and
match the byte counts that Cxbx's "DSOUND: XBtoHost ... xbBytes=N" lines report
against them, so a buffer in diagnostics.txt can be named.

The in-memory banks (steef.xwb, townsfolk.xwb, region_0N.xwb, ...) carry an
ENTRYNAMES segment, so every wave has a friendly name.  A Play in the log gives
xbBytes (= the wave's play-region length), codec and sample rate; the triple is
usually unique across the 16 in-memory banks.

    python tools/ds3d_xwb.py                       # summary of every bank
    python tools/ds3d_xwb.py list steef            # every wave in one bank
    python tools/ds3d_xwb.py match 3168 5436 2592  # which waves have these sizes
    python tools/ds3d_xwb.py log oddbeta/diagnostics.txt   # match every xbBytes= in a log

WBND v3 layout (verified against steef.xwb / region_03.xwb):
    0x00  'WBND', u32 version(3)
    0x08  4 regions {u32 offset, u32 length}: BANKDATA, ENTRYMETADATA, ENTRYNAMES, ENTRYWAVEDATA
    BANKDATA: u32 flags (0x00010000 = names present), u32 entryCount, char name[16],
              u32 metaElemSize(24), u32 nameElemSize(64), u32 alignment, u32 compactFormat
    ENTRY (24 bytes): u32 flagsAndDuration, u32 format, u32 playOffset, u32 playLength,
                      u32 loopStart, u32 loopTotal
    format bits: tag[0:2] (0=PCM 1=XADPCM), channels[2:5], rate[5:23], blockAlign[23:31], bits16[31]
"""
import argparse
import glob
import os
import re
import struct
import sys

DEF_DIR = r"C:\Users\<you>\SWBeta\Game\data\audio\xwb"
TAGS = {0: "PCM", 1: "XADPCM", 2: "WMA?", 3: "?"}


def decode_format(f):
    tag = f & 3
    ch = (f >> 2) & 7
    rate = (f >> 5) & 0x3FFFF
    balign = (f >> 23) & 0xFF
    bits = 16 if (f >> 31) & 1 else 8
    return tag, ch, rate, balign, bits


def fmt_str(f):
    tag, ch, rate, balign, bits = decode_format(f)
    s = "%s ch=%d %dHz" % (TAGS.get(tag, "?"), ch, rate)
    if tag == 0:
        s += " %dbit" % bits
    return s


class Bank:
    def __init__(self, path):
        self.path = path
        self.file = os.path.basename(path)
        d = open(path, "rb").read()
        if d[:4] != b"WBND":
            raise ValueError("not a WBND: %s" % path)
        self.version = struct.unpack_from("<I", d, 4)[0]
        regions = [struct.unpack_from("<II", d, 8 + 8 * i) for i in range(4)]
        bd_off = regions[0][0]
        self.flags, self.count = struct.unpack_from("<II", d, bd_off)
        self.name = d[bd_off + 8: bd_off + 24].split(b"\0")[0].decode("latin1")
        meta_sz, name_sz, self.alignment, self.compact = struct.unpack_from("<IIII", d, bd_off + 24)
        meta_off, meta_len = regions[1]
        names_off, names_len = regions[2]
        self.wave_off = regions[3][0]
        self.entries = []
        for i in range(self.count):
            o = meta_off + i * meta_sz
            fad, fmt, poff, plen, lstart, ltotal = struct.unpack_from("<6I", d, o)
            nm = ""
            if names_len and name_sz:
                no = names_off + i * name_sz
                nm = d[no: no + name_sz].split(b"\0")[0].decode("latin1")
            self.entries.append(dict(index=i, name=nm, flags=fad, format=fmt,
                                     offset=poff, length=plen,
                                     loop_start=lstart, loop_total=ltotal))


def load_banks(directory, include_streams=False):
    banks = []
    for p in sorted(glob.glob(os.path.join(directory, "*.xwb"))):
        if not include_streams and p.lower().endswith("_stream.xwb"):
            continue
        try:
            banks.append(Bank(p))
        except Exception as e:  # noqa: BLE001
            print("skip %s: %s" % (p, e), file=sys.stderr)
    return banks


def cmd_summary(banks):
    print("%-22s %-14s %5s  %s" % ("file", "bank", "waves", "formats"))
    for b in banks:
        fmts = {}
        for e in b.entries:
            fmts[fmt_str(e["format"])] = fmts.get(fmt_str(e["format"]), 0) + 1
        print("%-22s %-14s %5d  %s" % (b.file, b.name, b.count,
                                       ", ".join("%s x%d" % kv for kv in sorted(fmts.items()))))


def cmd_list(banks, which):
    for b in banks:
        if which and which.lower() not in (b.name.lower(), b.file.lower(), b.file.lower()[:-4]):
            continue
        print("== %s (%s) %d waves, flags=0x%08X" % (b.file, b.name, b.count, b.flags))
        for e in b.entries:
            print("  %3d %-40s %-24s bytes=%-7d off=0x%06X loop=%d/%d" % (
                e["index"], e["name"], fmt_str(e["format"]), e["length"], e["offset"],
                e["loop_start"], e["loop_total"]))


def match_sizes(banks, sizes, rate=None, tag=None):
    hits = {}
    for n in sizes:
        hits[n] = []
        for b in banks:
            for e in b.entries:
                if e["length"] != n:
                    continue
                t, ch, r, _, _ = decode_format(e["format"])
                if rate and r != rate:
                    continue
                if tag is not None and t != tag:
                    continue
                hits[n].append((b, e))
    return hits


def print_hits(hits):
    for n, lst in hits.items():
        if not lst:
            print("xbBytes=%-6d  NO MATCH in in-memory banks" % n)
            continue
        print("xbBytes=%-6d  %d match(es):" % (n, len(lst)))
        for b, e in lst:
            print("    %-14s #%-3d %-40s %s" % (b.name, e["index"], e["name"], fmt_str(e["format"])))


LOG_RE = re.compile(r"XBtoHost (\S+) xbBytes=(\d+) pcmBytes=\d+ ch=(\d+) bits=(\d+) rate=(\d+)")


def cmd_log(banks, path):
    seen = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = LOG_RE.search(line)
        if not m:
            continue
        codec, n, ch, bits, rate = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
        key = (n, codec, rate)
        seen[key] = seen.get(key, 0) + 1
    for (n, codec, rate), cnt in sorted(seen.items()):
        tag = 1 if codec.startswith("XADPCM") else 0
        hits = match_sizes(banks, [n], rate=rate, tag=tag)
        print("-- %s xbBytes=%d rate=%d (seen %d time(s))" % (codec, n, rate, cnt))
        print_hits(hits)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=DEF_DIR)
    ap.add_argument("--streams", action="store_true", help="include *_stream.xwb banks")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("summary")
    p = sub.add_parser("list"); p.add_argument("bank", nargs="?", default=None)
    p = sub.add_parser("match"); p.add_argument("sizes", nargs="+", type=int)
    p.add_argument("--rate", type=int, default=None); p.add_argument("--pcm", action="store_true")
    p = sub.add_parser("log"); p.add_argument("path")
    args = ap.parse_args()

    banks = load_banks(args.dir, args.streams)
    if args.cmd in (None, "summary"):
        cmd_summary(banks)
    elif args.cmd == "list":
        cmd_list(banks, args.bank)
    elif args.cmd == "match":
        print_hits(match_sizes(banks, args.sizes, rate=args.rate, tag=(0 if args.pcm else None)))
    elif args.cmd == "log":
        cmd_log(banks, args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
