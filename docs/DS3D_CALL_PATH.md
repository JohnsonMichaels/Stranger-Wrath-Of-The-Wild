# DS3D_CALL_PATH — the positional-sound call path, statically traced

Scope: the May-2004 Xbox devkit beta (`C:\Users\<you>\SWBeta\Game\default.xbe`, base
0x10000, guest VA = PDB RVA + 0x10920) running on the Cxbx-Reloaded fork. Question:
what path does a positional sound effect take from the game into the Xbox DirectSound
library, and where does the emulator's patch layer intercept it.

Method. Every edge below is a disassembled `call`/`jmp` (VA -> target) produced by
`tools/ds3d_disasm.py` (new; reads the 2-column `swsyms.tsv`, annotates every target
with its patch status from `oddbeta/diagnostics.txt`), or a whole-image byte scan for
`E8/E9/0F 8x rel32` sites (`callers`, `xbe_refs.py calls`). Nothing was run.

Status legend (authoritative source: `diagnostics.txt` `HLE:` lines, libraries
DSOUND 5849 / XACTENG):

| tag | meaning |
|---|---|
| PATCHED | `HLE: X Patched` — Cxbx intercepts; the guest body never runs |
| PATCHED-STUB | PATCHED, and the Cxbx body is `LOG_UNIMPLEMENTED` / `LOG_NOT_SUPPORTED` (returns success, does nothing) |
| UNPATCHED-NATIVE | `HLE: X: No patch registered (symbol found at ...)` — guest code runs |
| NATIVE | not in Cxbx's symbol database at all (all of XACTENG, all of the game) — guest code runs |

Object-layout notes used throughout (from `XACT::CSoundSource::Reset3DProperties`
0x1fbfbd and the setters): `XACT::CSoundSource` is 0x164 bytes; `+0x14` flags
(`0x20000000` = title-created source = DSound MIXIN submix, `0x40000000` = stream,
`0x2` = XACT_FLAG_SOUNDSOURCE_3D), `+0x1c` IDirectSoundBuffer*, `+0x20`
IDirectSoundStream*, `+0x40` "has voice", `+0x44` dirty flags + DS3DBUFFER-like params
(`+0x4c` position, `+0x58` velocity, `+0x84` mode ...), `+0xc0` I3DL2 source params,
`+0xe8` DS3DVOICEDATA (0x38 bytes, `dwFlags` first), `+0x150` output (parent) source,
`+0x154/+0x15c` child list links.

---

## 1. The positional path, in order

### 1a. Setup (once, `AudioResourceMgr::AppInit` 0x12e515)

Every game-side sound source is an XACT sound source created with flags = 2
(`XACT_FLAG_SOUNDSOURCE_3D`); one extra "wet 2D" source is created with flags = 1.

```
0012e5e8  push 2
0012e5ea  call AudioDevice::CreateSoundSource        ; loop -> s_temp3DXACTSoundSources (0x2b8a50)
0012e63e  push 0x2b8a40                              ; &s_pWet2DSoundSource
0012e643  push 1
0012e645  call AudioDevice::CreateSoundSource
00146225  push 2                                     ; SoundSource::SoundSource (every AudioEmitter source)
00146235  call AudioDevice::CreateSoundSource
00149cc2  call IXACTEngine_CreateSoundSource         ; AudioDevice::CreateSoundSource -> XACTENG
```

`IXACTEngine_CreateSoundSource` 0x1f7cea ORs `0x20000000` into the flags (0x1f7cfe)
and allocates:

```
001f7d12  call XACT::CEngine::AllocateSoundSource
001f8d1c  mov  [esi+0x14], eax                       ; flags stored
001f8d1f  call XACT::CSoundSource::CreateDSoundVoice (0x1fb059)
001fb07a  test edi(0x20000000), eax
001fb090  or   byte [ebp-0x27], 0x20                 ; DSBUFFERDESC.dwFlags |= 0x2000  (DSBCAPS_MIXIN)
001fb094  test al, 2                                 ; XACT 3D source?
001fb096  mov  [ebp-0x1c], 0x1de114                  ; lpMixBins = DirectSoundDefaulMixBins_5Channel3D_PlusLFE {6,8,7,9,2,10,3}
001fb09f  or   byte [ebp-0x26], 0x60                 ; 3D: dwFlags |= 0x600000
001fb0ab  mov  [ebp-0x1c], 0x1de11c                  ; 3D: lpMixBins = DirectSoundRequiredMixBins_5Channel3D {6,8,7,9,2}
001fb11b  call IDirectSound_CreateSoundBuffer        ; PATCHED   (lpwfxFormat = NULL for a MIXIN buffer)
001fb137..0x1fb164                                   ; 3D: link into XACT::CSoundSource::m_3DSubmixSources (0x202490)
001f7d21  call XACT::CSoundSource::Initialize (0x1fb16a)
001fb18a  call XACT::CSoundSource::SetOutputBuffer   ; (NULL, 0)
001fb1a1  call XACT::CSoundSource::Reset3DProperties
```

So an XACT "sound source" on Xbox is a DirectSound **submix (MIXIN) buffer** carrying the
3D mixbins. The audible wave voices are separate (1e) and are *routed into it*.

Cxbx side of `IDirectSound_CreateSoundBuffer` (`DirectSoundBuffer.cpp:163-275`):
`dwAcceptableMask = 0x10|0x20|0x80|0x100|0x20000|0x40000`, so `0x2000` and `0x600000`
are stripped (warning), no `DSBCAPS_CTRL3D` is ever passed by XACT, hence no host 3D
buffer and the fork's `DS3DMODE_DISABLE` hack (lines 244-268) never applies to any XACT
voice. With `lpwfxFormat == NULL` the source becomes a `DSE_FLAG_RECIEVEDATA` host
buffer that never receives data and is never played. The submix does not exist on the
host. (Those are the twelve `DSOUND: volume=-600 emuFlags=0x00100000` lines in
diagnostics.txt — creation-time volume of the MIXIN sources; see §5.)

### 1b. Per frame: positions, listener, commit (game .text)

```
00126478  jmp  AudioMgr::TickSub (0x1263e6)                       ; AudioMgr::Tick
001263fe  call AudioResourceMgr::TickAllActiveAudioEmitters
0012e24d  call AudioEmitter::Tick (0x139299)
001392c1  call SoundSource::SetPosition (0x14616f)                ; and 0x1392d4
0014620b  call SoundSource::SetPositionNoVelocity (0x145fca)
00145fed  call AudioUtil::TransformToXACTCoordSystem
00145ff7  push 1                                                  ; fDeferred = TRUE
00146010  call IXACTSoundSource::SetPosition (0x125514)
00125534  call IXACTSoundSource_SetPosition (0x1f841d)            ; -> XACTENG
001f843d  call XACT::CSoundSource::SetPosition (0x1fb921)
001fb940  mov  [edx], eax   ... [esi+0x50], [esi+0x54]            ; vPosition at +0x4c
001fb95b  or   [esi+0x44], ebx(0x10000)                           ; position dirty; propagated to children (+0x15c list)
001fb983  call XACT::CSoundSource::CommitDeferredSettings         ; only if !fDeferred (skipped here)
```

Fire-and-forget positional cues set position/velocity on the chosen source in
`AudioMgr::PlayAutoReleaseSub` (0x1259f6 / 0x125a28). Listener:
`GameAudioMgr::UpdateListener` (0xe04f8, 0xe0645) -> `Listener::SetTransform` 0x143995
-> `IXACTEngine::SetListenerPosition` 0x149c7d -> `IXACTEngine_SetListenerPosition`
0x1f7f4c -> `call XACT::CSoundSource::CommitDeferredSettings` 0x1f7f8b (orientation
0x1f7f1c, velocity 0x1f7fee, doppler 0x1f82a6 likewise).

Commit, every tick:

```
0012640b  call AudioDevice::CommitDeferredSettings (0x149e93)
00149e9e  call IXACTEngine_CommitDeferredSettings (0x1f823b)
001f8259  call IDirectSound_CommitDeferredSettings (0x1d4bb2)     ; PATCHED  (host 3D listener only, DirectSound.cpp:527-556)
001f8264  call XACT::CSoundSource::CommitDeferredSettings (0x1fbce0)
00126418  call XACTEngineDoWork (0x1f85a4)
001f85bb  call DirectSoundDoWork (0x1d383f)                       ; PATCHED  (DirectSound.cpp:344-364)
001f85c2  call XACT::CEngine::DoWork (0x1f90b0)
001f90fa  call IDirectSound_CommitDeferredSettings                ; PATCHED
001f90ff  call XACT::CSoundSource::CommitDeferredSettings
```

### 1c. `XACT::CSoundSource::CommitDeferredSettings` 0x1fbce0 (NATIVE)

Reads `g_dwDirectSoundSpeakerConfig` (DSOUND data 0x1de3c8) directly at 0x1fbce6 to
derive `m_fSurround`; walks `m_3DSubmixSources` (0x202490) and `m_3DNormalSources`
(0x202488) and, for every source with a voice (`[src+0x40] != 0`):

```
001fbe2c  call XACT::CSoundSource::Update3DProperties (0x1fbebb)   ; submix sources
001fbe8e  call XACT::CSoundSource::Update3DProperties              ; normal (track) sources
001fbea5  and  [m_3DListener], 0 ; 001fbeac and [m_I3DL2Listener], 0
```

### 1d. `XACT::CSoundSource::Update3DProperties` 0x1fbebb — the 3D calculator calls

For a submix/title source (`[esi+0x17] & 0x20`, i.e. flags & 0x20000000):

```
001fbed8  and  dword [eax], 0x10000                  ; keep only the position-dirty bit of +0x44
001fbedf  mov  edi, 0x2026e0                         ; &XACT::CSoundSource::m_3DListener
001fbee5  call IDirectSound3DCalculator_Calculate3D (0x1d46f2)   ; (pListener, &this->+0x44)
001fbf02  call IDirectSound3DCalculator_GetVoiceData (0x1d3836)  ; (pListener, &this->+0x44, &m_I3DL2Listener 0x202498, &this->+0xc0, &this->+0xe8)
001fbf07  and  dword [ebx], 0xdc                      ; DS3DVOICEDATA.dwFlags mask
001fbf1d  or   dword [eax-0x6c], 0x10                 ; propagate bit 0x10 to every child voice
001fbf8c  call IDirectSoundBuffer_Set3DVoiceData (0x1d37af)      ; (this->+0x1c buffer, &this->+0xe8)
001fbfa3  mov  [esi+0x44], 0 ; 001fbfa6 mov [esi+0xe8], 0        ; clear dirty flags and voice-data flags
```

For a track voice (no 0x20000000) the same two calculator calls (0x1fbf47, 0x1fbf64)
run only if it has a 3D parent (`[esi+0x150]` with `+0x14 & 2`, tests at 0x1fbf33 /
0x1fbf37), then `and byte [ebx], 0x33` (0x1fbf69), copy of the parent's `+0x108`
(0x1fbf7d) and the same `Set3DVoiceData` (buffer 0x1fbf8c, or stream
`IDirectSoundStream_Set3DVoiceData` 0x1fbf97 -> 0x1d382c -> `jmp` 0x1d34c9 PATCHED).

The DSOUND entries, one level down:

| entry (VA) | status | what it is |
|---|---|---|
| `IDirectSound3DCalculator_Calculate3D` 0x1d46f2 | UNPATCHED-NATIVE | 5 bytes: `e9dafcffff jmp 0x1d43d1` |
| `DirectSound::CDirectSound3DCalculator::Calculate3D` 0x1d43d1 | **PATCHED-STUB** | `EMUPATCH(CDirectSound3DCalculator_Calculate3D)` = `LOG_UNIMPLEMENTED` (DirectSound3DCalculator.cpp:52-66, Patches.cpp:241). Native body: `CFullHRTFListener::CalcNormOrient` 0x1d441c, `CHRTFSource::CalcPolarCoords` 0x1d4514, CHRTFSource vtable calls 0x1d449c/0x1d4567/0x1d45b2 |
| `IDirectSound3DCalculator_GetVoiceData` 0x1d3836 | UNPATCHED-NATIVE | `push ebp; mov ebp,esp; pop ebp;` 0x1d383a `e993f3ffff jmp 0x1d2bd2` |
| `DirectSound::CDirectSound3DCalculator::GetVoiceData` 0x1d2bd2 | **PATCHED-STUB** | `EMUPATCH(CDirectSound3DCalculator_GetVoiceData)` = `LOG_UNIMPLEMENTED` (:71-89, Patches.cpp:242). Native body fills DS3DVOICEDATA from `CHRTFSource::GetDistanceVolume` 0x1d2c44, `GetConeVolume` 0x1d2c80, `GetFrontRearVolume` 0x1d2cd9, `GetCenterVolume` 0x1d2d2c, `GetDopplerPitch` 0x1d2d86, `CI3DL2Source::CalculateI3DL2` 0x1d2e4d |
| `IDirectSoundBuffer_Set3DVoiceData` 0x1d37af | **PATCHED-STUB** | `EMUPATCH(IDirectSoundBuffer_Set3DVoiceData)` = `LOG_UNIMPLEMENTED`, returns success (DirectSoundBuffer.cpp:1690-1707, Patches.cpp:304). Native: 0x1d37c3 `call CDirectSoundBuffer::Set3DVoiceData` 0x1d319f (PATCHED as CDirectSoundBuffer_Set3DVoiceData) -> 0x1d31d0 `call CDirectSoundVoice::Set3DVoiceData` 0x1d223f (UNPATCHED-NATIVE, never reached) which copies fields per `dwFlags` bits 1/2/4/8/0x10/0x20/0x40/0x80 into the voice settings (+0xb8) and calls `CMcpxVoiceClient::Apply3dSettings` 0x1d2353 (hardware) |

Net effect on Cxbx: `+0xe8` is never written (the stubs do not touch their out
parameters; XACT zeroes it at 0x1fbfa6 after every update), and even a computed voice
data block would be dropped by the `Set3DVoiceData` stub. This is the "3D calculator
missing" gap already in BETA_REPORT — it is real, but on its own it would make positional
voices play *unpositioned*, not silent. What silences them is in 1f/§2.

### 1e. Per play: game -> XACT -> track voice (ordered)

Game side, both play entry points end in `IXACTSoundBank_Play`:

```
00139c9f  call AudioMgr::Play (0x125b50)  -> 00125b5a jmp AudioMgr::PlaySub (0x125a91)   ; AudioEmitter::Play
00125ac5  call SoundCueMgr::GetForced2DState (0x13a425)
00125ae4  call AudioResourceMgr::LockSoundSource         ; 3D: a SoundSource (flags-2 XACT source)
00125b04  call AudioResourceMgr::GetWet2DSoundSource     ; forced-2D == 1: s_pWet2DSoundSource
00125b1a  call CueTracker::PrepareForUse
001398b6  call CueTracker::Trigger (0x145dff)            ; AudioEmitter::Trigger
00145e48  call IXACTSoundBank_Play (0x125591)

00125a8b  jmp  AudioMgr::PlayAutoReleaseSub (0x12595d)   ; AudioMgr::PlayAutoRelease
00125986  call SoundCueMgr::GetForced2DState
001259ba  call AudioResourceMgr::GetTemp3DXACTSoundSource ; 3D without an emitter: rotating temp source
001259f6  call IXACTSoundSource::SetPosition ; 00125a28 SetVelocity
00125a34  call AudioResourceMgr::GetWet2DSoundSource     ; forced-2D == 1 ; otherwise source = NULL
0012643f  call AudioMgr::Flush (0x1257b7)                ; from TickSub
00125805  call IXACTSoundBank_Play

001255b7  call IXACTSoundBank_PlayEx (0x1f836d)          ; IXACTSoundBank_Play wrapper
001f8383  call XACT::CSoundBank::Play (0x1fa604)
```

`XACT::CSoundBank::Play` 0x1fa604:

```
001fa69a  call XACT::CSoundBank::Prepare        ; -> CreateCue 0x1fa571 -> CSoundCueInstance::Initialize 0x1fc756 -> CSoundCue::Initialize -> 0x1fdbea AttachSoundSource
001fa6ba  call XACT::CSoundCueInstance::AttachSoundSource (0x1fcc6f)
001fcc84  mov  [edi+0x1c], esi                  ; cue instance keeps the title source
001fcc9a  call XACT::CSoundCue::AttachSoundSource (0x1fdc07)
001fdc32  movzx ecx, byte [ecx+1] ; and ecx,1 ; push ecx     ; per-track flag
001fdc3a  call XACT::CSoundSource::SetOutputBuffer (0x1fb200) ; for every track that already has a voice
001fa6db  call XACT::CSoundCueInstance::Set3DProperties (0x1fd042)
001fd058  test byte [eax+0x14], 2 ; je            ; only for a 3D title source
001fd07d  call XACT::CSoundCue::Set3DProperties (0x2005e2)   ; -> 0x20062c CSoundCue::Update3DProperties per track, 0x20064c SetMode, 0x200660 CommitDeferredSettings
001fd086  call XACT::CSoundCue::UpdateVolume (0x1fe323)      ; -> 0x1fe338 UpdateTrackVolume per track
001fa6e6  call XACT::CSoundCueInstance::EnQueueCue | 001fa6ef call XACT::CSoundCueInstance::Activate
001fcb66  call XACT::CSoundCue::Activate (0x1fde14)  ; (queued cues: 0x1ffe9b from CSoundCue::DoWork)
001fde3b  call XACT::CSoundCue::Prepare (0x1fdd1d)
001fddbc  call XACT::CSoundCue::PreProcessTrackPlayEvent (0x1feed4)
001fde80  call XACT::CSoundCue::ScheduleContentEvents
```

`XACT::CSoundCue::PreProcessTrackPlayEvent` 0x1feed4, per track, in address order:

| # | site | call | DSOUND entry reached | status |
|---|---|---|---|---|
| 1 | 0x1feffb `cmp [esi],0` / 0x1ff035 | `XACT::CEngine::CreateSoundSourceInternal` (only if no voice yet) -> 0x1f8e91 `AllocateSoundSource` -> 0x1f8d1f `CreateDSoundVoice` (no 0x20000000: 0x1fb0d3 `XAudioCreatePcmFormat`, 0x1fb0e6 `dwFlags |= 0x400000`, lpMixBins = NULL) -> 0x1fb11b | `IDirectSound_CreateSoundBuffer` | PATCHED (host mixbins default {0,1}) |
| 2 | 0x1ff050 | `XAudioCalculatePitch` 0x1d1e1a | pure arithmetic | UNPATCHED-NATIVE |
| 3 | 0x1ff05f | `XACT::CSoundSource::SetFormat` -> 0x1fb752 | `IDirectSoundBuffer_SetFormat` 0x1d46b1 | PATCHED (regenerates `Xb_VoiceProperties` = {0,1}, DirectSoundBuffer.cpp:1001-1020, DirectSoundInline.hpp:1197-1222) |
| 4 | 0x1ff068 | `UpdateTrackPitch` -> `CSoundSource::SetPitch` 0x1fb52a | `IDirectSoundBuffer_SetPitch` 0x1d35e3 | PATCHED |
| 5 | 0x1ff06f | `UpdateTrackVolume` 0x1fe352 -> 0x1fe3ec `SetVolume` -> 0x1fb4e4; 0x1fe3fb `GetVoiceProperties` -> 0x1fb70c; (0x1fe4bf `SetMixBinVolumes` only if bins 3 or 10 are present — not yet) | `IDirectSoundBuffer_SetVolume` 0x1d35c7, `_GetVoiceProperties` 0x1d3793 | PATCHED |
| 6 | 0x1ff083 | `UpdateTrackFilter` -> 0x1fb680 | `IDirectSoundBuffer_SetFilter` 0x1d3637 | PATCHED |
| 7 | 0x1ff088-0x1ff0a0 | `mov eax,[edi+0x44]; mov eax,[eax+0x1c]` (cue instance's title source) ; `mov cl,[ebx+1]; and ecx,1; push ecx` ; `mov eax,[ebx+4]` (voice) ; **`call XACT::CSoundSource::SetOutputBuffer`** | see 1f | see 1f |
| 8 | 0x1ff0c6 `test byte [eax+0xb],2` / 0x1ff0cf `push 0` / 0x1ff0d1 | `CSoundSource::SetHeadroom(voice, 0)` -> 0x1fb6c6 | `IDirectSoundBuffer_SetHeadroom` 0x1d3653 | PATCHED |
| 9 | 0x1ff0e8 | `IsPlaying` -> 0x1fb1c9 | `IDirectSoundBuffer_GetStatus` 0x1d370b | PATCHED |
| 10 | 0x1ff171 | `XACT::CWaveBank::SetBufferData` -> `CSoundSource::SetBufferData` 0x1fb645, 0x200e6b | `IDirectSoundBuffer_SetBufferData` 0x1d46cd, `_SetPlayRegion` 0x1d4068 | PATCHED |
| 11 | 0x1ff18b / 0x1ff19d | `SetCurrentPosition` -> 0x1fb794 | `IDirectSoundBuffer_SetCurrentPosition` 0x1d3747 | PATCHED |
| 12 | 0x1ff1d0 | direct | `IDirectSoundBuffer_SetLoopRegion` 0x1d36eb | PATCHED |
| 13 | 0x1ff222 | `XACT::CSoundCue::Update3DProperties` 0x20066f -> 0x2006f2 `SetMode`, 0x20070b/0x20074c `SetMaxDistance`, 0x2007e4 `SetRolloffCurve`, 0x2007f3 `CSoundSource::CommitDeferredSettings` -> 1c/1d | 3D calculator + `Set3DVoiceData` | PATCHED-STUB (all three) |

Then the wave is actually started by the sequencer. `CSoundCue::Activate` ->
0x1fde80 `ScheduleContentEvents` -> events queued; `XACT::CEngine::DispatchEvent`
0x2019ab has no direct caller — `CEngine::Dispatch` 0x20192b / `DispatchEventsUntil`
0x201953 sit in `XACT::CEngine::vftable` (0x259408 / 0x25940c) and `DPCTimerCallBack`
0x202200 is registered in the constructor (0x1f871c); the DoWork -> dispatcher edge is
therefore **INFERRED** (indirect). The PLAY-event case of the `jmp [eax*4+0x201e01]`
switch (0x2019ec) is unambiguous:

```
00201a0e  call XACT::CEngine::SelectParameterVariation   ; pitch variation
00201a1f  call XACT::CEngine::SelectParameterVariation   ; volume variation
00201a2c  call XACT::CSoundCue::UpdateTrackPitch
00201a33  call XACT::CSoundCue::UpdateTrackVolume        ; <-- runs AFTER SetOutputBuffer (step 7)
00201a42  test byte [esi], 1
00201a49  call XACT::CSoundSource::Play (0x1fb552)
001fb574  call IDirectSoundBuffer_Play (0x1d368b)        ; PATCHED  (the S_OK seen in the log)
```

`XACT::CSoundSource::SetFrequency` is not used; XACT only calls `SetPitch`.
`IDirectSoundBuffer_SetFrequency` has no XACTENG caller.

### 1f. `XACT::CSoundSource::SetOutputBuffer` 0x1fb200 (this = eax, parent = edi, flag on stack) — THE DIVERGENCE

```
001fb21f  mov  ebx, [edi+0x1c]                  ; parent's DSound buffer (0 if no parent)
001fb267  ; ---- buffer voice path ----
001fb274  call IDirectSoundBuffer_GetVoiceProperties (0x1d3793)  ; PATCHED   (only if flag)
001fb27d  call IDirectSoundBuffer_SetOutputBuffer (0x1d4030)     ; PATCHED-STUB  LOG_NOT_SUPPORTED, returns S_OK (DirectSoundBuffer.cpp:1351-1372)
001fb285  test byte [esi+0x14], bl(2) ; 001fb28e test byte [edi+0x14], bl   ; this or parent 3D?
001fb29c  call IDirectSoundBuffer_Use3DVoiceData (0x1d37cb)      ; PATCHED-STUB  LOG_NOT_SUPPORTED (:1711-1727); arg = 1 iff 3D
001fb2a3  cmp  [ebp+0x7c], ecx(0)               ; flag == 0 ?
001fb2df  cmp  edi, 0 ; 001fb2e3 test byte [edi+0x14], 2         ; parent exists and is 3D ?
001fb2f1  mov  [ebp+0x6c], ebx(2)               ; DSMIXBINS.dwCount = 2
001fb2f4  mov  [ebp+0x28], 0xa ; 001fb2fb [ebp+0x2c], 0            ; pair 0 = (DSMIXBIN_I3DL2 = 10, 0)
001fb2fe  mov  [ebp+0x30], 3   ; 001fb305 [ebp+0x34], 0            ; pair 1 = (DSMIXBIN_LOW_FREQUENCY = 3, 0)
001fb31f  call IDirectSoundBuffer_SetMixBins (0x1d404c)          ; PATCHED  -> GenerateMixBinDefault: Xb_VoiceProperties = {(10,0),(3,0)}
001fb349  mov  [esi+0x150], edi                 ; remember parent; link into parent's child list (+0x15c)
001fb379  test byte [eax+0x14], bl              ; parent 3D ?
001fb380  call XACT::CSoundSource::Get3DProperties (0x1fc0dc)     ; copy parent position/velocity/cone/mode into this, set dirty bits
001fb385  call XACT::CSoundSource::Update3DProperties (0x1fbebb)  ; -> 1d (both calculator stubs, Set3DVoiceData stub)
```

(With flag = 1 the alternative block 0x1fb2a8-0x1fb2d3 re-submits the voice's own
eight pairs plus bin 0x1f instead; which tracks carry that flag is data — unknown.)

On Xbox this is coherent: the track voice keeps only its I3DL2 send and LFE bins
*directly*, its dry signal goes through `SetOutputBuffer` into the title source's
MIXIN buffer, and that submix carries the 3D bins {6,8,7,9,2} whose volumes come from
`Set3DVoiceData`. On Cxbx `SetOutputBuffer` and `Use3DVoiceData` are no-ops, so the
track voice plays *directly* to the host — with a mixbin set that contains no speaker
bin at all.

### 1g. Patch-status summary of every DSOUND entry on the positional path

| DSOUND entry | VA | status | Cxbx body |
|---|---|---|---|
| `IDirectSound_CreateSoundBuffer` | 0x1d4e1a | PATCHED | `DirectSoundCreateBuffer` (strips MIXIN/0x600000, no CTRL3D) |
| `IDirectSoundBuffer_SetFormat` | 0x1d46b1 | PATCHED | resets `Xb_VoiceProperties` to stereo |
| `IDirectSoundBuffer_SetPitch` | 0x1d35e3 | PATCHED | host frequency |
| `IDirectSoundBuffer_SetVolume` | 0x1d35c7 | PATCHED | `HybridDirectSoundBuffer_SetVolume` = voice volume + `Xb_volumeMixbin` |
| `IDirectSoundBuffer_GetVoiceProperties` | 0x1d3793 | PATCHED | copies `Xb_VoiceProperties` |
| `IDirectSoundBuffer_SetFilter` | 0x1d3637 | PATCHED | — |
| `IDirectSoundBuffer_SetOutputBuffer` | 0x1d4030 | **PATCHED-STUB** | `LOG_NOT_SUPPORTED` |
| `IDirectSoundBuffer_Use3DVoiceData` | 0x1d37cb | **PATCHED-STUB** | `LOG_NOT_SUPPORTED` |
| `IDirectSoundBuffer_SetMixBins` | 0x1d404c | PATCHED | `GenerateMixBinDefault` (honoured: {10,3}) |
| `IDirectSound3DCalculator_Calculate3D` | 0x1d46f2 | UNPATCHED-NATIVE -> `jmp` | `CDirectSound3DCalculator::Calculate3D` 0x1d43d1 **PATCHED-STUB** |
| `IDirectSound3DCalculator_GetVoiceData` | 0x1d3836 | UNPATCHED-NATIVE -> `jmp` | `CDirectSound3DCalculator::GetVoiceData` 0x1d2bd2 **PATCHED-STUB** |
| `IDirectSoundBuffer_Set3DVoiceData` | 0x1d37af | **PATCHED-STUB** | `LOG_UNIMPLEMENTED` |
| `IDirectSoundBuffer_SetHeadroom` | 0x1d3653 | PATCHED | HLE voice headroom |
| `IDirectSoundBuffer_GetStatus` | 0x1d370b | PATCHED | host status |
| `IDirectSoundBuffer_SetBufferData` / `_SetPlayRegion` | 0x1d46cd / 0x1d4068 | PATCHED | host data (does not touch mixbins) |
| `IDirectSoundBuffer_SetCurrentPosition` / `_SetLoopRegion` | 0x1d3747 / 0x1d36eb | PATCHED | host |
| `IDirectSoundBuffer_SetMixBinVolumes` | 0x1d366f | PATCHED (as `_SetMixBinVolumes_8`) | `HybridDirectSoundBuffer_SetMixBinVolumes_8` — see §2 |
| `IDirectSoundBuffer_Play` | 0x1d368b | PATCHED | host `Play`, returns DS_OK |
| `IDirectSound_CommitDeferredSettings` | 0x1d4bb2 | PATCHED | host primary 3D listener commit only |
| `DirectSoundDoWork` | 0x1d383f | PATCHED | buffer/stream packet work, nothing 3D |

---

## 2. The non-positional path and the exact divergence

Game side. `SoundCueMgr::GetForced2DState(cue)` is consulted at 0x125986
(`PlayAutoReleaseSub`), 0x125ac5 (`PlaySub`) and 0x145e2c (`CueTracker::Trigger`):
0 -> 3D source (emitter's `SoundSource`, or a temp 3D source), 1 -> `s_pWet2DSoundSource`
(0x2b8a40, created with flags = 1), anything else -> no source (NULL). Which cues are
forced-2D is data (`SoundCueMgr::SoundCueInfo`); that moolah/UI use the wet-2D or NULL
source is INFERRED from the observed behaviour, not from the tables.

XACT side, a NULL or flags-1 parent takes the *same* code with the 3D tests false:

| step | 3D parent (`[parent+0x14] & 2`) | 2D / NULL parent |
|---|---|---|
| `CSoundCueInstance::Set3DProperties` 0x1fd058 | runs (`Update3DProperties`, `SetMode`, `UpdateVolume`) | `je` — skipped |
| `SetOutputBuffer` 0x1fb29c `Use3DVoiceData` | arg 1 | arg 0 |
| `SetOutputBuffer` 0x1fb2e3 -> 0x1fb31f `SetMixBins` | **voice mixbins := {(10,0),(3,0)}** | not called — voice keeps its creation/SetFormat default {0,1} |
| `SetOutputBuffer` 0x1fb379 -> `Get3DProperties`/`Update3DProperties` | runs -> calculator stubs | not called |
| `Update3DProperties` on commit (0x1fbf37) | runs | returns 0 (no 3D parent) |

So the single point where the two paths hand the emulator *different* state for the
audible voice is `SetMixBins` at 0x1fb31f. Everything else that is 3D-specific lands in
a stub that returns success.

What Cxbx then does with that state (static reading of the fork, all in
`DirectSoundInline.hpp`):

1. `HybridDirectSoundBuffer_SetMixBins` (:1351-1362) -> `GenerateMixBinDefault`
   (:180-296): `Xb_VoiceProperties = {dwMixBinCount 2, (10,0), (3,0), rest 0xFFFFFFFF}`.
   No host change yet.
2. At the PLAY event `UpdateTrackVolume` (0x201a33) runs again: `GetVoiceProperties`
   (0x1fe3fb -> PATCHED, returns the table above); its loop looks for bin 3
   (0x1fe43b-0x1fe43e) and bin 10 (0x1fe46a-0x1fe46d), finds both, and calls
   `SetMixBinVolumes` (0x1fe4bf -> 0x1fb600 -> `IDirectSoundBuffer_SetMixBinVolumes`
   0x1d366f, PATCHED) with pairs (3, -50*LFE) and (10, -(I3DL2 send)<<8).
3. `HybridDirectSoundBuffer_SetMixBinVolumes_8` (:1366-1410) picks the "dominant"
   volume as the max over pairs with `dwMixBin != XDSMIXBIN_LOW_FREQUENCY(3) &&
   dwMixBin < XDSMIXBIN_SPEAKERS_MAX(6)` (:1396). Neither 10 nor 3 qualifies, so
   `Xb_volumeMixBin = DSBVOLUME_MIN (-10000)`, then `HybridDirectSoundBuffer_SetVolume`
   (:1557-1573) adds it to the voice volume and `DSoundBufferUpdateHostVolume`
   (:750-800) clamps anything `<= -6400` to `DSBVOLUME_MIN` and calls the host
   `SetVolume(-10000)`.
4. `CSoundSource::Play` -> `IDirectSoundBuffer_Play` succeeds (DS_OK) on a buffer that
   is now at minimum volume. Nothing later restores it: `Xb_VoiceProperties` is only
   rewritten by `SetFormat` (before the attach) and `SetMixBins`; `SetBufferData`,
   `SetPlayRegion`, `SetLoopRegion`, `SetCurrentPosition` and `Play` do not touch it
   (grep over DirectSoundBuffer.cpp: lines 228, 1014-1018, 1228, 1250, 1273 only).

For a 2D/NULL-parent voice the properties stay {(0,0),(1,0)}: no bin 3/10, so
`SetMixBinVolumes` is never issued, `Xb_volumeMixbin` stays 0 and the host volume is the
track volume. That is the observed split: 2D audible, positional silent, every `Play`
returning DS_OK with valid decoded data.

Marking: steps 1-4 are INFERRED from static reading of both binaries and the fork's
source; the runtime `-10000` has not been observed (see §5 for why the existing
diagnostics could not have shown it).

Second-order consequence even after that mute is fixed: because `SetOutputBuffer` is
unsupported, the dry signal of a positional voice has no path to any 3D processing; the
XACT-side position (`+0x4c`), the listener (`m_3DListener` 0x2026e0) and the calculator
outputs are all computed for the *submix* object, which Cxbx never plays. Real
positioning needs the calculator (or an HLE equivalent applied to the child voices),
not just un-muting.

---

## 3. XACTENG -> unpatched DSOUND: the complete list

Every direct `call`/`jmp` leaving XACTENG for a DSOUND symbol was enumerated
(`ds3d_disasm.py seccalls XACTENG`, per-function linear sweep; 70 sites). Targets that
run natively:

| site (XACTENG) | target | status / where it lands |
|---|---|---|
| 0x1ff050 `PreProcessTrackPlayEvent+0x17c` | `XAudioCalculatePitch` 0x1d1e1a | UNPATCHED-NATIVE — pure arithmetic |
| 0x1fb0d3, 0x1ff155, 0x1ff270, 0x200a75, 0x2010b2 | `XAudioCreatePcmFormat` 0x1d1e64 | UNPATCHED-NATIVE — fills a WAVEFORMATEX |
| 0x2010cf `CWaveBank::GetWaveEntryFormat+0x59` | `XAudioCreateAdpcmFormat` 0x1d1e69 | UNPATCHED-NATIVE — fills a WAVEFORMATEX |
| 0x1fbee5, 0x1fbf47 `Update3DProperties` | `IDirectSound3DCalculator_Calculate3D` 0x1d46f2 | UNPATCHED thunk -> `jmp 0x1d43d1` PATCHED-STUB |
| 0x1fbf02, 0x1fbf64 `Update3DProperties` | `IDirectSound3DCalculator_GetVoiceData` 0x1d3836 | UNPATCHED thunk -> `jmp 0x1d2bd2` PATCHED-STUB |
| 0x1fb60b `CSoundSource::SetMixBinVolumes+0x29` | `IDirectSoundStream_SetMixBinVolumes` 0x1d3805 | UNPATCHED thunk -> `jmp CDirectSoundStream::SetMixBinVolumes` 0x1d3425 PATCHED |
| 0x1fb19a, 0x1fb3d6, 0x1fb581 | `IDirectSoundStream_Pause` 0x1d380a | UNPATCHED thunk -> `jmp` 0x1d2b17 PATCHED |
| 0x1fb439, 0x1ff719 | `IDirectSoundStream_FlushEx` 0x1d380f | UNPATCHED wrapper -> 0x1d381f `call CDirectSoundStream::FlushEx` 0x1d2b68 PATCHED |
| 0x1fb238, 0x1fb717 | `IDirectSoundStream_GetVoiceProperties` 0x1d3827 | UNPATCHED thunk -> `jmp` 0x1d3477 PATCHED |
| 0x1fb241 `SetOutputBuffer+0x41` | `IDirectSoundStream_SetOutputBuffer` 0x1d4088 | UNPATCHED thunk -> `jmp` 0x1d3f70 PATCHED |
| 0x1fb75d `CSoundSource::SetFormat+0x29` | `IDirectSoundStream_SetFormat` 0x1d46ed | UNPATCHED thunk -> `jmp` 0x1d465f PATCHED |

Every other XACTENG -> DSOUND site (58 of them: `IDirectSoundBuffer_*`,
`IDirectSoundStream_*` (patched ones), `IDirectSound_*`, `DirectSoundCreate`,
`DirectSoundDoWork`) targets a PATCHED entry. **No XACTENG site targets any
`CDirectSoundVoice_*`, `CDirectSoundVoiceSettings_*` or `CDirectSound_DoWork`
function**, directly or via a pointer table: a byte scan of every executable section
for `E8/E9/0F 8x` transfers to the 15 unpatched Voice functions (`xbe_refs.py calls`,
`ds3d_disasm.py callers`) finds only DSOUND-internal sites —

| unpatched function | callers (all DSOUND, all inside PATCHED class methods) |
|---|---|
| `CDirectSoundVoice::SetVolume` 0x1d2186 | 0x1d2f60 (`CDirectSoundBuffer::SetVolume`), 0x1d32c0 (`CDirectSoundStream::SetVolume`) |
| `CDirectSoundVoice::SetPitch` 0x1d216d | 0x1d2fae, 0x1d326e, 0x1d3d1a (`CDirectSoundVoice::SetFrequency`) |
| `CDirectSoundVoice::SetLFO` 0x1d21a2 / `SetEG` 0x1d21b5 / `SetFilter` 0x1d21c8 | 0x1d2ffc, 0x1d3312 / 0x1d304a, 0x1d3364 / 0x1d3098, 0x1d33b6 (`CDirectSoundBuffer::*` / `CDirectSoundStream::*`) |
| `CDirectSoundVoice::SetHeadroom` 0x1d21db | 0x1d30e6, 0x1d3408 |
| `CDirectSoundVoice::SetMixBinVolumes` 0x1d21fe | 0x1d3134, 0x1d345a |
| `CDirectSoundVoice::Set3DVoiceData` 0x1d223f | 0x1d31d0, 0x1d34fe |
| `CDirectSoundVoice::Use3DVoiceData` 0x1d2370 | 0x1d321d, 0x1d354f |
| `CDirectSoundVoice::SetFrequency` 0x1d3cff | 0x1d3eb7 (`CDirectSoundBuffer::SetFrequency`) |
| `CDirectSoundVoice::SetOutputBuffer` 0x1d3d23 | 0x1d3f05, 0x1d3fa5 |
| `CDirectSoundVoice::SetMixBins` 0x1d3d77 | 0x1d3f53, 0x1d3ff7 |
| `CDirectSoundVoice::SetFormat` 0x1d422e / `GetVoiceProperties` 0x1d222c | 0x1d4642, 0x1d4694 / 0x1d3182, 0x1d34ac (`CDirectSoundBuffer::*` / `CDirectSoundStream::*`) |
| `CDirectSoundVoice::CommitDeferredSettings` 0x1d221b | 0x1d47e3 (`CDirectSound::CommitDeferredSettings`, PATCHED) |
| `CDirectSoundVoiceSettings::SetMixBinVolumes` 0x1d208d | 0x1d220a (`CDirectSoundVoice::SetMixBinVolumes`) |
| `CDirectSound::DoWork` 0x1d205c | 0x1d3852 (`DirectSoundDoWork`, PATCHED), 0x1d40f5 (`CDirectSound::Release`, PATCHED as `IDirectSound_Release`) |

— and `ptr` scans for 0x1d2186, 0x1d223f, 0x1d21fe, 0x1d43d1 find no data references.
The BETA_REPORT claim ("all their callers are inside DSOUND") therefore holds for
XACTENG as well; under HLE these fifteen functions are dead code, and XACTENG never
touches voice state that Cxbx cannot see. The state it *does* set that Cxbx sees but
handles wrongly is the mixbin set of §2.

One genuinely un-emulated read: `XACT::CSoundSource::CommitDeferredSettings` reads the
DSOUND global `g_dwDirectSoundSpeakerConfig` (0x1de3c8) directly (0x1fbce6). Its static
value is 0 and, `DirectSoundCreate` being patched, nothing native ever writes it, so XACT
runs in the "not surround" configuration (`m_fSurround = 0`). Harmless for this bug.

---

## 4. SetMixBinHeadroom, DoWork, CommitDeferredSettings, I3DL2 on the positional path

- `IDirectSound_SetMixBinHeadroom` 0x1d3587 — one caller, `XACT::CEngine::Initialize`:
  `001f8908 push 0 ; push esi ; push [edi] ; 001f890d call` in a loop `esi < 0x20`
  (0x1f8913) — headroom 0 for all 32 mixbins at engine init. PATCHED,
  `LOG_UNIMPLEMENTED` (DirectSound.cpp:946-962, Patches.cpp:368). Cxbx models no
  per-mixbin headroom, so it is a no-op both ways. (Its native body: 0x1d359f
  `call CDirectSound::SetMixBinHeadroom` 0x1d1fb7 -> `CMcpxAPU::SetMixBinHeadroom`.)
- `DirectSoundDoWork` 0x1d383f — callers 0x1f85bb (`XACTEngineDoWork+0x17`) and
  0x1ffb15 (`XACT::CSoundCue::ProcessSource+0x315`). PATCHED (DirectSound.cpp:344-364:
  `DirectSoundDoWork_Buffer` + `_Stream`). Native: 0x1d3852 `call CDirectSound::DoWork`
  0x1d205c -> 0x1d2075 `CMcpxAPU::ServiceDeferredCommandsLow` (never runs).
  `CDirectSound_DoWork` is unpatched but unreachable.
- `IDirectSound_CommitDeferredSettings` 0x1d4bb2 — callers 0x1f8259
  (`IXACTEngine_CommitDeferredSettings+0x1e`) and 0x1f90fa (`XACT::CEngine::DoWork+0x4a`).
  PATCHED: `g_pDSoundPrimary3DListener8->CommitDeferredSettings()` (DirectSound.cpp:527-556).
  Native `CDirectSound::CommitDeferredSettings` 0x1d4786 would run 0x1d47cb
  `Calculate3D` (for DS3D buffers), 0x1d47d3 `CMcpxAPU::CommitI3DL2Listener` 0x1d650f
  (-> 0x1d656a `CalculateI3DL2Reverb` 0x1d2eb3) and per voice 0x1d47e3
  `CDirectSoundVoice::CommitDeferredSettings`; none of it runs, and XACT does not depend
  on it: XACT's 3D is driven by its own `CSoundSource::CommitDeferredSettings` (1c).
- I3DL2: `IXACTEngine_SetI3dl2Listener` 0x1f7e93 -> 0x1f7ea6 `CSoundSource::SetI3DL2Listener`
  0x1fbc45 -> 0x1fbcc8 `IDirectSound_SetI3DL2Listener` 0x1d4e62, PATCHED,
  `LOG_NOT_SUPPORTED` (DirectSound.cpp:922-942); also from `CEngine::DispatchEvent`
  0x201d8d. `IXACTSoundSource_SetI3DL2Source` -> 0x1f847d `CSoundSource::SetI3DL2Source`
  0x1fba81 stores the source parameters at `+0xc0`, consumed only by
  `GetVoiceData` (stub). `IDirectSoundBuffer_SetI3DL2Source` is never called from
  XACTENG. `CI3DL2Source::CalculateI3DL2` (inside the `GetVoiceData` stub) and
  `CDirectSound3DCalculator::CalculateI3DL2Reverb` (inside the never-run native
  commit) are unreachable.

---

## 5. Open questions and verification hooks

1. The mute in §2 is a static conclusion. The existing runtime evidence could not
   have caught it: `DSoundBufferUpdateHostVolume` prints only its first 12 calls, and
   all 12 in `diagnostics.txt` are `volume=-600 emuFlags=0x00100000` — creation-time
   updates of the `RECIEVEDATA` MIXIN sources from 1a, never a track voice at play time.
   Cheapest confirmation: log `maxVolume` and the bin list in
   `HybridDirectSoundBuffer_SetMixBinVolumes_8` (DirectSoundInline.hpp:1391-1408), or
   lift the 12-line cap.
2. The per-track flag (`byte [track+1] & 1`, pushed at 0x1fdc32 and 0x1ff09c) selects
   the alternative `SetOutputBuffer` block (0x1fb2a8-0x1fb2d3: own eight pairs plus bin
   0x1f). Tracks carrying it would keep speaker bins and stay audible; whether any
   positional cue uses it is data.
3. For flag-1 (2D) title sources XACT still requests the `_PlusLFE` 3D mixbin table on
   the *submix* (0x1fb096); irrelevant to Cxbx because the submix is never played, but
   worth knowing if `SetOutputBuffer` is ever emulated.
4. The DoWork -> `DispatchEvent` edge is indirect (vtable + DPC timer); the PLAY-event
   ordering (`UpdateTrackVolume` then `Play`) is direct and does not depend on it.
5. Fix shape implied by the trace (not done here): (a) in
   `HybridDirectSoundBuffer_SetMixBinVolumes_8`, a voice whose mixbin set holds no
   speaker bin must not collapse to `DSBVOLUME_MIN` — with `SetOutputBuffer`
   unsupported such a voice *is* the audible voice; (b) real positioning means either
   implementing `CDirectSound3DCalculator_GetVoiceData` + `IDirectSoundBuffer_Set3DVoiceData`
   (per-bin volumes -> host volume/pan) or forwarding the parent submix's
   `+0x4c` position to a host 3D buffer on the child voice.

Tools: `tools/ds3d_disasm.py` (`disasm`, `callees`, `callers`, `seccalls`, `ptr`,
`status`, `name`; defaults point at this build and at `oddbeta/diagnostics.txt`).
