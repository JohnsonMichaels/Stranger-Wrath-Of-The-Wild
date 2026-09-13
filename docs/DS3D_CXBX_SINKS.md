# DS3D_CXBX_SINKS - every way a created, filled, Play()ed Xbox buffer goes silent on the host

Scope: the fork's DirectSound HLE only, read-only audit. Sources are under
`C:\Users\<you>\SWBeta\src\Cxbx-Reloaded\src\core\hle\DSOUND\` (abbreviated below as
`Buffer.cpp` = `DirectSound/DirectSoundBuffer.cpp`, `Inline.hpp` = `DirectSound/DirectSoundInline.hpp`,
`DirectSound.cpp`, `Stream.cpp` = `DirectSound/DirectSoundStream.cpp`,
`Packet.cpp` = `DirectSound/DSStream_PacketManager.cpp`, `Calc.cpp` = `DirectSound/DirectSound3DCalculator.cpp`,
`Voice.cpp` = `common/XbInternalDSVoice.cpp`, `Struct.hpp` = `common/XbInternalStruct.hpp`,
`Types.h` = `XbDSoundTypes.h`, `DirectSound.hpp` = `DirectSound/DirectSound.hpp`).
The log is `C:\Users\<you>\SWBeta\oddbeta\diagnostics.txt` (`log:N`). `Patches.cpp` is
`C:\Users\<you>\SWBeta\src\Cxbx-Reloaded\src\core\hle\Patches.cpp`.

## 0. Bottom line

1. The title links **no** per-buffer 3D setters and **no** listener setters. Its entire 3D pipeline is
   `IDirectSound3DCalculator_Calculate3D` / `GetVoiceData` -> `IDirectSoundBuffer_Set3DVoiceData` /
   `Use3DVoiceData`, plus the plain voice controls (`SetMixBins`, `SetMixBinVolumes_8`, `SetVolume`,
   `SetHeadroom`, `SetPitch`, `SetFrequency`). In the fork all four 3D calls are no-ops, so the only things
   that can silence a positional voice on the host are the **volume, pitch and stop** paths listed below.
2. The single most 3D-specific sink is `HybridDirectSoundBuffer_SetMixBinVolumes_8` (Inline.hpp:1366-1410):
   on any voice whose mixbin table is the 3D default `{6,8,7,9,10}` it computes a "dominant" volume of
   `DSBVOLUME_MIN` (because only bins `< 6` count, Inline.hpp:1396), stores it in `Xb_VolumeMixbin`
   (Inline.hpp:1403) and every later `SetVolume` adds it back (Inline.hpp:1572) -> host `SetVolume(-10000)`.
   A 2D voice (`{0,1}`) goes through the same code and works. That is exactly the observed 2D-works /
   3D-silent split. It is the top suspect but is **not yet measured**.
3. The 12 `volume=-600` lines measure nothing about the positional buffers: the 12-line budget of
   `DSoundBufferUpdateHostVolume` (Inline.hpp:789) was spent at log:1369-1380 on twelve format-less 2D
   voices created before any `Play` (first `Play` is log:3893). The host volume of every SFX buffer at
   Play time is unmeasured.
4. The "DS3DMODE_DISABLE did not help" experiment was not a valid elimination: the disable is applied only
   at creation (Buffer.cpp:265-267) and is lost at every host-buffer re-create because
   `DSoundBufferTransferSettings` passes an uninitialised `DS3DBUFFER` (Inline.hpp:479, 493-495). It
   still does not matter for this title - listener and source both sit at the host origin (no listener /
   position calls are linked, see section 1), so host 3D processing cannot attenuate anything.

## 1. What actually reaches the host for this title

DSOUND library build is 5849 (log:1213), so `g_LibVersion_DSOUND = 5849` and the `_r4134_upper` voice
layout is used (Voice.cpp:192-206; Struct.hpp:133-144). `LoggedModules = 0x0` (settings.ini:29-31), so
every `EmuLog`, `LOG_FUNC_*`, `LOG_UNIMPLEMENTED` / `LOG_NOT_SUPPORTED` line is discarded
(`common/Logging.h:479-518` gate on `LOG_CHECK_ENABLED`); the log contains zero such lines. Only `printf`
survives, and only three DSOUND printfs exist (Play count Buffer.cpp:609, Play result Buffer.cpp:680,
XBtoHost Inline.hpp:92/131, volume Inline.hpp:790, plus `RETURN_RESULT_CHECK` in DirectSoundGlobal.hpp:76-80).

Symbols the title's DSOUND section contains (SymbolCache, log:174-248 and 437-499):

| family | present | absent (never linked, therefore never called) |
|---|---|---|
| `IDirectSoundBuffer_*` (log:439-464) | GetCurrentPosition, GetStatus, GetVoiceProperties, Lock, Play, Release, **Set3DVoiceData**, SetBufferData, SetCurrentPosition, SetEG, SetFilter, SetFormat, SetFrequency, SetHeadroom, SetLFO, SetLoopRegion, **SetMixBinVolumes_8**, **SetMixBins**, SetOutputBuffer, **SetPitch**, SetPlayRegion, **SetVolume**, Stop, StopEx, Unlock, **Use3DVoiceData** | SetPosition, SetVelocity, SetAllParameters, SetMode, SetMinDistance, SetMaxDistance, SetCone*, SetDistanceFactor, SetDopplerFactor, SetRolloffFactor, SetRolloffCurve, SetI3DL2Source, Pause, PauseEx, PlayEx, SetNotificationPositions |
| `IDirectSound_*` (log:480-487) | CommitDeferredSettings, CreateSoundBuffer, CreateSoundStream, GetCaps, GetOutputLevels, Release, SetI3DL2Listener, SetMixBinHeadroom | SetPosition, SetOrientation, SetVelocity, SetAllParameters, SetDistanceFactor, SetRolloffFactor, SetDopplerFactor |
| calculator (log:174-175, 437-438) | `CDirectSound3DCalculator_Calculate3D`, `_GetVoiceData`, `IDirectSound3DCalculator_Calculate3D`, `_GetVoiceData` | GetMixBinVolumes, GetPanData |
| other | `XAudioCalculatePitch` (log:499) - the title converts its own pitch values | |

The Final build's symbol table (`swse/research/symbols/SteefFinal_syms.tsv`) shows the same shape: the
library's `CDirectSound` class has no listener methods and `CDirectSoundBuffer` has no 3D setters at all;
the calculator exposes only `Calculate3D`, `GetVoiceData`, `CalculateI3DL2Reverb`.

Patch binding (log:552-602, 847-874): every `IDirectSoundBuffer_*` wrapper above is patched (log:849-874)
and every `CDirectSoundBuffer_*` class method is patched through the C->I alias (log:555-602).
`CDirectSound3DCalculator_Calculate3D/GetVoiceData` are patched with the stubs (log:552-553; Patches.cpp:241-242);
the `IDirectSound3DCalculator_*` wrappers are **unpatched** (log:847-848) and run as guest code that calls the
patched stubs. The 15 `CDirectSoundVoice_*` internals are unpatched (log:628-643) but unreachable - their
only callers are the patched buffer/stream methods.

Consequence: for a positional voice the host ever receives only `SetVolume` (via SetVolume / SetHeadroom /
SetMixBinVolumes), `SetFrequency` (via SetPitch / SetFrequency), `Play`, `Stop`, `SetCurrentPosition`,
`Lock/Unlock` (data), and buffer (re)creation. Nothing else exists for it to receive.

## 2. The audit: every inaudibility path

Format: trigger -> file:line -> host call -> garbage-triggerable by Set3DVoiceData / Use3DVoiceData /
SetMixBinVolumes carrying zero or uninitialised data? -> status against the existing log.

### P1. Mixbin "dominant volume" fold on a 3D-default voice -> `SetVolume(DSBVOLUME_MIN)`, sticky
- Trigger: any `IDirectSoundBuffer_SetMixBinVolumes_8` call (Buffer.cpp:1260-1277) on a voice whose
  `Xb_VoiceProperties.MixBinVolumePairs` are the 3D default. `GenerateMixBinDefault` assigns
  `{6,8,7,9,10}` to every buffer created with `DSBCAPS_CTRL3D` and no explicit `mixBinsOutput`
  (Inline.hpp:213-233, called from Inline.hpp:377 with `is3D = DSBufferDesc.dwFlags & DSBCAPS_CTRL3D`);
  the same happens on `SetMixBins(NULL)` (Buffer.cpp:1228 -> Inline.hpp:1351-1362).
- Mechanism: incoming pairs are only stored when their `dwMixBin` matches an existing entry
  (Inline.hpp:1378-1390); the dominant-volume loop counts only `dwMixBin != 3 && dwMixBin < 6`
  (Inline.hpp:1393-1401), so for `{6,8,7,9,10}` nothing qualifies and `maxVolume` stays `DSBVOLUME_MIN`
  (-10000). `Xb_volumeMixBin = -10000` (Inline.hpp:1403), then `HybridDirectSoundBuffer_SetVolume`
  (Inline.hpp:1405-1406) computes `GetVolume() + (-10000)` (Inline.hpp:1570-1572) ->
  `DSoundBufferUpdateHostVolume` clamps `<= -6400` to `DSBVOLUME_MIN` (Inline.hpp:771-772) ->
  `pDSBuffer->SetVolume(-10000)` (Inline.hpp:798).
- Sticky: `Xb_VolumeMixbin` lives in `EmuDirectSoundBuffer` (DirectSound.hpp:109); every later
  `IDirectSoundBuffer_SetVolume` passes it back in (Buffer.cpp:1559-1560 -> Inline.hpp:1572), and the host
  volume is copied across every host-buffer re-create (Inline.hpp:489-490). Only `SetHeadroom`
  (Inline.hpp:1276-1277, which omits the mixbin term) or a later `SetMixBinVolumes` naming a bin `< 6`
  can undo it. An empty list (`dwCount == 0`) or a list with unmatched ids also mutes.
- Garbage-triggerable: yes, and content-independent - the call itself mutes a default 3D voice. With
  explicit bins `0..5` at creation, a title that pre-sets them to `DSBVOLUME_MIN` "until 3D data arrives"
  mutes too, because the data never arrives (P20).
- Status: **not measured**. No printf covers this function; `SetMixBinVolumes_8` is linked (log:455).
  This is the one path that discriminates 2D `{0,1}` (works) from 3D `{6..10}` (mutes) by construction.

### P2. `IDirectSoundBuffer_SetVolume` with an effective volume <= -6400 -> `SetVolume(DSBVOLUME_MIN)`
- Buffer.cpp:1546-1563 -> Inline.hpp:1557-1577 (`SetVolume` stores `volume - headroom`, Voice.cpp:124-127;
  then `+ Xb_volumeMixbin`) -> Inline.hpp:771-772 clamp -> Inline.hpp:798.
- Garbage-triggerable: yes. A title-side "3D attenuation" derived from `GetVoiceData` output that the stub
  never wrote (P20) is whatever the title's output struct already held; anything at or below -6400 mB
  (or `+ Xb_volumeMixbin` from P1) is a hard mute. Note Xbox and host share the -10000..0 range, so a
  legitimately quiet -6500 is also muted; that is not a discriminator.
- Status: not measured (budget exhausted, section 4). Host `SetVolume(-10000)` succeeds, so
  `RETURN_RESULT_CHECK` prints nothing.

### P3. `IDirectSoundBuffer_SetHeadroom` -> `SetVolume(volume - headroom)`
- Buffer.cpp:1048-1066 -> Inline.hpp:1263-1281. `Xb_Voice->SetHeadroom` does
  `volume = volume + oldHeadroom - newHeadroom` (Voice.cpp:138-142) and the host gets `GetVolume()`
  directly (Inline.hpp:1276-1277) - with **no** mixbin term, so it can also un-mute P1. Values > 10000 are
  rejected but the function still returns `DS_OK` (Inline.hpp:1271-1272, 1280).
- Garbage-triggerable: yes (any headroom in 0..10000 is accepted; volume - headroom <= -6400 mutes).
- Status: not measured; `SetHeadroom` is linked (log:452).

### P4. Pitch / frequency -> `SetFrequency(<sub-audible>)`, re-applied on every re-create
- `IDirectSoundBuffer_SetPitch` (Buffer.cpp:1375-1392) -> Inline.hpp:1468-1479: stores the pitch in the
  emulator-owned voice (Voice.cpp:110-113, offset 0x18 of `_r4134_upper`, Struct.hpp:140), converts with
  `converter_pitch2freq` (`common/audio/converter.hpp:48-51`, `48000 * 2^(pitch/4096)`) and calls host
  `SetFrequency` under `RETURN_RESULT_CHECK`. `SetFrequency(0)` maps to the voice's default rate
  (Inline.hpp:1251). `DSoundBufferTransferSettings` re-applies `pitch2freq(Xb_Voice->GetPitch())` to every
  re-created host buffer (Inline.hpp:486-487).
- Effect of garbage: pitch -32768 -> 187 Hz, accepted by the host (`DSBFREQUENCY_MIN` 100): a 22050 Hz
  effect plays ~118x slower, sub-audible. Pitch >= +8448 -> > 200000 Hz -> host rejects with
  `DSERR_INVALIDPARAM` and `RETURN_RESULT_CHECK` would print `Return result report`.
- Garbage-triggerable: yes (`XAudioCalculatePitch` is linked, log:499; `SetPitch` and `SetFrequency` are
  linked, log:451, 458).
- Status: the log has **no** `Return result report` line, so no out-of-range frequency was ever applied.
  In-range but extreme values are unmeasured.

### P5. Stop, StopEx, Pause -> host `Stop()` right after `Play`
- `IDirectSoundBuffer_Stop`: host `Stop` + `SetCurrentPosition(0)` (Buffer.cpp:1578-1583).
- `IDirectSoundBuffer_StopEx`: `X_DSBSTOPEX_IMMEDIATE` -> `Stop` (Buffer.cpp:1617-1620);
  `X_DSBSTOPEX_ENVELOPE` -> the release phase is not emulated: with `rtTimeStamp == 0` or a non-playing
  status the buffer is stopped immediately (Buffer.cpp:1671-1677), otherwise it is re-`Play`ed
  (Buffer.cpp:1672) and a deferred stop is scheduled in `Xb_rtStopEx` (Buffer.cpp:1626-1636) that
  `DirectSoundDoWork_Buffer` never fires for an unlocked buffer (Buffer.cpp:65-67, see P14).
- `IDirectSoundBuffer_Pause(PAUSE)` / `PauseEx`: host `Stop` (Inline.hpp:931-938); not linked (absent from
  log:439-464), so irrelevant here.
- Garbage-triggerable: no (control flow, not data).
- Status: `Stop`/`StopEx` are linked (log:461-462); no printf covers them. A title that fires
  `StopEx(0, ENVELOPE)` shortly after `Play` (expecting the DSP release tail) gets an instant cut. Not
  ruled out.

### P6. `Release` after `Play` destroys the host buffer immediately
- Buffer.cpp:137-159: host `Release()`; at refcount 0 the `SharedDSBuffer` is deleted (Buffer.cpp:151-155)
  and the destructor releases the 3D interface and cache (Buffer.cpp:113-132). On hardware a released
  voice keeps playing to completion; here the sound stops with the object.
- Garbage-triggerable: no.
- Status: the same Xbox buffer pointers recur across plays (log:3893-3985: 19D86DA4, 19D86E64, 19D87324,
  19D86F64 each twice), which suggests a voice pool rather than fire-and-forget, but a host-heap address
  can be recycled, so not ruled out.

### P7. Host 3D re-enabled behind the DISABLE hack (uninitialised `DS3DBUFFER` at re-create)
- The `DS3DMODE_DISABLE` hack runs once, in `DirectSoundCreateBuffer` (Buffer.cpp:265-267). Every
  `SetBufferData` / `SetFormat` / `Play` whose PCM size differs re-creates the host buffer
  (`DSoundBufferResizeSetSize` Inline.hpp:546-587, `DSoundBufferRegenWithNewFormat` Inline.hpp:660-716,
  both via `DSoundBufferReCreate` Inline.hpp:502-522). `DSoundBufferTransferSettings` declares
  `DS3DBUFFER ds3dBuffer;` without setting `dwSize` (Inline.hpp:479) and calls
  `GetAllParameters` / `SetAllParameters` on it (Inline.hpp:493-495); DirectSound rejects both with
  `DSERR_INVALIDPARAM` (return values ignored, nothing logged). The new 3D interface therefore keeps
  defaults: `DS3DMODE_NORMAL`, position (0,0,0), min distance 1.0, max distance 1e9.
- The log proves re-creates happen: the same Xbox buffer plays through different host buffers
  (log:3913-3916 vs 3941-3944: `19D86DA4` -> `1CD62408` then `1CD62340`; log:3967-3969 `19D87324` on a
  PCM 8-bit 11025 Hz format -> `SetFormat` regen).
- Why it is **not** the silencer for this title: no `IDirectSound_SetPosition/SetOrientation/...` and no
  `IDirectSoundBuffer_SetPosition/SetAllParameters/SetMode` are linked (section 1), so the host listener
  and every host source stay at the origin; distance 0 is inside the default min distance and plays at
  full volume, centred. `DSBCAPS_MUTE3DATMAXDISTANCE` (accepted, Buffer.cpp:198) cannot engage at
  distance 0.
- Garbage-triggerable: no.
- Status: real upstream bug; the DISABLE experiment measured only the first-created host buffer, so it
  eliminated nothing - but host 3D is still excluded by the absent listener/position calls.

### P8. Format-less creation strips `DSBCAPS_GLOBALFOCUS` (and CTRL3D/LOCDEFER) - `DSE_FLAG_RECIEVEDATA`
- `GeneratePCMFormat` with `lpwfxFormat == NULL` sets `DSE_FLAG_RECIEVEDATA` and **overwrites**
  `DSBufferDesc.dwFlags = DSBCAPS_CTRLPAN | DSBCAPS_CTRLVOLUME | DSBCAPS_CTRLFREQUENCY`
  (Inline.hpp:351-358), discarding the `GLOBALFOCUS` that Buffer.cpp:205-206 / Stream.cpp:253-254 added
  according to `mute_on_unfocus`. Later `SetFormat` calls never restore the flags (Inline.hpp:325-350
  touches only the format). Host buffers without `GLOBALFOCUS` under `DSSCL_PRIORITY`
  (DirectSound.cpp:168) are muted whenever the emulator window is not foreground, regardless of the
  MuteOnUnfocus setting that was tested.
- Garbage-triggerable: no.
- Status: at least twelve such objects exist (log:1369-1380 show `emuFlags=0x00100000` =
  `DSE_FLAG_RECIEVEDATA`, DirectSound.hpp:188). The logged SFX plays carry `emuFlags=0x80000002`
  (`DSE_FLAG_BUFFER_EXTERNAL | DSE_FLAG_XADPCM`, DirectSound.hpp:180,192) with no RECIEVEDATA bit, so
  those buffers were created with a format and kept their flags. Whether the silent 3D voices are among
  the logged plays or among the format-less population is unknown (the Play printf omits `Xb_Flags`).
  Low probability while the window is focused (music and 2D SFX are heard).

### P9. SynchPlayback gating skips the host `Play`
- `Play(..., X_DSBPLAY_SYNCHPLAYBACK)` sets `DSE_FLAG_SYNCHPLAYBACK_CONTROL` (Buffer.cpp:645-649) and the
  host `Play` is skipped until `IDirectSound_SynchPlayback` (Buffer.cpp:659-667; DirectSound.cpp:1075-1080).
  `Pause(X_DSSPAUSE_SYNCHPLAYBACK)` does the same (Inline.hpp:939-947).
- Status: **ruled out** for the logged plays: `playFlags=0x0` and bit 10 is clear in
  `emuFlags=0x80000002` (log:3896 etc.), and every host `Play` returned `hRet=0`.

### P10. Codec-disabled / debug-mute / positive-volume clamp
- Inline.hpp:758-770 (codec flags), 773-776 (>0 -> 0), 777-779 (`DSE_FLAG_DEBUG_MUTE`).
- Status: **ruled out**: `codecs[pcm=1 xadpcm=1 unknown=1]` (log:1369-1380); `DSoundDebugMuteFlag` is
  compiled out (Inline.hpp:69-80) and the flag is set nowhere else.

### P11. `SetFormat` on a playing buffer stops it and never restarts it
- `HybridDirectSoundBuffer_SetFormat` calls host `Stop()` first (Inline.hpp:1212), then
  `DSoundBufferRegenWithNewFormat` reads `GetStatus` (Inline.hpp:674) and only re-`Play`s if it saw
  `PLAYING` (Inline.hpp:703-705) - which it never does. Likewise `SetBufferData` stops and spin-waits
  (Buffer.cpp:779-785).
- Status: harmless in the normal prepare -> Play order; only a title that re-formats mid-play loses the
  sound. Not a mute of a buffer that was Play()ed afterwards.

### P12. Play / loop regions
- `DSoundBufferRegionCurrentLocation` (Inline.hpp:614-644) falls back to the full buffer when a length is
  zero; a start beyond the cache underflows the length and the re-create fails (P13), it does not mute.
  `DSBSIZE_MIN` (4) clamps tiny regions (Inline.hpp:562-564), giving a click, not silence.
- Status: the logged plays decode whole buffers (e.g. 3168 XADPCM bytes -> 11440 PCM bytes,
  log:3894-3896), so regions were sane for them. Not a candidate.

### P13. Host re-create failure
- `DSoundBufferReCreate` with a bad size leaves `pDSBufferNew == nullptr` (Inline.hpp:513-519) and
  `DSoundBufferResizeSetSize` then releases the old buffer and installs the null (Inline.hpp:579-582):
  the next call crashes. Silence is not the symptom. Not a candidate.

### P14. Deferred StopEx / PauseEx in DoWork are dead for unlocked buffers
- `DirectSoundDoWork_Buffer` skips every buffer whose `Host_lock.pLockPtr1 == nullptr` (Buffer.cpp:65-67),
  and `Play` always unlocks (Buffer.cpp:623-630), so `Xb_rtStopEx` / `Xb_rtPauseEx` (Buffer.cpp:79-89) never
  fire. Cannot mute; it only prolongs.

### P15. Routing ignored: `SetOutputBuffer`, `SetMixBins`, MIXIN buffers
- `IDirectSoundBuffer_SetOutputBuffer` is a no-op (Buffer.cpp:1351-1370, comment at 1363); a voice that
  the title routes into a submix plays straight to the primary instead - louder, never quieter.
  `SetMixBins` only rewrites the emulator-side table (Inline.hpp:1351-1362) and does not touch the host
  volume (it does, however, set up P1). `DSBCAPS_MIXIN` (0x2000) is not in the accepted mask
  (Buffer.cpp:198), so a submix buffer becomes an ordinary silent host buffer. None of these silence the
  source voice.

### P16. DSP-side controls ignored
- `SetFilter` (Buffer.cpp:978-995), `SetLFO` (Buffer.cpp:1097-1114), `SetI3DL2Source` (Buffer.cpp:1071-1092)
  return success and do nothing; `SetEG` only stores the envelope (Buffer.cpp:970) for the dead P14 timing.
  Ignoring a filter/EG can only make a sound audible that hardware would have shaped. Not sinks.

### P17. Emulator-owned voice settings (`p_CDSVoice`)
- The `CDirectSoundVoice` the emulator reads volume/headroom/pitch from is allocated by the emulator
  (Struct.hpp:237-238) with the 5849 layout (Struct.hpp:133-144); the title's interface pointer is
  `&SharedDSBuffer::dsb_i` (Buffer.cpp:215). The log's `-600` = `0 - 600` (Voice.cpp:126, 146) proves the
  accessors work. Nothing guest-side writes them (the unpatched `CDirectSoundVoice_*` never run). Not a
  sink unless a guest write is found.

### P18. Worker re-upload (`StreamBufferAudio`)
- `dsound_worker` re-decodes ~51 ms of Xbox data at the host write cursor for every playing buffer
  (DirectSound.cpp:465-503, 365-451). It is identical for 2D and 3D buffers, and the wrapped region is
  re-decoded from the sound's start (DirectSound.cpp:434-443), so it cannot be the 2D/3D discriminator.

### P19. `IDirectSound_SetMixBinHeadroom` ignored
- DirectSound.cpp:946-963 (`LOG_UNIMPLEMENTED`). Linked (log:487). Worth at most 6 dB either way on
  hardware; not a mute.

### P20. The 3D calls themselves (what they do, exactly)
- `IDirectSoundBuffer_Set3DVoiceData(pHybridThis, dword a2)`: takes the mutex, `LOG_FUNC_*` (silenced),
  `LOG_UNIMPLEMENTED()` (silenced), returns `X_STATUS_SUCCESS` (Buffer.cpp:1690-1706). Nothing is read
  from the buffer, nothing written, no host call.
- `IDirectSoundBuffer_Use3DVoiceData(pHybridThis, LPUNKNOWN pUnknown)`: mutex, `LOG_NOT_SUPPORTED()`
  (silenced), returns `DS_OK` (Buffer.cpp:1711-1726). Same for the stream variants (Stream.cpp:1502-1539).
- `CDirectSound3DCalculator_Calculate3D(a1, a2)` and `_GetVoiceData(a1..a5)`: mutex, silenced log,
  `LOG_UNIMPLEMENTED()`, return `void` (Calc.cpp:52-66, 71-91). `GetVoiceData` writes none of its output
  parameters, so the title's receiving struct keeps whatever it held (stack garbage or a zero-init).
  Both wrappers are stdcall stubs with guessed arities; a mismatch with the real `__thiscall` arity would
  unbalance the guest stack, and the game does not crash, so either the arities are right or the title
  reaches them rarely.
- None of the four mutes anything by itself. Their effect is indirect: (a) the voice never receives the
  computed mixbin volumes that would lift a "start silent" initial state (P1/P2), and (b) whatever the
  title applies from the unwritten `GetVoiceData` output flows into P1-P4.

## 3. What the 12 `volume=-600` lines prove and do not prove

- Printed by `DSoundBufferUpdateHostVolume` (Inline.hpp:787-796), budget `s_VolReported <= 12`
  (Inline.hpp:789), reached from `HybridDirectSoundBuffer_SetVolume` (Inline.hpp:1574) and
  `SetHeadroom` (Inline.hpp:1277). They appear at log:1369-1380, right after `global_audiobanks.smb`
  opens (log:1368) and before the 27 `*_stream.xwb` banks open (log:1382+); the first `Play` is log:3893.
- `emuFlags=0x00100000` is `DSE_FLAG_RECIEVEDATA` alone (DirectSound.hpp:188): set only when the Xbox
  format pointer is null (Inline.hpp:351-352). No codec bit, so these are creation-time
  `HybridDirectSoundBuffer_SetVolume(..., 0L, ...)` calls (Buffer.cpp:273-274 or Stream.cpp:312-313) on
  format-less objects, or an early `SetHeadroom(600)` on them.
- `-600` = `0 - headroom`, headroom initialised to 600 for 2D and 0 for 3D (Voice.cpp:144-147, 152).
  All twelve were therefore **2D** voices; a 3D voice would have printed `volume=0`.
- They prove: all three codec switches are on; the 5849 voice layout returns sane volume/headroom; the
  first twelve voices were format-less 2D objects (buffer or stream - indistinguishable here).
- They do not prove: anything about a positional buffer. No line corresponds to a buffer with
  `emuFlags=0x80000002`; every `SetVolume`/`SetHeadroom`/`SetMixBinVolumes` after the twelfth call printed
  nothing, so a later `<-- MUTED` outcome is invisible. `-600` is not the final host volume of the
  positional buffers; that value has never been measured. The volume copied at re-create
  (Inline.hpp:489-490) bypasses this printf entirely.

## 4. Stream path (works) versus buffer path (silent)

| aspect | stream | buffer |
|---|---|---|
| host flags | `CTRLVOLUME|CTRLFREQUENCY|GETCURRENTPOSITION2|GLOBALFOCUS?` + `CTRL3D` or `CTRLPAN` (Stream.cpp:253-260) | `(xbox & mask)|CTRLVOLUME|GETCURRENTPOSITION2|CTRLFREQUENCY|GLOBALFOCUS?` (Buffer.cpp:205-206); both lose everything on a null format (P8) |
| 3D interface | created, mode left `NORMAL` (Stream.cpp:305-307) | created, `DS3DMODE_DISABLE` once (Buffer.cpp:265-267), lost on re-create (P7) |
| host buffer lifetime | one 5-second ring buffer (Stream.cpp:277), re-created only by `SetFormat` (Stream.cpp:1007) | re-created on every size/format change (Inline.hpp:546-587, 660-716) via the lossy transfer (Inline.hpp:471-500) |
| how it plays | `Play(0,0,DSBPLAY_LOOPING)` once when packets exist (Packet.cpp:347-352), data streamed in (Packet.cpp:105-143) | `Play(0,0,EmuPlayFlags)` per call after a full decode (Buffer.cpp:651-661) |
| volume | same `HybridDirectSoundBuffer_SetVolume` with `&pThis->Xb_Voice` and `pThis->Xb_VolumeMixbin` (Stream.cpp:1477-1478) | same, with `p_CDSVoice` (Buffer.cpp:1559-1560) |
| mixbins | same `SetMixBins` / `SetMixBinVolumes_8` code (Stream.cpp:1217, 1297-1298); music streams are 2D so the default table is `{0,1}` and P1 works | same code; a 3D buffer gets `{6,8,7,9,10}` and P1 mutes |
| headroom | 600 (2D) | 0 for `CTRL3D` (Voice.cpp:146) |
| pitch | same `SetPitch`/`SetFrequency` (Stream.cpp:1031, 1342) | same, plus re-apply on every re-create (Inline.hpp:486-487) |
| output buffer | no-op (Stream.cpp:1306-1325) | no-op (Buffer.cpp:1351-1370) |
| 3D voice data | no-op (Stream.cpp:1502-1539) | no-op (Buffer.cpp:1690-1726) |
| stop semantics | packet-driven stop/starve (Packet.cpp:145-180) | `Stop`, `StopEx` immediate (P5), dead deferred stop (P14) |

The differences that can produce "streams fine, 3D buffers silent" are therefore the mixbin table
(2D `{0,1}` vs 3D `{6..10}`, P1) and, secondarily, the per-play re-create (P4 re-applying a stored pitch,
P7 losing the disable). Everything else is shared code.

## 5. Ranked shortlist and the one measurement for each

All five can be settled from one added printf at the end of `IDirectSoundBuffer_Play` (Buffer.cpp:677-688)
that also prints `pThis->Xb_Flags & 0x10` (CTRL3D), `pThis->Xb_VolumeMixbin`,
`pThis->EmuDirectSoundBuffer8->GetVolume()`, `->GetFrequency()`, `pThis->EmuBufferDesc.dwFlags`, and
`pThis->EmuDirectSound3DBuffer8 ? GetMode() : -1`; the per-path measurements below are the targeted form.

1. **P1 - mixbin fold mutes every 3D-default voice** (Inline.hpp:1393-1406 -> 1571-1572 -> 771-772).
   Measurement: printf in `HybridDirectSoundBuffer_SetMixBinVolumes_8` for `CTRL3D` voices: the voice's
   current `dwMixBin` ids, the incoming `(dwMixBin, lVolume)` pairs, `maxVolume`, and the host volume that
   results. Expected if guilty: ids `6 8 7 9 10`, `maxVolume = -10000`, host `SetVolume(-10000)`.
2. **P2/P3 - title-applied SetVolume / SetHeadroom lands at or below -6400** (Buffer.cpp:1546-1563,
   1048-1066; Inline.hpp:771-772). Measurement: printf `lVolume`, `Xb_Voice->GetVolume()`,
   `GetHeadroom()`, `Xb_VolumeMixbin` and the final host volume in both patches for `CTRL3D` voices,
   plus the `a1..a5` of the `GetVoiceData` stub so the output the title reads back can be inspected.
3. **P4 - stored pitch drives a sub-audible host frequency, re-applied on every re-create**
   (Inline.hpp:1468-1479, 486-487). Measurement: printf `lPitch` and the converted frequency in
   `HybridDirectSoundBuffer_SetPitch`, and `GetFrequency()` at Play. Guilty if the host frequency is far
   from the format's 22050 Hz.
4. **P5 - Stop/StopEx cuts the buffer right after Play** (Buffer.cpp:1578-1583, 1617-1620, 1671-1677).
   Measurement: printf in `IDirectSoundBuffer_Stop`/`StopEx` with the buffer pointer, `dwFlags`,
   `rtTimeStamp`, host status before the stop, and the elapsed time since that buffer's last Play.
   Guilty if a stop lands within the sound's own duration on the silent voices.
5. **P7/P8 - host-buffer re-create losing the DISABLE mode, or a format-less buffer losing GLOBALFOCUS**
   (Inline.hpp:479, 493-495; Inline.hpp:356). Not expected to silence this title (origin listener and
   source; focused window) but cheap to close: at Play print `GetMode()` (expect `0` = NORMAL after any
   re-create, proving the earlier experiment measured nothing) and `EmuBufferDesc.dwFlags`
   (expect `0x8000` GLOBALFOCUS present on the SFX buffers).

## 6. Corrections to earlier eliminations

- "DS3DMODE_DISABLE did not restore sound, so host 3D is not the silencer": the premise was wrong (P7),
  the conclusion survives for a different reason - the title links no listener or source position calls,
  so host 3D has nothing to attenuate.
- "codec mute ruled out because no buffer was muted": true only for the first twelve volume updates, all
  on 2D format-less voices, none of them a positional buffer (section 3).
- "MuteOnUnfocus ruled out": the setting only controls `DSBCAPS_GLOBALFOCUS` on buffers created with a
  format; the format-less path drops the flag unconditionally (P8).
