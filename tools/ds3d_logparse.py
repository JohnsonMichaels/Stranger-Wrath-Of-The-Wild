#!/usr/bin/env python3
"""ds3d_logparse.py - read a Cxbx diagnostics.txt and print the audio trail per
Xbox buffer, so one footstep can be read top to bottom.

Understands two generations of lines:

  legacy (what the fork prints today, all in DirectSoundBuffer.cpp / DirectSoundInline.hpp):
      DSOUND: IDirectSoundBuffer_Play #N (buffer X, flags 0x...)
      DSOUND: XBtoHost XADPCM->decode xbBytes=N pcmBytes=N ch=N bits=N rate=N
      DSOUND:   -> decoded peak amplitude = N [<-- SILENCE]
      DSOUND:   -> hRet=0x.. hostBuf=X bytes=N playFlags=0x.. emuFlags=0x..
      DSOUND: volume=N emuFlags=0x.. codecs[...] [<-- MUTED]
  planned (swse/research/DS3D_MEASUREMENT_PLAN.md, Part B):
      DS3D: <event> key=value key=value ... t=<tick>
      MARK: MARKn at swap N tick N            (already printed by CxbxrPollDiagnosticKeys)

Usage:
    python tools/ds3d_logparse.py C:/Users/<you>/SWBeta/oddbeta/diagnostics.txt
    python tools/ds3d_logparse.py diagnostics.txt --buf 19D87324      # one buffer's trail
    python tools/ds3d_logparse.py diagnostics.txt --window 1          # events between MARK1 and MARK2
    python tools/ds3d_logparse.py diagnostics.txt --verdict           # silence verdict per Play
    python tools/ds3d_logparse.py - < extract.txt                     # from stdin

Nothing here needs the game; it is a pure text pass.
"""
import argparse
import re
import sys
from collections import OrderedDict, defaultdict

DSBVOLUME_MIN = -10000
DSBSTATUS_PLAYING = 0x1
DSBCAPS_GLOBALFOCUS = 0x8000

R_LEGACY_PLAY = re.compile(r"DSOUND: IDirectSoundBuffer_Play #(\d+) \(buffer (?:0x)?([0-9A-Fa-f]+), flags 0x([0-9A-Fa-f]+)\)")
R_LEGACY_XB = re.compile(r"DSOUND: XBtoHost (\S+) xbBytes=(\d+) pcmBytes=(\d+) ch=(\d+) bits=(\d+) rate=(\d+)")
R_LEGACY_PEAK = re.compile(r"DSOUND:\s+-> decoded peak amplitude = (-?\d+)\s*(<-- SILENCE)?")
R_LEGACY_HRET = re.compile(r"DSOUND:\s+-> hRet=0x([0-9A-Fa-f]+) hostBuf=(?:0x)?([0-9A-Fa-f]+) bytes=(\d+) playFlags=0x([0-9A-Fa-f]+) emuFlags=0x([0-9A-Fa-f]+)")
R_LEGACY_VOL = re.compile(r"DSOUND: volume=(-?\d+) emuFlags=0x([0-9A-Fa-f]+) codecs\[pcm=(\d) xadpcm=(\d) unknown=(\d)\]\s*(<-- MUTED)?")
R_MARK = re.compile(r"MARK: (MARK\d) at swap (\d+) tick (\d+)")
# Deployed 2026-09-12 (DirectSoundBuffer.cpp:1737-1749, 1770-1777):
#   DS3D: Use3DVoiceData buffer 19970FAC arg 00000001
#   DS3D: Set3DVoiceData buffer 19970FAC data 0D171E28 = 00000000 00000000 ... (8 dwords)
R_DEP_BUF = re.compile(r"DS3D: (Use3DVoiceData|Set3DVoiceData) buffer (?:0x)?([0-9A-Fa-f]+) (arg|data) (?:0x)?([0-9A-Fa-f]+)"
                       r"(?: = ((?:[0-9A-Fa-f]{8}\s?)+))?")
R_DS3D = re.compile(r"DS3D: (\w+)\s*(.*)")
R_KV = re.compile(r"(\w+)=(\S+)")
# Deployed calculator lines (DirectSound3DCalculator.cpp:66-72, 99-106) use the planned key=value
# form already; only the event names differ.
EV_ALIAS = {"Calculate3D": "calc3d", "GetVoiceData": "getvd", "Use3DVoiceData": "use3d", "Set3DVoiceData": "set3d"}


def norm_ptr(s):
    s = s.lower()
    if s.startswith("0x"):
        s = s[2:]
    return s.upper().rjust(8, "0")


def to_int(v):
    try:
        if v.lower().startswith("0x"):
            return int(v, 16)
        return int(v)
    except (ValueError, AttributeError):
        return None


class Event:
    __slots__ = ("line", "kind", "ev", "kv", "text", "buf", "tick")

    def __init__(self, line, kind, ev, kv, text):
        self.line, self.kind, self.ev, self.kv, self.text = line, kind, ev, kv, text
        self.buf = norm_ptr(kv["buf"]) if "buf" in kv else None
        self.tick = to_int(kv.get("t", "")) if "t" in kv else None


def parse(fp):
    events = []
    marks = []
    for n, raw in enumerate(fp, 1):
        line = raw.rstrip("\r\n")
        m = R_MARK.search(line)
        if m:
            kv = {"label": m.group(1), "swap": m.group(2), "t": m.group(3)}
            e = Event(n, "mark", m.group(1), kv, line)
            events.append(e)
            marks.append(e)
            continue
        m = R_DEP_BUF.search(line)
        if m:
            kv = OrderedDict([("buf", m.group(2))])
            if m.group(1) == "Use3DVoiceData":
                kv["on"] = m.group(4)
            else:
                dwords = (m.group(5) or "").split()
                kv["vd"] = m.group(4)
                kv["mask"] = ("0x" + dwords[0]) if dwords else "?"
                kv["d"] = ",".join(dwords)
            events.append(Event(n, "ds3d", EV_ALIAS[m.group(1)], kv, line))
            continue
        m = R_DS3D.search(line)
        if m:
            kv = OrderedDict(R_KV.findall(m.group(2)))
            events.append(Event(n, "ds3d", EV_ALIAS.get(m.group(1), m.group(1)), kv, line))
            continue
        m = R_LEGACY_PLAY.search(line)
        if m:
            kv = {"n": m.group(1), "buf": m.group(2), "flags": "0x" + m.group(3)}
            events.append(Event(n, "legacy", "play", kv, line))
            continue
        m = R_LEGACY_XB.search(line)
        if m:
            kv = {"codec": m.group(1), "xbBytes": m.group(2), "pcmBytes": m.group(3),
                  "ch": m.group(4), "bits": m.group(5), "rate": m.group(6)}
            events.append(Event(n, "legacy", "xbtohost", kv, line))
            continue
        m = R_LEGACY_PEAK.search(line)
        if m:
            events.append(Event(n, "legacy", "peak", {"peak": m.group(1), "silence": "1" if m.group(2) else "0"}, line))
            continue
        m = R_LEGACY_HRET.search(line)
        if m:
            kv = {"hRet": "0x" + m.group(1), "host": m.group(2), "hostBytes": m.group(3),
                  "playFlags": "0x" + m.group(4), "emu": "0x" + m.group(5)}
            events.append(Event(n, "legacy", "playresult", kv, line))
            continue
        m = R_LEGACY_VOL.search(line)
        if m:
            kv = {"vol": m.group(1), "emu": "0x" + m.group(2), "pcm": m.group(3), "xadpcm": m.group(4),
                  "unknown": m.group(5), "muted": "1" if m.group(6) else "0"}
            events.append(Event(n, "legacy", "hostvol", kv, line))
            continue
    return events, marks


def attach_legacy(events):
    """The legacy decode/result lines carry no buffer id; they follow their Play line.
    Attribute them to the most recent Play on the same thread of text."""
    cur = None
    for e in events:
        if e.kind == "legacy" and e.ev == "play":
            cur = e.buf
            e.kv["_plays"] = "1"
        elif e.kind == "legacy" and e.ev in ("xbtohost", "peak", "playresult"):
            e.buf = cur
        elif e.kind == "ds3d" and e.ev == "play":
            cur = e.buf


def attach_3d(events):
    """calc3d/getvd lines carry guest pointers only. XACT hands GetVoiceData's a5 (the
    DS3DVOICEDATA at source+0xE8) straight to Set3DVoiceData as `data`/`vd`, and
    Calculate3D's a2 equals GetVoiceData's a2 (source+0x44), so the three can be
    attributed to the buffer of the set3d that consumes them."""
    by_a2 = {}
    by_a5 = {}
    for e in events:
        if e.kind != "ds3d":
            continue
        if e.ev == "calc3d" and "a2" in e.kv:
            by_a2.setdefault(norm_ptr(e.kv["a2"]), []).append(e)
        elif e.ev == "getvd":
            group = by_a2.pop(norm_ptr(e.kv.get("a2", "0")), []) + [e]
            by_a5.setdefault(norm_ptr(e.kv.get("a5", "0")), []).extend(group)
        elif e.ev == "set3d" and "vd" in e.kv and e.buf:
            for f in by_a5.pop(norm_ptr(e.kv["vd"]), []):
                f.buf = e.buf


def buffers(events):
    per = OrderedDict()
    for e in events:
        if e.buf is None:
            continue
        per.setdefault(e.buf, []).append(e)
    return per


def fmt_kv(e, skip=("buf", "t")):
    return " ".join("%s=%s" % (k, v) for k, v in e.kv.items() if k not in skip and not k.startswith("_"))


def cmd_summary3d(events):
    """Counts for the deployed calculator/3D-voice lines (they carry no play context)."""
    c = defaultdict(int)
    masks = defaultdict(int)
    bufs = defaultdict(set)
    for e in events:
        if e.ev in ("calc3d", "getvd", "set3d", "use3d"):
            c[e.ev] += 1
            if e.buf:
                bufs[e.ev].add(e.buf)
            if e.ev == "set3d":
                masks[e.kv.get("mask", "?")] += 1
    if not c:
        return
    print("3D path: calc3d=%d getvd=%d set3d=%d (buffers %d) use3d=%d (buffers %d); set3d masks: %s" % (
        c["calc3d"], c["getvd"], c["set3d"], len(bufs["set3d"]), c["use3d"], len(bufs["use3d"]),
        ", ".join("%s x%d" % kv for kv in sorted(masks.items()))))


def cmd_table(events):
    cmd_summary3d(events)
    per = buffers(events)
    print("%-9s %5s %-7s %-34s %-22s %s" % ("buffer", "plays", "created", "format/bytes (first seen)", "last host state", "notes"))
    for buf, evs in per.items():
        plays = [e for e in evs if e.ev == "play"]
        created = [e for e in evs if e.ev == "create"]
        fmts = []
        for e in evs:
            if e.ev == "xbtohost":
                s = "%s %s@%s" % (e.kv.get("codec", "?").split("-")[0], e.kv.get("xbBytes"), e.kv.get("rate"))
                if s not in fmts:
                    fmts.append(s)
            if e.ev == "setformat" and "fmt" in e.kv:
                s = "fmt " + e.kv["fmt"]
                if s not in fmts:
                    fmts.append(s)
        host = ""
        for e in reversed(evs):
            if e.ev == "play" and "vol" in e.kv:
                host = "vol=%s freq=%s status=%s" % (e.kv.get("vol"), e.kv.get("freq"), e.kv.get("status"))
                break
            if e.ev == "playresult":
                host = "hRet=%s emu=%s" % (e.kv.get("hRet"), e.kv.get("emu"))
                break
        notes = []
        if any(e.ev == "set3d" for e in evs):
            notes.append("set3d x%d" % sum(1 for e in evs if e.ev == "set3d"))
        if any(e.ev == "setoutput" for e in evs):
            notes.append("routed")
        if any(e.ev == "peak" and e.kv.get("silence") == "1" for e in evs):
            notes.append("DECODED-SILENCE")
        cflags = created[0].kv.get("xbFlags", "?") if created else "-"
        print("%-9s %5d %-7s %-34s %-22s %s" % (buf, len(plays), cflags, "; ".join(fmts)[:34], host, " ".join(notes)))


def cmd_trail(events, buf):
    buf = norm_ptr(buf)
    for e in events:
        if e.kind == "mark":
            print("%6d  ---- %s (swap %s, tick %s)" % (e.line, e.ev, e.kv["swap"], e.kv["t"]))
        elif e.buf == buf or (e.ev in ("calc3d", "getvd") and buf in (norm_ptr(v) for v in e.kv.values() if re.fullmatch(r"(0x)?[0-9A-Fa-f]{6,8}", v))):
            print("%6d  %-10s %s" % (e.line, e.ev, fmt_kv(e)))


def windows(events):
    """Pairs of MARK(open) .. next MARK(any) define a window."""
    out = []
    open_e = None
    for e in events:
        if e.kind == "mark":
            if open_e is not None:
                out.append((open_e, e))
            open_e = e
    if open_e is not None:
        out.append((open_e, None))
    return out


def cmd_window(events, idx):
    ws = windows(events)
    if not ws:
        print("no MARK lines in this log")
        return
    if idx < 1 or idx > len(ws):
        print("windows available: %d" % len(ws))
        return
    a, b = ws[idx - 1]
    lo = a.line
    hi = b.line if b else 10 ** 9
    print("window %d: %s line %d .. %s line %s" % (idx, a.ev, a.line, b.ev if b else "EOF", b.line if b else "-"))
    for e in events:
        if lo <= e.line <= hi and e.kind != "mark":
            print("%6d  %-10s buf=%s %s" % (e.line, e.ev, e.buf or "-", fmt_kv(e)))


def cmd_verdict(events):
    per = buffers(events)
    n = 0
    for buf, evs in per.items():
        for i, e in enumerate(evs):
            if e.ev != "play":
                continue
            n += 1
            flags = []
            vol = to_int(e.kv.get("vol", "")) if "vol" in e.kv else None
            freq = to_int(e.kv.get("freq", "")) if "freq" in e.kv else None
            status = to_int(e.kv.get("status", "")) if "status" in e.kv else None
            caps = to_int(e.kv.get("caps", "")) if "caps" in e.kv else None
            fg = to_int(e.kv.get("fg", "")) if "fg" in e.kv else None
            if vol is not None and vol <= -6400:
                flags.append("MUTED(host vol %d)" % vol)
            if freq is not None and (freq < 2000 or freq > 96000):
                flags.append("FREQ %d" % freq)
            if status is not None and not (status & DSBSTATUS_PLAYING):
                flags.append("NOT-PLAYING(status 0x%x)" % status)
            if caps is not None and not (caps & DSBCAPS_GLOBALFOCUS) and fg == 0:
                flags.append("NO-GLOBALFOCUS+unfocused")
            # a stop on the same buffer within 60 ms of the play
            for f in evs[i + 1:]:
                if f.ev in ("stop", "stopex"):
                    if e.tick is not None and f.tick is not None and f.tick - e.tick <= 60:
                        flags.append("STOPPED after %d ms" % (f.tick - e.tick))
                    break
                if f.ev == "play":
                    break
            # calculator signature: every set3d before this play carried flags=0
            s3 = [f for f in evs[:i] if f.ev == "set3d"]
            if s3:
                zero = sum(1 for f in s3 if to_int(f.kv.get("mask", f.kv.get("flags", ""))) == 0)
                flags.append("set3d %d/%d mask=0" % (zero, len(s3)))
            legacy = ""
            for f in evs[i + 1:i + 4]:
                if f.ev == "playresult":
                    legacy = "hRet=%s emu=%s" % (f.kv.get("hRet"), f.kv.get("emu"))
                    break
            print("line %6d buf=%s play %s %s %s" % (e.line, buf, fmt_kv(e, skip=("buf", "t", "props", "d")),
                                                    legacy, " | ".join(flags) if flags else "| no host-side reason visible"))
    if n == 0:
        print("no Play lines in this log")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="diagnostics.txt, or - for stdin")
    ap.add_argument("--buf", help="print the trail of one Xbox buffer (hex pointer as printed)")
    ap.add_argument("--window", type=int, help="print every event inside the Nth MARK window")
    ap.add_argument("--verdict", action="store_true", help="silence verdict per Play")
    ap.add_argument("--all", action="store_true", help="dump every parsed event")
    args = ap.parse_args()

    fp = sys.stdin if args.path == "-" else open(args.path, encoding="utf-8", errors="replace")
    events, marks = parse(fp)
    attach_legacy(events)
    attach_3d(events)
    print("parsed %d audio events, %d marks" % (len(events), len(marks)))
    if args.all:
        for e in events:
            print("%6d  %-6s %-10s buf=%s %s" % (e.line, e.kind, e.ev, e.buf or "-", fmt_kv(e)))
    if args.buf:
        cmd_trail(events, args.buf)
    elif args.window:
        cmd_window(events, args.window)
    elif args.verdict:
        cmd_verdict(events)
    else:
        cmd_table(events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
