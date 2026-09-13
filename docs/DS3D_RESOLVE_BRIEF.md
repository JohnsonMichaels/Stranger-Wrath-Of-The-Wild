# 3D sound — resolve brief

Shared brief for the three resolvers. Read this first, then the report named in your
own section. Everything here was established by four independent investigations
(`DS3D_CXBX_SINKS.md`, `DS3D_CALL_PATH.md`, `DS3D_CALCULATOR_CONTRACT.md`,
`DS3D_MATH.md`) that converged; do not re-derive it.

## Diagnosis (converged, three independent lines)

Positional effects are silent because of ONE sink in the emulator, reached by ONE
trigger from the title's XACT layer:

1. XACT creates a positional effect as an ordinary *track* voice (mixbins `{0,1}`,
   not CTRL3D) and a parent *source* voice (a MIXIN submix, flag 0x2000).
2. `XACT::CSoundSource::SetOutputBuffer` routes the track's dry signal into the parent
   (`IDirectSoundBuffer_SetOutputBuffer` — a Cxbx stub, `LOG_NOT_SUPPORTED`) and then
   calls `IDirectSoundBuffer_SetMixBins(track, {10, 3})` — reverb send + LFE only —
   which Cxbx DOES honour. On Xbox that is correct: the dry level lives on the parent.
3. At the PLAY event `UpdateTrackVolume -> IDirectSoundBuffer_SetMixBinVolumes` runs
   `HybridDirectSoundBuffer_SetMixBinVolumes_8` (DirectSoundInline.hpp, the "dominant
   volume" fold), which scores only bins `< XDSMIXBIN_SPEAKERS_MAX` (6) excluding LFE
   (3). With table `{10,3}` nothing qualifies, `maxVolume` stays `DSBVOLUME_MIN`, it is
   stored in `Xb_VolumeMixbin`, and `HybridDirectSoundBuffer_SetVolume` adds it to the
   host volume on that call and on EVERY later `SetVolume`: host `SetVolume(-10000)`,
   `Play` returns `DS_OK`, nothing is heard.
4. 2D effects and NULL-parent voices keep `{0,1}`, never take that path, and play.

The 3D calculator stubs are NOT the mute: with them, XACT's voice data stays zero,
which is FULL volume. They are why nothing is *positioned*.

Runtime confirmation is being taken with the instrumentation already in the tree
(`DSPLAY:`, `DSMIX:`, `DS3D:` lines in `C:\Users\<you>\SWBeta\oddbeta\diagnostics.txt`,
added by `tools\ds3d_instrument.py`). Expect for a footstep: `DSMIX: SetMixBins ...
table={10:0 3:0}`, then `DSMIX: SetMixBinVolumes ... -> maxVolume=-10000`, then
`DSPLAY: ... hostVol=-10000`. If the log contradicts this, STOP and report.

## The fix, in three disjoint parts

### Part A — the Xbox's own calculator runs natively; the emulator receives its output

The 3D calculator is pure CPU code inside the XBE (light-HRTF 5-channel; only
externals are `KeSave/RestoreFloatingPointState`, which return success in Cxbx). It
never runs because THREE patches sit on it. Remove exactly these `PATCH_ENTRY` lines
from `src/core/hle/Patches.cpp`:

    CDirectSound3DCalculator_Calculate3D     (line ~241)
    CDirectSound3DCalculator_GetVoiceData    (line ~242)
    DirectSoundUseLightHRTF                  (line ~292)  <- installs the algorithm
                                                            table; MUST go with the two above

Leave `DirectSoundUseFullHRTF`, `*4Channel` patched (this title calls only Light).
Both calculator functions are **static stdcall with no object** (`ret 8` / `ret 0x14`);
only XACT calls them (`CSoundSource::Update3DProperties`), with the listener at a
static address. Details and every struct offset: `DS3D_CALCULATOR_CONTRACT.md`.

Then implement the receiver, which XACT calls with the calculator's output:

- `IDirectSoundBuffer_Set3DVoiceData(pHybridThis, X_DS3DCALCVOICEDATA*)` and
  `IDirectSoundBuffer_Use3DVoiceData(pHybridThis, BOOL)` in `DirectSoundBuffer.cpp`
  (currently stubs; arities and stdcall verified correct).
- `X_DS3DCALCVOICEDATA` is 0x38 bytes: `+0 dwChangeMask, +4 lDistanceVolume (bit 0x01),
  +8 lConeVolume (0x02), +0xC lFrontVolume / +0x10 lRearVolume (0x04),
  +0x14 lLeftRightVolume / +0x18 lCenterVolume (0x08), +0x1C lDirectVolume /
  +0x20 lReverbVolume (0x10), +0x24 lDopplerPitch (0x20, 1/4096 octave),
  +0x28 flFIRFilterAzimuth / +0x2C flFIRFilterElevation (0x40, degrees, + = right),
  +0x30/+0x34 dwIIRFilter* (0x80)`. Volumes in mB (hundredths of dB), <= 0.
- Merge rule: copy a field only when its bit is set in `dwChangeMask` (the calculator
  sets a bit only when the value changed).
- XACT masks per voice kind: the parent (mixin) source keeps only PANNING bits
  (`&= 0xDC`: front/rear, left/right, centre, azimuth); the track (wave) voice keeps
  only distance/cone/I3DL2/doppler (`&= 0x33`). So the audible track voice carries its
  own attenuation, and its PAN comes from the parent -> Part A also implements
  `IDirectSoundBuffer_SetOutputBuffer(child, parent)` as: record `parent` on the child
  hybrid buffer (`Xb_OutputParent`), and when a parent receives voice data, propagate
  the pan to every child whose `Xb_OutputParent == parent` (scan `g_pDSoundBufferCache`).
- Host mapping (from `DS3D_MATH.md`, Xbox `ConvertVolumeValues`):
    volume offset (mB) = clamp(lDistanceVolume + lConeVolume + lDirectVolume, -10000, 0)
    pan: on Xbox L/R of each 3D pair are EQUAL and the placement is the HRTF FIR chosen
         by azimuth; there is no L/R volume field. Derive host pan from
         flFIRFilterAzimuth: pan = clamp(sin(az) * 10000 * k, -10000, 10000) with
         k = 0.5 to start (the measured HRTF ILD is only ~3 dB at 90°; make k a single
         named constant). Front/rear: fold rear into the same pan, attenuate by
         lRearVolume - lFrontVolume only if that is negative.
    pitch delta = lDopplerPitch (1/4096 octave), applied on top of the voice's pitch.
- Put the struct, the state, the merge and the mapping in NEW files
  `src/core/hle/DSOUND/DirectSound/DirectSound3DVoice.hpp/.cpp` and add the .cpp to
  the explicit source list in `CMakeLists.txt` (line ~363, next to
  `DirectSound3DCalculator.cpp`). Public API (Part B and C compile against this
  exactly):

        #include "DirectSound3DVoice.hpp"
        namespace xbox { struct X_DS3DCALCVOICEDATA; }               // 0x38 bytes, static_assert it
        struct Cxbxr3DVoiceState {
            LONG lDistanceVolume, lConeVolume, lFrontVolume, lRearVolume,
                 lLeftRightVolume, lCenterVolume, lDirectVolume, lReverbVolume, lDopplerPitch;
            float flAzimuth, flElevation;
            DWORD dwHave;   // bits received so far (same numbering as dwChangeMask)
            bool  bUse;     // Use3DVoiceData(TRUE)
        };
        void Cxbxr3DVoice_Init(Cxbxr3DVoiceState&);
        void Cxbxr3DVoice_Merge(Cxbxr3DVoiceState&, const xbox::X_DS3DCALCVOICEDATA*);
        LONG Cxbxr3DVoice_VolumeOffsetMb(const Cxbxr3DVoiceState&);  // <= 0; 0 unless bUse
        LONG Cxbxr3DVoice_PanMb(const Cxbxr3DVoiceState&);           // -10000..10000; 0 unless bUse
        LONG Cxbxr3DVoice_PitchDelta(const Cxbxr3DVoiceState&);      // 1/4096 octave; 0 unless bUse

  Applying to the host: call `HybridDirectSoundBuffer_SetVolume(..., lVolume3DMb)` and
  `HybridDirectSoundBuffer_SetPan3D(...)` from Part B (signatures below) whenever the
  state changes and at `Play`. Keep a bounded `printf("DS3D: ...")` per merge showing
  the fields received and the resulting offset/pan/pitch.

### Part B — the fold stops muting, and the host volume/pan path learns about 3D

All in `src/core/hle/DSOUND/DirectSound/DirectSoundInline.hpp` and the struct in
`DirectSound.hpp`:

1. Fold fix in `HybridDirectSoundBuffer_SetMixBinVolumes_8`: score bins
   `{0,1,2,4,5}` AND the 3D speaker feeds `{6,7,8,9}` (bins 6/7 = 3D front pair,
   8/9 = 3D rear pair, per `DS3D_MATH.md`); never score 3 (LFE), 10 (I3DL2 send) or
   11+ (FX sends). If NO scored bin is present in the table, `maxVolume = 0` — the
   table then holds only send levels and the dry level is defined by the parent's 3D
   data, not by them. Keep the existing `DSMIX` printf (it is the proof).
2. `HybridDirectSoundBuffer_SetVolume` gains a trailing DEFAULTED parameter
   `LONG lVolume3DMb = 0`, added to `lVolume` before `DSoundBufferUpdateHostVolume`.
   `HybridDirectSoundBuffer_SetPitch` (or SetFrequency, whichever applies the voice
   pitch) gains `LONG lPitchDelta3D = 0` likewise. Defaulted so no existing call site
   changes; Parts A and C pass the values where they hold the hybrid buffer.
3. New `static inline HRESULT HybridDirectSoundBuffer_SetPan3D(LPDIRECTSOUNDBUFFER8 pDSBuffer, LONG lPanMb)`
   guarding the host `SetPan` (needs `DSBCAPS_CTRLPAN` on the host buffer — verify the
   creation flags in this file add CTRLPAN alongside CTRLVOLUME/CTRLFREQUENCY, and add
   it if not; note `DSBCAPS_CTRLPAN` is not allowed together with `DSBCAPS_CTRL3D` on
   the host, so only add it for non-CTRL3D buffers).
4. `DirectSound.hpp` hybrid buffer struct (`EmuDirectSoundBuffer`, the one with
   `Xb_VolumeMixbin` at ~line 109 and 331 — there are two declarations, keep them in
   step): add `Cxbxr3DVoiceState Xb_3D;` and `struct XbHybridDSBuffer* Xb_OutputParent;`
   (forward-declare; include `DirectSound3DVoice.hpp`). Initialise both where
   `Xb_VolumeMixbin = 0L` is initialised (Inline.hpp ~line 451).

### Part C — streams get the same receiver; tooling and the record

1. `src/core/hle/DSOUND/DirectSound/DirectSoundStream.cpp`: implement
   `IDirectSoundStream_Set3DVoiceData` / `Use3DVoiceData` / `SetOutputBuffer` with the
   Part A API and the Part B signatures, mirroring the buffer implementation (streams
   are the music path and CURRENTLY WORK — every change here must keep music playing;
   default state must be a no-op). The stream's voice sub-object is at +4 (contract doc).
2. `tools/ds3d_trail.py`: parse `diagnostics.txt` into a per-buffer trail between
   `MARK:` lines (NUMPAD 1-4 stamp `MARK: MARKn at swap N`), printing for each buffer
   the SetMixBins table, the fold result, the 3D fields received, and the DSPLAY
   read-back — so a footstep is one readable block.
3. Append a "3D sound — solved" section to `swse/research/BETA_REPORT.md` after the
   fix is verified (leave a clearly marked DRAFT until then).

## Rules for all three

- Edit with the Edit tool only (exact-string replace). Never Write/overwrite an
  existing file, never reformat. Another agent is editing neighbouring files at the
  same time; whole-file rewrites will destroy their work.
- Own only the files listed in your part. If you need a change in someone else's
  file, write the exact change you need into your report instead of making it.
- Do NOT build and do NOT launch the game. The coordinator builds all three parts
  together and runs the verification; concurrent builds have corrupted this build
  tree before. Write code that compiles on the first try: match the surrounding
  style, check every identifier you use exists in the headers you include.
- Compile-safety: this is MSVC x86, C++17. `xbox::long_xt` is 32-bit, `LONG` is
  Windows. Bounded `printf` only (EmuLog is disabled).
- Calling conventions are VERIFIED in `DS3D_CALCULATOR_CONTRACT.md`; do not change
  any patch signature.
- The listener is static and XACT-owned; nothing in the emulator needs to model it.

## Verification (coordinator)

Marked run: NUMPAD1 around three steps, NUMPAD2 around one attack, NUMPAD3 around a
moolah pickup. Success = footsteps and attacks audible; `DSPLAY: ... hostVol` for them
> -6400; `DS3D:` lines show non-zero distance/azimuth changing as the player moves
relative to an NPC; music unchanged.
