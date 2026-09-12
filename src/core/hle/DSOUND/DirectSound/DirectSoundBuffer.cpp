// This is an open source non-commercial project. Dear PVS-Studio, please check it.
// PVS-Studio Static Code Analyzer for C, C++ and C#: http://www.viva64.com
// ******************************************************************
// *
// *  This file is part of the Cxbx project.
// *
// *  Cxbx and Cxbe are free software; you can redistribute them
// *  and/or modify them under the terms of the GNU General Public
// *  License as published by the Free Software Foundation; either
// *  version 2 of the license, or (at your option) any later version.
// *
// *  This program is distributed in the hope that it will be useful,
// *  but WITHOUT ANY WARRANTY; without even the implied warranty of
// *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// *  GNU General Public License for more details.
// *
// *  You should have recieved a copy of the GNU General Public License
// *  along with this program; see the file COPYING.
// *  If not, write to the Free Software Foundation, Inc.,
// *  59 Temple Place - Suite 330, Bostom, MA 02111-1307, USA.
// *
// *  (c) 2002-2003 Aaron Robinson <caustik@caustik.com>
// *  (c) 2017 blueshogun96
// *  (c) 2017-2020 RadWolfie
// *
// *  All rights reserved
// *
// ******************************************************************
#define LOG_PREFIX CXBXR_MODULE::DSBUFFER


#include <core\kernel\exports\xboxkrnl.h>
#include <dsound.h>
#include "DirectSoundGlobal.hpp" // Global variables

#include "Logging.h"
#include "DirectSoundLogging.hpp"
#include "..\XbDSoundLogging.hpp"



// TODO: Tasks need to do for DirectSound HLE
// * Missing IDirectSoundBuffer patch
//   * IDirectSoundBuffer_QueryInterface (not require)
//   * IDirectSoundBuffer_QueryInterfaceC (not require)

/* NOTE: SUCCEEDED define is only checking for is equal or greater than zero value.
    And FAILED check for less than zero value. Since DS_OK is only 0 base on DirectSound documentation,
    there is chance of failure which contain value greater than 0.
 */

#include "DirectSoundInline.hpp"
#include "DirectSound3DVoice.hpp"

#include <cstdarg> // DS3D trace formatting
#include <cstdio>

// ******************************************************************
// * 3D voice data -> host buffer (volume offset, pan, pitch delta)
// ******************************************************************
// XACT builds a positional effect out of two voices: a MIXIN "sound source" (the
// parent, which never plays on the host) and the audible track voice, routed into
// the parent with SetOutputBuffer. The native calculator's output is split by XACT
// between them: the track keeps distance/cone/I3DL2/doppler (its own attenuation),
// the parent keeps the panning bits. So a buffer's volume offset and pitch delta are
// its own, and its pan is its parent's when it has one.

// The parent recorded by SetOutputBuffer, or nullptr once that buffer is gone.
static xbox::XbHybridDSBuffer* DSoundBuffer_3DOutputParent(xbox::XbHybridDSBuffer* pHybridThis)
{
    xbox::XbHybridDSBuffer* pParent = pHybridThis->emuDSBuffer->Xb_OutputParent;
    if (pParent != nullptr
        && std::find(g_pDSoundBufferCache.begin(), g_pDSoundBufferCache.end(), pParent) == g_pDSoundBufferCache.end()) {
        // ~EmuDirectSoundBuffer unlinks the children of a released parent; this is
        // the safety net for a parent that left the cache any other way.
        pHybridThis->emuDSBuffer->Xb_OutputParent = nullptr;
        pParent = nullptr;
    }
    return pParent;
}

// The pan this buffer should carry on the host: its parent's if it is routed, else its own.
static LONG DSoundBuffer_3DPanMb(xbox::XbHybridDSBuffer* pHybridThis)
{
    xbox::XbHybridDSBuffer* pParent = DSoundBuffer_3DOutputParent(pHybridThis);
    return Cxbxr3DVoice_PanMb((pParent != nullptr) ? pParent->emuDSBuffer->Xb_3D : pHybridThis->emuDSBuffer->Xb_3D);
}

// Re-apply the merged 3D state to the host buffer. The voice's own volume and pitch
// are re-sent unchanged (the same round trip HybridDirectSoundBuffer_SetMixBinVolumes_8
// makes for the volume) with the 3D terms on top.
static void DSoundBuffer_Apply3DVoiceState(xbox::XbHybridDSBuffer* pHybridThis)
{
    xbox::EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    xbox::CDirectSoundVoice* Xb_Voice = pHybridThis->p_CDSVoice;
    if (pThis->EmuDirectSoundBuffer8 == nullptr || Xb_Voice == nullptr) {
        return;
    }
    int32_t Xb_volume = Xb_Voice->GetVolume() + Xb_Voice->GetHeadroom();
    HybridDirectSoundBuffer_SetVolume(pThis->EmuDirectSoundBuffer8, Xb_volume, pThis->EmuFlags,
                                      pThis->Xb_VolumeMixbin, Xb_Voice,
                                      Cxbxr3DVoice_VolumeOffsetMb(pThis->Xb_3D));
    HybridDirectSoundBuffer_SetPan3D(pThis->EmuDirectSoundBuffer8, DSoundBuffer_3DPanMb(pHybridThis));
    HybridDirectSoundBuffer_SetPitch(pThis->EmuDirectSoundBuffer8, Xb_Voice->GetPitch(), Xb_Voice,
                                     Cxbxr3DVoice_PitchDelta(pThis->Xb_3D));
}

// A parent's pan reaches the speakers through the children routed into it.
static void DSoundBuffer_Propagate3DPan(xbox::XbHybridDSBuffer* pHybridParent)
{
    const LONG lPanMb = Cxbxr3DVoice_PanMb(pHybridParent->emuDSBuffer->Xb_3D);
    for (xbox::XbHybridDSBuffer* pHybridChild : g_pDSoundBufferCache) {
        xbox::EmuDirectSoundBuffer* pChild = pHybridChild->emuDSBuffer;
        if (pChild->Xb_OutputParent == pHybridParent && pChild->EmuDirectSoundBuffer8 != nullptr) {
            HybridDirectSoundBuffer_SetPan3D(pChild->EmuDirectSoundBuffer8, lPanMb);
        }
    }
}

// Bounded formatter for the DS3D trace lines.
static void DS3D_Append(char* Buf, size_t Cap, size_t& Used, const char* Fmt, ...)
{
    if (Used >= Cap - 1) {
        return;
    }
    va_list Args;
    va_start(Args, Fmt);
    int n = std::vsnprintf(Buf + Used, Cap - Used, Fmt, Args);
    va_end(Args);
    if (n > 0) {
        Used += (size_t)n;
        if (Used > Cap - 1) {
            Used = Cap - 1;
        }
    }
}

// ******************************************************************
// * patch: DirectSoundDoWork (buffer)
// ******************************************************************
void DirectSoundDoWork_Buffer(xbox::LARGE_INTEGER &time)
{

    vector_ds_buffer::iterator ppDSBuffer = g_pDSoundBufferCache.begin();
    for (; ppDSBuffer != g_pDSoundBufferCache.end(); ppDSBuffer++) {
        xbox::EmuDirectSoundBuffer* pThis = ((*ppDSBuffer)->emuDSBuffer);
        if (pThis->Host_lock.pLockPtr1 == nullptr || pThis->EmuBufferToggle != xbox::X_DSB_TOGGLE_DEFAULT) {
            continue;
        }
        // However there's a chance of locked buffers has been set which needs to be unlock.
        DSoundGenericUnlock(pThis->EmuFlags,
                            pThis->EmuDirectSoundBuffer8,
                            pThis->EmuBufferDesc,
                            pThis->Host_lock,
                            pThis->X_BufferCache,
                            pThis->X_lock.dwLockOffset,
                            pThis->X_lock.dwLockBytes1,
                            pThis->X_lock.dwLockBytes2);

        // TODO: Do we need this in async thread loop?
        if (pThis->Xb_rtPauseEx != 0LL && pThis->Xb_rtPauseEx <= time.QuadPart) {
            pThis->Xb_rtPauseEx = 0LL;
            pThis->EmuFlags &= ~DSE_FLAG_PAUSE;
            pThis->EmuDirectSoundBuffer8->Play(0, 0, pThis->EmuPlayFlags);
			pThis->EmuStreamingInfo.playRequested = true;
        }

        if (pThis->Xb_rtStopEx != 0LL && pThis->Xb_rtStopEx <= time.QuadPart) {
            pThis->Xb_rtStopEx = 0LL;
            pThis->EmuDirectSoundBuffer8->Stop();
        }
    }
}

// ******************************************************************
// * patch: IDirectSoundBuffer_AddRef
// ******************************************************************
xbox::ulong_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_AddRef)
(
    XbHybridDSBuffer*       pHybridThis)
{
    DSoundMutexGuardLock;

	LOG_FUNC_ONE_ARG(pHybridThis);
    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;

    ULONG uRet = HybridDirectSoundBuffer_AddRef(pThis->EmuDirectSoundBuffer8);

    return uRet;
}

// ******************************************************************
// * EmuDirectSoundBuffer destructor handler
// ******************************************************************
xbox::EmuDirectSoundBuffer::~EmuDirectSoundBuffer()
{
    if (this->EmuDirectSound3DBuffer8 != nullptr) {
        this->EmuDirectSound3DBuffer8->Release();
    }

    // 3D: a voice still routed into this buffer (SetOutputBuffer) must not keep
    // pointing at it.
    for (XbHybridDSBuffer* pHybridChild : g_pDSoundBufferCache) {
        if (pHybridChild->emuDSBuffer->Xb_OutputParent == this->pHybridThis) {
            pHybridChild->emuDSBuffer->Xb_OutputParent = nullptr;
        }
    }

    // remove cache entry
    vector_ds_buffer::iterator ppDSBuffer = std::find(g_pDSoundBufferCache.begin(), g_pDSoundBufferCache.end(), this->pHybridThis);
    if (ppDSBuffer != g_pDSoundBufferCache.end()) {
        g_pDSoundBufferCache.erase(ppDSBuffer);
    }

    if (this->EmuBufferDesc.lpwfxFormat != nullptr) {
        free(this->EmuBufferDesc.lpwfxFormat);
    }
    if (this->X_BufferCache != xbox::zeroptr && (this->EmuFlags & DSE_FLAG_BUFFER_EXTERNAL) == 0) {
        free(this->X_BufferCache);
        DSoundSGEMemDealloc(this->X_BufferCacheSize);
    }
}

// ******************************************************************
// * patch: IDirectSoundBuffer_Release
// ******************************************************************
xbox::ulong_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_Release)
(
    XbHybridDSBuffer*       pHybridThis)
{
    DSoundMutexGuardLock;

	LOG_FUNC_ONE_ARG(pHybridThis);

    ULONG uRet = 0;
    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;

    //if (!(pThis->EmuFlags & DSE_FLAG_RECIEVEDATA)) {
        uRet = pThis->EmuDirectSoundBuffer8->Release();

        if (uRet == 0) {
            size_t size = sizeof(SharedDSBuffer) - sizeof(XbHybridDSBuffer);
            SharedDSBuffer* pSharedThis = reinterpret_cast<SharedDSBuffer*>(reinterpret_cast<uint8_t*>(pHybridThis) - size);
            delete pSharedThis;
        }
    //}

    RETURN(uRet);
}

// ******************************************************************
// * patch: DirectSoundCreateBuffer
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(DirectSoundCreateBuffer)
(
    X_DSBUFFERDESC*         pdsbd,
    OUT XbHybridDSBuffer**  ppBuffer)
{
    DSoundMutexGuardLock;

    // Research reveal DirectSound creation check is part of the requirement.
    if (!g_pDSound8 && !g_bDSoundCreateCalled) {
        HRESULT hRet;

        hRet = xbox::EMUPATCH(DirectSoundCreate)(nullptr, &g_pDSound8, nullptr);
        if (hRet != DS_OK) {
            CxbxrAbort("Unable to initialize DirectSound!");
        }
    }

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pdsbd)
		LOG_FUNC_ARG_OUT(ppBuffer)
		LOG_FUNC_END;

    HRESULT hRet = DS_OK;

    //If out of space, return out of memory.
    if (X_DIRECTSOUND_CACHE_COUNT == X_DIRECTSOUND_CACHE_MAX || !DSoundSGEMenAllocCheck(pdsbd->dwBufferBytes)) {

        hRet = DSERR_OUTOFMEMORY;
        *ppBuffer = xbox::zeroptr;
    } else {

        DSBUFFERDESC DSBufferDesc = { 0 };

        //TODO: Find out the cause for DSBCAPS_MUTE3DATMAXDISTANCE to have invalid arg.
        DWORD dwAcceptableMask = 0x00000010 | 0x00000020 | 0x00000080 | 0x00000100 | 0x00020000 | 0x00040000 /*| 0x00080000*/;

        if (pdsbd->dwFlags & (~dwAcceptableMask)) {
            EmuLog(LOG_LEVEL::WARNING, "Use of unsupported pdsbd->dwFlags mask(s) (0x%.08X)", pdsbd->dwFlags & (~dwAcceptableMask));
        }

        DSBufferDesc.dwSize = sizeof(DSBUFFERDESC);
        DSBufferDesc.dwFlags = (pdsbd->dwFlags & dwAcceptableMask) | DSBCAPS_CTRLVOLUME | DSBCAPS_GETCURRENTPOSITION2 | DSBCAPS_CTRLFREQUENCY |
            (g_XBAudio.mute_on_unfocus ? 0 : DSBCAPS_GLOBALFOCUS);

        // HACK: Hot fix for titles not giving CTRL3D flag. Xbox might accept it, however the host does not.
        if ((pdsbd->dwFlags & DSBCAPS_MUTE3DATMAXDISTANCE) > 0 && (pdsbd->dwFlags & DSBCAPS_CTRL3D) == 0) {
            DSBufferDesc.dwFlags &= ~DSBCAPS_MUTE3DATMAXDISTANCE;
        }

        // TODO: Garbage Collection
        SharedDSBuffer* pBuffer = new SharedDSBuffer((DSBufferDesc.dwFlags & DSBCAPS_CTRL3D) != 0);
        XbHybridDSBuffer* pHybridBuffer = reinterpret_cast<XbHybridDSBuffer*>(&pBuffer->dsb_i);
        *ppBuffer = pHybridBuffer;
        EmuDirectSoundBuffer* pEmuBuffer = pBuffer->emuDSBuffer;
        pEmuBuffer->pHybridThis = pHybridBuffer;

        DSoundBufferSetDefault(pEmuBuffer, 0, pdsbd->dwFlags);
        pEmuBuffer->Host_lock = { 0 };
        pEmuBuffer->Xb_rtStopEx = 0LL;

        DSoundBufferRegionSetDefault(pEmuBuffer);

        // We have to set DSBufferDesc last due to EmuFlags must be either 0 or previously written value to preserve other flags.
        GeneratePCMFormat(DSBufferDesc, pdsbd->lpwfxFormat, (DWORD &)pdsbd->dwFlags, pEmuBuffer->EmuFlags, pdsbd->dwBufferBytes,
                          &pEmuBuffer->X_BufferCache, pEmuBuffer->X_BufferCacheSize, pEmuBuffer->Xb_VoiceProperties, pdsbd->mixBinsOutput,
                          pHybridBuffer->p_CDSVoice);
        pEmuBuffer->EmuBufferDesc = DSBufferDesc;

        EmuLog(LOG_LEVEL::DEBUG, "DirectSoundCreateBuffer: bytes := 0x%08X", pEmuBuffer->EmuBufferDesc.dwBufferBytes);

        hRet = DSoundBufferCreate(&DSBufferDesc, pEmuBuffer->EmuDirectSoundBuffer8);
        if (FAILED(hRet)) {
            std::stringstream output;
            output << "Xbox:\n" << pdsbd;
            output << "\nHost converison:\n" << DSBufferDesc;
            EmuLog(LOG_LEVEL::WARNING, output.str().c_str());
            output.str("");
            output << static_cast<DS_RESULT>(hRet);
            CxbxrAbort("DSB: DSoundBufferCreate error: %s", output.str().c_str());
        }
        else {
            if (pdsbd->dwFlags & DSBCAPS_CTRL3D) {
                DSound3DBufferCreate(pEmuBuffer->EmuDirectSoundBuffer8, pEmuBuffer->EmuDirectSound3DBuffer8);

                // Play positional sounds as plain 2D.
                //
                // Cxbx does not implement IDirectSound3DCalculator at all - there is
                // no Calculate3D anywhere and no patch entry for it - so a 3D voice
                // is created, filled with real audio and played successfully, but
                // never receives a position or per-speaker volumes. It then sits at
                // the default coordinates and attenuates to nothing. Measured in this
                // title: moolah pickup and UI sounds (2D) play, while footsteps,
                // attacks and impacts (3D) are silent, despite decoded peaks up to
                // full scale and every Play returning DS_OK.
                //
                // DS3DMODE_DISABLE takes the buffer out of 3D processing entirely, so
                // it mixes like an ordinary 2D sound at its own volume. Positional
                // cues are lost - everything sounds centred, with no distance
                // falloff - but an audible sound in the wrong place beats a correct
                // one nobody can hear. Implementing real 3D would mean emulating
                // listener orientation, distance rolloff and mixbin volumes.
                if (pEmuBuffer->EmuDirectSound3DBuffer8 != nullptr) {
                    pEmuBuffer->EmuDirectSound3DBuffer8->SetMode(DS3DMODE_DISABLE, DS3D_IMMEDIATE);
                }
            }

            DSoundDebugMuteFlag(pEmuBuffer->X_BufferCacheSize, pEmuBuffer->EmuFlags);

            // Pre-set volume to enforce silence if one of audio codec is disabled.
            HybridDirectSoundBuffer_SetVolume(pEmuBuffer->EmuDirectSoundBuffer8, 0L, pEmuBuffer->EmuFlags,
                pEmuBuffer->Xb_VolumeMixbin, pHybridBuffer->p_CDSVoice);

            g_pDSoundBufferCache.push_back(pHybridBuffer);
        }
    }

    LOG_FUNC_BEGIN_ARG_RESULT
        LOG_FUNC_ARG_RESULT(ppBuffer)
    LOG_FUNC_END_ARG_RESULT;

    RETURN(hRet);
}

// ******************************************************************
// * patch: IDirectSound_CreateSoundBuffer
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSound_CreateSoundBuffer)
(
    LPDIRECTSOUND8          pThis,
    X_DSBUFFERDESC*         pdsbd,
    OUT XbHybridDSBuffer**  ppBuffer,
    LPUNKNOWN               pUnkOuter)
{
    DSoundMutexGuardLock;

    LOG_FORWARD("DirectSoundCreateBuffer");

    HRESULT hRet = EMUPATCH(DirectSoundCreateBuffer)(pdsbd, ppBuffer);

    return hRet;
}

/* ------------- Sorted relative functions begin ------------------*/

// ******************************************************************
// * patch: IDirectSoundBuffer_GetCurrentPosition
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_GetCurrentPosition)
(
    XbHybridDSBuffer*           pHybridThis,
    OUT PDWORD                  pdwCurrentPlayCursor,
    OUT PDWORD                  pdwCurrentWriteCursor)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG_OUT(pdwCurrentPlayCursor)
		LOG_FUNC_ARG_OUT(pdwCurrentWriteCursor)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    xbox::hresult_xt hRet = HybridDirectSoundBuffer_GetCurrentPosition(pThis->EmuDirectSoundBuffer8, (::PDWORD)pdwCurrentPlayCursor, (::PDWORD)pdwCurrentWriteCursor, pThis->EmuFlags);

    LOG_FUNC_BEGIN_ARG_RESULT
        LOG_FUNC_ARG_RESULT(pdwCurrentPlayCursor)
        LOG_FUNC_ARG_RESULT(pdwCurrentWriteCursor)
    LOG_FUNC_END_ARG_RESULT;

    RETURN(hRet);
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetCurrentPosition
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetCurrentPosition)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwNewPosition)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwNewPosition)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    dwNewPosition = DSoundBufferGetPCMBufferSize(pThis->EmuFlags, dwNewPosition);

    // NOTE: TODO: This call *will* (by MSDN) fail on primary buffers!
    HRESULT hRet = pThis->EmuDirectSoundBuffer8->SetCurrentPosition(dwNewPosition);

    if (hRet != DS_OK) {
        EmuLog(LOG_LEVEL::WARNING, "SetCurrentPosition Failed!");
    }

    RETURN_RESULT_CHECK(hRet);
}

// ******************************************************************
// * patch: IDirectSoundBuffer_GetStatus
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_GetStatus)
(
    XbHybridDSBuffer*       pHybridThis,
    OUT LPDWORD             pdwStatus)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG_OUT(pdwStatus)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    DWORD dwStatusXbox = 0, dwStatusHost;
    HRESULT hRet = pThis->EmuDirectSoundBuffer8->GetStatus(&dwStatusHost);

    // Conversion is a requirement to xbox.
    if (hRet == DS_OK) {
        if ((pThis->EmuFlags & DSE_FLAG_PAUSE) > 0) {
            dwStatusXbox = X_DSBSTATUS_PAUSED;
        } else if ((dwStatusHost & DSBSTATUS_PLAYING) > 0) {
            dwStatusXbox = X_DSBSTATUS_PLAYING;
        }
        if ((dwStatusHost & DSBSTATUS_LOOPING) > 0) {
            dwStatusXbox |= X_DSBSTATUS_LOOPING;
        }
    }

    if (pdwStatus != xbox::zeroptr) {
        *pdwStatus = dwStatusXbox;
    } else {
        hRet = DSERR_INVALIDPARAM;
    }

    LOG_FUNC_BEGIN_ARG_RESULT
        LOG_FUNC_ARG_RESULT_TYPE(DSBSTATUS_FLAG, pdwStatus)
    LOG_FUNC_END_ARG_RESULT;

    RETURN(hRet);
}

// ******************************************************************
// * patch: IDirectSoundBuffer_GetVoiceProperties
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_GetVoiceProperties)
(
    XbHybridDSBuffer*       pHybridThis,
    OUT X_DSVOICEPROPS*     pVoiceProps)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pHybridThis)
        LOG_FUNC_ARG_OUT(pVoiceProps)
        LOG_FUNC_END;

    if (pVoiceProps == xbox::zeroptr) {
        LOG_TEST_CASE("pVoiceProps == xbox::zeroptr");
        RETURN(DS_OK);
    }

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSoundBuffer_GetVoiceProperties(pThis->Xb_VoiceProperties, pVoiceProps);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_Lock
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_Lock)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwOffset,
    dword_xt                   dwBytes,
    LPVOID*                 ppvAudioPtr1,
    LPDWORD                 pdwAudioBytes1,
    LPVOID*                 ppvAudioPtr2,
    LPDWORD                 pdwAudioBytes2,
    dword_xt                   dwFlags)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwOffset)
		LOG_FUNC_ARG(dwBytes)
		LOG_FUNC_ARG(ppvAudioPtr1)
		LOG_FUNC_ARG(pdwAudioBytes1)
		LOG_FUNC_ARG(ppvAudioPtr2)
		LOG_FUNC_ARG(pdwAudioBytes2)
		LOG_FUNC_ARG(dwFlags)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;

	// Xbox directsound doesn't require locking buffers
	// This Xbox api only exists to match PC

	if (dwOffset + dwBytes <= pThis->X_BufferCacheSize) {
		*pdwAudioBytes1 = dwBytes;
		*ppvAudioPtr1 = (PBYTE)pThis->X_BufferCache + dwOffset;
		if (ppvAudioPtr2 != nullptr) {
			*ppvAudioPtr2 = nullptr;
			*pdwAudioBytes2 = 0;
		}
	}
	else {
		*pdwAudioBytes1 = pThis->X_BufferCacheSize - dwOffset;
		*ppvAudioPtr1 = (PBYTE)pThis->X_BufferCache + dwOffset;
		if (ppvAudioPtr2 != nullptr) {
			*pdwAudioBytes2 = dwBytes - *pdwAudioBytes1;
			*ppvAudioPtr2 = (PBYTE)pThis->X_BufferCache;
		}
	}

    LOG_FUNC_BEGIN_ARG_RESULT
        LOG_FUNC_ARG_RESULT(ppvAudioPtr1)
        LOG_FUNC_ARG_RESULT(pdwAudioBytes1)
        LOG_FUNC_ARG_RESULT(ppvAudioPtr2)
        LOG_FUNC_ARG_RESULT(pdwAudioBytes2)
    LOG_FUNC_END_ARG_RESULT;

	RETURN(DS_OK);
}

// ******************************************************************
// * patch: IDirectSoundBuffer_Unlock
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_Unlock)
(
    XbHybridDSBuffer*       pHybridThis,
    LPVOID                  ppvAudioPtr1,
    dword_xt                   pdwAudioBytes1,
    LPVOID                  ppvAudioPtr2,
    dword_xt                   pdwAudioBytes2
    )
{
    // DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pHybridThis)
        LOG_FUNC_ARG(ppvAudioPtr1)
        LOG_FUNC_ARG(pdwAudioBytes1)
        LOG_FUNC_ARG(ppvAudioPtr2)
        LOG_FUNC_ARG(pdwAudioBytes2)
        LOG_FUNC_END;

	// Xbox directsound doesn't require locking buffers
	// This Xbox api only exists to match PC

    return DS_OK;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_Pause
// ******************************************************************
// Introduced in XDK 4721 revision.
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_Pause)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwPause)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwPause)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    DSoundGenericUnlock(pThis->EmuFlags,
                        pThis->EmuDirectSoundBuffer8,
                        pThis->EmuBufferDesc,
                        pThis->Host_lock,
                        pThis->X_BufferCache,
                        pThis->X_lock.dwLockOffset,
                        pThis->X_lock.dwLockBytes1,
                        pThis->X_lock.dwLockBytes2);

    HRESULT hRet = HybridDirectSoundBuffer_Pause(pThis->EmuDirectSoundBuffer8, dwPause, pThis->EmuFlags, pThis->EmuPlayFlags,
                                                 1, 0LL, pThis->Xb_rtPauseEx);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_PauseEx
// ******************************************************************
// Introduced in XDK 4721 revision.
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_PauseEx)
(
    XbHybridDSBuffer*       pHybridThis,
    REFERENCE_TIME          rtTimestamp,
    dword_xt                   dwPause)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(rtTimestamp)
		LOG_FUNC_ARG(dwPause)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSoundBuffer_Pause(pThis->EmuDirectSoundBuffer8, dwPause, pThis->EmuFlags, pThis->EmuPlayFlags,
                                                 1, rtTimestamp, pThis->Xb_rtPauseEx);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_Play
// ******************************************************************
// Where requested sound effects are lost. Effects ARE reaching Play (measured), so
// they die at one of three places: the buffer update failing before Play is reached,
// the host Play returning an error, or the host buffer holding no audio data.
static unsigned g_DSoundStat_HostPlayCalled = 0;
static unsigned g_DSoundStat_HostPlayFailed = 0;
static unsigned g_DSoundStat_UpdateFailed = 0;
static unsigned g_DSoundStat_SkippedSynch = 0;

xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_Play)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwReserved1,
    dword_xt                   dwReserved2,
    dword_xt                   dwFlags)
{
    DSoundMutexGuardLock;

    // Sound effects are silent while music plays. Music is streamed
    // (CDirectSoundStream_*); effects are in-memory buffers, and THIS is the call
    // that starts one. Counting it splits the problem cleanly in two:
    //   0 calls  -> nothing ever asks for a sound effect, so the fault is upstream
    //               in XACT / wave-bank loading, not in DirectSound at all
    //   >0 calls -> effects are requested and the loss is inside playback here
    // printf, because EmuLog is silent under LoggedModules = 0x0 and a shipped build
    // routes stdout to diagnostics.txt.
    {
        static unsigned s_PlayCount = 0;
        if (++s_PlayCount <= 20 || (s_PlayCount % 250) == 0) {
            printf("DSOUND: IDirectSoundBuffer_Play #%u (buffer %p, flags 0x%08X)\n",
                s_PlayCount, pHybridThis, (unsigned)dwFlags);
            fflush(stdout);
        }
    }

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwReserved1)
		LOG_FUNC_ARG(dwReserved2)
		LOG_FUNC_ARG(dwFlags)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    DSoundGenericUnlock(pThis->EmuFlags,
                        pThis->EmuDirectSoundBuffer8,
                        pThis->EmuBufferDesc,
                        pThis->Host_lock,
                        pThis->X_BufferCache,
                        pThis->X_lock.dwLockOffset,
                        pThis->X_lock.dwLockBytes1,
                        pThis->X_lock.dwLockBytes2);

    if (dwFlags & ~(X_DSBPLAY_LOOPING | X_DSBPLAY_FROMSTART | X_DSBPLAY_SYNCHPLAYBACK)) {
        CxbxrAbort("Unsupported Playing Flags");
    }
    pThis->EmuPlayFlags = dwFlags;

    // rewind buffer
    if ((dwFlags & X_DSBPLAY_FROMSTART)) {

        pThis->EmuPlayFlags &= ~X_DSBPLAY_FROMSTART;
    }

    HRESULT hRet = DS_OK;

    if ((dwFlags & X_DSBPLAY_SYNCHPLAYBACK) > 0) {
        //SynchPlayback flag append should only occur in HybridDirectSoundBuffer_Pause function, nothing else is able to do this.
        hRet = DSoundBufferSynchPlaybackFlagAdd(pThis->EmuFlags);
        pThis->EmuPlayFlags ^= X_DSBPLAY_SYNCHPLAYBACK;
    }

    DSoundBufferUpdate(pHybridThis, dwFlags, hRet);

    // 3D: put the merged voice data (own attenuation, parent pan, doppler) back on the
    // host object before it starts. UpdateTrackVolume runs SetMixBinVolumes right before
    // this Play, and the mixbin fold re-sends the host volume without the 3D offset.
    if (hRet == DS_OK && (pThis->Xb_3D.bUse || pThis->Xb_OutputParent != nullptr)) {
        DSoundBuffer_Apply3DVoiceState(pHybridThis);
    }

    if (hRet == DS_OK) {
        if (dwFlags & X_DSBPLAY_FROMSTART) {
            if (pThis->EmuDirectSoundBuffer8->SetCurrentPosition(0) != DS_OK) {
                EmuLog(LOG_LEVEL::WARNING, "Rewinding buffer failed!");
            }
        }
        if ((pThis->EmuFlags & DSE_FLAG_SYNCHPLAYBACK_CONTROL) == 0) {
            hRet = pThis->EmuDirectSoundBuffer8->Play(0, 0, pThis->EmuPlayFlags);
			pThis->EmuStreamingInfo.playRequested = true;
            g_DSoundStat_HostPlayCalled++;
            if (FAILED(hRet)) { g_DSoundStat_HostPlayFailed++; }
        }
        else {
            g_DSoundStat_SkippedSynch++;
        }
    }
    else {
        g_DSoundStat_UpdateFailed++;
    }

    // Report the first few outcomes in full. A sound effect that is requested but
    // never audible has to die at one of exactly three places: the buffer update
    // failing before Play is reached, the host Play returning an error, or the host
    // buffer having no audio data in it. Print enough to tell them apart.
    {
        static unsigned s_Reported = 0;
        if (++s_Reported <= 12) {
            printf("DSOUND:   -> hRet=0x%08X hostBuf=%p bytes=%u playFlags=0x%X emuFlags=0x%X\n",
                (unsigned)hRet,
                (void *)pThis->EmuDirectSoundBuffer8,
                (unsigned)pThis->EmuBufferDesc.dwBufferBytes,
                (unsigned)pThis->EmuPlayFlags,
                (unsigned)pThis->EmuFlags);
            fflush(stdout);
        }
    }

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
            LONG HostVolume = 0x7FFFFFFF, HostPan = 0x7FFFFFFF; DWORD HostFreq = 0, HostStatus = 0, Mode3D = 0xFFFFFFFF;
            pThis->EmuDirectSoundBuffer8->GetVolume(&HostVolume);
            pThis->EmuDirectSoundBuffer8->GetPan(&HostPan);
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
            printf("DSPLAY: buffer %p xbFlags=0x%08X ctrl3d=%d hostFlags=0x%08X volMixbin=%ld hostVol=%ld hostFreq=%u status=0x%X mode3d=%u mixbins[%u]={%s} hostPan=%ld off3d=%ld pan3d=%ld pitch3d=%ld use3d=%d have3d=0x%02X parent=%p\n",
                (void *)pHybridThis, (unsigned)pThis->Xb_Flags, (int)((pThis->Xb_Flags & 0x10) != 0),
                (unsigned)pThis->EmuBufferDesc.dwFlags, (long)pThis->Xb_VolumeMixbin, (long)HostVolume,
                (unsigned)HostFreq, (unsigned)HostStatus, (unsigned)Mode3D,
                (unsigned)pThis->Xb_VoiceProperties.dwMixBinCount, Bins,
                (long)HostPan, (long)Cxbxr3DVoice_VolumeOffsetMb(pThis->Xb_3D), (long)DSoundBuffer_3DPanMb(pHybridThis),
                (long)Cxbxr3DVoice_PitchDelta(pThis->Xb_3D), (int)pThis->Xb_3D.bUse, (unsigned)pThis->Xb_3D.dwHave,
                (void *)pThis->Xb_OutputParent);
            fflush(stdout);
        }
    }

    RETURN_RESULT_CHECK(hRet);
}

// ******************************************************************
// * patch: IDirectSoundBuffer_PlayEx
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_PlayEx)
(
    XbHybridDSBuffer*   pThis,
    REFERENCE_TIME        rtTimeStamp,
    dword_xt                 dwFlags)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(rtTimeStamp)
		LOG_FUNC_ARG(dwFlags)
		LOG_FUNC_END;

    //TODO: Need implement support for rtTimeStamp.
    if (rtTimeStamp != 0) {
        EmuLog(LOG_LEVEL::WARNING, "Not implemented for rtTimeStamp greater than 0 of %08d", rtTimeStamp);
    }

    HRESULT hRet = xbox::EMUPATCH(IDirectSoundBuffer_Play)(pThis, xbox::zero, xbox::zero, dwFlags);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetAllParameters
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetAllParameters)
(
    XbHybridDSBuffer*       pHybridThis,
    X_DS3DBUFFER*            pc3DBufferParameters,
    dword_xt                    dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pc3DBufferParameters)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSound3DBuffer_SetAllParameters(pThis->EmuDirectSound3DBuffer8, pc3DBufferParameters, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetBufferData
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetBufferData)
(
    XbHybridDSBuffer*       pHybridThis,
    LPVOID                  pvBufferData,
    dword_xt                   dwBufferBytes)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pHybridThis)
        LOG_FUNC_ARG(pvBufferData)
        LOG_FUNC_ARG(dwBufferBytes)
        LOG_FUNC_END;


    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    // Release old buffer if exists, this is needed in order to set lock pointer buffer to nullptr.
    if (pThis->Host_lock.pLockPtr1 != nullptr) {

        DSoundGenericUnlock(pThis->EmuFlags,
                            pThis->EmuDirectSoundBuffer8,
                            pThis->EmuBufferDesc,
                            pThis->Host_lock,
                            xbox::zeroptr,
                            pThis->X_lock.dwLockOffset,
                            pThis->X_lock.dwLockBytes1,
                            pThis->X_lock.dwLockBytes2);
    }

    HRESULT hRet = DSERR_OUTOFMEMORY;
    DWORD dwStatus;

    // stop and force wait until the buffer has stopped playing.
    pThis->EmuDirectSoundBuffer8->Stop();
    pThis->EmuDirectSoundBuffer8->GetStatus(&dwStatus);
    while ((dwStatus & DSBSTATUS_PLAYING) != 0) {
        // TODO: Add a timeout, like on xbox
        SwitchToThread();
        pThis->EmuDirectSoundBuffer8->GetStatus(&dwStatus);
    }

    //TODO: Current workaround method since dwBufferBytes do set to zero. Otherwise it will produce lock error message.
    if (dwBufferBytes == 0) {

        return DS_OK;
    }

    // Allocate memory whenever made request internally
    if (pvBufferData == xbox::zeroptr && DSoundSGEMenAllocCheck(dwBufferBytes)) {

        // Confirmed it perform a reset to default.
        DSoundBufferRegionSetDefault(pThis);

        GenerateXboxBufferCache(pThis->EmuBufferDesc, pThis->EmuFlags, dwBufferBytes, &pThis->X_BufferCache, pThis->X_BufferCacheSize);

        // Copy if given valid pointer.
        memcpy_s(pThis->X_BufferCache, pThis->X_BufferCacheSize, pvBufferData, dwBufferBytes);

        pThis->EmuFlags  ^= DSE_FLAG_BUFFER_EXTERNAL;

        DSoundDebugMuteFlag(pThis->X_BufferCacheSize, pThis->EmuFlags);

        // Only perform a resize, for lock emulation purpose.
        DSoundBufferResizeSetSize(pHybridThis, hRet, dwBufferBytes);

    } else if (pvBufferData != xbox::zeroptr) {
        // Free internal buffer cache if exist
        if ((pThis->EmuFlags & DSE_FLAG_BUFFER_EXTERNAL) == 0) {
            free(pThis->X_BufferCache);
            DSoundSGEMemDealloc(pThis->X_BufferCacheSize);
        }
        pThis->X_BufferCache = pvBufferData;
        pThis->X_BufferCacheSize = dwBufferBytes;
        pThis->EmuFlags |= DSE_FLAG_BUFFER_EXTERNAL;

        DSoundDebugMuteFlag(pThis->X_BufferCacheSize, pThis->EmuFlags);

        // Only perform a resize, for lock emulation purpose.
        DSoundBufferResizeSetSize(pHybridThis, hRet, dwBufferBytes);

    }

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetConeAngles
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetConeAngles)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwInsideConeAngle,
    dword_xt                   dwOutsideConeAngle,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwInsideConeAngle)
		LOG_FUNC_ARG(dwOutsideConeAngle)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSound3DBuffer_SetConeAngles(pThis->EmuDirectSound3DBuffer8, dwInsideConeAngle, dwOutsideConeAngle, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetConeOrientation
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetConeOrientation)
(
    XbHybridDSBuffer*       pHybridThis,
    float_xt                   x,
    float_xt                   y,
    float_xt                   z,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(x)
		LOG_FUNC_ARG(y)
		LOG_FUNC_ARG(z)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSound3DBuffer_SetConeOrientation(pThis->EmuDirectSound3DBuffer8, x, y, z, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetConeOutsideVolume
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetConeOutsideVolume)
(
    XbHybridDSBuffer*       pHybridThis,
    long_xt                    lConeOutsideVolume,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(lConeOutsideVolume)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSound3DBuffer_SetConeOutsideVolume(pThis->EmuDirectSound3DBuffer8, lConeOutsideVolume, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetDistanceFactor
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetDistanceFactor)
(
    XbHybridDSBuffer*       pHybridThis,
    float_xt                   flDistanceFactor,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(flDistanceFactor)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DListener_SetDistanceFactor(g_pDSoundPrimary3DListener8, flDistanceFactor, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetDopplerFactor
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetDopplerFactor)
(
    XbHybridDSBuffer*       pThis,
    float_xt                   flDopplerFactor,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(flDopplerFactor)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DListener_SetDopplerFactor(g_pDSoundPrimary3DListener8, flDopplerFactor, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetEG
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetEG)
(
    XbHybridDSBuffer*       pHybridThis,
    X_DSENVOLOPEDESC*       pEnvelopeDesc)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pEnvelopeDesc)
		LOG_FUNC_END;

    // NOTE: DSP relative function

    LOG_NOT_SUPPORTED();

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    pThis->Xb_EnvolopeDesc = *pEnvelopeDesc;

    return S_OK;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetFilter
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetFilter)
(
    XbHybridDSBuffer*       pHybridThis,
    X_DSFILTERDESC*         pFilterDesc)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pFilterDesc)
		LOG_FUNC_END;

    // NOTE: DSP relative function

    LOG_NOT_SUPPORTED();

    return S_OK;
}

// +s
// ******************************************************************
// * patch: IDirectSoundBuffer_SetFormat
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetFormat)
(
    XbHybridDSBuffer*       pHybridThis,
    LPCWAVEFORMATEX         pwfxFormat)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pwfxFormat)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSoundBuffer_SetFormat(pThis->EmuDirectSoundBuffer8, pwfxFormat, pThis->Xb_Flags,
                                                     pThis->EmuBufferDesc, pThis->EmuFlags,
                                                     pThis->EmuPlayFlags, pThis->EmuDirectSound3DBuffer8,
                                                     0, pThis->X_BufferCache, pThis->X_BufferCacheSize,
                                                     pThis->Xb_VoiceProperties, xbox::zeroptr, pHybridThis->p_CDSVoice);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetFrequency
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetFrequency)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwFrequency)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwFrequency)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSoundBuffer_SetFrequency(pThis->EmuDirectSoundBuffer8, dwFrequency,
                                                        pHybridThis->p_CDSVoice);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetHeadroom
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetHeadroom)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwHeadroom)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwHeadroom)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSoundBuffer_SetHeadroom(pThis->EmuDirectSoundBuffer8, dwHeadroom,
                                                       pThis->Xb_VolumeMixbin, pThis->EmuFlags,
                                                       pHybridThis->p_CDSVoice);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetI3DL2Source
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetI3DL2Source)
(
    XbHybridDSBuffer*       pHybridThis,
    X_DSI3DL2BUFFER*        pds3db,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pds3db)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    // NOTE: SetI3DL2Source is using DSFXI3DL2Reverb structure, aka different interface.

    // NOTE: DSP relative function

    LOG_NOT_SUPPORTED();

    return DS_OK;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetLFO
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetLFO) //Low Frequency Oscillators
(
    XbHybridDSBuffer*       pHybridThis,
    LPCDSLFODESC            pLFODesc)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pLFODesc)
		LOG_FUNC_END;

    // NOTE: DSP relative function

    LOG_NOT_SUPPORTED();

    return S_OK;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetLoopRegion
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetLoopRegion)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwLoopStart,
    dword_xt                   dwLoopLength)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwLoopStart)
		LOG_FUNC_ARG(dwLoopLength)
		LOG_FUNC_END;

    // TODO: Ensure that 4627 & 4361 are intercepting far enough back
    // (otherwise pThis is manipulated!)

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    pThis->EmuRegionLoopStartOffset = dwLoopStart;
    pThis->EmuRegionLoopLength = dwLoopLength;
    pThis->EmuBufferToggle = X_DSB_TOGGLE_LOOP;

    DWORD dwStatus;
    pThis->EmuDirectSoundBuffer8->GetStatus(&dwStatus);
    HRESULT hRet;

    if ((dwStatus & DSBSTATUS_PLAYING) > 0) {
        if ((dwStatus & DSBSTATUS_LOOPING) > 0) {
            pThis->EmuDirectSoundBuffer8->Stop();
            DSoundBufferUpdate(pHybridThis, pThis->EmuPlayFlags, hRet);
            pThis->EmuDirectSoundBuffer8->SetCurrentPosition(0);
            pThis->EmuDirectSoundBuffer8->Play(0, 0, pThis->EmuPlayFlags);
        }
    }

    return DS_OK;
}

// s+
// ******************************************************************
// * patch: IDirectSoundBuffer_SetMaxDistance
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetMaxDistance)
(
    XbHybridDSBuffer*       pHybridThis,
    float_xt                   flMaxDistance,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(flMaxDistance)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSound3DBuffer_SetMaxDistance(pThis->EmuDirectSound3DBuffer8, flMaxDistance, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetMinDistance
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetMinDistance)
(
    XbHybridDSBuffer*       pHybridThis,
    float_xt                   flMinDistance,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(flMinDistance)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSound3DBuffer_SetMinDistance(pThis->EmuDirectSound3DBuffer8, flMinDistance, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetMixBins
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetMixBins)
(
    XbHybridDSBuffer*       pHybridThis,
    X_DSMIXBINBUNION  mixBins)
{
    DSoundMutexGuardLock;

    if (g_LibVersion_DSOUND < 4039) {
        LOG_FUNC_BEGIN
            LOG_FUNC_ARG(pHybridThis)
            LOG_FUNC_ARG(mixBins.dwMixBinMask)
            LOG_FUNC_END;
    }
    else {
        LOG_FUNC_BEGIN
            LOG_FUNC_ARG(pHybridThis)
            LOG_FUNC_ARG(mixBins.pMixBins)
            LOG_FUNC_END;
    }

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    return HybridDirectSoundBuffer_SetMixBins(pThis->Xb_VoiceProperties, mixBins, pThis->EmuBufferDesc);
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetMixBinVolumes_12
// ******************************************************************
// This revision API was used in XDK 3911 until API had changed in XDK 4039.
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetMixBinVolumes_12)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwMixBinMask,
    const long_xt*             alVolumes)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pHybridThis)
        LOG_FUNC_ARG(dwMixBinMask)
        LOG_FUNC_ARG(alVolumes)
        LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSoundBuffer_SetMixBinVolumes_12(pThis->EmuDirectSoundBuffer8, dwMixBinMask, alVolumes, pThis->Xb_VoiceProperties,
                                                              pThis->EmuFlags, pThis->Xb_VolumeMixbin, pHybridThis->p_CDSVoice);

    // The fold re-sent the host volume without the 3D offset; put it back.
    if (pThis->Xb_3D.bUse || pThis->Xb_OutputParent != nullptr) {
        DSoundBuffer_Apply3DVoiceState(pHybridThis);
    }

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetMixBinVolumes_8
// ******************************************************************
// This revision is only used in XDK 4039 and higher.
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetMixBinVolumes_8)
(
    XbHybridDSBuffer*       pHybridThis,
    X_LPDSMIXBINS           pMixBins)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pMixBins)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSoundBuffer_SetMixBinVolumes_8(pThis->EmuDirectSoundBuffer8, pMixBins, pThis->Xb_VoiceProperties,
                                                              pThis->EmuFlags, pThis->Xb_VolumeMixbin, pHybridThis->p_CDSVoice);

    // The fold re-sent the host volume without the 3D offset; put it back.
    if (pThis->Xb_3D.bUse || pThis->Xb_OutputParent != nullptr) {
        DSoundBuffer_Apply3DVoiceState(pHybridThis);
    }

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetMode
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetMode)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwMode,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwMode)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSound3DBuffer_SetMode(pThis->EmuDirectSound3DBuffer8, dwMode, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetNotificationPositions
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetNotificationPositions)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwNotifyCount,
    LPCDSBPOSITIONNOTIFY    paNotifies)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwNotifyCount)
		LOG_FUNC_ARG(paNotifies)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = DSERR_INVALIDPARAM;

    // If we have a valid buffer, query a PC IDirectSoundNotify pointer and
    // use the paramaters as is since they are directly compatible, then release
    // the pointer. Any buffer that uses this *MUST* be created with the
    // DSBCAPS_CTRLPOSITIONNOTIFY flag!

    IDirectSoundNotify* pNotify = nullptr;

    if (pThis) {
        if (pThis->EmuDirectSoundBuffer8) {
            hRet = pThis->EmuDirectSoundBuffer8->QueryInterface(IID_IDirectSoundNotify8, (LPVOID*)&pNotify);
            if (hRet == DS_OK) {
                hRet = pNotify->SetNotificationPositions(dwNotifyCount, paNotifies);
                if (hRet != DS_OK) {
                    EmuLog(LOG_LEVEL::WARNING, "Could not set notification position(s)!");
                }

                pNotify->Release();
            } else {
                EmuLog(LOG_LEVEL::WARNING, "Could not create notification interface!");
            }
        }
    }

    RETURN_RESULT_CHECK(hRet);
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetOutputBuffer
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetOutputBuffer)
(
    XbHybridDSBuffer*   pHybridThis,
    XbHybridDSBuffer*   pOutputBuffer)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pOutputBuffer)
		LOG_FUNC_END;

    // NOTE: SetOutputBuffer is not possible in PC's DirectSound due to 3D controller requirement on ouput buffer to work simultaneously.
    // Test case: MultiPass sample
    // Best to emulate this LLE instead of HLE.

    LOG_NOT_SUPPORTED(); // the host-side routing; the bookkeeping below is what this title needs from it

    // XACT routes every track voice into its "sound source" - a MIXIN submix that never
    // plays on the host - with this call, and that submix is where the calculator's
    // panning data lands (Set3DVoiceData). Remember the parent and take its pan now;
    // a NULL parent un-routes. Only a buffer we created is ever dereferenced.
    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    XbHybridDSBuffer* pOldParent = pThis->Xb_OutputParent;
    XbHybridDSBuffer* pNewParent = pOutputBuffer;
    if (pNewParent != nullptr
        && std::find(g_pDSoundBufferCache.begin(), g_pDSoundBufferCache.end(), pNewParent) == g_pDSoundBufferCache.end()) {
        pNewParent = nullptr;
    }
    pThis->Xb_OutputParent = pNewParent;
    if ((pNewParent != nullptr || pOldParent != nullptr) && pThis->EmuDirectSoundBuffer8 != nullptr) {
        HybridDirectSoundBuffer_SetPan3D(pThis->EmuDirectSoundBuffer8, DSoundBuffer_3DPanMb(pHybridThis));
    }

    { // DS3D trace
        static unsigned s_Seen = 0;
        if (++s_Seen <= 40) {
            printf("DS3D: SetOutputBuffer buffer %p parent %p%s -> pan=%ld\n",
                (void *)pHybridThis, (void *)pOutputBuffer,
                (pOutputBuffer != nullptr && pNewParent == nullptr) ? " (not a cached buffer, ignored)" : "",
                (long)DSoundBuffer_3DPanMb(pHybridThis));
            fflush(stdout);
        }
    }

    return S_OK;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetPitch
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetPitch)
(
    XbHybridDSBuffer*       pHybridThis,
    long_xt                    lPitch)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(lPitch)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSoundBuffer_SetPitch(pThis->EmuDirectSoundBuffer8, lPitch,
                                                    pHybridThis->p_CDSVoice,
                                                    Cxbxr3DVoice_PitchDelta(pThis->Xb_3D));

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetPlayRegion
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetPlayRegion)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt                   dwPlayStart,
    dword_xt                   dwPlayLength)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(dwPlayStart)
		LOG_FUNC_ARG(dwPlayLength)
		LOG_FUNC_END;

    // TODO: Ensure that 4627 & 4361 are intercepting far enough back
    // (otherwise pThis is manipulated!)

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    pThis->EmuBufferToggle = X_DSB_TOGGLE_PLAY;
    pThis->EmuRegionPlayStartOffset = dwPlayStart;
    pThis->EmuRegionPlayLength = dwPlayLength;

    // Confirmed play region do overwrite loop region value.
    pThis->EmuRegionLoopStartOffset = 0;
    pThis->EmuRegionLoopLength = 0;

    DWORD dwStatus;
    pThis->EmuDirectSoundBuffer8->GetStatus(&dwStatus);
    HRESULT hRet;

    if ((dwStatus & DSBSTATUS_PLAYING) > 0) {
        if ((dwStatus & DSBSTATUS_LOOPING) == 0) {
            pThis->EmuDirectSoundBuffer8->Stop();
            DSoundBufferUpdate(pHybridThis, pThis->EmuPlayFlags, hRet);
            pThis->EmuDirectSoundBuffer8->SetCurrentPosition(0);
            pThis->EmuDirectSoundBuffer8->Play(0, 0, pThis->EmuPlayFlags);
        }
    }

    return DS_OK;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetPosition
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetPosition)
(
    XbHybridDSBuffer*       pHybridThis,
    float_xt                   x,
    float_xt                   y,
    float_xt                   z,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(x)
		LOG_FUNC_ARG(y)
		LOG_FUNC_ARG(z)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSound3DBuffer_SetPosition(pThis->EmuDirectSound3DBuffer8, x, y, z, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetRolloffCurve
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetRolloffCurve)
(
    XbHybridDSBuffer*       pHybridThis,
    const float_xt*            pflPoints,
    dword_xt                   dwPointCount,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pflPoints)
		LOG_FUNC_ARG(dwPointCount)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    // NOTE: Individual 3D buffer function.

    LOG_NOT_SUPPORTED();

    return DS_OK;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetRolloffFactor
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetRolloffFactor)
(
    XbHybridDSBuffer*       pHybridThis,
    float_xt                   flRolloffFactor,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(flRolloffFactor)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    // NOTE: SetRolloffFactor is only supported for host primary buffer's 3D Listener.

    LOG_UNIMPLEMENTED();

    return DS_OK;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetVelocity
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetVelocity)
(
    XbHybridDSBuffer*       pHybridThis,
    float_xt                   x,
    float_xt                   y,
    float_xt                   z,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(x)
		LOG_FUNC_ARG(y)
		LOG_FUNC_ARG(z)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSound3DBuffer_SetVelocity(pThis->EmuDirectSound3DBuffer8, x, y, z, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_SetVolume
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_SetVolume)
(
    XbHybridDSBuffer*       pHybridThis,
    long_xt                    lVolume)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(lVolume)
		LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = HybridDirectSoundBuffer_SetVolume(pThis->EmuDirectSoundBuffer8, lVolume, pThis->EmuFlags,
                                                     pThis->Xb_VolumeMixbin, pHybridThis->p_CDSVoice,
                                                     Cxbxr3DVoice_VolumeOffsetMb(pThis->Xb_3D));

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_Stop
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_Stop)
(
    XbHybridDSBuffer*       pHybridThis)
{
    DSoundMutexGuardLock;

	LOG_FUNC_ONE_ARG(pHybridThis);

    HRESULT hRet = D3D_OK;

    if (pHybridThis != nullptr) {
        // TODO : Test Stop (emulated via Stop + SetCurrentPosition(0)) :
        EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
        hRet = pThis->EmuDirectSoundBuffer8->Stop();
        pThis->EmuDirectSoundBuffer8->SetCurrentPosition(0);
    }

    RETURN_RESULT_CHECK(hRet);
}

// ******************************************************************
// * patch: IDirectSoundBuffer_StopEx
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_StopEx)
(
    XbHybridDSBuffer*       pHybridThis,
    REFERENCE_TIME          rtTimeStamp,
    dword_xt                   dwFlags)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pHybridThis)
        LOG_FUNC_ARG(rtTimeStamp)
        LOG_FUNC_ARG(dwFlags)
        LOG_FUNC_END;

    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    HRESULT hRet = DS_OK;

    //TODO: RadWolfie - Rayman 3 crash at end of first intro for this issue...
    // if only return DS_OK, then it works fine until end of 2nd intro it crashed.

    // Do not allow to process - Xbox reject it.
    if (dwFlags > X_DSBSTOPEX_ALL) {
        hRet = DSERR_INVALIDPARAM;

    // Only flags can be process here.
    } else {
        if (dwFlags == X_DSBSTOPEX_IMMEDIATE) {
            hRet = pThis->EmuDirectSoundBuffer8->Stop();
            pThis->Xb_rtStopEx = 0LL;
        }
        else if(dwFlags & X_DSBSTOPEX_ENVELOPE) {
            bool isLooping = pThis->EmuPlayFlags & X_DSBPLAY_LOOPING;

            double releaseSamples = pThis->Xb_EnvolopeDesc.dwRelease * 512.0;

            if (rtTimeStamp == 0LL) {
                xbox::LARGE_INTEGER getTime;
                xbox::KeQuerySystemTime(&getTime);
                pThis->Xb_rtStopEx = getTime.QuadPart;
            }
            else {
                pThis->Xb_rtStopEx = rtTimeStamp;
            }
            const double samplesToTicks = 10000000 / 48000.0;
            xbox::REFERENCE_TIME releaseTicks = static_cast<xbox::REFERENCE_TIME>(releaseSamples * samplesToTicks);
            pThis->Xb_rtStopEx += releaseTicks;

            DWORD currentPos, dwStatus;
            pThis->EmuDirectSoundBuffer8->GetStatus(&dwStatus);

            if (pThis->EmuBufferToggle != X_DSB_TOGGLE_DEFAULT) {

                pThis->EmuDirectSoundBuffer8->GetCurrentPosition(nullptr, &currentPos);
                hRet = pThis->EmuDirectSoundBuffer8->Stop();

                // Determine the range of bytes we need to play
                // Test case: Outrun 2006 - converting large buffers tanks the FPS
                // Is set within DSoundBufferRegionCurrentLocation function
                DWORD bufferRangeStart;
                DWORD bufferRangeSize;
                DSoundBufferRegionCurrentLocation(pHybridThis, pThis->EmuPlayFlags, bufferRangeStart, bufferRangeSize);

                if (pThis->EmuBufferToggle == X_DSB_TOGGLE_LOOP) {
                    // if we are to release from loop region, then we need change the size to end of actual buffer cache.
                    if (dwFlags & X_DSBSTOPEX_RELEASEWAVEFORM) {
                        bufferRangeSize = pThis->X_BufferCacheSize - bufferRangeStart;
                    }
                }

                DSoundBufferResizeUpdate(pHybridThis, pThis->EmuPlayFlags, hRet, bufferRangeStart, bufferRangeSize);

                pThis->EmuBufferToggle = X_DSB_TOGGLE_DEFAULT;
                pThis->EmuDirectSoundBuffer8->SetCurrentPosition(currentPos);
            }

            if (dwFlags & X_DSBSTOPEX_RELEASEWAVEFORM) {
                // Release from loop region.
                pThis->EmuPlayFlags &= ~X_DSBPLAY_LOOPING;
            }

            if (dwStatus & DSBSTATUS_PLAYING && rtTimeStamp != 0LL) {
                pThis->EmuDirectSoundBuffer8->Play(0, 0, pThis->EmuPlayFlags);
            }
            else {
                pThis->EmuDirectSoundBuffer8->Stop();
                pThis->Xb_rtStopEx = 0LL;
            }
        }
        else {
            LOG_TEST_CASE("Expected X_DSBSTOPEX_ENVELOPE");
        }
    }

    return hRet;
}

// ******************************************************************
// * patch:  IDirectSoundBuffer_Set3DVoiceData
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_Set3DVoiceData)
(
    XbHybridDSBuffer*       pHybridThis,
    dword_xt a2
)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pHybridThis)
        LOG_FUNC_ARG(a2)
        LOG_FUNC_END;

    // XACT hands us the native calculator's output (X_DS3DCALCVOICEDATA, 0x38 bytes),
    // already masked per voice kind: 0xDC on the MIXIN parent (panning), 0x33 on the
    // audible track voice (attenuation + doppler). Merge the flagged fields and put the
    // result on the host: own volume offset / pitch delta here, pan on the children.
    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    const X_DS3DCALCVOICEDATA* pData = (const X_DS3DCALCVOICEDATA*)(uintptr_t)a2;
    const bool bReadable = (pData != nullptr) && !IsBadReadPtr(pData, sizeof(X_DS3DCALCVOICEDATA));
    if (bReadable) {
        Cxbxr3DVoice_Merge(pThis->Xb_3D, pData);
    }
    DSoundBuffer_Apply3DVoiceState(pHybridThis);
    DSoundBuffer_Propagate3DPan(pHybridThis);

    // DS3D trace: the proof that the native calculator produces data, and what the
    // host makes of it. printf because EmuLog is off in this build.
    {
        static unsigned s_Seen = 0;
        if (++s_Seen <= 60) {
            char Fields[256]; size_t Used = 0; Fields[0] = 0;
            const unsigned mask = bReadable ? (unsigned)(pData->dwChangeMask & 0xFF) : 0u;
            if (mask & X_DS3DVOICEDATA_DISTANCE)  { DS3D_Append(Fields, sizeof(Fields), Used, " dist=%ld", (long)pData->lDistanceVolume); }
            if (mask & X_DS3DVOICEDATA_CONE)      { DS3D_Append(Fields, sizeof(Fields), Used, " cone=%ld", (long)pData->lConeVolume); }
            if (mask & X_DS3DVOICEDATA_FRONTREAR) { DS3D_Append(Fields, sizeof(Fields), Used, " front=%ld rear=%ld", (long)pData->lFrontVolume, (long)pData->lRearVolume); }
            if (mask & X_DS3DVOICEDATA_CENTER)    { DS3D_Append(Fields, sizeof(Fields), Used, " lr=%ld ctr=%ld", (long)pData->lLeftRightVolume, (long)pData->lCenterVolume); }
            if (mask & X_DS3DVOICEDATA_I3DL2)     { DS3D_Append(Fields, sizeof(Fields), Used, " direct=%ld reverb=%ld", (long)pData->lDirectVolume, (long)pData->lReverbVolume); }
            if (mask & X_DS3DVOICEDATA_DOPPLER)   { DS3D_Append(Fields, sizeof(Fields), Used, " doppler=%ld", (long)pData->lDopplerPitch); }
            if (mask & X_DS3DVOICEDATA_FIRFILTER) { DS3D_Append(Fields, sizeof(Fields), Used, " az=%.1f el=%.1f", (double)pData->flFIRFilterAzimuth, (double)pData->flFIRFilterElevation); }
            if (mask & X_DS3DVOICEDATA_IIRFILTER) { DS3D_Append(Fields, sizeof(Fields), Used, " iir=%08X/%08X", (unsigned)pData->dwIIRFilterDirect, (unsigned)pData->dwIIRFilterReverb); }
            printf("DS3D: Set3DVoiceData buffer %p data %p%s mask=0x%02X {%s } have=0x%02X use=%d parent=%p -> offset=%ld pan=%ld pitch=%ld\n",
                (void *)pHybridThis, (void *)pData, bReadable ? "" : " (unreadable)", mask, Fields,
                (unsigned)pThis->Xb_3D.dwHave, (int)pThis->Xb_3D.bUse, (void *)pThis->Xb_OutputParent,
                (long)Cxbxr3DVoice_VolumeOffsetMb(pThis->Xb_3D), (long)DSoundBuffer_3DPanMb(pHybridThis),
                (long)Cxbxr3DVoice_PitchDelta(pThis->Xb_3D));
            fflush(stdout);
        }
    }

    return DS_OK;
}

// ******************************************************************
// * patch: IDirectSoundBuffer_Use3DVoiceData
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundBuffer_Use3DVoiceData)
(
    XbHybridDSBuffer*       pHybridThis,
    LPUNKNOWN               pUnknown)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pHybridThis)
		LOG_FUNC_ARG(pUnknown)
		LOG_FUNC_END;

    // The second argument is a BOOL (DS3D_CALCULATOR_CONTRACT.md 1.4): XACT passes
    // "this voice or the voice it is routed into is 3D". On the Xbox it gates every
    // 3D term (settings flag 0x01000000); here it gates the three host terms likewise.
    EmuDirectSoundBuffer* pThis = pHybridThis->emuDSBuffer;
    const bool bWasUse = pThis->Xb_3D.bUse;
    pThis->Xb_3D.bUse = (pUnknown != nullptr);
    if (pThis->Xb_3D.bUse || bWasUse || pThis->Xb_OutputParent != nullptr) {
        DSoundBuffer_Apply3DVoiceState(pHybridThis);
        DSoundBuffer_Propagate3DPan(pHybridThis);
    }

    { // DS3D trace (see Set3DVoiceData)
        static unsigned s_Seen = 0;
        if (++s_Seen <= 40) {
            printf("DS3D: Use3DVoiceData buffer %p use=%d parent=%p -> offset=%ld pan=%ld pitch=%ld\n",
                (void *)pHybridThis, (int)pThis->Xb_3D.bUse, (void *)pThis->Xb_OutputParent,
                (long)Cxbxr3DVoice_VolumeOffsetMb(pThis->Xb_3D), (long)DSoundBuffer_3DPanMb(pHybridThis),
                (long)Cxbxr3DVoice_PitchDelta(pThis->Xb_3D));
            fflush(stdout);
        }
    }

    return DS_OK;
}

