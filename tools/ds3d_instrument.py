"""Add bounded printf instrumentation to the Cxbx fork's DirectSound HLE.

    python tools/ds3d_instrument.py            # apply
    python tools/ds3d_instrument.py --check    # report whether each edit is present

WHY
---
3D positional sound effects are silent while 2D effects and music play. The code
audit found a "by construction" candidate: HybridDirectSoundBuffer_SetMixBinVolumes_8
scores only mixbins < XDSMIXBIN_SPEAKERS_MAX (6) for its "dominant volume", but a
CTRL3D voice's default table is {6,8,7,9,10}, so the fold yields DSBVOLUME_MIN
whatever the title sent, stores it in Xb_VolumeMixbin, and SetVolume re-adds it on
every later call. Content-independent, and it is exactly the 2D-works/3D-silent split.

That is a reading of the code. This turns it into a measurement:
  DSPLAY  - at every Play: host volume/frequency/status/3D-mode READ BACK from the host
            object, plus Xb_VolumeMixbin and the voice's mixbin table.
  DSMIX   - inside the fold: pairs in, table present, maxVolume out, the SetVolume made.
  DS3D    - whether Set3DVoiceData / Use3DVoiceData / Calculate3D / GetVoiceData are
            reached at all, and with what. LOG_UNIMPLEMENTED goes through EmuLog,
            which LoggedModules = 0x0 silences, so today these leave no trace.

All output is printf (reaches diagnostics.txt) and capped, because this title makes
thousands of audio calls per minute. No behaviour is changed.

Idempotent: each edit is keyed on an exact anchor and a marker comment; re-running
reports "already applied". Written as a script rather than a shell heredoc because
a 130-line heredoc with mixed quotes broke on shell quoting - the third time that
has happened in this project.
"""
import io
import os
import sys

ROOT = r"C:\Users\<you>\SWBeta\src\Cxbx-Reloaded\src\core\hle\DSOUND\DirectSound"
NL = "\\n"  # the two characters backslash-n, for C format strings


def read(name):
    return io.open(os.path.join(ROOT, name), encoding="utf-8", errors="surrogateescape").read()


def write(name, text):
    io.open(os.path.join(ROOT, name), "w", encoding="utf-8", errors="surrogateescape").write(text)


EDITS = []  # (file, marker, anchor, replacement)


def edit(file, marker, anchor, replacement):
    EDITS.append((file, marker, anchor, replacement))


# --------------------------------------------------------------- DirectSoundBuffer.cpp
PLAY_ANCHOR = (
    '            printf("DSOUND:   -> hRet=0x%08X hostBuf=%p bytes=%u playFlags=0x%X emuFlags=0x%X' + NL + '",\n'
    '                (unsigned)hRet,\n'
    '                (void *)pThis->EmuDirectSoundBuffer8,\n'
    '                (unsigned)pThis->EmuBufferDesc.dwBufferBytes,\n'
    '                (unsigned)pThis->EmuPlayFlags,\n'
    '                (unsigned)pThis->EmuFlags);\n'
    '            fflush(stdout);\n'
    '        }\n'
    '    }\n'
)
PLAY_ADD = PLAY_ANCHOR + '''
    // DSPLAY silence detector. Every Play so far returned DS_OK with real audio in
    // the buffer, so an inaudible sound is explained by the host object's state at
    // the moment it starts - not by the call succeeding. Read that state BACK from
    // the host rather than reporting what we believe we set. Xb_VolumeMixbin is the
    // mixbin "dominant volume" SetVolume adds on every call; for a buffer whose
    // table is the 3D default {6,8,7,9,10}, the fold that computes it ignores every
    // bin >= 6 and yields DSBVOLUME_MIN.
    {
        static unsigned s_Detect = 0;
        if (++s_Detect <= 200 && pThis->EmuDirectSoundBuffer8 != nullptr) {
            LONG HostVolume = 0x7FFFFFFF; DWORD HostFreq = 0, HostStatus = 0, Mode3D = 0xFFFFFFFF;
            pThis->EmuDirectSoundBuffer8->GetVolume(&HostVolume);
            pThis->EmuDirectSoundBuffer8->GetFrequency(&HostFreq);
            pThis->EmuDirectSoundBuffer8->GetStatus(&HostStatus);
            if (pThis->EmuDirectSound3DBuffer8 != nullptr) {
                pThis->EmuDirectSound3DBuffer8->GetMode(&Mode3D);
            }
            char Bins[160]; size_t Used = 0; Bins[0] = 0;
            for (unsigned i = 0; i < pThis->Xb_VoiceProperties.dwMixBinCount && i < 8 && Used < sizeof(Bins) - 24; ++i) {
                Used += (size_t)std::snprintf(Bins + Used, sizeof(Bins) - Used, "%s%u:%ld", i ? " " : "",
                    (unsigned)pThis->Xb_VoiceProperties.MixBinVolumePairs[i].dwMixBin,
                    (long)pThis->Xb_VoiceProperties.MixBinVolumePairs[i].lVolume);
            }
            printf("DSPLAY: buffer %p xbFlags=0x%08X ctrl3d=%d hostFlags=0x%08X volMixbin=%ld hostVol=%ld hostFreq=%u status=0x%X mode3d=%u mixbins[%u]={%s}''' + NL + '''",
                (void *)pHybridThis, (unsigned)pThis->Xb_Flags, (int)((pThis->Xb_Flags & 0x10) != 0),
                (unsigned)pThis->EmuBufferDesc.dwFlags, (long)pThis->Xb_VolumeMixbin, (long)HostVolume,
                (unsigned)HostFreq, (unsigned)HostStatus, (unsigned)Mode3D,
                (unsigned)pThis->Xb_VoiceProperties.dwMixBinCount, Bins);
            fflush(stdout);
        }
    }
'''
edit("DirectSoundBuffer.cpp", "DSPLAY silence detector", PLAY_ANCHOR, PLAY_ADD)

SET3D_ANCHOR = (
    "    LOG_FUNC_BEGIN\n"
    "        LOG_FUNC_ARG(pHybridThis)\n"
    "        LOG_FUNC_ARG(a2)\n"
    "        LOG_FUNC_END;\n"
    "\n"
    "    LOG_UNIMPLEMENTED();\n"
    "\n"
    "    RETURN(X_STATUS_SUCCESS);\n"
    "}\n"
)
SET3D_ADD = (
    "    LOG_FUNC_BEGIN\n"
    "        LOG_FUNC_ARG(pHybridThis)\n"
    "        LOG_FUNC_ARG(a2)\n"
    "        LOG_FUNC_END;\n"
    "\n"
    "    LOG_UNIMPLEMENTED();\n"
    "\n"
    "    // DS3D trace: does the title reach this, and what does it hand us? LOG_UNIMPLEMENTED\n"
    "    // goes through EmuLog, which is off in this build, so until now this was invisible.\n"
    "    {\n"
    "        static unsigned s_Seen = 0;\n"
    "        if (++s_Seen <= 40) {\n"
    "            const uint32_t *p = (const uint32_t *)(uintptr_t)a2;\n"
    "            const bool ok = (p != nullptr) && !IsBadReadPtr(p, 32);\n"
    '            printf("DS3D: Set3DVoiceData buffer %p data %p%s", (void *)pHybridThis, (void *)p, ok ? " =" : " (unreadable)");\n'
    '            if (ok) { for (int i = 0; i < 8; ++i) printf(" %08X", (unsigned)p[i]); }\n'
    '            printf("' + NL + '"); fflush(stdout);\n'
    "        }\n"
    "    }\n"
    "\n"
    "    RETURN(X_STATUS_SUCCESS);\n"
    "}\n"
)
edit("DirectSoundBuffer.cpp", "DS3D trace: does the title reach this", SET3D_ANCHOR, SET3D_ADD)

USE3D_ANCHOR = (
    "\tLOG_FUNC_BEGIN\n"
    "\t\tLOG_FUNC_ARG(pHybridThis)\n"
    "\t\tLOG_FUNC_ARG(pUnknown)\n"
    "\t\tLOG_FUNC_END;\n"
    "\n"
    "    LOG_NOT_SUPPORTED();\n"
    "\n"
    "    return DS_OK;\n"
    "}\n"
)
USE3D_ADD = (
    "\tLOG_FUNC_BEGIN\n"
    "\t\tLOG_FUNC_ARG(pHybridThis)\n"
    "\t\tLOG_FUNC_ARG(pUnknown)\n"
    "\t\tLOG_FUNC_END;\n"
    "\n"
    "    LOG_NOT_SUPPORTED();\n"
    "\n"
    "    { // DS3D trace (see Set3DVoiceData)\n"
    "        static unsigned s_Seen = 0;\n"
    "        if (++s_Seen <= 40) {\n"
    '            printf("DS3D: Use3DVoiceData buffer %p arg %p' + NL + '", (void *)pHybridThis, (void *)pUnknown);\n'
    "            fflush(stdout);\n"
    "        }\n"
    "    }\n"
    "\n"
    "    return DS_OK;\n"
    "}\n"
)
edit("DirectSoundBuffer.cpp", "DS3D trace (see Set3DVoiceData)", USE3D_ANCHOR, USE3D_ADD)

# --------------------------------------------------------------- DirectSoundInline.hpp
FOLD_ANCHOR = (
    "        Xb_volumeMixBin = maxVolume;\n"
    "        int32_t Xb_volume = Xb_Voice->GetVolume() + Xb_Voice->GetHeadroom();\n"
    "        hRet = HybridDirectSoundBuffer_SetVolume(pDSBuffer, Xb_volume, EmuFlags,\n"
    "                                                    Xb_volumeMixBin, Xb_Voice);"
)
FOLD_ADD = (
    "        Xb_volumeMixBin = maxVolume;\n"
    "        int32_t Xb_volume = Xb_Voice->GetVolume() + Xb_Voice->GetHeadroom();\n"
    "\n"
    "        // DSMIX fold trace. The fold above is the prime suspect for silent 3D voices:\n"
    "        // it scores only bins < XDSMIXBIN_SPEAKERS_MAX (6), and a CTRL3D voice's table\n"
    "        // is the 3D default {6,8,7,9,10}, so maxVolume never leaves DSBVOLUME_MIN no\n"
    "        // matter what the title sent. Report what came in, what was already there and\n"
    "        // what went out, so that becomes a measurement rather than a reading.\n"
    "        {\n"
    "            static unsigned s_Seen = 0;\n"
    "            if (++s_Seen <= 60) {\n"
    "                char In[160]; size_t u = 0; In[0] = 0;\n"
    "                for (unsigned i = 0; pMixBins != xbox::zeroptr && i < pMixBins->dwCount && i < 8 && u < sizeof(In) - 24; ++i) {\n"
    '                    u += (size_t)std::snprintf(In + u, sizeof(In) - u, "%s%u:%ld", i ? " " : "",\n'
    "                        (unsigned)pMixBins->lpMixBinVolumePairs[i].dwMixBin, (long)pMixBins->lpMixBinVolumePairs[i].lVolume);\n"
    "                }\n"
    "                char Have[160]; u = 0; Have[0] = 0;\n"
    "                for (unsigned i = 0; i < Xb_VoiceProperties.dwMixBinCount && i < 8 && u < sizeof(Have) - 24; ++i) {\n"
    '                    u += (size_t)std::snprintf(Have + u, sizeof(Have) - u, "%s%u:%ld", i ? " " : "",\n'
    "                        (unsigned)Xb_VoiceProperties.MixBinVolumePairs[i].dwMixBin, (long)Xb_VoiceProperties.MixBinVolumePairs[i].lVolume);\n"
    "                }\n"
    '                printf("DSMIX: SetMixBinVolumes in={%s} table={%s} -> maxVolume=%ld voiceVol=%ld headroom=%ld => SetVolume(%ld + %ld)' + NL + '",\n'
    "                    In, Have, (long)maxVolume, (long)Xb_Voice->GetVolume(), (long)Xb_Voice->GetHeadroom(),\n"
    "                    (long)Xb_volume, (long)Xb_volumeMixBin);\n"
    "                fflush(stdout);\n"
    "            }\n"
    "        }\n"
    "\n"
    "        hRet = HybridDirectSoundBuffer_SetVolume(pDSBuffer, Xb_volume, EmuFlags,\n"
    "                                                    Xb_volumeMixBin, Xb_Voice);"
)
edit("DirectSoundInline.hpp", "DSMIX fold trace", FOLD_ANCHOR, FOLD_ADD)

SETMIXBINS_ANCHOR = (
    "    HRESULT ret = DS_OK;\n"
    "\n"
    "    GenerateMixBinDefault(Xb_VoiceProperties, BufferDesc.lpwfxFormat, mixBins, ((BufferDesc.dwFlags & DSBCAPS_CTRL3D) > 0));\n"
    "\n"
    "    return ret;\n"
    "}"
)
SETMIXBINS_ADD = (
    "    HRESULT ret = DS_OK;\n"
    "\n"
    "    GenerateMixBinDefault(Xb_VoiceProperties, BufferDesc.lpwfxFormat, mixBins, ((BufferDesc.dwFlags & DSBCAPS_CTRL3D) > 0));\n"
    "\n"
    "    { // DSMIX SetMixBins trace: which bins did the title ask for, and what table resulted?\n"
    "        static unsigned s_Seen = 0;\n"
    "        if (++s_Seen <= 60) {\n"
    "            char Have[160]; size_t u = 0; Have[0] = 0;\n"
    "            for (unsigned i = 0; i < Xb_VoiceProperties.dwMixBinCount && i < 8 && u < sizeof(Have) - 24; ++i) {\n"
    '                u += (size_t)std::snprintf(Have + u, sizeof(Have) - u, "%s%u:%ld", i ? " " : "",\n'
    "                    (unsigned)Xb_VoiceProperties.MixBinVolumePairs[i].dwMixBin, (long)Xb_VoiceProperties.MixBinVolumePairs[i].lVolume);\n"
    "            }\n"
    '            printf("DSMIX: SetMixBins mask=0x%08X pMixBins=%p ctrl3d=%d -> table[%u]={%s}' + NL + '",\n'
    "                (unsigned)mixBins.dwMixBinMask, (void *)mixBins.pMixBins, (int)((BufferDesc.dwFlags & DSBCAPS_CTRL3D) > 0),\n"
    "                (unsigned)Xb_VoiceProperties.dwMixBinCount, Have);\n"
    "            fflush(stdout);\n"
    "        }\n"
    "    }\n"
    "\n"
    "    return ret;\n"
    "}"
)
edit("DirectSoundInline.hpp", "DSMIX SetMixBins trace", SETMIXBINS_ANCHOR, SETMIXBINS_ADD)

# --------------------------------------------------------------- DirectSound3DCalculator.cpp
CALC_ANCHOR = (
    "    LOG_FUNC_BEGIN\n"
    "        LOG_FUNC_ARG(a1)\n"
    "        LOG_FUNC_ARG(a2)\n"
    "        LOG_FUNC_END;\n"
    "\n"
    "    LOG_UNIMPLEMENTED();\n"
    "}"
)
CALC_ADD = (
    "    LOG_FUNC_BEGIN\n"
    "        LOG_FUNC_ARG(a1)\n"
    "        LOG_FUNC_ARG(a2)\n"
    "        LOG_FUNC_END;\n"
    "\n"
    "    LOG_UNIMPLEMENTED();\n"
    "\n"
    "    { // DS3D trace: is the calculator ever reached? (EmuLog is off; this is the only visible sign.)\n"
    "        static unsigned s_Seen = 0;\n"
    "        if (++s_Seen <= 40) {\n"
    '            printf("DS3D: Calculate3D a1=%08X a2=%08X' + NL + '", (unsigned)a1, (unsigned)a2);\n'
    "            fflush(stdout);\n"
    "        }\n"
    "    }\n"
    "}"
)
edit("DirectSound3DCalculator.cpp", "DS3D trace: is the calculator ever reached", CALC_ANCHOR, CALC_ADD)

GVD_ANCHOR = (
    "    LOG_FUNC_BEGIN\n"
    "        LOG_FUNC_ARG(a1)\n"
    "        LOG_FUNC_ARG(a2)\n"
    "        LOG_FUNC_ARG(a3)\n"
    "        LOG_FUNC_ARG(a4)\n"
    "        LOG_FUNC_ARG(a5)\n"
    "        LOG_FUNC_END;\n"
    "\n"
    "    LOG_UNIMPLEMENTED();\n"
    "}"
)
GVD_ADD = (
    "    LOG_FUNC_BEGIN\n"
    "        LOG_FUNC_ARG(a1)\n"
    "        LOG_FUNC_ARG(a2)\n"
    "        LOG_FUNC_ARG(a3)\n"
    "        LOG_FUNC_ARG(a4)\n"
    "        LOG_FUNC_ARG(a5)\n"
    "        LOG_FUNC_END;\n"
    "\n"
    "    LOG_UNIMPLEMENTED();\n"
    "\n"
    "    { // DS3D trace (see Calculate3D)\n"
    "        static unsigned s_Seen = 0;\n"
    "        if (++s_Seen <= 40) {\n"
    '            printf("DS3D: GetVoiceData a1=%08X a2=%08X a3=%08X a4=%08X a5=%08X' + NL + '",\n'
    "                (unsigned)a1, (unsigned)a2, (unsigned)a3, (unsigned)a4, (unsigned)a5);\n"
    "            fflush(stdout);\n"
    "        }\n"
    "    }\n"
    "}"
)
edit("DirectSound3DCalculator.cpp", "DS3D trace (see Calculate3D)", GVD_ANCHOR, GVD_ADD)

INCLUDE_ANCHOR = '#include "DirectSoundInline.hpp"'
INCLUDE_ADD = '#include <cstdio> // printf: the DS3D trace below\n#include "DirectSoundInline.hpp"'
edit("DirectSound3DCalculator.cpp", "printf: the DS3D trace below", INCLUDE_ANCHOR, INCLUDE_ADD)


def main():
    check = "--check" in sys.argv
    ok = True
    for file, marker, anchor, replacement in EDITS:
        text = read(file)
        if marker in text:
            print("  already applied : %-30s %s" % (file, marker))
            continue
        if anchor not in text:
            print("  ANCHOR MISSING  : %-30s %s" % (file, marker))
            ok = False
            continue
        if check:
            print("  would apply     : %-30s %s" % (file, marker))
            continue
        write(file, text.replace(anchor, replacement, 1))
        print("  applied         : %-30s %s" % (file, marker))
    if not ok:
        sys.exit("one or more anchors were not found - the code has moved; re-derive them")


if __name__ == "__main__":
    main()
