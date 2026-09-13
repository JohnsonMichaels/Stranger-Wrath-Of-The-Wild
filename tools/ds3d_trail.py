#!/usr/bin/env python3
"""ds3d_trail.py - one readable block per voice, per marked window, from diagnostics.txt.

Usage:
    python tools/ds3d_trail.py                        # C:/Users/<you>/SWBeta/oddbeta/diagnostics.txt
    python tools/ds3d_trail.py path/to/diagnostics.txt
    python tools/ds3d_trail.py --window 2             # only the 2nd MARK window (0 = the lead-in)
    python tools/ds3d_trail.py --buf 19D87324         # one voice (the pointer as printed), every window
    python tools/ds3d_trail.py --all                  # expand the lead-in before the first MARK too
    python tools/ds3d_trail.py --max-3d 20            # DS3D lines shown per voice per window (default 6)

WHY
---
The 3D-sound fix is verified by a marked run: NUMPAD1 before and after three footsteps,
NUMPAD2 around one attack, NUMPAD3 around a moolah pickup (each press prints
`MARK: MARKn at swap N tick T`). diagnostics.txt then holds those actions among thousands
of audio calls - this title issues dozens of DirectSound calls per voice per second, on top
of RENDERSTATS / NPCIO / FILEOPEN traffic. Whether ONE footstep was audible is a chain of
five facts about ONE voice, printed by five different functions in three source files at
different moments:

    DSMIX:  SetMixBins ... -> table={10:0 3:0}         the mixbins XACT gave the voice
    DSMIX:  SetMixBinVolumes ... -> maxVolume=N          what the "dominant volume" fold made of them
    DS3D:   ... buffer X ... / ... stream X ...          the 3D data the voice (or its parent) received
    DSOUND: IDirectSoundBuffer_Play #N (buffer X, ...)   the play request
    DSPLAY: buffer X ... hostVol=N status=0x..           what the HOST object held as it started

Matching those by pointer, by eye, across hundreds of interleaved lines is how earlier
sessions lost hours. This tool cuts the log at the MARK lines, groups every audio line in a
window by the voice it belongs to, prints each voice's trail in log order, and ends each
window with a verdict: AUDIBLE if every DSPLAY in it read back hostVol > -6400 (the level
DSoundBufferUpdateHostVolume snaps to DSBVOLUME_MIN), MUTED(n) otherwise.

DSMIX lines carry no pointer (the fold lives in a header shared by buffers and streams), so
each is attached to the NEXT Play / DSPLAY line in the same window, in order - the call
order XACT uses (SetMixBins and SetMixBinVolumes precede the Play). A stream's DSMIX lines
therefore land on the next buffer's Play; the tool flags a SetMixBinVolumes whose table does
not match that voice's DSPLAY mixbins. The Play result line (`DSOUND:   -> hRet=...`) is
folded into the Play it follows. DS3D lines from the calculator stubs (`Calculate3D a1=...`,
`GetVoiceData a1=...`) carry no voice; they are counted per window, and a non-zero count
means the stub patches are still in the build.

Pure text pass; nothing here needs the game.
"""
import argparse
import re
import sys
from collections import OrderedDict

DEFAULT_LOG = r"C:\Users\<you>\SWBeta\oddbeta\diagnostics.txt"
MUTE_THRESHOLD = -6400  # DSoundBufferUpdateHostVolume: anything <= -6400 becomes DSBVOLUME_MIN (-10000)

HEX = r"(?:0x)?([0-9A-Fa-f]{4,16})"
R_MARK = re.compile(r"^MARK: (MARK\d) at swap (\d+) tick (\d+)")
R_PLAY = re.compile(r"^DSOUND: IDirectSoundBuffer_Play #(\d+) \(buffer " + HEX + r", flags 0x([0-9A-Fa-f]+)\)")
R_PLAYRES = re.compile(r"^DSOUND:\s+-> hRet=0x([0-9A-Fa-f]+) hostBuf=" + HEX
                       + r" bytes=(\d+) playFlags=0x([0-9A-Fa-f]+) emuFlags=0x([0-9A-Fa-f]+)")
R_DSPLAY = re.compile(r"^DSPLAY: buffer " + HEX + r" xbFlags=0x([0-9A-Fa-f]+) ctrl3d=(\d) hostFlags=0x([0-9A-Fa-f]+)"
                      r" volMixbin=(-?\d+) hostVol=(-?\d+) hostFreq=(\d+) status=0x([0-9A-Fa-f]+) mode3d=(\d+)"
                      r" mixbins\[(\d+)\]=\{([^}]*)\}")
R_MIXBINS = re.compile(r"^DSMIX: SetMixBins mask=0x([0-9A-Fa-f]+) pMixBins=" + HEX
                       + r" ctrl3d=(\d) -> table\[(\d+)\]=\{([^}]*)\}")
R_FOLD = re.compile(r"^DSMIX: SetMixBinVolumes in=\{([^}]*)\} table=\{([^}]*)\} -> maxVolume=(-?\d+)"
                    r" voiceVol=(-?\d+) headroom=(-?\d+) => SetVolume\((-?\d+) \+ (-?\d+)\)")
R_DS3D = re.compile(r"^DS3D: (.*)$")
R_VOICE = re.compile(r"\b(buffer|stream) " + HEX + r"\b")
R_PARENT = re.compile(r"\bparent[= ]" + HEX + r"\b")  # Part A prints "parent X", the stream side "parent=X"


def norm(p):
    """Pointer as the fork prints it with %p on x86: 8 upper-case hex digits, no 0x."""
    return "%08X" % int(p, 16)


def bins(s):
    return set(s.split())


class Ev(object):
    __slots__ = ("line", "kind", "voice", "text", "hostvol", "maxvol", "parent", "stream", "table")

    def __init__(self, line, kind, voice, text):
        self.line, self.kind, self.voice, self.text = line, kind, voice, text
        self.hostvol = None   # DSPLAY only
        self.maxvol = None    # SetMixBinVolumes only
        self.parent = None    # DS3D lines that name a parent
        self.stream = False   # DS3D line printed by the stream receiver
        self.table = None     # mixbin table string (SetMixBins result, fold table, DSPLAY mixbins)


def new_window(label, swap, tick, start):
    return {"label": label, "swap": swap, "tick": tick, "start": start, "end": None,
            "events": [], "stubs": 0, "next": None}


def parse(fp):
    windows = []
    cur = new_window("(lead-in, before the first MARK)", None, None, 1)
    n = 0
    for n, raw in enumerate(fp, 1):
        line = raw.rstrip("\r\n")
        m = R_MARK.match(line)
        if m:
            cur["end"] = n - 1
            cur["next"] = "%s @swap %s" % (m.group(1), m.group(2))
            windows.append(cur)
            cur = new_window(m.group(1), int(m.group(2)), int(m.group(3)), n)
            continue
        if line.startswith("DSPLAY:"):
            m = R_DSPLAY.match(line)
            if m:
                e = Ev(n, "DSPLAY", norm(m.group(1)),
                       "hostVol=%s hostFreq=%s status=0x%s volMixbin=%s ctrl3d=%s mode3d=%s mixbins[%s]={%s}"
                       % (m.group(6), m.group(7), m.group(8), m.group(5), m.group(3), m.group(9), m.group(10), m.group(11)))
                e.hostvol = int(m.group(6))
                e.table = m.group(11)
                cur["events"].append(e)
            continue
        if line.startswith("DSMIX:"):
            m = R_MIXBINS.match(line)
            if m:
                e = Ev(n, "SetMixBins", None,
                       "table[%s]={%s} ctrl3d=%s mask=0x%s" % (m.group(4), m.group(5), m.group(3), m.group(1)))
                e.table = m.group(5)
                cur["events"].append(e)
                continue
            m = R_FOLD.match(line)
            if m:
                e = Ev(n, "SetMixBinVolumes", None,
                       "in={%s} table={%s} -> maxVolume=%s voiceVol=%s headroom=%s => SetVolume(%s + %s)" % m.groups())
                e.maxvol = int(m.group(3))
                e.table = m.group(2)
                cur["events"].append(e)
            continue
        if line.startswith("DS3D:"):
            m = R_DS3D.match(line)
            body = m.group(1) if m else line
            v = R_VOICE.search(body)
            if not v:
                cur["stubs"] += 1  # Calculate3D / GetVoiceData stub trace: names no voice
                continue
            e = Ev(n, "DS3D", norm(v.group(2)), body)
            e.stream = (v.group(1) == "stream")
            p = R_PARENT.search(body)
            if p and int(p.group(1), 16) != 0:
                e.parent = norm(p.group(1))
            cur["events"].append(e)
            continue
        if line.startswith("DSOUND:"):
            m = R_PLAY.match(line)
            if m:
                cur["events"].append(Ev(n, "Play", norm(m.group(2)), "#%s flags=0x%s" % (m.group(1), m.group(3))))
                continue
            m = R_PLAYRES.match(line)
            if m:
                cur["events"].append(Ev(n, "PlayResult", None,
                                        "-> hRet=0x%s hostBuf=%s bytes=%s playFlags=0x%s emuFlags=0x%s" % m.groups()))
            continue
    cur["end"] = n
    windows.append(cur)
    return windows


def attach(window):
    """DSMIX lines -> the voice of the next Play/DSPLAY; PlayResult -> the Play it follows."""
    pending = []
    last_play = None
    kept = []
    for e in window["events"]:
        if e.kind in ("SetMixBins", "SetMixBinVolumes"):
            pending.append(e)
            kept.append(e)
        elif e.kind == "PlayResult":
            if last_play is not None:
                last_play.text += "  " + e.text
            else:
                kept.append(e)  # orphan, shown unattached
        else:
            if e.kind in ("Play", "DSPLAY") and pending:
                for p in pending:
                    p.voice = e.voice
                pending = []
            if e.kind == "Play":
                last_play = e
            kept.append(e)
    window["events"] = kept


def by_voice(window):
    per = OrderedDict()
    for e in window["events"]:
        per.setdefault(e.voice or "(unattached)", []).append(e)
    return per


def verdict(window):
    plays = [e for e in window["events"] if e.kind == "DSPLAY"]
    if not plays:
        return "NO PLAYS (no DSPLAY line in this window)"
    muted = [e for e in plays if e.hostvol <= MUTE_THRESHOLD]
    if muted:
        return "MUTED(%d of %d)" % (len(muted), len(plays))
    return "AUDIBLE (%d plays, every hostVol > %d)" % (len(plays), MUTE_THRESHOLD)


def window_head(idx, w):
    head = "window %d: %s" % (idx, w["label"])
    if w["swap"] is not None:
        head += " @swap %d tick %d" % (w["swap"], w["tick"])
    return head + "  ->  %s   [lines %d..%d]" % (w["next"] or "end of log", w["start"], w["end"])


def print_window(idx, w, only_voice, max_3d):
    print("=" * 100)
    print(window_head(idx, w))
    if w["stubs"]:
        print("  calculator stub lines (Calculate3D/GetVoiceData, no voice): %d  <- the 3D calculator patches are still in this build"
              % w["stubs"])
    shown = 0
    for voice, evs in by_voice(w).items():
        if only_voice and voice != only_voice:
            continue
        shown += 1
        parent = next((e.parent for e in reversed(evs) if e.parent), None)
        kind = "stream" if any(e.stream for e in evs) else "buffer"
        nplay = sum(1 for e in evs if e.kind == "DSPLAY")
        print("  voice %s  [%s]%s  DSPLAY x%d" % (voice, kind, ("  parent %s" % parent) if parent else "", nplay))
        ds3d = [e for e in evs if e.kind == "DS3D"]
        hide = set()
        if len(ds3d) > max_3d:
            for e in ds3d[max_3d - 1:-1]:  # keep the first max_3d-1 and the last; the middle is per-tick repetition
                hide.add(e.line)
        hidden = 0
        for i, e in enumerate(evs):
            if e.line in hide:
                hidden += 1
                continue
            if hidden:
                print("    %6s  %-16s ... %d more DS3D lines (--max-3d to show)" % ("", "", hidden))
                hidden = 0
            flag = ""
            if e.kind == "DSPLAY":
                flag = "   <- MUTED" if e.hostvol <= MUTE_THRESHOLD else "   <- audible"
            elif e.kind == "SetMixBinVolumes":
                if e.maxvol is not None and e.maxvol <= MUTE_THRESHOLD:
                    flag = "   <- fold collapsed to DSBVOLUME_MIN (the mute)"
                nxt = next((f for f in evs[i + 1:] if f.kind == "DSPLAY"), None)
                if nxt is not None and e.table is not None and bins(e.table) != bins(nxt.table):
                    flag += "   <- table differs from this voice's DSPLAY mixbins: probably another voice's (streams print no pointer)"
            print("    %6d  %-16s %s%s" % (e.line, e.kind, e.text, flag))
        if hidden:
            print("    %6s  %-16s ... %d more DS3D lines (--max-3d to show)" % ("", "", hidden))
    if only_voice and shown == 0:
        print("  (voice %s not in this window)" % only_voice)
    print("  verdict: %s" % verdict(w))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=DEFAULT_LOG, help="diagnostics.txt (default: the oddbeta one), or - for stdin")
    ap.add_argument("--window", type=int, help="print only the Nth MARK window (1-based; 0 = the lead-in)")
    ap.add_argument("--buf", help="print only this voice (buffer/stream pointer as printed, e.g. 19D87324)")
    ap.add_argument("--all", action="store_true", help="expand the lead-in before the first MARK too")
    ap.add_argument("--max-3d", type=int, default=6, help="DS3D lines shown per voice per window (default 6)")
    args = ap.parse_args()

    only_voice = None
    if args.buf:
        try:
            only_voice = norm(args.buf)
        except ValueError:
            sys.exit("--buf wants the pointer as printed in the log, e.g. 19D87324")

    fp = sys.stdin if args.path == "-" else open(args.path, encoding="utf-8", errors="replace")
    windows = parse(fp)
    for w in windows:
        attach(w)
    marks = len(windows) - 1
    nevents = sum(len(w["events"]) for w in windows)
    print("%s: %d audio events, %d MARK lines, %d windows" % (args.path, nevents, marks, len(windows)))
    if marks == 0:
        windows[0]["label"] = "(no MARK lines - the whole log)"

    for idx, w in enumerate(windows):
        if args.window is not None and idx != args.window:
            continue
        if idx == 0 and marks > 0 and w["end"] < w["start"]:
            continue  # the log opened with a MARK: there is no lead-in
        if idx == 0 and marks > 0 and not args.all and args.window is None:
            print("=" * 100)
            print("window 0: %s  [lines %d..%d]  %d events, %d voices - use --all to expand.  verdict: %s"
                  % (w["label"], w["start"], w["end"], len(w["events"]), len(by_voice(w)), verdict(w)))
            continue
        print_window(idx, w, only_voice, args.max_3d)

    print("=" * 100)
    print("summary: " + " | ".join("%d %s: %s" % (i, w["label"], verdict(w).split(" (")[0])
                                   for i, w in enumerate(windows) if not (i == 0 and marks > 0 and w["end"] < w["start"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
