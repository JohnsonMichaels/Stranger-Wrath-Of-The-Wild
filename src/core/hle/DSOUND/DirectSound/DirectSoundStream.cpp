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
#define LOG_PREFIX CXBXR_MODULE::DSSTREAM


#include <core\kernel\exports\xboxkrnl.h>
#include <dsound.h>
#include "DirectSoundGlobal.hpp" // Global variables

#include "Logging.h"
#include "DirectSoundLogging.hpp"
#include "..\XbDSoundLogging.hpp"

#include "DSStream_PacketManager.hpp"
#include "DirectSound3DVoice.hpp" // Cxbxr3DVoiceState: the 3D voice data XACT hands a voice (DS3D resolve, Part A)
#include <cstdio> // printf: the bounded DS3D stream trace below (EmuLog is off in the shipped configuration)

// TODO: Tasks need to do for DirectSound HLE
// * Missing CDirectSoundStream patch
//   * CDirectSoundStream_Set3DVoiceData (new, undocument)
//   * CDirectSoundStream_Use3DVoiceData (new, undocument)
//   * IDirectSoundStream_QueryInterface (not require)
//   * IDirectSoundStream_QueryInterfaceC (not require)

xbox::X_CMcpxStream::_vtbl xbox::X_CMcpxStream::vtbl =
{
    0xBEEFC001,                     // 0x00
    0xBEEFC002,                     // 0x04
    0xBEEFC003,                     // 0x08
    0xBEEFC004,                     // 0x0C
    &xbox::EMUPATCH(CMcpxStream_Dummy_0x10),// 0x10
};

xbox::X_CDirectSoundStream::_vtbl xbox::X_CDirectSoundStream::vtbl_r1 =
{
    &xbox::EMUPATCH(CDirectSoundStream_AddRef),          // 0x00
    &xbox::EMUPATCH(CDirectSoundStream_Release),         // 0x04
/*
    STDMETHOD(GetInfo)(THIS_ LPXMEDIAINFO pInfo) PURE;
*/
    &xbox::EMUPATCH(CDirectSoundStream_GetInfo),        // 0x08
    &xbox::EMUPATCH(CDirectSoundStream_GetStatus__r1),  // 0x0C
    &xbox::EMUPATCH(CDirectSoundStream_Process),        // 0x10
    &xbox::EMUPATCH(CDirectSoundStream_Discontinuity),  // 0x14
    &xbox::EMUPATCH(CDirectSoundStream_Flush),          // 0x18
    0xBEEFB003,                                         // 0x1C // unknown function
    0xBEEFB004,                                         // 0x20 // DS_CRefCount_AddRef
    0xBEEFB005,                                         // 0x24 // DS_CRefCount_Release
    0xBEEFB006,                                         // 0x28
    0xBEEFB007,                                         // 0x2C
    0xBEEFB008,                                         // 0x30
    0xBEEFB009,                                         // 0x34
    0xBEEFB00A,                                         // 0x38
};

xbox::X_CDirectSoundStream::_vtbl xbox::X_CDirectSoundStream::vtbl_r2 =
{
    &xbox::EMUPATCH(CDirectSoundStream_AddRef),          // 0x00
    &xbox::EMUPATCH(CDirectSoundStream_Release),         // 0x04
/*
    STDMETHOD(GetInfo)(THIS_ LPXMEDIAINFO pInfo) PURE;
*/
    &xbox::EMUPATCH(CDirectSoundStream_GetInfo),        // 0x08
    &xbox::EMUPATCH(CDirectSoundStream_GetStatus__r2),  // 0x0C
    &xbox::EMUPATCH(CDirectSoundStream_Process),        // 0x10
    &xbox::EMUPATCH(CDirectSoundStream_Discontinuity),  // 0x14
    &xbox::EMUPATCH(CDirectSoundStream_Flush),          // 0x18
    0xBEEFB003,                                         // 0x1C // unknown function
    0xBEEFB004,                                         // 0x20 // DS_CRefCount_AddRef
    0xBEEFB005,                                         // 0x24 // DS_CRefCount_Release
    0xBEEFB006,                                         // 0x28
    0xBEEFB007,                                         // 0x2C
    0xBEEFB008,                                         // 0x30
    0xBEEFB009,                                         // 0x34
    0xBEEFB00A,                                         // 0x38
};

// construct vtable (or grab ptr to existing)
xbox::X_CDirectSoundStream::X_CDirectSoundStream(bool is3D) : Xb_Voice(is3D)
{
    pMcpxStream = new xbox::X_CMcpxStream(this);
    if (g_LibVersion_DSOUND < 4134) {
        pVtbl = &vtbl_r1;
    }
    else {
        pVtbl = &vtbl_r2;
    }
}

/* NOTE: SUCCEEDED define is only checking for is equal or greater than zero value.
    And FAILED check for less than zero value. Since DS_OK is only 0 base on DirectSound documentation,
    there is chance of failure which contain value greater than 0.
 */

#include "DirectSoundInline.hpp"

// ******************************************************************
// * 3D voice data on streams (DS3D resolve, Part C)
// ******************************************************************
// XACT drives positional sound with the Xbox's own 3D calculator and hands the result
// to the voice through Set3DVoiceData. A streamed wave (this class) routed into a 3D
// sound source with SetOutputBuffer receives its own distance/cone/I3DL2/doppler
// values; its placement (the azimuth) is delivered to the PARENT mixin buffer instead,
// which Part A records in that buffer's Xb_3D. Host DirectSound cannot chain buffers,
// so the parent's pan is applied to this stream directly.
//
// Streams are the music path and work today, so every path below is a no-op until 3D
// data is actually in use: Cxbxr3DVoice_VolumeOffsetMb/PanMb/PitchDelta return 0 while
// Xb_3D.bUse is false (no Use3DVoiceData(TRUE) yet), and the re-apply helper returns
// early on the same flag. A stream that never gets 3D data never changes behaviour.

static constexpr DWORD CXBXR_STREAM3D_VOLUME = 1 << 0;
static constexpr DWORD CXBXR_STREAM3D_PAN    = 1 << 1;
static constexpr DWORD CXBXR_STREAM3D_PITCH  = 1 << 2;
static constexpr DWORD CXBXR_STREAM3D_ALL    = CXBXR_STREAM3D_VOLUME | CXBXR_STREAM3D_PAN | CXBXR_STREAM3D_PITCH;

// The hybrid buffer this stream was routed into, if it is still alive. Looked up in
// g_pDSoundBufferCache rather than dereferenced blindly: nothing clears Xb_OutputParent
// when the parent is released.
static xbox::EmuDirectSoundBuffer* CxbxrStream3D_Parent(xbox::X_CDirectSoundStream* pThis)
{
    xbox::XbHybridDSBuffer* pParent = pThis->Xb_OutputParent;
    if (pParent == nullptr) {
        return nullptr;
    }
    if (std::find(g_pDSoundBufferCache.begin(), g_pDSoundBufferCache.end(), pParent) == g_pDSoundBufferCache.end()) {
        return nullptr;
    }
    return pParent->emuDSBuffer;
}

// Pan for this stream: its own azimuth if it ever received one (change bit 0x40),
// otherwise the parent's. XACT masks a routed track's voice data to distance / cone /
// I3DL2 / doppler (&= 0x33), so in this title the azimuth only ever reaches the parent.
// 0 = centre, which is also what both sides yield while no 3D data is in use.
static LONG CxbxrStream3D_PanMb(xbox::X_CDirectSoundStream* pThis, xbox::EmuDirectSoundBuffer* pParent)
{
    if (pParent == nullptr || (pThis->Xb_3D.dwHave & 0x40) != 0) {
        return Cxbxr3DVoice_PanMb(pThis->Xb_3D);
    }
    return Cxbxr3DVoice_PanMb(pParent->Xb_3D);
}

// Push the stream's 3D state to the host object. Volume goes through the same call the
// title's own SetVolume takes, with the title's volume reconstructed the way the mixbin
// fold does (the voice stores volume - headroom); pitch likewise on top of the voice's
// own pitch; pan straight to the host. Callers decide whether to apply: with bUse false
// the three mapping functions return 0, so applying then CLEARS the 3D terms.
static void CxbxrStream3D_Apply(xbox::X_CDirectSoundStream* pThis, DWORD dwWhat)
{
    if (pThis->EmuDirectSoundBuffer8 == nullptr) {
        return;
    }
    if ((dwWhat & CXBXR_STREAM3D_VOLUME) != 0) {
        HybridDirectSoundBuffer_SetVolume(pThis->EmuDirectSoundBuffer8,
                                          pThis->Xb_Voice.GetVolume() + (LONG)pThis->Xb_Voice.GetHeadroom(),
                                          pThis->EmuFlags, pThis->Xb_VolumeMixbin, &pThis->Xb_Voice,
                                          Cxbxr3DVoice_VolumeOffsetMb(pThis->Xb_3D));
    }
    if ((dwWhat & CXBXR_STREAM3D_PAN) != 0) {
        HybridDirectSoundBuffer_SetPan3D(pThis->EmuDirectSoundBuffer8, CxbxrStream3D_PanMb(pThis, CxbxrStream3D_Parent(pThis)));
    }
    if ((dwWhat & CXBXR_STREAM3D_PITCH) != 0) {
        HybridDirectSoundBuffer_SetPitch(pThis->EmuDirectSoundBuffer8, pThis->Xb_Voice.GetPitch(), &pThis->Xb_Voice,
                                         Cxbxr3DVoice_PitchDelta(pThis->Xb_3D));
    }
}

// For the title's own SetMixBinVolumes / SetHeadroom / SetFormat / SetOutputBuffer paths,
// which rewrite the host volume or rebuild the host buffer without the 3D terms: put them
// back. THE GUARD for the music path: nothing happens unless Use3DVoiceData(TRUE) is in
// effect on this stream.
static inline void CxbxrStream3D_Reapply(xbox::X_CDirectSoundStream* pThis, DWORD dwWhat)
{
    if (!pThis->Xb_3D.bUse) {
        return;
    }
    CxbxrStream3D_Apply(pThis, dwWhat);
}

// ******************************************************************
// * patch: DirectSoundDoWork (stream)
// ******************************************************************
void DirectSoundDoWork_Stream(xbox::LARGE_INTEGER& time)
{
    // Actually, DirectSoundStream need to process buffer packets here.
    vector_ds_stream::iterator ppDSStream = g_pDSoundStreamCache.begin();
    for (; ppDSStream != g_pDSoundStreamCache.end(); ppDSStream++) {
        if ((*ppDSStream)->Host_BufferPacketArray.empty()) {
            continue;
        }
        xbox::X_CDirectSoundStream* pThis = (*ppDSStream);
        // TODO: Do we need this in async thread loop?
        if (pThis->Xb_rtPauseEx != 0LL && pThis->Xb_rtPauseEx <= time.QuadPart) {
            pThis->Xb_rtPauseEx = 0LL;
            pThis->EmuFlags &= ~(DSE_FLAG_PAUSE | DSE_FLAG_PAUSENOACTIVATE);
            // Don't call play here, let DSStream_Packet_Process deal with it.
        }
        // If has flush async requested then verify time has expired to perform flush process.
        if ((pThis->EmuFlags & DSE_FLAG_FLUSH_ASYNC) > 0 && pThis->Xb_rtFlushEx <= time.QuadPart) {
            if (pThis->Xb_rtFlushEx == 0LL) {
                EmuLog(LOG_LEVEL::WARNING, "Attempted to flush without Xb_rtFlushEx set to non-zero");
            }
            DSStream_Packet_Flush(pThis);
        } else {
            DSStream_Packet_Process(pThis);
        }
    }
}

// ******************************************************************
// * patch: CDirectSoundStream_AddRef
// ******************************************************************
xbox::ulong_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_AddRef)
(
    X_CDirectSoundStream*   pThis)
{
    DSoundMutexGuardLock;

	LOG_FUNC_ONE_ARG(pThis);

    ULONG uRet = HybridDirectSoundBuffer_AddRef(pThis->EmuDirectSoundBuffer8);

    return uRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_Release
// ******************************************************************
xbox::ulong_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_Release)
(
    X_CDirectSoundStream*   pThis)
{
    DSoundMutexGuardLock;

	LOG_FUNC_ONE_ARG(pThis);

    ULONG uRet = 0;
    if (pThis != 0 && (pThis->EmuDirectSoundBuffer8 != 0)) {
        uRet = pThis->EmuDirectSoundBuffer8->Release();

        if (uRet == 0) {
            if (pThis->EmuDirectSound3DBuffer8 != nullptr) {
                pThis->EmuDirectSound3DBuffer8->Release();
            }

            // remove cache entry
            vector_ds_stream::iterator ppDSStream = std::find(g_pDSoundStreamCache.begin(), g_pDSoundStreamCache.end(), pThis);
            if (ppDSStream != g_pDSoundStreamCache.end()) {
                g_pDSoundStreamCache.erase(ppDSStream);
            }

            for (auto buffer = pThis->Host_BufferPacketArray.begin(); buffer != pThis->Host_BufferPacketArray.end();) {
                DSStream_Packet_Clear(buffer, XMP_STATUS_FLUSHED, pThis->Xb_lpfnCallback, pThis->Xb_lpvContext, pThis);
            }

            if (pThis->EmuBufferDesc.lpwfxFormat != nullptr) {
                free(pThis->EmuBufferDesc.lpwfxFormat);
            }
            // NOTE: Do not release X_BufferCache! X_BufferCache is using xbox buffer.

            delete pThis;
        }
    }

    RETURN(uRet);
}

// ******************************************************************
// * patch: DirectSoundCreateStream
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(DirectSoundCreateStream)
(
    X_DSSTREAMDESC*         pdssd,
    OUT X_CDirectSoundStream**  ppStream)
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
		LOG_FUNC_ARG(pdssd)
		LOG_FUNC_ARG_OUT(ppStream)
		LOG_FUNC_END;

    HRESULT hRet = DS_OK;

    //If out of space, return out of memory.
    if (X_DIRECTSOUND_CACHE_COUNT == X_DIRECTSOUND_CACHE_MAX) {

        hRet = DSERR_OUTOFMEMORY;
        *ppStream = xbox::zeroptr;
    } else {

        DSBUFFERDESC DSBufferDesc = { 0 };


        DWORD dwAcceptableMask = 0x00000010; // TODO: Note 0x00040000 is being ignored (DSSTREAMCAPS_LOCDEFER)

        if (pdssd->dwFlags & (~dwAcceptableMask)) {
            EmuLog(LOG_LEVEL::WARNING, "Use of unsupported pdssd->dwFlags mask(s) (0x%.08X)", pdssd->dwFlags & (~dwAcceptableMask));
        }
        DSBufferDesc.dwSize = sizeof(DSBUFFERDESC);
        //DSBufferDesc->dwFlags = (pdssd->dwFlags & dwAcceptableMask) | DSBCAPS_CTRLVOLUME | DSBCAPS_GETCURRENTPOSITION2;
        DSBufferDesc.dwFlags = DSBCAPS_CTRLVOLUME | DSBCAPS_CTRLFREQUENCY | DSBCAPS_GETCURRENTPOSITION2 | //aka DSBCAPS_DEFAULT + control position
            (g_XBAudio.mute_on_unfocus ? 0 : DSBCAPS_GLOBALFOCUS);

        if ((pdssd->dwFlags & DSBCAPS_CTRL3D) > 0) {
            DSBufferDesc.dwFlags |= DSBCAPS_CTRL3D;
        } else {
            DSBufferDesc.dwFlags |= DSBCAPS_CTRLPAN;
        }

        // TODO: Garbage Collection
        *ppStream = new X_CDirectSoundStream((DSBufferDesc.dwFlags & DSBCAPS_CTRL3D) != 0);

        DSoundBufferSetDefault((*ppStream), DSBPLAY_LOOPING, pdssd->dwFlags);
        (*ppStream)->Xb_rtFlushEx = 0LL;
        // 3D voice data starts absent: no volume offset, no pan, no pitch delta, no parent
        // (the Cxbxr3DVoice_* mapping functions return 0 while bUse is false). Part B's
        // DSoundBufferSetDefault initialises the same two members; repeating it here keeps
        // the music path's no-op guarantee independent of that macro.
        Cxbxr3DVoice_Init((*ppStream)->Xb_3D);
        (*ppStream)->Xb_OutputParent = nullptr;

        // We have to set DSBufferDesc last due to EmuFlags must be either 0 or previously written value to preserve other flags.
        GeneratePCMFormat(DSBufferDesc, pdssd->lpwfxFormat, (DWORD &)pdssd->dwFlags, (*ppStream)->EmuFlags, 0,
                          xbox::zeroptr, (*ppStream)->X_BufferCacheSize, (*ppStream)->Xb_VoiceProperties, pdssd->mixBinsOutput,
                          &(*ppStream)->Xb_Voice);

        // Test case: Star Wars: KotOR has one packet greater than 5 seconds worth. Increasing to 10 seconds allow stream to work until
        // another test case below proven host's buffer size does not matter since packet's size can be greater than host's buffer size.
        // Test case: GTA 3 / Vice City, and some other titles has packet's buffer size are bigger than 10 seconds worth of buffer size.
        // Allocate at least 5 second worth of bytes in PCM format to allow partial upload packet's buffer.
        DSBufferDesc.dwBufferBytes = DSBufferDesc.lpwfxFormat->nAvgBytesPerSec * 5;
        (*ppStream)->EmuBufferDesc = DSBufferDesc;

        (*ppStream)->X_MaxAttachedPackets = pdssd->dwMaxAttachedPackets;
        (*ppStream)->Host_BufferPacketArray.reserve(pdssd->dwMaxAttachedPackets);
        (*ppStream)->Host_dwWriteOffsetNext = 0;
        (*ppStream)->Host_dwLastWritePos = 0;
        (*ppStream)->Host_isProcessing = false;
        (*ppStream)->Xb_lpfnCallback = pdssd->lpfnCallback;
        (*ppStream)->Xb_lpvContext = pdssd->lpvContext;
        (*ppStream)->Xb_Status = 0;
        //TODO: Implement mixbin variable support. Or just merge pdssd struct into DS Stream class.

        LOG_FUNC_BEGIN_ARG_RESULT
            LOG_FUNC_ARG_RESULT(ppStream)
        LOG_FUNC_END_ARG_RESULT;

        hRet = DSoundBufferCreate(&DSBufferDesc, (*ppStream)->EmuDirectSoundBuffer8);
        if (FAILED(hRet)) {
            std::stringstream output;
            output << "Xbox:\n" << pdssd;
            output << "\nHost converison:\n" << DSBufferDesc;
            EmuLog(LOG_LEVEL::WARNING, output.str().c_str());
            output.str("");
            output << static_cast<DS_RESULT>(hRet);
            CxbxrAbort("DSS: DSoundBufferCreate error: %s", output.str().c_str());
        }
        else {
            if (DSBufferDesc.dwFlags & DSBCAPS_CTRL3D) {
                DSound3DBufferCreate((*ppStream)->EmuDirectSoundBuffer8, (*ppStream)->EmuDirectSound3DBuffer8);
            }

            DSoundDebugMuteFlag((*ppStream)->EmuBufferDesc.dwBufferBytes, (*ppStream)->EmuFlags);

            // Pre-set volume to enforce silence if one of audio codec is disabled.
            HybridDirectSoundBuffer_SetVolume((*ppStream)->EmuDirectSoundBuffer8, 0L, (*ppStream)->EmuFlags,
                (*ppStream)->Xb_VolumeMixbin, &(*ppStream)->Xb_Voice);

            g_pDSoundStreamCache.push_back(*ppStream);
        }
    }

    return hRet;
}

// ******************************************************************
// * patch: IDirectSound_CreateSoundStream
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSound_CreateSoundStream)
(
    LPDIRECTSOUND8          pThis,
    X_DSSTREAMDESC*         pdssd,
    OUT X_CDirectSoundStream**  ppStream,
    PVOID                   pUnknown)
{
    DSoundMutexGuardLock;

    LOG_FORWARD("DirectSoundCreateStream");

    HRESULT hRet = EMUPATCH(DirectSoundCreateStream)(pdssd, ppStream);

    return hRet;
}

// ******************************************************************
// * patch: CMcpxStream_Dummy_0x10
// ******************************************************************
xbox::void_xt WINAPI xbox::EMUPATCH(CMcpxStream_Dummy_0x10)(dword_xt dwDummy1, dword_xt dwDummy2)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(dwDummy1)
		LOG_FUNC_ARG(dwDummy2)
		LOG_FUNC_END;

    // Causes deadlock in Halo...
    // TODO: Verify that this is a Vista related problem (I HATE Vista!)
//    EmuLog(LOG_LEVEL::WARNING, "EmuCMcpxStream_Dummy_0x10 is ignored!");

    return;
}

/* ------------- Sorted relative functions begin ------------------*/

// ******************************************************************
// * patch: CDirectSoundStream_Discontinuity
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_Discontinuity)
(
    X_CDirectSoundStream*   pThis)
{
    DSoundMutexGuardLock;

	LOG_FUNC_ONE_ARG(pThis);

    // default ret = DSERR_GENERIC

    // Perform check if packets exist, then mark the last submited packet as end of stream.
    if (!pThis->Host_BufferPacketArray.empty()) {
        pThis->Host_BufferPacketArray.back().isStreamEnd = true;
    }

    return DS_OK;
}

xbox::hresult_xt CxbxrImpl_CDirectSoundStream_Flush
(
    xbox::X_CDirectSoundStream* pThis)
{

    DSoundBufferSynchPlaybackFlagRemove(pThis->EmuFlags);

    while (DSStream_Packet_Flush(pThis));

    return DS_OK;
}

// ******************************************************************
// * patch: CDirectSoundStream_Flush
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_Flush)
(
    X_CDirectSoundStream*   pThis)
{
    DSoundMutexGuardLock;

	LOG_FUNC_ONE_ARG(pThis);

    return CxbxrImpl_CDirectSoundStream_Flush(pThis);
}

// ******************************************************************
// * patch: CDirectSoundStream_FlushEx
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_FlushEx)
(
    X_CDirectSoundStream*   pThis,
    REFERENCE_TIME          rtTimeStamp,
    dword_xt                   dwFlags)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(rtTimeStamp)
		LOG_FUNC_ARG_TYPE(DSSFLUSHEX_FLAG, dwFlags)
		LOG_FUNC_END;

    HRESULT hRet = DSERR_INVALIDPARAM;
    // Reset flags here to reprocess dwFlags request.
    DSStream_Packet_FlushEx_Reset(pThis);

    // Cannot use rtTimeStamp here, it must be flush.
    if (dwFlags == X_DSSFLUSHEX_IMMEDIATE) {

        hRet = CxbxrImpl_CDirectSoundStream_Flush(pThis);

    }
    // Remaining flags require X_DSSFLUSHEX_ASYNC to be include.
    else if ((dwFlags & X_DSSFLUSHEX_ASYNC) > 0 && !pThis->Host_BufferPacketArray.empty()) {
        // If rtTimeStamp is zero'd, then call flush once and allow process flush in worker thread.
        if (rtTimeStamp == 0LL) {
            xbox::LARGE_INTEGER getTime;
            xbox::KeQuerySystemTime(&getTime);
            pThis->Xb_rtFlushEx = getTime.QuadPart;
            pThis->EmuFlags |= DSE_FLAG_IS_FLUSHING;
        }
        else {
            pThis->Xb_rtFlushEx = rtTimeStamp;
        }

        pThis->EmuFlags |= DSE_FLAG_FLUSH_ASYNC;

        // Set or remove flags (This is the only place it will set/remove other than flush perform remove the flags.)
        if ((dwFlags & X_DSSFLUSHEX_ENVELOPE) > 0) {
            pThis->Xb_rtFlushEx += (pThis->Xb_EnvolopeDesc.dwRelease * 512) / 48000;
        }

        if ((dwFlags & X_DSSFLUSHEX_ENVELOPE2) > 0) {
            pThis->EmuFlags |= DSE_FLAG_ENVELOPE2;
        }
        else {
            pThis->EmuFlags ^= DSE_FLAG_ENVELOPE2;
        }
    }

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_GetInfo
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_GetInfo)
(
    X_CDirectSoundStream*   pThis,
    OUT LPXMEDIAINFO            pInfo)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG_OUT(pInfo)
		LOG_FUNC_END;

    if (pInfo) {
        pInfo->dwFlags = XMO_STREAMF_FIXED_SAMPLE_SIZE | XMO_STREAMF_INPUT_ASYNC;
        pInfo->dwInputSize = pThis->EmuBufferDesc.lpwfxFormat->nBlockAlign;
        pInfo->dwOutputSize = 0;
        pInfo->dwMaxLookahead = std::max(static_cast<uint32_t>(pThis->EmuBufferDesc.lpwfxFormat->nChannels * static_cast<uint32_t>(pThis->EmuBufferDesc.lpwfxFormat->wBitsPerSample) / 8) * 32, static_cast<uint32_t>(pThis->EmuBufferDesc.lpwfxFormat->nBlockAlign) * 2);
    }

    return DS_OK;
}

// ******************************************************************
// * patch: CDirectSoundStream_GetStatus (3911+)
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_GetStatus__r1)
(
    X_CDirectSoundStream*   pThis,
    OUT dword_xt*           pdwStatus)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pThis)
        LOG_FUNC_ARG_OUT(pdwStatus)
        LOG_FUNC_END;

    DWORD dwStatusXbox = 0, dwStatusHost;
    HRESULT hRet = pThis->EmuDirectSoundBuffer8->GetStatus(&dwStatusHost);

    // Convert host to xbox status flag.
    if (hRet == DS_OK) {
        if (pThis->Host_BufferPacketArray.size() != pThis->X_MaxAttachedPackets) {
            dwStatusXbox = X_DSSSTATUS_READY;
        }

    }
    else {
        hRet = DSERR_GENERIC;
    }

    if (pdwStatus != xbox::zeroptr) {
        *pdwStatus = dwStatusXbox;
    }

    // Only used for debug any future issues with custom stream's packet management
    EmuLog(LOG_LEVEL::DEBUG, "packet array size: %d", pThis->Host_BufferPacketArray.size());

    LOG_FUNC_BEGIN_ARG_RESULT
        LOG_FUNC_ARG_RESULT_TYPE(DSSSTATUS_FLAG, pdwStatus)
    LOG_FUNC_END_ARG_RESULT;

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_GetStatus (4134+)
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_GetStatus__r2)
(
    X_CDirectSoundStream*   pThis,
    OUT dword_xt*           pdwStatus)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pThis)
        LOG_FUNC_ARG_OUT(pdwStatus)
        LOG_FUNC_END;

    DWORD dwStatusXbox = pThis->Xb_Status, dwStatusHost;
    HRESULT hRet = pThis->EmuDirectSoundBuffer8->GetStatus(&dwStatusHost);

    // Convert host to xbox status flag.
    if (hRet == DS_OK) {
        if (pThis->Host_BufferPacketArray.size() != pThis->X_MaxAttachedPackets) {
            dwStatusXbox |= X_DSSSTATUS_READY;
        }
        // HACK: Likely a hack but force mimic stream's status is playing while in background is ongoing in flush process.
        // Testcase: Obscure; Crash Twinsanity
        if ((pThis->EmuFlags & DSE_FLAG_IS_FLUSHING) != 0) {
            // TODO: Find a way to implement deterred commands system if possible.
            // Then this hack may could be remove or replace to determine internal status base on deterred commands?
            // NOTE: It may not be likely behave like on hardware in paused/stopped state. See todo note above.
            LOG_TEST_CASE("Internal stream is currently flushing, enforcing status to playing state");
            dwStatusXbox |= X_DSSSTATUS_PLAYING;
        }
        else if (!pThis->Host_BufferPacketArray.empty()) {
            if ((pThis->EmuFlags & DSE_FLAG_PAUSE) != 0) {
                dwStatusXbox |= X_DSSSTATUS_PAUSED;
            }
            else if ((pThis->EmuFlags & (DSE_FLAG_PAUSE | DSE_FLAG_PAUSENOACTIVATE | DSE_FLAG_IS_FLUSHING)) == 0) {
                dwStatusXbox |= X_DSSSTATUS_PLAYING;
            }
        }
    } else {
        dwStatusXbox = 0;
        hRet = DSERR_GENERIC;
    }

    if (pdwStatus != xbox::zeroptr) {
        *pdwStatus = dwStatusXbox;
    }

    // Only used for debug any future issues with custom stream's packet management
    EmuLog(LOG_LEVEL::DEBUG, "packet array size: %d", pThis->Host_BufferPacketArray.size());

    LOG_FUNC_BEGIN_ARG_RESULT
        LOG_FUNC_ARG_RESULT_TYPE(DSSSTATUS_FLAG, pdwStatus)
    LOG_FUNC_END_ARG_RESULT;

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_GetVoiceProperties
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_GetVoiceProperties)
(
    X_CDirectSoundStream*   pThis,
    OUT X_DSVOICEPROPS*     pVoiceProps
)
{
    DSoundMutexGuardLock;
 LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pThis)
        LOG_FUNC_ARG_OUT(pVoiceProps)
        LOG_FUNC_END;

    if (pVoiceProps == xbox::zeroptr) {
        LOG_TEST_CASE("pVoiceProps == xbox::zeroptr");
        RETURN(DS_OK);
    }

    HRESULT hRet = HybridDirectSoundBuffer_GetVoiceProperties(pThis->Xb_VoiceProperties, pVoiceProps);

    return hRet;
}


xbox::hresult_xt CxbxrImpl_CDirectSoundStream_PauseEx(
    xbox::X_CDirectSoundStream* pThis,
    xbox::REFERENCE_TIME        rtTimestamp,
    xbox::dword_xt              dwPause)
{
    xbox::hresult_xt hRet = HybridDirectSoundBuffer_Pause(pThis->EmuDirectSoundBuffer8, dwPause, pThis->EmuFlags, pThis->EmuPlayFlags,
        pThis->Host_isProcessing, rtTimestamp, pThis->Xb_rtPauseEx);

    if ((pThis->EmuFlags & DSE_FLAG_PAUSE) != 0) {
        pThis->Host_isProcessing = false;
    }
    else if (!pThis->Host_isProcessing) {
        DSStream_Packet_Process(pThis);
    }

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_Pause
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_Pause)
(
    X_CDirectSoundStream*   pThis,
    dword_xt                dwPause)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG_TYPE(DSSPAUSE_FLAG, dwPause)
		LOG_FUNC_END;

	if (!pThis) {
		LOG_TEST_CASE("CDirectSoundStream_Pause called with pThis = nullptr");
		return X_STATUS_SUCCESS;
	}

    HRESULT hRet = CxbxrImpl_CDirectSoundStream_PauseEx(pThis, 0LL, dwPause);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_PauseEx
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_PauseEx)
(
    X_CDirectSoundStream   *pThis,
    REFERENCE_TIME          rtTimestamp,
    dword_xt                dwPause)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pThis)
        LOG_FUNC_ARG(rtTimestamp)
        LOG_FUNC_ARG_TYPE(DSSPAUSE_FLAG, dwPause)
        LOG_FUNC_END;

    // This function wasn't part of the XDK until 4721. (Same as IDirectSoundBuffer_PauseEx?)

    HRESULT hRet = CxbxrImpl_CDirectSoundStream_PauseEx(pThis, rtTimestamp, dwPause);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_Process
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_Process)
(
    X_CDirectSoundStream   *pThis,
    PXMEDIAPACKET           pInputBuffer,
    PXMEDIAPACKET           pOutputBuffer)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(pInputBuffer)
		LOG_FUNC_ARG(pOutputBuffer)
		LOG_FUNC_END;

    // Research data:
    // * Max packet size permitted is 0x2000 (or 8,192 decimal) of buffer.
    //   * Somehow other titles are using more than 0x2000 for max size. Am using a hacky host method for now (see pBuffer_data).

    if (pThis->EmuDirectSoundBuffer8 != nullptr) {

        if (pInputBuffer != xbox::zeroptr) {

            // Add packets from title until it gets full.
            if (pThis->Host_BufferPacketArray.size() != pThis->X_MaxAttachedPackets) {
                host_voice_packet packet_input;
                packet_input.pBuffer_data = nullptr;
                packet_input.xmp_data = *pInputBuffer;
                packet_input.xmp_data.dwMaxSize = DSoundBufferGetPCMBufferSize(pThis->EmuFlags, pInputBuffer->dwMaxSize);
                if (packet_input.xmp_data.dwMaxSize != 0) {
                    packet_input.pBuffer_data = malloc(packet_input.xmp_data.dwMaxSize);
                    DSoundSGEMemAlloc(packet_input.xmp_data.dwMaxSize);
                }
                packet_input.nextWriteOffset = pThis->Host_dwWriteOffsetNext;
                packet_input.lastWritePos = packet_input.nextWriteOffset;
                pThis->Host_dwWriteOffsetNext += packet_input.xmp_data.dwMaxSize;
                // Packet size may be larger than host's pre-allocated buffer size, loop is a requirement until within range is known.
                while (pThis->EmuBufferDesc.dwBufferBytes <= pThis->Host_dwWriteOffsetNext) {
                    pThis->Host_dwWriteOffsetNext -= pThis->EmuBufferDesc.dwBufferBytes;
                }
                packet_input.bufWrittenBytes = 0;
                packet_input.bufPlayed = 0;
                packet_input.isPlayed = false;
                packet_input.isStreamEnd = false;

                DSoundBufferOutputXBtoHost(pThis->EmuFlags, pThis->EmuBufferDesc, pInputBuffer->pvBuffer, pInputBuffer->dwMaxSize, packet_input.pBuffer_data, packet_input.xmp_data.dwMaxSize);

                pThis->Host_BufferPacketArray.push_back(packet_input);

                if (pInputBuffer->pdwStatus != xbox::zeroptr) {
                    (*pInputBuffer->pdwStatus) = XMP_STATUS_PENDING;
                }
                if (pInputBuffer->pdwCompletedSize != xbox::zeroptr) {
                    (*pInputBuffer->pdwCompletedSize) = 0;
                }
                if (pThis->Host_isProcessing == false && pThis->Host_BufferPacketArray.size() == 1) {
                    pThis->EmuDirectSoundBuffer8->SetCurrentPosition(packet_input.nextWriteOffset);
                }

                if ((pThis->Xb_Status & X_DSSSTATUS_STARVED) > 0) {
                    pThis->Xb_Status &= ~X_DSSSTATUS_STARVED;
                }
                pThis->EmuFlags &= ~DSE_FLAG_IS_FLUSHING;
                DSStream_Packet_Process(pThis);
            // Once full it needs to change status to flushed when cannot hold any more packets.
            } else {
                if (pInputBuffer->pdwStatus != xbox::zeroptr) {
                    (*pInputBuffer->pdwStatus) = XMP_STATUS_FAILURE;
                }
            }
        }

        //TODO: What to do with output buffer audio variable? Need test case or functional source code.
        // NOTE: pOutputBuffer is reserved, must be set to NULL from titles.
        if (pOutputBuffer != xbox::zeroptr) {
            LOG_TEST_CASE("pOutputBuffer is not nullptr, please report title test case to issue tracker. Thanks!");
        }

    } else {
        if (pInputBuffer != xbox::zeroptr && pInputBuffer->pdwStatus != xbox::zeroptr) {
            (*pInputBuffer->pdwStatus) = XMP_STATUS_SUCCESS;
        }
    }

    return DS_OK;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetAllParameters
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetAllParameters)
(
    X_CDirectSoundStream*   pThis,
    X_DS3DBUFFER*           pc3DBufferParameters,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(pc3DBufferParameters)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DBuffer_SetAllParameters(pThis->EmuDirectSound3DBuffer8, pc3DBufferParameters, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetConeAngles
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetConeAngles)
(
    X_CDirectSoundStream*   pThis,
    dword_xt                   dwInsideConeAngle,
    dword_xt                   dwOutsideConeAngle,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(dwInsideConeAngle)
		LOG_FUNC_ARG(dwOutsideConeAngle)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DBuffer_SetConeAngles(pThis->EmuDirectSound3DBuffer8, dwInsideConeAngle, dwOutsideConeAngle, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetConeOrientation
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetConeOrientation)
(
    X_CDirectSoundStream*   pThis,
    D3DVALUE                x,
    D3DVALUE                y,
    D3DVALUE                z,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(x)
		LOG_FUNC_ARG(y)
		LOG_FUNC_ARG(z)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DBuffer_SetConeOrientation(pThis->EmuDirectSound3DBuffer8, x, y, z, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetConeOutsideVolume
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetConeOutsideVolume)
(
    X_CDirectSoundStream*   pThis,
    long_xt                    lConeOutsideVolume,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(lConeOutsideVolume)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DBuffer_SetConeOutsideVolume(pThis->EmuDirectSound3DBuffer8, lConeOutsideVolume, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetDistanceFactor
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetDistanceFactor)
(
    X_CDirectSoundStream*   pThis,
    float_xt                   flDistanceFactor,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(flDistanceFactor)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DListener_SetDistanceFactor(g_pDSoundPrimary3DListener8, flDistanceFactor, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetDopplerFactor
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetDopplerFactor)
(
    X_CDirectSoundStream*   pThis,
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
// * patch: CDirectSoundStream_SetEG
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetEG)
(
    X_CDirectSoundStream*   pThis,
    X_DSENVOLOPEDESC*       pEnvelopeDesc)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(pEnvelopeDesc)
		LOG_FUNC_END;

    // NOTE: DSP relative function

    LOG_NOT_SUPPORTED();

    pThis->Xb_EnvolopeDesc = *pEnvelopeDesc;

    return S_OK;
}

// ******************************************************************
// * patch: IDirectSoundStream_SetEG
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_SetEG)
(
    X_CDirectSoundStream*   pThis,
    X_DSENVOLOPEDESC*       pEnvelopeDesc)
{
    DSoundMutexGuardLock;

    LOG_FORWARD("CDirectSoundStream_SetEG");

    return xbox::EMUPATCH(CDirectSoundStream_SetEG)(pThis, pEnvelopeDesc);
}

// ******************************************************************
// * patch: CDirectSoundStream_SetFilter
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetFilter)
(
    X_CDirectSoundStream*   pThis,
    X_DSFILTERDESC*         pFilterDesc)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(pFilterDesc)
		LOG_FUNC_END;

    // NOTE: DSP relative function

    LOG_NOT_SUPPORTED();

    return S_OK;
}

// ******************************************************************
// * patch: IDirectSoundStream_SetFilter
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_SetFilter)
(
    X_CDirectSoundStream*   pThis,
    X_DSFILTERDESC*         pFilterDesc)
{
    DSoundMutexGuardLock;

    LOG_FORWARD("CDirectSoundStream_SetFilter");

    return xbox::EMUPATCH(CDirectSoundStream_SetFilter)(pThis, pFilterDesc);
}

// ******************************************************************
// * patch: CDirectSoundStream::SetFormat
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetFormat)
(
    X_CDirectSoundStream*   pThis,
    LPCWAVEFORMATEX         pwfxFormat)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(pwfxFormat)
		LOG_FUNC_END;

    while (DSStream_Packet_Flush(pThis));

    HRESULT hRet = HybridDirectSoundBuffer_SetFormat(pThis->EmuDirectSoundBuffer8, pwfxFormat, pThis->Xb_Flags,
                                                     pThis->EmuBufferDesc, pThis->EmuFlags, pThis->EmuPlayFlags,
                                                     pThis->EmuDirectSound3DBuffer8, 0, pThis->X_BufferCache,
                                                     pThis->X_BufferCacheSize, pThis->Xb_VoiceProperties,
                                                     xbox::zeroptr, &pThis->Xb_Voice);

    // SetFormat may rebuild the host buffer; nothing to redo unless 3D data is in use.
    CxbxrStream3D_Reapply(pThis, CXBXR_STREAM3D_ALL);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetFrequency
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetFrequency)
(
    X_CDirectSoundStream*   pThis,
    dword_xt                   dwFrequency)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(dwFrequency)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSoundBuffer_SetFrequency(pThis->EmuDirectSoundBuffer8, dwFrequency, &pThis->Xb_Voice);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundStream_SetFrequency
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_SetFrequency)
(
    X_CDirectSoundStream*   pThis,
    dword_xt                   dwFrequency)
{
    DSoundMutexGuardLock;

    LOG_FORWARD("CDirectSoundStream_SetFrequency");

    return xbox::EMUPATCH(CDirectSoundStream_SetFrequency)(pThis, dwFrequency);
}

// ******************************************************************
// * patch: CDirectSoundStream_SetHeadroom
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetHeadroom)
(
    X_CDirectSoundStream*   pThis,
    dword_xt                   dwHeadroom)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(dwHeadroom)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSoundBuffer_SetHeadroom(pThis->EmuDirectSoundBuffer8, dwHeadroom,
                                                       pThis->Xb_VolumeMixbin, pThis->EmuFlags, &pThis->Xb_Voice);

    // SetHeadroom rewrites the host volume with no 3D term; nothing to redo unless 3D data is in use.
    CxbxrStream3D_Reapply(pThis, CXBXR_STREAM3D_VOLUME);

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundStream_SetHeadroom
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_SetHeadroom)
(
    X_CDirectSoundStream*   pThis,
    dword_xt                   dwHeadroom)
{
    DSoundMutexGuardLock;

    LOG_FORWARD("CDirectSoundStream_SetHeadroom");

    return xbox::EMUPATCH(CDirectSoundStream_SetHeadroom)(pThis, dwHeadroom);
}

// ******************************************************************
// * patch: CDirectSoundStream_SetI3DL2Source
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetI3DL2Source)
(
    X_CDirectSoundStream*   pThis,
    X_DSI3DL2BUFFER*        pds3db,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(pds3db)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    // NOTE: SetI3DL2Source is using DSFXI3DL2Reverb structure, aka different interface.

    // NOTE: DSP relative function

    LOG_NOT_SUPPORTED();

    return S_OK;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetLFO
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetLFO)
(
    X_CDirectSoundStream*   pThis,
    LPCDSLFODESC            pLFODesc)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pThis)
        LOG_FUNC_ARG(pLFODesc)
        LOG_FUNC_END;

    // NOTE: DSP relative function

    LOG_NOT_SUPPORTED();

    return S_OK;
}

// ******************************************************************
// * patch: IDirectSoundStream_SetLFO
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_SetLFO)
(
    X_CDirectSoundStream*   pThis,
    LPCDSLFODESC            pLFODesc)
{
    DSoundMutexGuardLock;

    LOG_FORWARD("CDirectSoundStream_SetLFO");

    return xbox::EMUPATCH(CDirectSoundStream_SetLFO)(pThis, pLFODesc);
}

// ******************************************************************
// * patch: CDirectSoundStream_SetMaxDistance
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetMaxDistance)
(
    X_CDirectSoundStream*   pThis,
    D3DVALUE                flMaxDistance,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(flMaxDistance)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DBuffer_SetMaxDistance(pThis->EmuDirectSound3DBuffer8, flMaxDistance, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetMinDistance
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetMinDistance)
(
    X_CDirectSoundStream*   pThis,
    D3DVALUE                fMinDistance,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(fMinDistance)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DBuffer_SetMinDistance(pThis->EmuDirectSound3DBuffer8, fMinDistance, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetMixBins
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetMixBins)
(
    X_CDirectSoundStream*   pThis,
    X_DSMIXBINBUNION  mixBins) // Can be dword_xt (up to 4039) or X_LPDSMIXBINS (4039+)
{
    DSoundMutexGuardLock;

    if (g_LibVersion_DSOUND < 4039) {
        LOG_FUNC_BEGIN
            LOG_FUNC_ARG(pThis)
            LOG_FUNC_ARG(mixBins.dwMixBinMask)
            LOG_FUNC_END;
    }
    else {
        LOG_FUNC_BEGIN
            LOG_FUNC_ARG(pThis)
            LOG_FUNC_ARG(mixBins.pMixBins)
            LOG_FUNC_END;
    }

    return HybridDirectSoundBuffer_SetMixBins(pThis->Xb_VoiceProperties, mixBins, pThis->EmuBufferDesc);
}

// ******************************************************************
// * patch: IDirectSoundStream_SetMixBins
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_SetMixBins)
(
    X_CDirectSoundStream*   pThis,
    X_DSMIXBINBUNION  mixBins) // Can be dword_xt (up to 4039) or X_LPDSMIXBINS (4039+)
{
    DSoundMutexGuardLock;

    LOG_FORWARD("CDirectSoundStream_SetMixBins");

    return xbox::EMUPATCH(CDirectSoundStream_SetMixBins)(pThis, mixBins);
}

// ******************************************************************
// * patch: CDirectSoundStream_SetMode
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetMode)
(
    X_CDirectSoundStream*   pThis,
    dword_xt                   dwMode,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(dwMode)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DBuffer_SetMode(pThis->EmuDirectSound3DBuffer8, dwMode, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetMixBinVolumes_12
// This revision API was used in XDK 3911 until API had changed in XDK 4039.
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetMixBinVolumes_12)
(
    X_CDirectSoundStream*   pThis,
    dword_xt                   dwMixBinMask,
    const long_xt*             alVolumes)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(dwMixBinMask)
		LOG_FUNC_ARG(alVolumes)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSoundBuffer_SetMixBinVolumes_12(pThis->EmuDirectSoundBuffer8, dwMixBinMask, alVolumes, pThis->Xb_VoiceProperties,
                                                              pThis->EmuFlags, pThis->Xb_VolumeMixbin, &pThis->Xb_Voice);

    // The fold's own SetVolume carries no 3D term; nothing to redo unless 3D data is in use.
    CxbxrStream3D_Reapply(pThis, CXBXR_STREAM3D_VOLUME);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetMixBinVolumes_8
// ******************************************************************
// This revision API is only used in XDK 4039 and higher.
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetMixBinVolumes_8)
(
    X_CDirectSoundStream*   pThis,
    X_LPDSMIXBINS           pMixBins)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(pMixBins)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSoundBuffer_SetMixBinVolumes_8(pThis->EmuDirectSoundBuffer8, pMixBins, pThis->Xb_VoiceProperties,
                                                              pThis->EmuFlags, pThis->Xb_VolumeMixbin, &pThis->Xb_Voice);

    // The fold's own SetVolume carries no 3D term; nothing to redo unless 3D data is in use.
    CxbxrStream3D_Reapply(pThis, CXBXR_STREAM3D_VOLUME);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetOutputBuffer
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetOutputBuffer)
(
    X_CDirectSoundStream*   pThis,
    XbHybridDSBuffer*       pOutputBuffer)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(pOutputBuffer)
		LOG_FUNC_END;

    // On the Xbox this routes the stream's dry signal into pOutputBuffer, a MIXIN buffer
    // owned by an XACT 3D sound source whose placement the 3D calculator drives (NULL
    // unroutes). Host DirectSound cannot chain buffers, so the submix itself is still not
    // emulated - the stream keeps playing directly, exactly as before - but the routing is
    // recorded so the parent's pan can be applied to this stream (CxbxrStream3D_PanMb).
    // Upstream test case for the routing itself: Red Faction 2.
    pThis->Xb_OutputParent = pOutputBuffer;

    { // DS3D trace, bounded (see IDirectSoundStream_Set3DVoiceData)
        static unsigned s_Seen = 0;
        if (++s_Seen <= 40) {
            printf("DS3D: stream %p SetOutputBuffer parent=%p use=%d\n",
                (void *)pThis, (void *)pOutputBuffer, (int)pThis->Xb_3D.bUse);
            fflush(stdout);
        }
    }

    // The pan source changed; nothing to redo unless 3D data is in use on this stream.
    CxbxrStream3D_Reapply(pThis, CXBXR_STREAM3D_PAN);

    return S_OK;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetPitch
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetPitch)
(
    X_CDirectSoundStream*   pThis,
    long_xt                    lPitch)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pThis)
        LOG_FUNC_ARG(lPitch)
        LOG_FUNC_END;

    HRESULT hRet = HybridDirectSoundBuffer_SetPitch(pThis->EmuDirectSoundBuffer8, lPitch, &pThis->Xb_Voice,
                                                    Cxbxr3DVoice_PitchDelta(pThis->Xb_3D)); // doppler; 0 unless 3D data is in use

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundStream_SetPitch
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_SetPitch)
(
    X_CDirectSoundStream*   pThis,
    long_xt                    lPitch)
{
    DSoundMutexGuardLock;

	LOG_FORWARD("CDirectSoundStream_SetPitch");

    return xbox::EMUPATCH(CDirectSoundStream_SetPitch)(pThis, lPitch);
}

// ******************************************************************
// * patch: CDirectSoundStream_SetPosition
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetPosition)
(
    X_CDirectSoundStream*   pThis,
    D3DVALUE                x,
    D3DVALUE                y,
    D3DVALUE                z,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(x)
		LOG_FUNC_ARG(y)
		LOG_FUNC_ARG(z)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DBuffer_SetPosition(pThis->EmuDirectSound3DBuffer8, x, y, z, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream::SetRolloffCurve
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetRolloffCurve)
(
    X_CDirectSoundStream*   pThis,
    const float_xt*            pflPoints,
    dword_xt                   dwPointCount,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(pflPoints)
		LOG_FUNC_ARG(dwPointCount)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    LOG_UNIMPLEMENTED();

    return DS_OK;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetRolloffFactor
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetRolloffFactor)
(
    X_CDirectSoundStream*   pThis,
    float_xt                   fRolloffFactor,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(fRolloffFactor)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    // NOTE: SetRolloffFactor is only supported for host primary buffer's 3D Listener.

    LOG_UNIMPLEMENTED();

    return DS_OK;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetVelocity
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetVelocity)
(
    X_CDirectSoundStream*   pThis,
    D3DVALUE                x,
    D3DVALUE                y,
    D3DVALUE                z,
    dword_xt                   dwApply)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(x)
		LOG_FUNC_ARG(y)
		LOG_FUNC_ARG(z)
		LOG_FUNC_ARG(dwApply)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSound3DBuffer_SetVelocity(pThis->EmuDirectSound3DBuffer8, x, y, z, dwApply);

    return hRet;
}

// ******************************************************************
// * patch: CDirectSoundStream_SetVolume
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(CDirectSoundStream_SetVolume)
(
    X_CDirectSoundStream*   pThis,
    long_xt                    lVolume)
{
    DSoundMutexGuardLock;

	LOG_FUNC_BEGIN
		LOG_FUNC_ARG(pThis)
		LOG_FUNC_ARG(lVolume)
		LOG_FUNC_END;

    HRESULT hRet = HybridDirectSoundBuffer_SetVolume(pThis->EmuDirectSoundBuffer8, lVolume, pThis->EmuFlags,
                                                     pThis->Xb_VolumeMixbin, &pThis->Xb_Voice,
                                                     Cxbxr3DVoice_VolumeOffsetMb(pThis->Xb_3D)); // 0 unless 3D data is in use

    return hRet;
}

// ******************************************************************
// * patch: IDirectSoundStream_SetVolume
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_SetVolume)
(
    X_CDirectSoundStream*   pThis,
    long_xt                    lVolume
)
{
    DSoundMutexGuardLock;

    LOG_FORWARD("CDirectSoundStream_SetVolume");

    return xbox::EMUPATCH(CDirectSoundStream_SetVolume)(pThis, lVolume);
}

// ******************************************************************
// * patch:  IDirectSoundStream_Set3DVoiceData
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_Set3DVoiceData)
(
    X_CDirectSoundStream*   pThis,
    dword_xt a2
)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pThis)
        LOG_FUNC_ARG(a2)
        LOG_FUNC_END;

    // a2 is the X_DS3DCALCVOICEDATA* that XACT fills from the 3D calculator: 0x38 bytes,
    // dwChangeMask first, one bit per field, set only when that field changed
    // (DS3D_CALCULATOR_CONTRACT.md 2.4). The patch is stdcall (this, pVoiceData), verified;
    // the declared signature is kept and the argument is reinterpreted here.
    const xbox::X_DS3DCALCVOICEDATA* pVoiceData = reinterpret_cast<const xbox::X_DS3DCALCVOICEDATA*>(static_cast<uintptr_t>(a2));
    if (pVoiceData == nullptr || IsBadReadPtr(pVoiceData, sizeof(xbox::X_DS3DCALCVOICEDATA))) {
        LOG_TEST_CASE("IDirectSoundStream_Set3DVoiceData with an unreadable voice data pointer");
        RETURN(X_STATUS_SUCCESS);
    }
    const DWORD dwChangeMask = *reinterpret_cast<const uint32_t*>(pVoiceData); // +0, read as the Xbox code reads it

    // Copy exactly the fields whose bits are set, as CDirectSoundVoice::Set3DVoiceData does.
    Cxbxr3DVoice_Merge(pThis->Xb_3D, pVoiceData);

    xbox::EmuDirectSoundBuffer* pParent = CxbxrStream3D_Parent(pThis);
    const LONG lVolume3DMb = Cxbxr3DVoice_VolumeOffsetMb(pThis->Xb_3D);
    const LONG lPanMb = CxbxrStream3D_PanMb(pThis, pParent);
    const LONG lPitchDelta = Cxbxr3DVoice_PitchDelta(pThis->Xb_3D);

    // DS3D stream trace, bounded: what arrived, what the merged state holds, what the host
    // gets. printf because EmuLog is off in this build; tools/ds3d_trail.py reads it.
    {
        static unsigned s_Seen = 0;
        if (++s_Seen <= 40) {
            printf("DS3D: stream %p Set3DVoiceData mask=0x%02X parent=%p use=%d dist=%ld cone=%ld direct=%ld reverb=%ld doppler=%ld az=%.1f have=0x%02X -> vol3d=%ld pan=%ld pitch=%ld\n",
                (void *)pThis, (unsigned)dwChangeMask, (void *)pThis->Xb_OutputParent, (int)pThis->Xb_3D.bUse,
                (long)pThis->Xb_3D.lDistanceVolume, (long)pThis->Xb_3D.lConeVolume, (long)pThis->Xb_3D.lDirectVolume,
                (long)pThis->Xb_3D.lReverbVolume, (long)pThis->Xb_3D.lDopplerPitch, (double)pThis->Xb_3D.flAzimuth,
                (unsigned)pThis->Xb_3D.dwHave, (long)lVolume3DMb, (long)lPanMb, (long)lPitchDelta);
            fflush(stdout);
        }
    }

    // Apply only while Use3DVoiceData(TRUE) is in effect: the Xbox copies the data
    // regardless and gates its use on that flag (ConvertVolumeValues / ConvertPitchValue).
    if (pThis->Xb_3D.bUse) {
        DWORD dwWhat = CXBXR_STREAM3D_PAN;                // the parent may have moved: its pan is pulled on every update
        if ((dwChangeMask & (0x01 | 0x02 | 0x10)) != 0) { // distance, cone, I3DL2 direct/reverb
            dwWhat |= CXBXR_STREAM3D_VOLUME;
        }
        if ((dwChangeMask & 0x20) != 0) {                 // doppler
            dwWhat |= CXBXR_STREAM3D_PITCH;
        }
        CxbxrStream3D_Apply(pThis, dwWhat);
    }

    RETURN(X_STATUS_SUCCESS);
}

// ******************************************************************
// * patch:  IDirectSoundStream_Use3DVoiceData
// ******************************************************************
xbox::hresult_xt WINAPI xbox::EMUPATCH(IDirectSoundStream_Use3DVoiceData)
(
    X_CDirectSoundStream*   pThis,
    dword_xt a2
)
{
    DSoundMutexGuardLock;

    LOG_FUNC_BEGIN
        LOG_FUNC_ARG(pThis)
        LOG_FUNC_ARG(a2)
        LOG_FUNC_END;

    // a2 is BOOL fUse (stdcall, verified). XACT calls this on every routing change with
    // TRUE iff this voice or its new parent is 3D; on the Xbox it only toggles the flag
    // that ConvertVolumeValues / ConvertPitchValue test before using the voice data.
    // Music streams start FALSE and receive FALSE, so nothing below runs for them.
    const bool bUse = (a2 != 0);
    const bool bWasInUse = pThis->Xb_3D.bUse;
    pThis->Xb_3D.bUse = bUse;

    { // DS3D trace, bounded (see IDirectSoundStream_Set3DVoiceData)
        static unsigned s_Seen = 0;
        if (++s_Seen <= 40) {
            printf("DS3D: stream %p Use3DVoiceData use=%d was=%d parent=%p\n",
                (void *)pThis, (int)bUse, (int)bWasInUse, (void *)pThis->Xb_OutputParent);
            fflush(stdout);
        }
    }

    if (bUse != bWasInUse) {
        // Turning on applies whatever has been received so far; turning off applies the
        // zeros the mapping functions now return - plain voice volume and pitch, centre
        // pan: the state a stream that never had 3D data is in.
        CxbxrStream3D_Apply(pThis, CXBXR_STREAM3D_ALL);
    }

    RETURN(X_STATUS_SUCCESS);
}
