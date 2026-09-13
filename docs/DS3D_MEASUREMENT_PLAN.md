# DS3D measurement plan: following one positional sound from the guest's request to the host's `IDirectSoundBuffer8`

Scope: the May-2004 Final beta (`XBE Library 2 : DSOUND (version 5849)`, diag:1213) on the
Cxbx fork at `C:\Users\<you>\SWBeta\src\Cxbx-Reloaded`. Music and 2D effects play; the
Stranger's footsteps, attacks and impacts are silent. Every fact below cites the line it
comes from. Nothing in this document was obtained by running the game; the two runs it
reads were started by someone else.

Citation keys

| key | file |
|---|---|
| `diag:N` | `C:\Users\<you>\SWBeta\oddbeta\diagnostics.txt` as it was on 2026-09-08 19:49 (356,640 bytes, 3,989 lines). **The file was overwritten twice today** (runs started 16:10 and 16:33) while this analysis ran - `betarun.ps1` deletes it before every launch (tools\betarun.ps1:46-47). Every DSOUND line of the Sep-8 file is preserved in Appendix A1 with its original line number. |
| `live:N` | the same path, run started 2026-09-12 16:33 with a DLL built 16:33 from a source state that adds four `DS3D:` printfs (CALC:66-72, 99-106; DSB:1737-1749, 1770-1777). Line numbers as of 16:41 (4,090 lines; the file was still growing and had reached no `Play` by 4,622 lines). Its 3D lines are in Appendix A2. |
| `step2:N` | `C:\Users\<you>\SWBeta\oddbeta\KrnlDebug.step2.txt` (2026-08-02, April **Debug** build, `XACTENID/DSOUNDD 5788`, step2:1606-1610). EmuLog was ON in that run, so it is the only log that shows what the unpatched 3D functions print. |
| `crash:N` | `C:\Users\<you>\New folder\crashlogs\run_180339.txt` (2026-09-08 18:03, Final build, an earlier run that never reached a Play). |
| `DSB:N` | `src\core\hle\DSOUND\DirectSound\DirectSoundBuffer.cpp` (line numbers of the current working copy, which includes the 16:33 additions) |
| `DSI:N` | `src\core\hle\DSOUND\DirectSound\DirectSoundInline.hpp` |
| `DS:N` | `src\core\hle\DSOUND\DirectSound\DirectSound.cpp` |
| `DSS:N` | `src\core\hle\DSOUND\DirectSound\DirectSoundStream.cpp` |
| `CALC:N` | `src\core\hle\DSOUND\DirectSound\DirectSound3DCalculator.cpp` (current working copy) |
| `VOICE:N` | `src\core\hle\DSOUND\common\XbInternalDSVoice.cpp`; `STRUCT:N` = `XbInternalStruct.hpp` |
| `D3D9:N` | `src\core\hle\D3D8\Direct3D9\Direct3D9.cpp` |
| `xbe 0x...` | disassembly of `C:\Users\<you>\SWBeta\Game\default.xbe` via `tools\ds3d_disasm.py --syms swse\research\symbols\SteefFinal_syms.tsv` (PDB RVA + 0x10920) |

---

## Part A - what the logs already say

### A.1 Every distinct Xbox buffer pointer in the Sep-8 log

Only one line type carries a buffer pointer in that log: `DSOUND: IDirectSoundBuffer_Play #N (buffer X, flags F)` (DSB:609, printed for the first 20 plays and every 250th after). The pointer is `pHybridThis`, the host-side `XbHybridDSBuffer` handed to the guest as its `IDirectSoundBuffer` (DSB:215-216). The lines that follow each Play carry no pointer but are emitted synchronously from inside the same call (`DSoundBufferUpdate` -> `DSoundGenericUnlock` -> `DSoundBufferOutputXBtoHost`, DSB:651 / DSI:395 / DSI:92, then the `-> hRet` line DSB:680), so they can be attributed to it. The log ended four lines after Play #13 (diag:3985 is the last DSOUND line of 3,989); the `-> hRet` report is capped at 12 (DSB:679), so #13 has none.

Creation flags are **not visible**: nothing in `DirectSoundCreateBuffer` prints (DSB:164-285; its only diagnostics are `EmuLog`, DSB:201/232/239). Per-buffer volume is **not visible** either (A.2). Wave identity comes from matching `xbBytes`/codec/rate against the in-memory wave banks with `tools\ds3d_xwb.py` (they carry an ENTRYNAMES segment; each match is unique unless stated).

| # | Xbox buffer | Play #s (diag line) | codec / xbBytes -> host bytes | rate | decoded peak | hRet | host buffer | emuFlags | wave (bank #index) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `19D87324` | #1 (3893), #9 (3967) | XADPCM 3168 -> 11440; then **PCM 8-bit** 27326 -> 27326 | 22050; 11025 | 31942; n/a (PCM not scanned) | 0, 0 | `1CD621D8` -> `1CD62200` | 0x80000002 -> 0x80000001 | `stranger_footstep_rock05` (steef #167); then `skid_loop` (steef #120) |
| 2 | `19D86D24` | #2 (3897) | XADPCM 5436 -> 19630 | 22050 | 9296 | 0 | `1CD62430` | 0x80000002 | `steef_footstep_spur03` or `_spur04` (steef #148/#149, equal length) |
| 3 | `19D86DA4` | #3 (3913), #5 (3941) | XADPCM 2592 -> 9360; 2376 -> 8580 | 22050 | 30838; 32012 | 0, 0 | `1CD62408` -> `1CD62340` | 0x80000002 | `stranger_footstep_rock02` (#164); `stranger_footstep_rock01` (#163) |
| 4 | `19D86E64` | #4 (3917), #8 (3963) | XADPCM 6444 -> 23270; 5436 -> 19630 | 22050 | 19230; 9296 | 0, 0 | `1CD62390` -> `1CD62458` | 0x80000002 | `steef_footstep_spur01` (#146); `spur03/04` |
| 5 | `19D86F24` | #6 (3945) | XADPCM 6228 -> 22490 | 22050 | 16215 | 0 | `1CD62520` | 0x80000002 | `steef_footstep_spur02` (#147) most likely; same length also `steef_run_foot02` (#80), `outlaw_cutter_impact01`, `machinegun_shoot01/03` (wolvark) |
| 6 | `19D86F64` | #7 (3959), #10 (3970) | XADPCM 3168 -> 11440; 2484 -> 8970 | 22050 | 31942; 32112 | 0, 0 | `1CD62228` -> `1CD621D8` | 0x80000002 | `stranger_footstep_rock05`; `stranger_footstep_rock04` (#166) |
| 7 | `19D87AA4` | #11 (3974) | XADPCM 6444 -> 23270 | 22050 | 19230 | 0 | `1CD625E8` | 0x80000002 | `steef_footstep_spur01` |
| 8 | `19D87664` | #12 (3978) | XADPCM 3168 -> 11440 | 22050 | 31942 | 0 | `1CD622A0` | 0x80000002 | `stranger_footstep_rock05` |
| 9 | `19D877A4` | #13 (3982) | XADPCM 5436 (3983-3984) | 22050 | 9296 | not printed (cap) | - | - | `steef_footstep_spur03/04` |

Readings:

* **All 13 plays are the Stranger's own footstep waves** from `steef.xwb` (rock footsteps, spur jingles, `skid_loop` on stopping) - exactly the class the owner reports silent. They are requested, decoded to full-scale audio, and every host `Play` returns `DS_OK`. The 2D sounds the owner hears (moolah, UI) do not occur in these 13; they live in `general.xwb` (`ds3d_xwb.py summary`) and simply were not triggered in the first 13 plays.
* The same wave is played through different buffers (`rock05` on #1/#7/#12) and one buffer plays different waves (`19D87324`: `rock05` then `skid_loop`, another codec and rate): **pooled XACT voices**, one wave per Play, with `SetFormat`/`SetBufferData` between plays. The host buffer changes between plays of the same Xbox buffer (`19D87324`: `1CD621D8` -> `1CD62200`) because `SetFormat` re-creates it (`DSoundBufferRegenWithNewFormat`, DSI:660-716) and `DSoundBufferTransferSettings` carries volume, pan and frequency across (DSI:471-500). The trail must capture that transfer (B.4, `hostxfer`).
* `emuFlags=0x80000002` = `DSE_FLAG_BUFFER_EXTERNAL | DSE_FLAG_XADPCM` (DirectSound.hpp:180,192): the data is the guest's own bank memory (`SetBufferData` with a non-null pointer, DSB:811-819). Bit 20 (`DSE_FLAG_RECIEVEDATA`) is **clear** on all nine - important for A.2.
* The unattributed decode lines (diag:3901-3912, 3921-3940: `xbBytes=612 pcmBytes=2210`, peaks alternating 31942/8891/30689/19230/9296) are not plays. 2210 = 17 x 130-byte XADPCM output blocks = the 51 ms window (`streamInterval 1 + streamAhead 50`, DirectSoundGlobal.hpp:64-68) that `StreamBufferAudio` re-copies from guest data into the **playing** host buffers (DS:365-451, `DSoundBufferOutputXBtoHost` at DS:425). `dsound_worker` touches only buffers whose host `GetStatus` reports `DSBSTATUS_PLAYING` (DS:494-500). So the log also proves that the host buffers holding the footsteps were **playing, with real samples in them**, while nothing was heard. No wave in any bank is 612 bytes long (`ds3d_xwb.py match 612`).

### A.2 The twelve `volume=-600 emuFlags=0x00100000` lines, and whether any Set* call is ever logged

diag:1369-1380 (identically crash:1350-1361 and live:1369-1391) are twelve `DSOUND: volume=-600 emuFlags=0x00100000 codecs[pcm=1 xadpcm=1 unknown=1]`, printed between `FILEOPEN: z:\data\global\global_audiobanks.smb` (diag:1368) and the first stream bank (diag:1382).

1. The printf is in `DSoundBufferUpdateHostVolume` (DSI:790) and is **capped at 12 for the life of the process** (DSI:789). All twelve were consumed at audio-engine init, so **no in-game volume decision has ever been logged** - the BETA_REPORT statement "no buffer muted" (BETA_REPORT.md:683) rests on twelve creation-time values: the wrong-population error the report already documents for the amplitude check (BETA_REPORT.md:687-690).
2. `emuFlags=0x00100000` is exactly `DSE_FLAG_RECIEVEDATA` (DirectSound.hpp:188), set only when the Xbox descriptor has **no `lpwfxFormat`** (DSI:351-352); no codec bit because no format. Nothing clears bit 20 (the one other reference is commented out, DSB:148). The nine footstep buffers of A.1 show `0x8000000x` without bit 20, so **none of the nine is one of the twelve**.
3. `volume=-600` is `SetVolume(0)` on a voice modelled with headroom 600: the creation paths call `HybridDirectSoundBuffer_SetVolume(..., 0L, ...)` (buffers DSB:273, streams DSS:312), the model stores `volume = set - headroom` (VOICE:126) and initialises `headroom = is3D ? 0 : 600` (VOICE:146), `is3D` being the host `DSBCAPS_CTRL3D` bit (DSB:214, DSS:263). So: format-less, non-CTRL3D voice creations.
4. What creates format-less voices in this title: `XACT::CSoundSource::CreateDSoundVoice` (xbe 0x1FB059-0x1FB169). Its 3D branch (source flag 0x20000000, 0x1FB075-0x1FB0C2) sets `dwFlags |= 0x2000` (Cxbx names it `DSBCAPS_MIXIN`, XbDSoundTypes.h:143), optionally `|= 0x600000`, points `mixBinsOutput` at `DirectSoundRequiredMixBins_5Channel3D` = {6,8,7,9,2} or `DirectSoundDefaulMixBins_5Channel3D_PlusLFE` = {6,8,7,9,2,10,3} (tables read from the XBE at 0x1DE11C/0x1DE114), and leaves `lpwfxFormat` NULL. Its 2D branch (0x1FB0C4-0x1FB0E9) builds a 48000 Hz 16-bit PCM format with `XAudioCreatePcmFormat` and sets `dwFlags |= 0x400000`. Either branch then calls `IDirectSound_CreateSoundStream` (0x1FB108, if source byte +0x17 has 0x40) or `IDirectSound_CreateSoundBuffer` (0x1FB11B). The April log agrees: 64 creations per run with unsupported flag bits `0x00602000` (step2:2121 and 63 more; the printed mask is `dwFlags & ~acceptable`, DSB:200-201).
5. **Measured today (live:1369-1420):** each of the twelve volume lines is immediately followed by `DS3D: Use3DVoiceData buffer <pHybridThis> arg 00000001` on a fresh buffer (`19970FAC, 199710AC, 19970D6C, ...`), and 28 more `Use3DVoiceData` follow until that printf's cap of 40 (DSB:1772). `IDirectSoundBuffer_Use3DVoiceData` is a *buffer* patch, so **the twelve are buffers, not streams**, and they are the 3D emitters of item 4: XACT binds every new source right after creation (`XACT::CSoundSource::Initialize+0x20 -> SetOutputBuffer`, which calls `Use3DVoiceData(self, 1)` at 0x1FB29C). The 40 pointers are 0x40 apart inside `19970C6C..19971A2C` and `19BB2FA4/19BB3564`: a pool. The footstep buffers are 2D-created wave voices - a different population (item 2).

Does any buffer ever receive `Set3DVoiceData`, `Use3DVoiceData`, `SetMixBinVolumes`, `SetMixBins`, `SetOutputBuffer` or `SetHeadroom`?

* **Sep-8 log: nothing is logged for any of the six.** All six patches are bound (diag:565-602 for the `C`->`I` mapping, diag:855-874) but their only output is `LOG_FUNC_BEGIN` / `LOG_UNIMPLEMENTED` / `LOG_NOT_SUPPORTED` (DSB:1727-1735, 1762-1768, 1358-1367, 1267-1270, 1215-1224, 1055-1058), all `EmuLog`-gated (`LOG_CHECK_ENABLED`, Logging.h:346-351, 479-491, 507-519) and dead under `LoggedModules = 0x0`; the same for the two calculator stubs and `IDirectSound_SetMixBinHeadroom` (DS:960). Zero `WARN` lines exist in the Final-build logs. That absence was the finding for that log: it could neither confirm nor refute the stub theory.
* **Today's run (deployed printfs): `Use3DVoiceData` and `Set3DVoiceData` are now measured** - 40 distinct buffers each (the caps), and `Set3DVoiceData` hits **exactly the same 40 buffers in the same order** (live:1423 `19970FAC`, 1426 `199710AC`, 1429 `19970D6C`, ...), one call per buffer, all at init before the first stream bank opens (live:1421-1540, then `general_stream.xwb` at 1542). Every call is preceded by `Calculate3D a1=002026E0 a2=<p>` and `GetVoiceData a1=002026E0 a2=<p> a3=00202498 a4=<p+0x7C> a5=<p+0xA4>`, and the `data` pointer of the `Set3DVoiceData` equals that `a5` (e.g. live:1422-1423 `0D171E28`). **All 40 dumps are eight zero dwords** (`ds3d_logparse.py` summary: `set3d masks: 0x00000000 x40`).
* `SetMixBinVolumes`, `SetMixBins`, `SetOutputBuffer`, `SetHeadroom`: **still nothing** - no printf exists for them in any build (DSB:1207-1229, 1260-1277, 1351-1370, 1048-1066). They are the missing half of the trail (B.4 items 9-14).
* April build with EmuLog on, one-shot warnings (Logging.h:479-491 print once per function): `IDirectSound_SetMixBinHeadroom unimplemented!` (step2:2119), `IDirectSoundBuffer_SetOutputBuffer not supported!` (step2:2122), `IDirectSoundBuffer_Use3DVoiceData not supported!` (step2:2123), `CDirectSound3DCalculator_Calculate3D unimplemented!` (step2:2187), `..._GetVoiceData unimplemented!` (step2:2188), `IDirectSoundBuffer_Set3DVoiceData unimplemented!` (step2:2189).

### A.3 `DirectSoundDoWork`, `CommitDeferredSettings`, primary buffer and listener

* No line in any log measures their frequency. `DirectSoundDoWork` is patched (diag:837, body DS:344-363, no printf); `CDirectSound_DoWork` runs unpatched (diag:649); `CDirectSound_CommitDeferredSettings` / `IDirectSound_CommitDeferredSettings` are patched (diag:644, 890; DS:527-555, no printf). XBE callers: `XACTEngineDoWork+0x17` and `XACT::CSoundCue::ProcessSource+0x315` -> `DirectSoundDoWork`; `XACT::CEngine::DoWork+0x4a` and `IXACTEngine_CommitDeferredSettings+0x1e` -> `IDirectSound_CommitDeferredSettings`. The title's per-frame `XACTEngineDoWork` is the cadence; B.4 item 3 adds a counter. Today's run shows the XACT-side `CSoundSource::CommitDeferredSettings` doing one 3D pass over all 40 emitters at init (live:1421-1540, A.2).
* Host primary buffer: created once in `DirectSoundCreate` with `DSBCAPS_PRIMARYBUFFER | DSBCAPS_CTRL3D` and a 3D listener queried from it (DS:190-213); cooperative level `DSSCL_PRIORITY` (DS:168). Nothing prints, so `g_pDSoundPrimary3DListener8` is never visible.
* Listener: the Final XBE contains **no** DSound listener or hardware-3D voice functions - the PDB has no `CDirectSound::SetPosition/SetOrientation/SetAllParameters/SetVelocity` and no `CDirectSoundVoice::SetPosition/SetMode/SetMinDistance...` (grep of `SteefFinal_syms.tsv`; the only `SetPosition` symbols are `XACT::CSoundSource::SetPosition`, `IXACTSoundSource::SetPosition`, `SoundSource::SetPosition`, `CAc97Channel::SetPosition`). XACT keeps its own listener block (`XACT::CSoundSource::m_3DListener` at 0x2026E0 = the `a1` seen live, `m_I3DL2Listener` at 0x202498 = `a3`) and hands it to the calculator. So the `WARNING: SetOrientation ...` printfs (DS:996, 999) can never fire, and the host listener stays at DirectSound defaults. `CDirectSound_SetI3DL2Listener` is patched to `LOG_NOT_SUPPORTED` (DS:938); `IDirectSound_SetMixBinHeadroom` is called once at `XACT::CEngine::Initialize+0x46` and is a no-op (DS:960).
* Why `DS3DMODE_DISABLE` (DSB:265-267) could not matter: it runs only when `EmuDirectSound3DBuffer8 != nullptr`, which exists only if the host buffer was created with `DSBCAPS_CTRL3D` (DSI:435-441). XACT never passes `0x10` (A.2 item 4), and for the format-less emitters `GeneratePCMFormat` replaces the host flags with `CTRLPAN|CTRLVOLUME|CTRLFREQUENCY` outright (DSI:356). No voice of this title ever had a host 3D buffer.

### A.4 What the XBE says the guest does with a positional sound (needed to place the probes)

From `tools\ds3d_disasm.py` on the Final XBE (all call sites aligned):

```
XACT::CEngine::AllocateSoundSource+0x94 -> XACT::CSoundSource::CreateDSoundVoice
   3D emitter : no format, dwFlags |= 0x2000 [| 0x600000], mixbins 5Channel3D {6,8,7,9,2[,10,3]}
   wave voice : 48000 Hz 16-bit PCM format, dwFlags |= 0x400000
   -> IDirectSound_CreateSoundStream (0x1FB108) or IDirectSound_CreateSoundBuffer (0x1FB11B)

XACT::CSoundSource::SetOutputBuffer(this=eax, parent=edi, dwFlag=[esp+4])   (0x1FB200; called from
   CSoundSource::Initialize+0x20, CSoundCue::AttachSoundSource+0x33, CSoundCue::PreProcessTrackPlayEvent+0x1cc,
   CEngine::FlushCachedAllocations+0xa1)
   [dwFlag!=0] IDirectSoundBuffer_GetVoiceProperties(self)          0x1FB274   (patched: copies Cxbx's Xb_VoiceProperties)
   IDirectSoundBuffer_SetOutputBuffer(self, parent->buffer)         0x1FB27D   (patched: LOG_NOT_SUPPORTED, no effect, DSB:1367)
   IDirectSoundBuffer_Use3DVoiceData(self, 0|1)                     0x1FB29C   (patched: no effect, DSB:1762-1768; measured: arg 1 on all 40)
   IDirectSoundBuffer_SetMixBins(self, ...)                         0x1FB31F   two outcomes, see below
   XACT::CSoundSource::Update3DProperties                            0x1FB385

XACT::CSoundSource::SetPosition -> CommitDeferredSettings -> Update3DProperties (0x1FBEBB):
   [src+0x44] &= 0x10000
   IDirectSound3DCalculator_Calculate3D (&m_3DListener, &src+0x44)                                          0x1FBEE5 / 0x1FBF47
   IDirectSound3DCalculator_GetVoiceData(&m_3DListener, &src+0x44, &m_I3DL2Listener, &src+0xC0, &src+0xE8)  0x1FBF02 / 0x1FBF64
   [src+0xE8] &= 0xDC  (listener path)  |  &= 0x33 (parent path)
   IDirectSoundBuffer_Set3DVoiceData([src+0x1C], &src+0xE8)   0x1FBF8C   (or IDirectSoundStream_Set3DVoiceData([src+0x20]) 0x1FBF97)
   [src+0x44] = 0 ; [src+0xE8] = 0

per cue: XACT::CSoundCue::UpdateTrackVolume -> CSoundSource::SetVolume (+0x9a) -> IDirectSoundBuffer_SetVolume
                                             -> CSoundSource::SetMixBinVolumes (+0x16d) -> IDirectSoundBuffer_SetMixBinVolumes  (pairs for bins 3 and 10 only, 0x1FE437-0x1FE4BF)
         XACT::CSoundCue::UpdateTrackPitch  -> CSoundSource::SetPitch -> IDirectSoundBuffer_SetPitch
         XACT::CSoundCue::PreProcessTrackPlayEvent -> SetHeadroom (+0x1fd), SetLoopRegion (+0x2fc); CWaveBank::SetBufferData -> SetPlayRegion (+0x3a)
         XACT::CSoundSource::SetFormat / SetBufferData / SetEG / SetFilter / Play / Stop(->StopEx) / Pause(->Stop) / IsPlaying(->GetStatus)
```

Facts about the two stubs that matter for instrumentation:

* Argument counts are right. The real `CDirectSound3DCalculator::Calculate3D` ends in `ret 8` (0x1D460E) and `GetVoiceData` in `ret 0x14` (0x1D2EB0); the Cxbx stubs are `WINAPI` with 2 and 5 dword args (CALC:53-57, 79-86). **No stack imbalance.** The live pointers confirm the layout: `a2` = source+0x44, `a4` = `a2`+0x7C = source+0xC0, `a5` = `a4`+0x28 = source+0xE8 (live:1422: `0D171D84 / 0D171E00 / 0D171E28`).
* `a5` is a `DS3DVOICEDATA` of 0x38 bytes (14 dwords) whose first dword is a field mask: `CDirectSoundVoice::Set3DVoiceData` (0x1D223F) copies +4 on bit0, +8 on bit1, +0xC/+0x10 on bit2, +0x14/+0x18 on bit3, +0x1C/+0x20 on bit4, +0x24 on bit5, +0x28/+0x2C on bit6 (also loads an HRTF filter), +0x30/+0x34 on bit7, then `CMcpxVoiceClient::Apply3dSettings`; with mask 0 it applies nothing. XACT zeroes the mask after every update (0x1FBF9E-0x1FBFA6), so with the stub every `Set3DVoiceData` carries mask 0 - **measured: 40 of 40 today** (A.2). The deployed dump prints 8 of the 14 dwords (DSB:1744-1745).
* Under Cxbx `SetOutputBuffer` and `Use3DVoiceData` are no-ops, so a wave voice bound to an emitter plays **directly** on the host at its own volume and pitch, and the emitter's `Set3DVoiceData` never touches it. By code reading no remaining Set* path should mute it, **with one exception worth naming**: `SetOutputBuffer` leaves the wave voice with one of two mixbin lists - eight pairs = its own current seven + `{bin 31}` (0x1FB2A8-0x1FB2D3, taken when `dwFlag != 0`), or `{10, 3}` (0x1FB2E8-0x1FB305, when `dwFlag == 0` and the parent source has flag 2). Cxbx's `SetMixBinVolumes_8` takes the host volume from the loudest pair among bins 0-5 except 3 (DSI:1393-1403); a voice whose list has no such bin gets `Xb_VolumeMixbin = DSBVOLUME_MIN` and its host volume becomes `-10000 + voice volume` (DSI:1403-1406, 1571-1574) on the very next `SetMixBinVolumes` - and `UpdateTrackVolume` sends exactly bins 3 and 10 (0x1FE43B-0x1FE4BF). The eight-pair outcome preserves a previous `{10,3}` (it copies the voice's current pairs back, DSI:180-207), so a pooled voice that once went through the `{10,3}` outcome stays mute-able for life. Whether footstep voices ever take that path is unknown - it is the first thing the `setmixbins` -> `setmixvol` -> `hostvol` lines of B.4 will show.

That is why the next run must **read the host object back** instead of inferring: if the readbacks say "audible", the stub theory is dead as the cause and the fault is below `IDirectSoundBuffer8`; if they say "muted", the trail names the Xbox-side call that did it.

---

## Part B - the instrumentation (printf only, bounded)

Design rules: every new line starts with `DS3D: <event>` followed by `key=value` tokens (no spaces inside a value, arrays comma-separated) and ends with `t=<GetTickCount>` so events can be bracketed against the existing `MARK: MARKn at swap N tick N` lines (D3D9:5705). Buffers are identified by `buf=<pHybridThis>` (the pointer the Play line prints) and `host=<EmuDirectSoundBuffer8>`. The four lines deployed today (CALC:66-72, 99-106; DSB:1737-1749, 1770-1777: `Calculate3D a1= a2=`, `GetVoiceData a1=..a5=`, `Set3DVoiceData buffer X data Y = <8 dwords>`, `Use3DVoiceData buffer X arg Y`, caps of 40, no tick) are the seed of items 15-18 below; `tools\ds3d_logparse.py` parses both that form and the planned one (plus the legacy `DSOUND:` lines) into per-buffer trails, MARK windows and a verdict per Play.

### B.0 Gating helper (bounded output)

Add to `DirectSoundGlobal.cpp` after line 58 and declare in `DirectSoundGlobal.hpp` after line 69:

```cpp
bool     g_bDS3DTraceWindow = false;   // set by NUMPAD1, cleared by NUMPAD2 (B.6)
unsigned g_DS3DTraceBudget  = 0;       // lines left in the current window
```

Add to `DirectSoundInline.hpp` after line 40 (included by all four DSOUND .cpp files):

```cpp
// Always-on quota per line type, plus an owner-opened window with a shared budget.
static inline bool DS3DTrace(unsigned &nAlways, unsigned cap) {
    if (nAlways < cap) { nAlways++; return true; }
    if (g_bDS3DTraceWindow && g_DS3DTraceBudget > 0) { g_DS3DTraceBudget--; return true; }
    return false;
}
#define DS3D_LOG(cap, ...) do { static unsigned s_n = 0; if (DS3DTrace(s_n, cap)) { \
    printf("DS3D: " __VA_ARGS__); printf(" t=%u\n", (unsigned)GetTickCount()); fflush(stdout); } } while (0)
// Formatters (static buffer; single-threaded under g_DSoundMutex):
static const char *DS3D_Dwords(const void *p, unsigned n);        // "d=%08x,%08x,..." (n dwords; "null" if p==0; guard with IsBadReadPtr as DSB:1743 does)
static const char *DS3D_Wfx(const WAVEFORMATEX *w);                // "tag=%u,ch=%u,rate=%u,bits=%u,balign=%u" or "null"
static const char *DS3D_MixBins(xbox::X_LPDSMIXBINS m);            // "%u:bin:vol,bin:vol,..." or "null"
static const char *DS3D_Props(const xbox::X_DSVOICEPROPS &p);      // "%u:bin:vol,..." over dwMixBinCount
```

Always-on caps below total under ~1,500 lines per run; a window is capped at 5,000 lines and re-armed by each NUMPAD1.

### B.4 Per-buffer audio trail - exact placements and format strings

| # | file:line (insert after the cited statement) | function | line printed |
|---|---|---|---|
| 1 | DS:213 (after the listener `CxbxrAbort` check) | `DirectSoundCreate` | `DS3D: dsinit ds=%p prim=%p listener=%p` with `g_pDSound8, g_pDSoundPrimaryBuffer, g_pDSoundPrimary3DListener8` (cap 1) |
| 2 | DS:958 | `IDirectSound_SetMixBinHeadroom` | `DS3D: mixhead mask=0x%08X head=%u` (`dwMixBinMask, dwHeadroom`) (cap 8) |
| 3 | DS:352 / DS:533 | `DirectSoundDoWork` / `CDirectSound_CommitDeferredSettings` | `DS3D: dowork n=%u` / `DS3D: commit n=%u` - static counters, print when `n % 600 == 0` (about every 10 s at 60 Hz) |
| 4 | DSB:276 (after `g_pDSoundBufferCache.push_back`) | `DirectSoundCreateBuffer` | `DS3D: create buf=%p voice=%p host=%p ds3d=%p xbFlags=0x%08X xbBytes=%u inbin=%u fmt=%s mixbins=%s hostFlags=0x%08X emu=0x%08X vvol=%ld head=%u pitch=%ld` with `pHybridBuffer, pHybridBuffer->p_CDSVoice, pEmuBuffer->EmuDirectSoundBuffer8, pEmuBuffer->EmuDirectSound3DBuffer8, pdsbd->dwFlags, pdsbd->dwBufferBytes, pdsbd->dwInputMixBin, DS3D_Wfx(pdsbd->lpwfxFormat), DS3D_MixBins(pdsbd->mixBinsOutput.pMixBins), DSBufferDesc.dwFlags, pEmuBuffer->EmuFlags, p_CDSVoice->GetVolume(), GetHeadroom(), GetPitch()` (cap 128 - the whole pool). This is the line that finally shows `0x2000`/`0x600000`/`0x400000` and the 5Channel3D tables per buffer. |
| 5 | DSS:316 (after `g_pDSoundStreamCache.push_back`) | `DirectSoundCreateStream` | `DS3D: screate strm=%p host=%p xbFlags=0x%08X fmt=%s mixbins=%s emu=0x%08X` (cap 64) |
| 6 | DSB:1018 | `IDirectSoundBuffer_SetFormat` | `DS3D: setformat buf=%p fmt=%s -> host=%p ds3d=%p hostFlags=0x%08X hostBytes=%u emu=0x%08X pitch=%ld hRet=0x%08X` (`pwfxFormat`; after the call `pThis->EmuDirectSoundBuffer8` is the **new** host buffer) (cap 64) |
| 7 | DSB:759 and before DSB:828 | `IDirectSoundBuffer_SetBufferData` | entry `DS3D: setdata buf=%p data=%p bytes=%u`; exit `DS3D: setdata-done buf=%p cache=%p size=%u host=%p hostBytes=%u emu=0x%08X hRet=0x%08X` (cap 64 each) |
| 8 | DSB:1421 / DSB:1139 | `SetPlayRegion` / `SetLoopRegion` | `DS3D: playregion buf=%p start=%u len=%u` / `DS3D: loopregion buf=%p start=%u len=%u` (cap 64) |
| 9 | DSB:1227 (before) and after the call at DSB:1228 | `IDirectSoundBuffer_SetMixBins` | `DS3D: setmixbins buf=%p in=%s -> props=%s` with `DS3D_MixBins(mixBins.pMixBins)` and `DS3D_Props(pThis->Xb_VoiceProperties)` (cap 64). `in=2:10:0,3:0` marks the `{10,3}` outcome of A.4; `in=8:...,31:...` the eight-pair one. |
| 10 | DSB:1273 (before/after) | `IDirectSoundBuffer_SetMixBinVolumes_8` | `DS3D: setmixvol buf=%p in=%s -> props=%s mixvol=%ld vvol=%ld head=%u hRet=0x%08X` (`pThis->Xb_VolumeMixbin`, `p_CDSVoice->GetVolume()`, `GetHeadroom()`) (cap 64). `mixvol=-10000` is the A.4 exception firing. |
| 11 | DSB:1560 | `IDirectSoundBuffer_SetVolume` | `DS3D: setvolume buf=%p in=%ld -> vvol=%ld mixvol=%ld hRet=0x%08X` (cap 64) |
| 12 | DSB:1063 | `IDirectSoundBuffer_SetHeadroom` | `DS3D: sethead buf=%p in=%u -> vvol=%ld head=%u hRet=0x%08X` (cap 64) |
| 13 | DSB:1389 / DSB:1040 | `SetPitch` / `SetFrequency` | `DS3D: setpitch buf=%p in=%ld -> freq=%u hRet=0x%08X` with `converter_pitch2freq(lPitch)` / `DS3D: setfreq buf=%p in=%u -> pitch=%ld` (cap 64). Model: `freq = 48000 * 2^(pitch/4096)`, pitch 0 = 48 kHz (converter.hpp:48-51); a 22050 Hz wave sits at pitch -4597. |
| 14 | DSB:1361 | `IDirectSoundBuffer_SetOutputBuffer` | `DS3D: setoutput buf=%p out=%p` (`pOutputBuffer`) - the emitter binding Cxbx ignores (cap 64) |
| 15 | DSB:1770-1777 (extend the deployed block) | `IDirectSoundBuffer_Use3DVoiceData` | `DS3D: use3d buf=%p on=%u` (`(unsigned)pUnknown` - a BOOL, see 0x1FB293/0x1FB297); add `t=`, gate with `DS3DTrace` (cap 64 + window) |
| 16 | DSB:1737-1749 (extend the deployed block) | `IDirectSoundBuffer_Set3DVoiceData` | `DS3D: set3d buf=%p vd=%p mask=0x%08X %s` with `a2, *(DWORD*)a2, DS3D_Dwords(a2, 14)` - all 14 dwords (the deployed line stops at 8), after the guest's `&= 0xDC/0x33` mask (cap 32 + window) |
| 17 | CALC:66-72 (extend the deployed block) | `CDirectSound3DCalculator_Calculate3D` | `DS3D: calc3d a1=%p a2=%p %s` with `DS3D_Dwords((void*)a2, 31)` - the source's 3D parameter block (0x44..0xC0: mask word + DS3DBUFFER-like fields), hex; floats are recognisable by exponent (cap 16 + window) |
| 18 | CALC:99-106 (extend the deployed block) | `CDirectSound3DCalculator_GetVoiceData` | `DS3D: getvd a1=%p a2=%p a3=%p a4=%p a5=%p before5=%s before4=%s` with `DS3D_Dwords(a5,14)`, `DS3D_Dwords(a4,10)` - the **BEFORE** image of the output struct. The stub writes nothing, so AFTER == BEFORE at return; what the guest then *uses* is what item 16 prints (`set3d`, same address = `a5`). To prove the stub inert, re-dump `a5` as `after5=` just before returning. (cap 16 + window) |
| 19 | DSB:963 / DSB:988 | `SetEG` / `SetFilter` | `DS3D: seteg buf=%p gen=%u mode=%u delay=%u attack=%u hold=%u decay=%u release=%u sustain=%u` / `DS3D: setfilter buf=%p` (cap 32) - `dwRelease` feeds `StopEx(ENVELOPE)` timing (DSB:1624 area) |
| 20 | DSB:1574 / DSB:1603 | `Stop` / `StopEx` | `DS3D: stop buf=%p` / `DS3D: stopex buf=%p rt=%lld flags=0x%X release=%u` (cap 64) - a footstep stopped milliseconds after Play is indistinguishable from silence |
| 21 | DSI:498 (end of function) | `DSoundBufferTransferSettings` | `DS3D: hostxfer old=%p new=%p vol=%ld pan=%ld freq=%u` - state carried to a re-created host buffer (cap 64) |
| 22 | DSI:787-796 (replace the capped block) | `DSoundBufferUpdateHostVolume` | `DS3D: hostvol host=%p vol=%ld emu=0x%08X hRet=0x%08X` - print when the value differs from the last printed for that `pDSBuffer` (small `std::unordered_map<LPDIRECTSOUNDBUFFER8, LONG>`) or inside a window; capture `hRet` of `pDSBuffer->SetVolume(volume)` (DSI:798) |
| 23 | DSI:89-100 and 118-135 (replace both capped blocks) | `DSoundBufferOutputXBtoHost` | keep the two existing lines, add `src=%p dst=%p` (`pXBaudioPtr`, `pPCaudioPtr`) so the wave is identified by its bank address, gate with `DS3DTrace` (cap 32 + window) |
| 24 | DSB:606-613 and 677-688 (replace) | `IDirectSoundBuffer_Play` | the silence detector, B.5 |
| 25 | D3D9:5701-5708 (inside the Markers loop) | `CxbxrPollDiagnosticKeys` | on `MARK1`: `g_bDS3DTraceWindow = true; g_DS3DTraceBudget = 5000;` and print `DS3D: window open budget=5000`; on `MARK2`: `g_bDS3DTraceWindow = false;` and print `DS3D: window close left=%u`. Declare the two globals `extern` next to D3D9:5674. NUMPAD3/4 stay pure labels. |

One footstep top to bottom then reads: `create` (once, at init) -> `setformat` -> `setdata`/`setdata-done` -> `playregion` -> `sethead` -> `setvolume` -> `setmixbins`/`setoutput`/`use3d` (if routed) -> `setpitch` -> `calc3d`/`getvd`/`set3d` (per frame while positioned) -> `play` (B.5) -> `XBtoHost` decode -> `hostvol`/`hostxfer` -> `stop`/`stopex`, all keyed by the same `buf=`.

### B.5 Silence detector at host `Play`

Replace the two capped blocks in `IDirectSoundBuffer_Play` (DSB:606-613, 677-688) with one line after the host `Play` (after DSB:668), reading the host object back:

```cpp
LONG hv = 0x7FFFFFFF, hp = 0x7FFFFFFF; DWORD hf = 0, hs = 0, mode = 0xFFFFFFFF;
DSBCAPS caps = { sizeof(DSBCAPS) };
pThis->EmuDirectSoundBuffer8->GetVolume(&hv);
pThis->EmuDirectSoundBuffer8->GetPan(&hp);
pThis->EmuDirectSoundBuffer8->GetFrequency(&hf);
pThis->EmuDirectSoundBuffer8->GetStatus(&hs);
pThis->EmuDirectSoundBuffer8->GetCaps(&caps);
if (pThis->EmuDirectSound3DBuffer8) pThis->EmuDirectSound3DBuffer8->GetMode(&mode);
DS3D_LOG(200, "play buf=%p flags=0x%08X hRet=0x%08X host=%p hostBytes=%u playFlags=0x%X emu=0x%08X "
              "vol=%ld pan=%ld freq=%u status=0x%X caps=0x%08X ds3d=%p mode=%u "
              "vvol=%ld head=%u pitch=%ld mixvol=%ld props=%s xbRate=%u fg=%u",
    pHybridThis, dwFlags, hRet, pThis->EmuDirectSoundBuffer8, pThis->EmuBufferDesc.dwBufferBytes, pThis->EmuPlayFlags, pThis->EmuFlags,
    hv, hp, hf, hs, caps.dwFlags, pThis->EmuDirectSound3DBuffer8, mode,
    pHybridThis->p_CDSVoice->GetVolume(), pHybridThis->p_CDSVoice->GetHeadroom(), pHybridThis->p_CDSVoice->GetPitch(),
    pThis->Xb_VolumeMixbin, DS3D_Props(pThis->Xb_VoiceProperties),
    pThis->EmuBufferDesc.lpwfxFormat ? pThis->EmuBufferDesc.lpwfxFormat->nSamplesPerSec : 0,
    (unsigned)(GetForegroundWindow() == GET_FRONT_WINDOW_HANDLE));
```

What each field decides (`ds3d_logparse.py --verdict` applies the same thresholds):

| readback | audible if | otherwise means |
|---|---|---|
| `vol` (host `GetVolume`) | > -6400 (DSI:771 maps anything below to `DSBVOLUME_MIN`) | muted by the volume path; the preceding `setvolume`/`sethead`/`setmixvol` -> `hostvol` lines name the call that did it |
| `freq` (host `GetFrequency`) | within ~2x of `xbRate` | wrong pitch model: a 22050 Hz wave forced to 100 Hz (DSBFREQUENCY_MIN) plays as an inaudible rumble; `setpitch in=` shows what XACT asked for |
| `status` | bit 0 (`DSBSTATUS_PLAYING`) set | host refused to start; compare `hRet` |
| `caps` | bit 0x8000 (`DSBCAPS_GLOBALFOCUS`) set, or `fg=1` | silenced by focus (format-less voices lose GLOBALFOCUS at DSI:356) |
| `stop`/`stopex` within 60 ms of the play | none | lifecycle: the cue stops the voice before it is audible (`StopEx(ENVELOPE)` with `release=0` stops at once, DSB:1621-1636) |
| `set3d` mask | every mask is 0 | the calculator stub returned nothing to the guest (measured today, A.2); a non-zero mask means the guest pre-filled the struct and the dwords are what it applied |

If a footstep shows `vol > -3000`, `freq` sane, `PLAYING` for the wave's duration, `GLOBALFOCUS`, and no early stop - **the host is emitting it**, the 3D-calculator/DSound-emulation theory is refuted as the cause, and the search moves below `IDirectSoundBuffer8` (device selection, primary format, mixing). If `vol <= -6400`, the trail names the Xbox-side call that produced it (A.4's `{10,3}` mixbin exception is the code-reading favourite) and the fix is that call's emulation, not the calculator. Only if the values are sane *and* the sound is inaudible while a 2D sound in the same window (moolah) shows the same values does comparing the two trails become the next question.

### B.6 Test protocol for the owner

Copy the previous `diagnostics.txt` aside first - `betarun.ps1` deletes it on launch (tools\betarun.ps1:46-47) and `crashwatch.sh` only saves on a crash. Launch, load the region-03 save (the one used on 2026-09-08), and let the level settle. Then, with the game window focused: (1) stand still for at least five seconds and touch nothing - a baseline in which no `play` line should appear; (2) press NUMPAD1, take exactly three steps forward and stop, press NUMPAD2 - window 1 must contain three to six `play` lines whose `xbBytes` match `steef_footstep_*` (`tools\ds3d_xwb.py log diagnostics.txt` names them); (3) NUMPAD1, walk onto one moolah and stop, NUMPAD2 - window 2 is the 2D control, one `general.xwb` wave, expected audible; (4) NUMPAD1, fire the crossbow once, NUMPAD2; (5) NUMPAD1, punch once, NUMPAD2; (6) stand still five more seconds and quit from the pause menu. Each NUMPAD press already stamps `MARK: MARKn at swap N tick N` (D3D9:5705) and the first two also open/close the trace window (B.4 item 25), so everything between MARK1 and MARK2 is that one action and nothing else. Then run `python tools\ds3d_logparse.py <copy> --verdict` (one line per Play with the B.5 verdict), `--window 1` (the full footstep trail) and `--buf <pointer>` (one voice's life). NUMPAD3/NUMPAD4 remain free labels ("about to fire" / "about to punch").

---

## Appendix A1 - every DSOUND line of the 2026-09-08 19:49 diagnostics.txt (file since overwritten)

```
1368:FILEOPEN: z:\data\global\global_audiobanks.smb
1369-1380: DSOUND: volume=-600 emuFlags=0x00100000 codecs[pcm=1 xadpcm=1 unknown=1]     (x12, identical)
1381:FILEOPEN: z:\data\global\global_globallip.smb
1382:FILEOPEN: d:\data\audio\xwb\general_stream.xwb   ... 1407: mus_b07_d03tens_stream.xwb   (27 *_stream.xwb; no in-memory bank is ever FILEOPENed - they are bundled in global_audiobanks.smb)
1409:FILEOPEN: d:\data\movies\main_screen.bik
2708:FILEOPEN: d:\data\bundles\region_03\lm_level_03.lvl
3866:FILEOPEN: U:\11185F0C9733\game.sav
3885:FILEOPEN: z:\data\bundles\region_03\lm_level_03\npc_26.smb
3893:DSOUND: IDirectSoundBuffer_Play #1 (buffer 19D87324, flags 0x00000000)
3894:DSOUND: XBtoHost XADPCM->decode xbBytes=3168 pcmBytes=11440 ch=1 bits=16 rate=22050
3895:DSOUND:   -> decoded peak amplitude = 31942
3896:DSOUND:   -> hRet=0x00000000 hostBuf=1CD621D8 bytes=11440 playFlags=0x0 emuFlags=0x80000002
3897:DSOUND: IDirectSoundBuffer_Play #2 (buffer 19D86D24, flags 0x00000000)
3898:DSOUND: XBtoHost XADPCM->decode xbBytes=5436 pcmBytes=19630 ch=1 bits=16 rate=22050
3899:DSOUND:   -> decoded peak amplitude = 9296
3900:DSOUND:   -> hRet=0x00000000 hostBuf=1CD62430 bytes=19630 playFlags=0x0 emuFlags=0x80000002
3901-3912: 6x "XBtoHost XADPCM->decode xbBytes=612 pcmBytes=2210 ch=1 bits=16 rate=22050" with peaks 31942,8891,31942,8891,31942,8891   (worker re-copies, A.1)
3913:DSOUND: IDirectSoundBuffer_Play #3 (buffer 19D86DA4, flags 0x00000000)
3914:DSOUND: XBtoHost XADPCM->decode xbBytes=2592 pcmBytes=9360 ch=1 bits=16 rate=22050
3915:DSOUND:   -> decoded peak amplitude = 30838
3916:DSOUND:   -> hRet=0x00000000 hostBuf=1CD62408 bytes=9360 playFlags=0x0 emuFlags=0x80000002
3917:DSOUND: IDirectSoundBuffer_Play #4 (buffer 19D86E64, flags 0x00000000)
3918:DSOUND: XBtoHost XADPCM->decode xbBytes=6444 pcmBytes=23270 ch=1 bits=16 rate=22050
3919:DSOUND:   -> decoded peak amplitude = 19230
3920:DSOUND:   -> hRet=0x00000000 hostBuf=1CD62390 bytes=23270 playFlags=0x0 emuFlags=0x80000002
3921-3940: 10x "XBtoHost XADPCM->decode xbBytes=612 pcmBytes=2210 ..." peaks 31942,8891,30689,19230,31942,8891,30689,19230,31942,9296
3941:DSOUND: IDirectSoundBuffer_Play #5 (buffer 19D86DA4, flags 0x00000000)
3942:DSOUND: XBtoHost XADPCM->decode xbBytes=2376 pcmBytes=8580 ch=1 bits=16 rate=22050
3943:DSOUND:   -> decoded peak amplitude = 32012
3944:DSOUND:   -> hRet=0x00000000 hostBuf=1CD62340 bytes=8580 playFlags=0x0 emuFlags=0x80000002
3945:DSOUND: IDirectSoundBuffer_Play #6 (buffer 19D86F24, flags 0x00000000)
3946:DSOUND: XBtoHost XADPCM->decode xbBytes=6228 pcmBytes=22490 ch=1 bits=16 rate=22050
3947:DSOUND:   -> decoded peak amplitude = 16215
3948:DSOUND:   -> hRet=0x00000000 hostBuf=1CD62520 bytes=22490 playFlags=0x0 emuFlags=0x80000002
3959:DSOUND: IDirectSoundBuffer_Play #7 (buffer 19D86F64, flags 0x00000000)
3960:DSOUND: XBtoHost XADPCM->decode xbBytes=3168 pcmBytes=11440 ch=1 bits=16 rate=22050
3961:DSOUND:   -> decoded peak amplitude = 31942
3962:DSOUND:   -> hRet=0x00000000 hostBuf=1CD62228 bytes=11440 playFlags=0x0 emuFlags=0x80000002
3963:DSOUND: IDirectSoundBuffer_Play #8 (buffer 19D86E64, flags 0x00000000)
3964:DSOUND: XBtoHost XADPCM->decode xbBytes=5436 pcmBytes=19630 ch=1 bits=16 rate=22050
3965:DSOUND:   -> decoded peak amplitude = 9296
3966:DSOUND:   -> hRet=0x00000000 hostBuf=1CD62458 bytes=19630 playFlags=0x0 emuFlags=0x80000002
3967:DSOUND: IDirectSoundBuffer_Play #9 (buffer 19D87324, flags 0x00000000)
3968:DSOUND: XBtoHost PCM->copy xbBytes=27326 pcmBytes=27326 ch=1 bits=8 rate=11025
3969:DSOUND:   -> hRet=0x00000000 hostBuf=1CD62200 bytes=27326 playFlags=0x0 emuFlags=0x80000001
3970:DSOUND: IDirectSoundBuffer_Play #10 (buffer 19D86F64, flags 0x00000000)
3971:DSOUND: XBtoHost XADPCM->decode xbBytes=2484 pcmBytes=8970 ch=1 bits=16 rate=22050
3972:DSOUND:   -> decoded peak amplitude = 32112
3973:DSOUND:   -> hRet=0x00000000 hostBuf=1CD621D8 bytes=8970 playFlags=0x0 emuFlags=0x80000002
3974:DSOUND: IDirectSoundBuffer_Play #11 (buffer 19D87AA4, flags 0x00000000)
3975:DSOUND: XBtoHost XADPCM->decode xbBytes=6444 pcmBytes=23270 ch=1 bits=16 rate=22050
3976:DSOUND:   -> decoded peak amplitude = 19230
3977:DSOUND:   -> hRet=0x00000000 hostBuf=1CD625E8 bytes=23270 playFlags=0x0 emuFlags=0x80000002
3978:DSOUND: IDirectSoundBuffer_Play #12 (buffer 19D87664, flags 0x00000000)
3979:DSOUND: XBtoHost XADPCM->decode xbBytes=3168 pcmBytes=11440 ch=1 bits=16 rate=22050
3980:DSOUND:   -> decoded peak amplitude = 31942
3981:DSOUND:   -> hRet=0x00000000 hostBuf=1CD622A0 bytes=11440 playFlags=0x0 emuFlags=0x80000002
3982:DSOUND: IDirectSoundBuffer_Play #13 (buffer 19D877A4, flags 0x00000000)
3983:DSOUND: XBtoHost XADPCM->decode xbBytes=5436 pcmBytes=19630 ch=1 bits=16 rate=22050
3984:DSOUND:   -> decoded peak amplitude = 9296
3985:DSOUND: XBtoHost PCM->copy xbBytes=27326 pcmBytes=27326 ch=1 bits=8 rate=11025      (16th and last XBtoHost report; cap DSI:91)
```

Patch-binding lines of the same file that matter: `CDirectSound3DCalculator_Calculate3D Patched` (552), `..._GetVoiceData Patched` (553), the `C`->`I` mappings for `CDirectSoundBuffer_Set3DVoiceData/SetHeadroom/SetMixBinVolumes_8/SetMixBins/SetOutputBuffer/Use3DVoiceData` (565-602), `CDirectSoundVoice_*` unpatched (628-643), `CDirectSound_DoWork` unpatched (649), `DirectSoundDoWork Patched` (837), `IDirectSound3DCalculator_Calculate3D/GetVoiceData: No patch registered` (847-848; they are `jmp`/`push ebp; mov ebp,esp; pop ebp; jmp` thunks into the patched C functions, xbe 0x1D46F2 / 0x1D3836).

## Appendix A2 - the 3D lines of today's 16:33 run (live file, line numbers as of 16:41)

```
1368:FILEOPEN: z:\data\global\global_audiobanks.smb
1369:DSOUND: volume=-600 emuFlags=0x00100000 codecs[pcm=1 xadpcm=1 unknown=1]
1370:DS3D: Use3DVoiceData buffer 19970FAC arg 00000001
1371:DSOUND: volume=-600 emuFlags=0x00100000 codecs[pcm=1 xadpcm=1 unknown=1]
1372:DS3D: Use3DVoiceData buffer 199710AC arg 00000001
      ... the pattern (volume line, Use3DVoiceData on a new buffer) repeats through 1391-1392 (12 volume lines, cap)
1393-1420: 28 more "DS3D: Use3DVoiceData buffer <new pointer> arg 00000001" (40 distinct buffers, cap; 19970C6C..19971A2C in 0x40 steps, then 19BB3564, 19BB2FA4)
1421:DS3D: Calculate3D a1=002026E0 a2=0D171D84
1422:DS3D: GetVoiceData a1=002026E0 a2=0D171D84 a3=00202498 a4=0D171E00 a5=0D171E28
1423:DS3D: Set3DVoiceData buffer 19970FAC data 0D171E28 = 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000
1424:DS3D: Calculate3D a1=002026E0 a2=0D19DF34
1425:DS3D: GetVoiceData a1=002026E0 a2=0D19DF34 a3=00202498 a4=0D19DFB0 a5=0D19DFD8
1426:DS3D: Set3DVoiceData buffer 199710AC data 0D19DFD8 = 00000000 x8
      ... 40 such triples, same buffers in the same order as the Use3DVoiceData lines, every dump eight zero dwords
1538:DS3D: Calculate3D a1=002026E0 a2=0D204954
1539:DS3D: GetVoiceData a1=002026E0 a2=0D204954 a3=00202498 a4=0D2049D0 a5=0D2049F8
1540:DS3D: Set3DVoiceData buffer 19BB2FA4 data 0D2049F8 = 00000000 x8
1541:FILEOPEN: z:\data\global\global_globallip.smb
1542:FILEOPEN: d:\data\audio\xwb\general_stream.xwb
      no Play / XBtoHost / DS3D line after 1540 up to line 4622 (the run had not reached gameplay)
```

## Appendix B - tools written for this plan (read-only analysis, no game needed)

* `tools\ds3d_xwb.py` - parses the WBND v3 in-memory banks (`summary`, `list <bank>`, `match <sizes>`, `log <diagnostics.txt>`); produced the wave names in A.1.
* `tools\ds3d_logparse.py` - parses legacy `DSOUND:` lines, the deployed and the planned `DS3D:` lines and `MARK:` stamps; attributes `Calculate3D`/`GetVoiceData` to the buffer whose `Set3DVoiceData` consumes their `a5`; `--verdict`, `--window N`, `--buf X`, `--all`.
* `tools\ds3d_disasm.py` (pre-existing) - the XBE cross-reference tool used for A.4; invoke with `--syms swse\research\symbols\SteefFinal_syms.tsv`.
