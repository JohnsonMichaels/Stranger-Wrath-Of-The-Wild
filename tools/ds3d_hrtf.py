#!/usr/bin/env python3
"""Decode the light-HRTF filter table of the Xbox DSOUND library into per-azimuth L/R gains.

    python tools/ds3d_hrtf.py <xbe> [<table_va_hex> <entries> <fmt>]

CLightHRTFSource::GetFilterPair (guest 0x1d5971) indexes a table at 0x1dd0d0 with
idx = (180 - round3(|azimuth|)) / 3, entry = 64 bytes = two 32-byte FIR kernels
(near ear at +0, far ear at +32; swapped when azimuth < 0).  This prints, for each
entry, the DC gain (sum of taps) and RMS energy of both kernels, so the interaural
level difference the Xbox hardware would apply can be reproduced as a plain stereo
pan on a host that does not convolve.  fmt = sm (sign-magnitude, default) or tc
(two's complement).
"""
import math
import sys

from xbe_disasm import sections, va_to_off


def decode(b, fmt):
    if fmt == 'tc':
        return [x - 256 if x >= 128 else x for x in b]
    return [-(x & 0x7f) if x & 0x80 else x for x in b]


def db(x):
    return 20 * math.log10(x) if x > 0 else float('-inf')


def main():
    d = open(sys.argv[1], 'rb').read()
    va = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x1dd0d0
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 61
    fmt = sys.argv[4] if len(sys.argv) > 4 else 'sm'
    secs = sections(d)
    off, sec = va_to_off(secs, va)
    print('; table %08x  section %s  file 0x%x  %d entries x 64 bytes  fmt=%s' % (va, sec, off, n, fmt))
    print('; A = pair[0] (far/left ear for az>=0), B = pair[1] (near/right ear for az>=0); rms/dc relative to 128')
    print('; idx  az(deg)  sumA  sumB  rmsA  rmsB   dcA(dB) dcB(dB) rmsA(dB) rmsB(dB)  A-B(dB)  itd(A/B)  kernelA(32 bytes)')
    for i in range(n):
        e = d[off + i * 64: off + i * 64 + 64]
        # bytes 0..30 are FIR taps; byte 31 is the interaural delay (samples) that
        # CMcpxVoiceClient::LoadHRTFFilter (0x1d8969) shifts into bits 25+ of the
        # 16th hardware dword, negated when azimuth < 0.
        a = decode(e[:31], fmt)
        b = decode(e[32:63], fmt)
        da, dbb = e[31], e[63]
        az = 180 - 3 * i
        sa, sb = sum(a), sum(b)
        ra = math.sqrt(sum(x * x for x in a))
        rb = math.sqrt(sum(x * x for x in b))
        print('%3d  %4d  %5d %5d  %7.1f %7.1f  %6.2f %6.2f  %6.2f %6.2f  %6.2f  itd=%2d/%2d  %s' % (
            i, az, sa, sb, ra, rb, db(sa / 128.0), db(sb / 128.0), db(ra / 128.0), db(rb / 128.0),
            db(ra) - db(rb) if ra and rb else float('nan'), da, dbb, e[:32].hex()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
