# DS3D_MATH — the Xbox DirectSound 3D calculator, recovered from the beta XBE

Scope: the software 3D calculator statically linked into the May-2004 devkit beta
(`C:\Users\<you>\SWBeta\Game\default.xbe`, DSOUND section guest 0x1d1b00..0x1dec2c).
Everything below is read from disassembly of that binary; each formula cites the
guest VA it was read at. Guest VA = PDB RVA + 0x10920.

Legend: **VERIFIED** = read from quoted disassembly. **INFERRED** = follows from the
code but one step is an interpretation (marked where it happens). **UNVERIFIED** =
general XDK knowledge, not backed by this binary; kept in section 11.

Tools written for this (all read-only over the XBE):
- `tools/ds3d_math_disasm.py` — whole-function listings, every absolute memory operand
  annotated with the float32/float64/int32 at that VA, call targets named.
- `tools/ds3d_dump.py` — dump a VA range as dwords/floats (tables, default structs).
- `tools/ds3d_hrtf.py` — decode the light-HRTF kernel table into per-azimuth L/R gains.

Two facts frame everything else:

1. **The whole calculator is CPU code with no hardware access.** `Calculate3D` and
   `GetVoiceData` only call the math helpers below, `CFpState::Save/Restore`
   (0x1d1b69/0x1d1b8e: a refcounted `KeSaveFloatingPointState` /
   `KeRestoreFloatingPointState`, taken only when `fs:[0x58]` is non-zero) and the
   algorithm vtable. The hardware is touched only *after* `Set3DVoiceData`, in
   `CMcpxVoiceClient::*` (writes to 0xfe82xxxx).
2. **The algorithm is a runtime-installed function table.** `CHRTFSource::m_vtable`
   (0x1dea7c, 10 slots) is all zeros in the file. The only writer in the binary is
   `CHRTFSource::SetLightHRTF5Channel` (0x1d59eb), whose only caller is
   `DirectSoundUseLightHRTF` (0x1d1dfb), which the title calls from its own `.text`
   at 0x1264bf. No Full-HRTF or 4-channel installer is linked. So the beta always
   runs the **"light HRTF, 5-channel" algorithm (m_nAlgorithm = 4)**, and its slots are:

   | slot | VA of impl | function |
   |---|---|---|
   | 0 | 0x1d572f | `CLightHRTFSource::CalcNormPos` |
   | 1 | 0x1d5769 | `CLightHRTFSource::CalcPolarCoords` |
   | 2 | 0x1d5434 | `CFullHRTFSource::CalcConeAngle` |
   | 3 | 0x1d54ca | `CFullHRTFSource::CalcRelativeVelocity` |
   | 4 | 0x1d5515 | `CFullHRTFSource::GetDistanceVolume` |
   | 5 | 0x1d5626 | `CFullHRTFSource::GetConeVolume` |
   | 6 | 0x1d5851 | `CLightHRTFSource::GetFrontRearVolume` |
   | 7 | 0x1d5908 | `CLightHRTFSource::GetCenterVolume` |
   | 8 | 0x1d56b6 | `CFullHRTFSource::GetDopplerPitch` |
   | 9 | 0x1d5971 | `CLightHRTFSource::GetFilterPair` |

   Cxbx patches `DirectSoundUseLightHRTF` as `LOG_IGNORED` (DirectSound.cpp:302), so
   under the emulator the guest vtable is never installed; calling the guest
   `Calculate3D` would hit `call [0x1dea7c]` = NULL. Cxbx also patches
   `CDirectSound3DCalculator_Calculate3D` / `_GetVoiceData` as `LOG_UNIMPLEMENTED`
   stubs (DirectSound3DCalculator.cpp:52,71), so no voice data is ever produced today.

---

## 1. Symbol map (guest VAs)

Calculator and helpers:

| VA | symbol |
|---|---|
| 0x1d1b69 / 0x1d1b8e | `CFpState::Save` / `Restore` (KeSave/RestoreFloatingPointState) |
| 0x1d1beb | `Math::FloatToLong` (`cvttss2si`: truncate toward zero) |
| 0x1d1bf4 | `Math::AmplitudeToVolume` |
| 0x1d5377 | `Math::MetersToVolume` |
| 0x1d53ae | `Math::PowerToVolume` |
| 0x1d7868 | `Math::RatioToPitch` |
| 0x1d5350 | `Math::MagnitudeVector3` |
| 0x1d77f1 | `Math::NormalizeVector2` (XZ plane, Y forced to 0) |
| 0x1d1c41..0x1d1d3e | `CHRTFSource::CalcPolarCoords/GetDistanceVolume/GetConeVolume/GetFrontRearVolume/GetCenterVolume/GetDopplerPitch` — pass-through thunks into `m_vtable` |
| 0x1d7f4b | `CHRTFSource::GetFilterPair` thunk (slot 9) |
| 0x1d1dfb | `DirectSoundUseLightHRTF` (public; title calls it at 0x1264bf) |
| 0x1d1e1a | `XAudioCalculatePitch` (Hz -> pitch units, confirms 4096/octave) |
| 0x1d59eb | `CHRTFSource::SetLightHRTF5Channel` (vtable installer) |
| 0x1d53f7 | `CFullHRTFListener::CalcNormOrient` |
| 0x1d5434..0x1d5a5a | `CFullHRTFSource::*`, `CLightHRTFSource::*` (table above) |
| 0x1d43d1 | `CDirectSound3DCalculator::Calculate3D` (public thunk `IDirectSound3DCalculator_Calculate3D` 0x1d46f2) |
| 0x1d2bd2 | `CDirectSound3DCalculator::GetVoiceData` (public thunk 0x1d3836) |
| 0x1d2eb3 | `CDirectSound3DCalculator::CalculateI3DL2Reverb` |
| 0x1dad7d / 0x1dabe5 | `CI3DL2Source::CalculateI3DL2` / `Get1PoleLoPass` |
| 0x1dea7c / 0x1deaa4 | `CHRTFSource::m_vtable` / `m_nAlgorithm` |

Voice side:

| VA | symbol |
|---|---|
| 0x1d483b | `CDirectSoundVoiceSettings::Initialize` (allocates 3D blocks, copies defaults) |
| 0x1d3c68 | `CDirectSoundVoice::Initialize` (sets hasRear / muteAtMax flags) |
| 0x1d3a85 | `CDirectSoundVoiceSettings::SetMixBins` (sets hasCenter flag) |
| 0x1d208d | `CDirectSoundVoiceSettings::SetMixBinVolumes` |
| 0x1d20b2 | `CDirectSoundVoiceSettings::IncludeSubMixBin` |
| 0x1d223f | `CDirectSoundVoice::Set3DVoiceData` |
| 0x1d2370 | `CDirectSoundVoice::Use3DVoiceData` |
| 0x1d2186 / 0x1d216d / 0x1d21db / 0x1d21fe | `CDirectSoundVoice::SetVolume / SetPitch / SetHeadroom / SetMixBinVolumes` |
| 0x1d1fb7 | `CDirectSound::SetMixBinHeadroom` (-> `CMcpxAPU::SetMixBinHeadroom` 0x1d6492) |
| 0x1d94b3 | `CMcpxVoiceClient::Apply3dSettings` |
| 0x1d96bb | `CMcpxVoiceClient::Commit3dSettings` (library-internal path: calls Calculate3D+GetVoiceData itself) |
| 0x1d936d / 0x1d8220 | `CMcpxVoiceClient::SetVolume` / `ConvertVolumeValues` (the per-mixbin combiner) |
| 0x1d9405 / 0x1d8474 | `CMcpxVoiceClient::SetPitch` / `ConvertPitchValue` |
| 0x1d8969 | `CMcpxVoiceClient::LoadHRTFFilter` |
| 0x1d8d99 | `CMcpxVoiceClient::SetFilter` |
| 0x1d816f | `CMcpxVoiceClient::ConvertMixBinValues` (packs bin indices into hw regs) |
| 0x1d46f7 | `CDirectSoundSettings::CDirectSoundSettings` (listener defaults) |

Data:

| VA | symbol / content |
|---|---|
| 0x1de030 | `DirectSoundI3DL2ListenerPreset_Default` = {-1000, -100, 0.0, 1.49, 0.83, -2602, 0.007, 200, 0.011, 100, 100, 5000} |
| 0x1de060 | `DirectSoundDefault3DListener` = {0x40, pos 0, vel 0, front (0,0,1), top (0,1,0), distF 1, rolloff 1, doppler 1} |
| 0x1de0a0 | `DirectSoundDefaultI3DL2Buffer` = {0,0,0,0, 0.0, obstr{0,0.0}, occl{0,0.25}} |
| 0x1de0c8 | `DirectSoundDefault3DBuffer` = {0x4c, pos 0, vel 0, inside 360, outside 360, coneOrient (0,0,1), outsideVol 0, minDist 1.0, maxDist 1e9, mode 0, distF 1, rolloff 1, doppler 1} |
| 0x1de114 | `DirectSoundDefaulMixBins_5Channel3D_PlusLFE` = 7 pairs @0x1de14c: (6,0)(8,0)(7,0)(9,0)(2,0)(10,0)(3,0) |
| 0x1de11c | `DirectSoundRequiredMixBins_5Channel3D` = 5 @0x1de184: 6,8,7,9,2 |
| 0x1de124 | `DirectSoundDefaultMixBins_3D` = 5 @0x1de1ac: 6,8,7,9,10 |
| 0x1de12c | `DirectSoundRequiredMixBins_3D` = 4 @0x1de1d4: 6,8,7,9 |
| 0x1de134/13c/144 | 6-channel 0..5; 4-channel 0,1,4,5; mono/stereo 0,1 |
| 0x1dcf4c | float 342.0 (speed of sound, m/s) |
| 0x1dcf50 | float3 (1,0,0) — default "right" for head-relative mode |
| 0x1dcf60 | 46 floats — centre-blend table B (section 5.4) |
| 0x1dd018 | 46 floats — centre-blend table A (section 5.4) |
| 0x1dd0d0 | 61 x 64 bytes — light-HRTF kernel pairs (section 5.6) |
| 0x1de27c | flat/identity HRTF kernel used when 3D is disabled |
| 0x1de3c8 | `g_dwDirectSoundSpeakerConfig` |

XACT is the only caller of the public calculator (all call sites are in the XACTENG
section): `XACT::CSoundSource::Update3DProperties` (0x1fbebb) calls
`IDirectSound3DCalculator_Calculate3D` (0x1fbee5, 0x1fbf47) and `_GetVoiceData`
(0x1fbf02, 0x1fbf64), then `IDirectSoundBuffer_Set3DVoiceData` (0x1fbf8c) or
`IDirectSoundStream_Set3DVoiceData` (0x1fbf97). XACT's listener is
`XACT::CSoundSource::m_3DListener` (0x2026e0), its I3DL2 listener 0x202498; the
source block lives at CSoundSource+0x44, the I3DL2 source at +0xc0 (= 0x44+0x7c)
and the voice data at +0xe8 (= 0x44+0xa4) — the same layouts as below.

---

## 2. Data structures (VERIFIED from field use)

All vectors are 3 x float32 (x, y, z). Volumes are `LONG` hundredths of dB (mB),
0 = full, -10000 = `DSBVOLUME_MIN`.

### 2.1 Listener block (arg 1 of Calculate3D / GetVoiceData) — 0x50 bytes

Lives at `CDirectSoundSettings+0x30` (ctor 0x1d46f7 copies 15 dwords from 0x1de064
to +0x38 and sets +0x30 = 0x3f); XACT keeps its own at 0x2026e0.

| off | field | notes |
|---|---|---|
| +0x00 | dwFlags (dirty) | bit0 position, bit1 velocity, bit2 orientation, bit3 distance factor, bit4 rolloff factor, bit5 doppler factor (0x3f = all) |
| +0x04 | dwFlags2 (valid) | bit 0x40 = right vector computed (see 4.1) |
| +0x08 | vPosition | |
| +0x14 | vVelocity | |
| +0x20 | vOrientFront | |
| +0x2c | vOrientTop | |
| +0x38 | flDistanceFactor | |
| +0x3c | flRolloffFactor | |
| +0x40 | flDopplerFactor | |
| +0x44 | vRight (derived) | = top x front, written by Calculate3D (0x1d443b) |

### 2.2 Source block (arg 2 of Calculate3D / GetVoiceData) — 0xa4 bytes

Allocated by `CDirectSoundVoiceSettings::Initialize` when `DSBCAPS_CTRL3D` (0x10) is
set: `MemAlloc('DSda', 0xa4)` (0x1d4866), stored at settings+0xb4; 18 dwords copied
from `DirectSoundDefault3DBuffer+4` to +0x08 (0x1d489d), 9 dwords from
`DirectSoundDefaultI3DL2Buffer` to +0x80 (0x1d48b3); flags set to 0x07ff0000
(0x1d48c0) and I3DL2 flags to 0x007f0000 (0x1d48cd).

| off | field | notes |
|---|---|---|
| +0x00 | dwFlags (dirty) | bits 16..26 = source params changed: 0x10000 pos, 0x20000 vel, 0x40000 cone angles, 0x80000 cone orient, 0x100000 cone outside vol, 0x200000 min/max dist, 0x400000 mode, 0x800000 distance factor, 0x1000000 rolloff factor, 0x2000000 doppler factor, 0x4000000 flags58 changed. Bits 27..31 = derived values changed: 0x08000000 normPos, 0x10000000 distance, 0x20000000 azimuth/elevation, 0x40000000 cone angle, 0x80000000 relative velocity |
| +0x04 | dwFlags2 (valid) | 0x18000000 normPos+dist, 0x20000000 polar, 0x40000000 cone, 0x80000000 relvel |
| +0x08 | vPosition | |
| +0x14 | vVelocity | |
| +0x20 | dwInsideConeAngle | degrees, full cone (default 360) |
| +0x24 | dwOutsideConeAngle | degrees, full cone (default 360) |
| +0x28 | vConeOrientation | default (0,0,1) |
| +0x34 | lConeOutsideVolume | mB |
| +0x38 | flMinDistance | default 1.0 |
| +0x3c | flMaxDistance | default 1e9 |
| +0x40 | dwMode | 0 normal, 1 head-relative, 2 = DS3DMODE_DISABLE |
| +0x44 | flDistanceFactor | |
| +0x48 | flRolloffFactor | |
| +0x4c | flDopplerFactor | |
| +0x50 | pflRolloffCurve | custom curve points (0 = none) |
| +0x54 | dwRolloffCurvePoints | |
| +0x58 | dwFlags58 | bit0 hasRear, bit1 hasCenter, bit2 muteAtMaxDistance (origins in 6.3) |
| +0x5c | vNormPos (derived) | unit vector listener->source, y = 0 (light algo) |
| +0x68 | flDistance (derived) | XZ-plane distance, world units |
| +0x6c | flRelativeVelocity (derived) | m/s along vNormPos (>0 receding) |
| +0x70 | flConeAngle (derived) | "full cone" measure 0..360 |
| +0x74 | flAzimuth (derived) | degrees, (-180,180], + = right |
| +0x78 | flElevation (derived) | always 0 in the light algorithm |
| +0x7c | I3DL2 source flags | |
| +0x80 | DSI3DL2BUFFER | lDirect +0x80, lDirectHF +0x84, lRoom +0x88, lRoomHF +0x8c, flRoomRolloffFactor +0x90, Obstruction {lHFLevel +0x94, flLFRatio +0x98}, Occlusion {lHFLevel +0x9c, flLFRatio +0xa0} |

### 2.3 Voice-data block (arg 5 of GetVoiceData; arg of Set3DVoiceData) — 0x38 bytes

Allocated `MemAlloc('DSda', 0x38)` at 0x1d48e0, stored at settings+0xb8.

| off | field | producer | consumer |
|---|---|---|---|
| +0x00 | dwFlags | bit set when the field changed | Set3DVoiceData copies only flagged fields |
| +0x04 | lDistanceVolume | 5.1 | bit 0x01 |
| +0x08 | lConeVolume | 5.2 | bit 0x02 |
| +0x0c | lFrontVolume | 5.3 | bit 0x04 (with +0x10) |
| +0x10 | lRearVolume | 5.3 | |
| +0x14 | lFrontLRAdjust (centre blend, front pair) | 5.4 | bit 0x08 (with +0x18) |
| +0x18 | lCenterVolume | 5.4 | |
| +0x1c | lI3DL2DirectVolume | 5.7 | bit 0x10 (with +0x20) |
| +0x20 | lI3DL2RoomVolume | 5.7 | |
| +0x24 | lDopplerPitch | 5.5 | bit 0x20 |
| +0x28 | flAzimuth | copy of src+0x74 | bit 0x40 (with +0x2c) -> LoadHRTFFilter |
| +0x2c | flElevation | copy of src+0x78 | |
| +0x30 | wDirectLowPass (16-bit coef, 0xffff = open) | 5.7 | bit 0x80 (with +0x34) -> SetFilter |
| +0x34 | wRoomLowPass | 5.7 | |

Field names are mine (the PDB has no struct names); offsets are VERIFIED.

### 2.4 Voice settings (`CDirectSoundVoiceSettings`, at CDirectSoundVoice+0x10)

| off | field |
|---|---|
| +0x08 | creation flags (DSBCAPS_*; 0x10 CTRL3D, 0x20000 MUTE3DATMAXDISTANCE, 0x2000/0x80000/0x100000 MIXIN/FXIN/FXIN2) |
| +0x0b | bit0 = use3DVoiceData (default 1 for 3D voices, 0x1d4900; toggled by Use3DVoiceData 0x1d237e/0x1d2384) |
| +0x0e | byte: sub-mix-bin count (IncludeSubMixBin) |
| +0x18 | lPitch (SetPitch 0x1d2178) |
| +0x1c | lVolume - dwHeadroom ("effective volume", SetVolume 0x1d2191, SetHeadroom 0x1d21ec) |
| +0x20 | dwHeadroom (default 600 mB for plain voices, 0 for CTRL3D and MIXIN/FXIN voices, 0x1d484f/0x1d485b) |
| +0x24 | mix-bin count (<= 8) |
| +0x28 | byte[8] mix-bin indices |
| +0x30 | LONG[32] lMixBinVolume indexed by mix-bin number (SetMixBinVolumes 0x1d20a5) |
| +0xb4 | -> source block (2.2) |
| +0xb8 | -> voice-data block (2.3) |

`CDirectSoundVoice`: +0x0c -> `CMcpxVoiceClient`, +0x10 -> settings. `CMcpxVoiceClient`
+0x70 -> settings, +0x64 = number of hardware voices the DS voice spans (multichannel).

---

## 3. Units and the math helpers (VERIFIED)

```
FloatToLong(x)        = (long) x, truncation toward zero            ; 0x1d1beb cvttss2si
AmplitudeToVolume(a)  = a <= 0 ? -10000 : a >= 1 ? 0 : FloatToLong(2000 * log10(a))   ; 0x1d1bf4
PowerToVolume(p)      = p <= 0 ? -10000 : p >= 1 ? 0 : FloatToLong(1000 * log10(p))   ; 0x1d53ae
MetersToVolume(m)     = m < 0 ? 0 : FloatToLong(-2000 * log10(1 + m))                 ; 0x1d5377
RatioToPitch(r)       = fistp(4096 * log2(r))   (round-to-nearest, 0x45800000 = 4096.0) ; 0x1d7868
XAudioCalculatePitch(hz) = hz == 48000 ? 0 : RatioToPitch(hz / 48000)                 ; 0x1d1e1a
```

- Volumes: hundredths of dB, <= 0. `-10000` (`0xffffd8f0`) is used as the mute value
  throughout. Hardware conversion (0x1d83a9..0x1d83be): `att = (-V * 64) / 100`
  (1/64 dB steps), clamped to 0xfff = 63.98 dB. So any per-bin total <= -6398 mB is
  already silent on the Xbox.
- Pitch: 1/4096 octave. Doppler pitch range [-32767, +4096]; final voice pitch
  clamp [-32767, +8191] (0x1d84d5..0x1d84e7); hardware register = pitch << 16.
- Angles in degrees. Distances in world units; `distanceFactor` (listener x source
  product) converts to metres and is used ONLY for doppler and the near-field
  front/rear blend, never for the distance attenuation.

---

## 4. Calculate3D (0x1d43d1): the derived geometry

`Calculate3D(pListener, pSource)`. Combined dirty flags `F = L.flags | S.flags`
(0x1d43ee..0x1d4403). If `F & 0x400000` (mode changed): mode == 2 (disable) clears all
other flags, else `F |= 0x07ff0000` (recalc everything; 0x1d4455). If `pSource == 0`
or mode == 2 nothing else is computed (0x1d4460..0x1d446e). On exit, listener gets
back `F & 0x7f` and source `F & 0xffff0000` (0x1d45d3..0x1d45fd).

### 4.1 Listener right vector (`CalcNormOrient`, 0x1d53f7) — recomputed if `F & 4` or `!(L.flags2 & 0x40)`

```
right = top x front:  right.x = top.y*front.z - top.z*front.y
                      right.y = front.x*top.z - top.x*front.z
                      right.z = top.x*front.y - front.x*top.y
```
No normalisation of any orientation vector anywhere (front/top are used as given).
Stored at L+0x44; if it changed `F |= 0x40`.

### 4.2 Normalised position and distance (`CLightHRTFSource::CalcNormPos`, 0x1d572f) — if `F & 0x410001` or `!(S.flags2 & 0x18000000)`

```
n.x = S.pos.x ; n.z = S.pos.z ; n.y = 0
if mode != 1 (not head-relative): n.x -= L.pos.x ; n.z -= L.pos.z
dist = sqrt(n.x^2 + n.z^2)            ; NormalizeVector2 0x1d77f1
if dist != 0: n.x /= dist ; n.z /= dist   (if n.x == n.z == 0 the vector stays 0 and dist = 0)
```
**Elevation is discarded: the Y difference never enters distance, azimuth or anything
else in this algorithm.** A source 100 units straight above the listener is at
distance 0 and full volume.
`S.normPos` (+0x5c) updated, `F |= 0x08000000` if changed; `S.dist` (+0x68), `F |= 0x10000000` if changed.

### 4.3 Azimuth / elevation (`CLightHRTFSource::CalcPolarCoords`, 0x1d5769) — if `F & 0x18400044` or `!(S.flags2 & 0x20000000)`

```
if mode == 1: front = (0,0,1) [0x1de07c], right = (1,0,0) [0x1dcf50]  else front = L.front, right = L.right
f = dot(front, n) ; r = dot(right, n)          ; full 3-D dots, but n.y == 0
elevation = 0                                   ; 0x1d57bb unconditionally
if dist == 0:            az = 0
elif |f| > |r|:          az = 45 * |r| / |f|          ; 0x1d57e7
elif |r| != 0:           az = 90 - 45 * |f| / |r|     ; 0x1d583b
else:                    az = 0
if f < 0: az = 180 - az                                ; 0x1d5817
if r < 0: az = -az                                     ; 0x1d5831
```
So azimuth is a piecewise-linear (tangent-ratio) approximation of atan2: exact at
0, 45, 90, 135, 180; error up to ~4 deg in between. Range (-180, 180], 0 = ahead,
+90 = right, 180 = behind. Stored at S+0x74/+0x78, `F |= 0x20000000` if changed.

### 4.4 Cone angle (`CFullHRTFSource::CalcConeAngle`, 0x1d5434) — if `F & 0x08080000` or `!(S.flags2 & 0x40000000)`

With `c = S.coneOrientation` (as given, not normalised) and `n = S.normPos`:
```
m1 = |c - n| ; m2 = |c + n|               ; MagnitudeVector3 0x1d5350
if m2 <  m1: angle = 180 * m2 / m1        ; 0x1d5498 (constants 45.0 @0x258bd0 * 4.0 @0x259430)
else:        angle = 360 - 180 * m1 / m2  ; 0x1d54ab (90 - 45*m1/m2, then *4)
```
For unit vectors, if phi is the angle between the cone axis and the direction
source->listener (-n), `angle = 180*tan(phi/2)` for phi <= 90 and `360 - 180*tan((180-phi)/2)`
above — a 0..360 "full cone angle" that is exact at 0/90/180 and compared directly
against `dwInsideConeAngle` / `dwOutsideConeAngle` (also full-cone degrees). Stored at
S+0x70, `F |= 0x40000000` if changed.

### 4.5 Relative velocity (`CFullHRTFSource::CalcRelativeVelocity`, 0x1d54ca) — if `F & 0x08420002` or `!(S.flags2 & 0x80000000)`

```
mode == 1: v = dot(S.vel, n)
else:      v = dot(S.vel - L.vel, n)        ; world units/s along listener->source (>0 = receding)
```
Stored at S+0x6c, `F |= 0x80000000` if changed.

---

## 5. GetVoiceData (0x1d2bd2): the audible outputs

`GetVoiceData(pListener, pSource, pI3DL2Listener, pI3DL2Source, pVoiceData)`.
`F = L.flags | S.flags`; if `F & 0x400000` then `F |= 0xffff0000` (0x1d2bf7). Every
output below is only recomputed when its mask matches, is forced to 0 when
`S.mode == 2`, and sets its bit in `VD.flags` only if the value changed.

### 5.1 Distance volume (`CFullHRTFSource::GetDistanceVolume`, 0x1d5515) — mask 0x15200010

Inputs (0x1d2c0f..0x1d2c44): `rolloff = L.rolloffFactor * S.rolloffFactor`, `minD = S+0x38`,
`maxD = S+0x3c`, `curve = S+0x50`, `nPts = S+0x54`, `dist = S+0x68`, `mute = (S.flags58 >> 2) & 1`.

```
if dist <= minD:                 vol = 0                            ; 0x1d551a..0x1d561a
elif mute && dist >= maxD:       vol = -10000                       ; 0x1d5533..0x1d5543
else:
   if dist > maxD: dist = maxD                                      ; 0x1d554e..0x1d555e (clamp, not mute)
   if curve && nPts:                                                ; custom rolloff curve
       seg  = (maxD - minD) / nPts
       i    = FloatToLong((dist - minD) / seg) ; if i >= nPts: i = nPts - 1
       y0   = i == 0 ? 1.0 : curve[i-1] ; y1 = curve[i]
       frac = (dist - minD - i*seg) / seg
       vol  = AmplitudeToVolume(y0 + frac * (y1 - y0))              ; 0x1d5577..0x1d55f4
   else:
       vol  = MetersToVolume((dist / minD - 1) * rolloff)           ; 0x1d5600..0x1d5613
            = FloatToLong(-2000 * log10(1 + rolloff * (dist/minD - 1)))
            = 20 log10( minD / (minD + rolloff * (dist - minD)) ) in mB
```
This is the standard inverse-distance law (gain = minD / (minD + rolloff*(dist-minD))).
Without `DSBCAPS_MUTE3DATMAXDISTANCE` the volume simply stops decreasing at maxD.
With default rolloff 1, minD 1: 10 units = -2000 mB, 100 units = -4000 mB, 1000 units
= -6000 mB. The hardware floor is -6398 mB, i.e. 1580 units at defaults.

### 5.2 Cone volume (`CFullHRTFSource::GetConeVolume`, 0x1d5626) — mask 0x40140000

Inputs: `inside = S+0x20`, `outside = S+0x24`, `outVol = S+0x34`, `angle = S+0x70`.
```
if angle <= inside:                     vol = 0                     ; 0x1d5639..0x1d564a
elif inside < outside && angle < outside:
     vol = FloatToLong( (angle - inside) * outVol / max(1, outside - inside) )   ; 0x1d567c..0x1d56a8
else:                                   vol = outVol                ; 0x1d5670..0x1d5678
```
Linear in dB between the two cone edges. With the defaults (360/360) it is always 0.

### 5.3 Front / rear (`CLightHRTFSource::GetFrontRearVolume`, 0x1d5851) — mask 0x34800008

Inputs (0x1d2ca9..0x1d2cd9): `x = L.distanceFactor * S.distanceFactor * dist` (metres),
`az = S+0x74`, `el = S+0x78` (unused), `hasRear = S.flags58 & 1`.
```
if !hasRear:  front = 0 ; rear = -10000                             ; 0x1d58f5
else:
   t = clamp(|az| / 90 - 0.5, 0, 1)        ; 0 up to 45 deg, 1 from 135 deg   ; 0x1d5878..0x1d58ad
   if x < 0.5:  t = 0.5 + 0.5 * x * (t - 0.5)   ; near-field blend toward equal ; 0x1d58b0..0x1d58c6
   front = PowerToVolume(1 - t)                                     ; 0x1d58d0..0x1d58d8
   rear  = PowerToVolume(t)                                         ; 0x1d58e0..0x1d58e9
```
Constant-power front/rear crossfade. Note the near-field branch is discontinuous at
x = 0.5 m (t' = 0.5 + 0.25(t-0.5) just below, t just above) — reproduce or not, it is
what the Xbox does. `hasRear` = speaker config is SURROUND or AC3 (6.3).

### 5.4 Centre blend (`CLightHRTFSource::GetCenterVolume`, 0x1d5908) — mask 0x24000000, only if `S.flags58 & 2`

```
i = FloatToLong(|az|)
if i >= 45:  frontLRAdjust = 0      ; centre = -10000               ; 0x1d595e
else:        centre        = AmplitudeToVolume(A[i])                 ; A @0x1dd018  -> VD+0x18
             frontLRAdjust = AmplitudeToVolume(1 - B[i])             ; B @0x1dcf60  -> VD+0x14
```
If the voice has no centre bin (`flags58 & 2` clear) both outputs are 0.

Table A (0x1dd018, centre amplitude, i = 0..45):
```
0.707107 0.706676 0.705384 0.703233 0.700225 0.696364 0.691655 0.686103 0.679715 0.672499
0.664463 0.655618 0.645974 0.635543 0.624338 0.612372 0.599661 0.586218 0.572061 0.557208
0.541675 0.525483 0.508650 0.491198 0.473147 0.454519 0.435338 0.415627 0.395409 0.374710
0.353553 0.331967 0.309975 0.287606 0.264887 0.241845 0.218508 0.194905 0.171065 0.147016
0.122788 0.098410 0.073913 0.049325 0.024678 0.0
```
Table B (0x1dcf60; front pair uses 1 - B[i]):
```
0.500000 0.499391 0.497567 0.494537 0.490315 0.484923 0.478386 0.470737 0.462012 0.452254
0.441511 0.429835 0.417283 0.403915 0.389798 0.375000 0.359593 0.343652 0.327254 0.310480
0.293412 0.276132 0.258725 0.241275 0.223868 0.206588 0.189520 0.172746 0.156348 0.140407
0.125000 0.110202 0.096085 0.082717 0.070165 0.058489 0.047746 0.037988 0.029263 0.021614
0.015077 0.009685 0.005463 0.002433 0.000609 0.0
```
At az = 0: centre -3.01 dB, front pair -6.02 dB (0.5^2*2 + 0.707^2 = 1: constant power).
At |az| >= 45: centre muted, front pair 0 dB.

### 5.5 Doppler pitch (`CFullHRTFSource::GetDopplerPitch`, 0x1d56b6) — mask 0x82800028

Inputs (0x1d2d65..0x1d2d86): `v = (L.distanceFactor*S.distanceFactor) * (L.dopplerFactor*S.dopplerFactor) * S.relVel` (m/s, >0 receding).
```
if v == 0:       pitch = 0
elif v >= 342:   pitch = -32767                                      ; 0x1d56dd..0x1d56ef
elif v <= -342:  pitch = +4096                                       ; 0x1d56f7..0x1d5709
else:            pitch = RatioToPitch(1 - v / 342)                   ; 0x1d5711..0x1d5721 (1/342 = 0x3b3fa030)
                       = round(4096 * log2(1 - v/342))
```
Simplified one-sided Doppler (frequency ratio 1 - v/c, c = 342 m/s @0x1dcf4c).

### 5.6 HRTF azimuth/elevation and the interaural pan — mask 0x20000000

GetVoiceData copies `S.az`, `S.el` to VD+0x28/+0x2c (0x1d2dac..0x1d2dea). They are consumed
by `LoadHRTFFilter` (0x1d8969), which calls `GetFilterPair(az, el, fold, out[2])`
(`CLightHRTFSource::GetFilterPair` 0x1d5971) with `fold = (speakerConfig & 0xffff) == 2 || (speakerConfig & 0x10000)`:
```
a3  = ((FloatToLong(|az| + 1.5)) / 3) * 3        ; |az| rounded to a multiple of 3
if fold && a3 > 90: a3 = 180 - a3                 ; with rear speakers the rear half mirrors the front half
idx = (180 - a3) / 3                              ; 0..60
A = 0x1dd0d0 + idx*64 ; B = A + 32
pair = az >= 0 ? (A, B) : (B, A)                  ; 0x1d59d0..0x1d59e5
```
Each 32-byte kernel = 31 FIR taps (bytes 0..30) + byte 31 = interaural delay in
samples; `LoadHRTFFilter` packs taps of both kernels into 15 hardware dwords
(0xfe820400..0xfe820438, 0x1d8a48..0x1d8a77) and puts `+A[31]` (az >= 0) or `-B[31]`
(az < 0) into bits 25+ of the 16th (0x1d8a8b..0x1d8ab0). The per-mixbin volumes of the
3D pairs are identical for L and R (section 6.2), so **on the Xbox the left/right
placement comes entirely from this per-voice FIR pair**, not from the mixbin volumes.

Decoding the taps as 8-bit sign-magnitude (INFERRED: two's complement gives
alternating +-120 taps and negative DC, implausible) and taking the RMS of taps 0..30
gives the interaural level difference the hardware applies. Normalised to the az = 0
kernel (rms 188.8), for az >= 0 (mirror L/R for az < 0), `tools/ds3d_hrtf.py`:

| az | L (far) dB | R (near) dB | ITD samples | az | L dB | R dB | ITD |
|---|---|---|---|---|---|---|---|
| 0 | 0.00 | 0.00 | 0 | 93 | -1.65 | +1.56 | 31 |
| 3 | -0.54 | +1.06 | 2 | 96 | -1.70 | +1.68 | 30 |
| 6 | -0.75 | +1.25 | 3 | 99 | -1.68 | +1.60 | 28 |
| 9 | -0.90 | +1.30 | 4 | 102 | -1.70 | +1.65 | 27 |
| 12 | -1.12 | +1.22 | 4 | 105 | -1.72 | +1.90 | 28 |
| 15 | -1.32 | +1.31 | 5 | 108 | -1.87 | +2.18 | 27 |
| 18 | -1.47 | +1.24 | 6 | 111 | -2.00 | +1.87 | 25 |
| 21 | -1.42 | +0.96 | 9 | 114 | -2.10 | +1.82 | 24 |
| 24 | -1.54 | +0.84 | 10 | 117 | -2.13 | +1.77 | 23 |
| 27 | -1.57 | +0.64 | 11 | 120 | -2.28 | +1.68 | 22 |
| 30 | -1.64 | +0.57 | 12 | 123 | -2.42 | +1.53 | 21 |
| 33 | -1.82 | +0.60 | 12 | 126 | -2.56 | +1.55 | 20 |
| 36 | -1.89 | +0.92 | 13 | 129 | -2.57 | +1.55 | 19 |
| 39 | -2.00 | +1.10 | 14 | 132 | -2.54 | +1.51 | 18 |
| 42 | -2.05 | +1.17 | 17 | 135 | -2.49 | +1.45 | 17 |
| 45 | -2.15 | +1.32 | 18 | 138 | -2.43 | +1.38 | 16 |
| 48 | -2.18 | +1.40 | 19 | 141 | -2.37 | +1.32 | 15 |
| 51 | -2.29 | +1.54 | 20 | 144 | -2.42 | +1.24 | 14 |
| 54 | -2.23 | +1.54 | 21 | 147 | -2.45 | +1.10 | 13 |
| 57 | -2.08 | +1.33 | 21 | 150 | -2.32 | +0.92 | 13 |
| 60 | -2.04 | +1.61 | 22 | 153 | -2.28 | +0.55 | 12 |
| 63 | -1.97 | +1.72 | 23 | 156 | -2.22 | +0.26 | 11 |
| 66 | -1.89 | +1.65 | 24 | 159 | -2.09 | -0.03 | 8 |
| 69 | -1.82 | +1.70 | 25 | 162 | -2.05 | -0.34 | 7 |
| 72 | -1.80 | +2.00 | 26 | 165 | -2.02 | -0.61 | 6 |
| 75 | -1.72 | +2.32 | 28 | 168 | -1.83 | -0.86 | 6 |
| 78 | -1.62 | +2.18 | 29 | 171 | -1.75 | -1.08 | 3 |
| 81 | -1.63 | +2.02 | 29 | 174 | -1.71 | -1.19 | 2 |
| 84 | -1.66 | +1.86 | 31 | 177 | -1.56 | -1.37 | 2 |
| 87 | -1.60 | +1.71 | 30 | 180 | -1.44 | -1.44 | 0 |
| 90 | -1.57 | +1.58 | 30 | | | | |

The "light" HRTF is deliberately subtle: ~3.2 dB total ILD at 90 deg plus up to 31
samples (0.65 ms) of delay and a strong low-pass on the far ear (the far kernel's DC
sum is 2x the near one's while its energy is lower). A gain-only host cannot
reproduce the delay/spectral cues; see section 7 for the recommendation.

### 5.7 I3DL2 direct / room volumes and filters (`CI3DL2Source::CalculateI3DL2`, 0x1dad7d)

Computed only if both I3DL2 pointers are non-null and (`F & 0x14000000` or
`(I3DL2L.flags | I3DL2S.flags) & 0x7f0804`). With `Src = S+0x7c` block (2.2) and
`Lst` the I3DL2 listener (+0x0c flRoomRolloffFactor, +0x30 flHFReference):
```
x       = dist * Src.flRoomRolloffFactor * Lst.flRoomRolloffFactor
occl    = Src.Occlusion.lHFLevel   * Src.Occlusion.flLFRatio
obst    = Src.Obstruction.lHFLevel * Src.Obstruction.flLFRatio
direct  = FloatToLong(obst + occl) + Src.lDirect                                  -> VD+0x1c  (0x1dad92..0x1dadb5)
directHF= clamp(FloatToLong((1-Obstr.LF)*Obstr.HF + (1-Occl.LF)*Occl.HF) + Src.lDirectHF, -10000, 0)  (0x1dadb0..0x1daded)
room    = FloatToLong(occl + (x > 1 ? FloatToLong(2000*log10(x)) : 0)) + Src.lRoom    -> VD+0x20  (0x1daded..0x1dae41)
          (a positive term: it offsets the distance attenuation that bin 10 also receives via l3D;
           the x <= 1e-5 -> -10000 branch at 0x1dae12 is unreachable because it sits under x > 1)
roomHF  = clamp(FloatToLong((1-Occl.LF)*Occl.HF) + Src.lRoom, -10000, 0)              (0x1dae44..0x1dae66; note lRoom, not lRoomHF)
VD+0x30 = Get1PoleLoPass(0, directHF, Lst.flHFReference, 48000)   ; 16-bit coefficient    (0x1dae68..0x1dae91)
VD+0x34 = Get1PoleLoPass(0, roomHF,   Lst.flHFReference, 48000)                           (0x1dae82..0x1daea5)
```
`Get1PoleLoPass` (0x1dabe5) returns 0 when its first arg <= -10000 or when the level
is exactly 0 (0x1dabec..0x1dabf8) — so the default (level 0) coefficient is **0**;
for other levels it computes a one-pole cutoff from `cos(2*pi*fRef/48000)` and
`mBToLinear(2*level)` and returns 0xffff when the coefficient reaches 1.0, else
`coef * 65536` (0x1dac48..0x1dac68). Whether 0 means "bypass" in the hardware
register is not visible from here (UNVERIFIED); it is the value every voice gets by
default, so it cannot be "closed". With the default source block (all 0,
Occlusion.LF 0.25) and default listener (RoomRolloff 0) direct = room = 0 — the
I3DL2 path cannot mute anything unless the title sets it. The title does have `AudioEmitter/Listener/SoundSource::SetI3DL2Properties`
and XACT calls `IDirectSound_SetI3DL2Listener` (0x1fbcc8), so `lDirect`/occlusion
values can and will reach VD+0x1c at runtime (see 8).

---

## 6. From voice data to per-mixbin volumes (VERIFIED)

### 6.1 Set3DVoiceData / Use3DVoiceData / Apply3dSettings

`CDirectSoundVoice::Set3DVoiceData(pVoice, pVD)` (0x1d223f): if the voice has a
voice-data block and `pVD->flags & 0xff` is non-zero, copies field by field under the
flag bits (0x1d2265..0x1d234d), ORs the flags into the block, then
`CMcpxVoiceClient::Apply3dSettings` (0x1d94b3): `flags & 0x1f` -> `SetVolume`, `0x20` ->
`SetPitch`, `0x40` -> `LoadHRTFFilter`, `0x80` -> `SetFilter`, then clears the flags.
`Use3DVoiceData(pVoice, fUse)` (0x1d2370) just sets/clears settings+0x0b bit 0; when
clear, `ConvertVolumeValues` ignores the 3D block entirely (0x1d8240).

Public wrappers: `IDirectSoundBuffer_Set3DVoiceData(pIface, pVD)` -> object = pIface - 0x1c
(0x1d37b9); `CDirectSoundStream::Set3DVoiceData` uses the voice at stream+4 (0x1d34fa).

### 6.2 `CMcpxVoiceClient::ConvertVolumeValues` (0x1d8220) — the combiner

With `VD` the voice-data block, `clamp(v) = min(0, max(-10000, v))`, computed only
when `settings+0xb8 != 0 && settings+0x0b & 1`:
```
l3D    = clamp(VD.cone + VD.distance)                                     ; 0x1d8252..0x1d826e
vFront = clamp(VD.i3dl2Direct + VD.frontLRAdjust + VD.front + l3D)        ; 0x1d827a..0x1d8295  (bins 6,7)
vRear  = clamp(VD.i3dl2Direct + VD.rear + l3D)                            ; 0x1d82a2..0x1d82ba  (bins 8,9)
vCentre= clamp(VD.i3dl2Direct + VD.center + VD.front + l3D)               ; 0x1d82c7..0x1d82e2  (bin 2)
vRoom  = clamp(VD.i3dl2Room + l3D)                                        ; 0x1d82ec..0x1d8301  (bin 10)
```
Then for each of the voice's mix bins `b` (settings+0x28[i]) — 0x1d8356..0x1d83be:
```
V(b) = lMixBinVolume[b] + (lVolume - headroom)      ; settings+0x30[b] + settings+0x1c
     + ( b == 2      ? vCentre
       : b <= 5      ? l3D          ; 0,1,3,4,5: distance+cone only
       : b <= 7      ? vFront
       : b <= 9      ? vRear
       : b == 10     ? vRoom
       :               l3D )        ; 11+ (FX sends)
hw(b) = min(0xfff, (-V(b) * 64) / 100)      ; unsigned divide (0x1d83b3): a total > 0 mB wraps and MUTES the bin
```
(All inputs are <= 0 by contract — `DSBVOLUME_MAX` is 0, headroom >= 0, every 3D term is
clamped — so the wrap never happens on the Xbox; a host that allowed positive mix-bin
volumes would diverge here.)
Mix-bin numbering used by the code (Cxbx `XDSMIXBIN_*` for 0..5 agrees):
0 FL, 1 FR, 2 FC, 3 LFE, 4 BL, 5 BR, **6/7 = 3D front pair, 8/9 = 3D rear pair**
(grouping VERIFIED at 0x1d8377..0x1d838e), 10 = I3DL2 reverb send, 11..30 FX sends.
Which of 6/7 is left is not observable from the CPU side (both get the same volume);
by the 0/1, 4/5 convention and the by-side ordering of the default tables (6,8,7,9)
6 = 3D-FL, 8 = 3D-BL, 7 = 3D-FR, 9 = 3D-BR (INFERRED).

Default bins for a `DSBCAPS_CTRL3D` voice created without an explicit list:
`DirectSoundDefaultMixBins_3D` = 6,8,7,9,10 (0x1d3a9b). XACT creates its 3D voices
with `DirectSoundDefaulMixBins_5Channel3D_PlusLFE` (6,8,7,9,2,10,3; XACTENG 0x1fb099)
or `DirectSoundRequiredMixBins_5Channel3D` (6,8,7,9,2; 0x1fb0ae).

### 6.3 Where the flags58 bits come from

- bit 0 `hasRear`: `CDirectSoundVoice::Initialize` 0x1d3ca3..0x1d3cce — set when
  `(g_dwDirectSoundSpeakerConfig & 0xffff) == 2` (SURROUND) or `& 0x10000` (AC3), and
  the config is non-negative. Speaker config comes from `XGetAudioFlags` (0x1d4758).
- bit 1 `hasCenter`: `CDirectSoundVoiceSettings::SetMixBins` 0x1d3b09..0x1d3b48 — set
  when the voice is CTRL3D, has >= 5 bins and **bin index #4 is 2** (that is why the
  3D tables list the centre fifth). Cleared otherwise.
- bit 2 `muteAtMax`: `CDirectSoundVoice::Initialize` 0x1d3ce0..0x1d3cec — set when
  creation flags bit 0x20000 (`DSBCAPS_MUTE3DATMAXDISTANCE`) is set.
Each also sets source flag 0x04000000 so the dependent outputs are recomputed.

### 6.4 Pitch and filter

`ConvertPitchValue` (0x1d8474): `pitch = settings.lPitch (+0x18)`; if the voice has a
voice-data block **and `settings.flags(+8) & 0x01000000`** then `pitch += VD.doppler`
(0x1d8496..0x1d84a4); parent/output-buffer pitch chain added (0x1d84a7..0x1d84d2);
clamp [-32767, 8191]; register = pitch << 16. Voices with flags 0x182000
(MIXIN/FXIN/FXIN2) always get pitch 0. The 0x01000000 flag is never written inside
DSOUND, so it is a creation flag supplied by the caller (name not in the Cxbx header;
UNVERIFIED which public define it is) — **if the buffer lacks it, doppler is ignored
on the Xbox too**.

`SetFilter` (0x1d8d99): with a 3D block and use3D set, VD+0x30/+0x34 replace the
voice's two low-pass coefficients (0x1d8e4b..0x1d8e5a). 0xffff = pass-through.

---

## 7. Host-side collapse to stereo — recommendation

Cxbx today folds all mix bins into one host volume (max over speaker bins, LFE
excluded; `HybridDirectSoundBuffer_SetMixBinVolumes_8`, DirectSoundInline.hpp ~1366)
and never touches pan. For a 3D voice the faithful stereo image is:

```
per voice, all in mB unless stated; a(V) = V <= -6398 ? 0 : 10^(V/2000)
compute V(b) for every bin b of the voice exactly as 6.2 (including lVolume - headroom
and lMixBinVolume[b]).

az   = VD.flAzimuth ; gL, gR = table 5.6 (mirror for az < 0), converted to linear
L = a(V6)*gL + a(V8)*gL + 0.7071*a(V2) + a(V0) + a(V4)
R = a(V7)*gR + a(V9)*gR + 0.7071*a(V2) + a(V1) + a(V5)
(bins 3 (LFE) and 10 (reverb send) and 11+ are dropped: on the Xbox they never reach
 the front speakers directly)
hostVolume = 2000*log10(max(L,R))  clamped to [-10000, 0]
hostPan    = 2000*log10(R/L)       (DirectSound pan: >0 attenuates left)   clamped to +-10000
hostPitch  = lPitch + ((flags & 0x01000000) ? VD.doppler : 0), clamp [-32767, 8191], 4096/octave
```
Sanity checks that fall out of the Xbox numbers: a source dead ahead with a centre
bin gives L = R = 0.5 (front pair -6 dB) + 0.5 (centre -3 dB * 0.707) = 1.0; at
|az| >= 45 deg the centre is muted and the pair is at 0 dB, again 1.0; front/rear is
constant-power so folding both pairs keeps unity. `hasRear` should follow the emulated
speaker config exactly as 6.3 (both settings fold to the same power).

The pan from the light-HRTF table is only ~3 dB. That *is* the Xbox level cue; the
rest of its localisation is the 0.65 ms delay and the far-ear low-pass, which a
volume/pan host cannot express. Two options, in order of faithfulness:
1. Apply the 31-tap kernel pair + ITD per voice in a software mix (the kernels are in
   the XBE at 0x1dd0d0; `ds3d_hrtf.py` shows the decode). Exact, if the host mixer
   allows it.
2. Use the table above for pan (recommended default with the current host-buffer
   design). If the owner finds it too flat, a constant-power pan
   (`gL = cos((az+90)/180*pi/2)`-style) is a documented deviation, not the Xbox law.

---

## 8. Could the title's silence come from pitch / filter / I3DL2? (risk list)

- **Doppler pitch**: only applied with creation flag 0x01000000; range [-32767, 4096].
  `-32767` = 8 octaves down (0.4% speed) happens only at >= 342 m/s receding — not a
  realistic silence source, but an implementation that misreads the field as Hz or
  feeds a 0 "ratio" would be. `RatioToPitch(0)` is `log2(0)` = -inf -> `fistp` gives
  0x80000000; the code never reaches it (v is clamped first).
- **Filter**: a 1-pole low-pass coefficient (0 by default, 0xffff at full cutoff,
  see 5.7); every voice starts with 0, so it cannot be the "closed" value. Cannot
  produce silence. Safe to ignore on the host.
- **I3DL2 direct volume (VD+0x1c)**: added to *every* speaker bin of the voice.
  Default 0, but occlusion/obstruction (`lDirect`, `Occlusion.lHFLevel*flLFRatio`)
  are exactly how a title silences sounds behind walls. The title and XACT do set
  I3DL2 properties (`IDirectSound_SetI3DL2Listener` at XACTENG 0x1fbcc8, the title's
  `*::SetI3DL2Properties`). Honour VD+0x1c; if the host has no I3DL2 source data,
  keep it at 0 rather than -10000.
- **Distance**: with `DSBCAPS_MUTE3DATMAXDISTANCE` and a small `flMaxDistance` the
  voice is at -10000 beyond maxD. Without the flag it only clamps. Note the hardware
  floor: anything below -6398 mB total is silent on the Xbox as well.
- **Elevation is ignored** (4.2): do not "improve" it, the title was tuned against it.
- **Vtable / stubs**: under Cxbx the guest calculator cannot run (section 0). Either
  un-patch `DirectSoundUseLightHRTF`, `CDirectSound3DCalculator_Calculate3D` and
  `_GetVoiceData` and let the guest compute (pure CPU code; only
  `Ke(Save|Restore)FloatingPointState` is external) and implement only
  `Set3DVoiceData`/`Use3DVoiceData` on the host, or implement section 9 host-side.
  In both cases `Set3DVoiceData` must keep a per-voice copy of the 0x38 block and apply
  only the flagged fields.
- **XACT strips fields**: in `Update3DProperties` the first path masks `VD.flags &= 0xdc`
  (drops distance, cone, doppler bits) before `Set3DVoiceData`, the second path masks
  `&= 0x33` (drops front/rear, centre, HRTF, filter). So a given voice receives either
  the spatial set or the attenuation set through this API; the other half arrives by
  other calls (outside this document's scope — see the call-path audit).

---

## 9. Reference implementation (pseudocode; every constant sourced above)

```c
// ---- helpers (section 3)
long FloatToLong(float x)         { return (long)x; }                                   // 0x1d1beb
long AmplitudeToVolume(float a)   { return a<=0?-10000 : a>=1?0 : FloatToLong(2000*log10f(a)); } // 0x1d1bf4
long PowerToVolume(float p)       { return p<=0?-10000 : p>=1?0 : FloatToLong(1000*log10f(p)); } // 0x1d53ae
long MetersToVolume(float m)      { return m<0 ? 0 : FloatToLong(-2000*log10f(1+m)); } // 0x1d5377
long RatioToPitch(float r)        { return lrintf(4096*log2f(r)); }                     // 0x1d7868

// ---- Calculate3D(L, S)  (section 4)
F = L.flags | S.flags;
if (F & 0x400000) F = (S.mode==2) ? 0x400000 : (F | 0x07ff0000);
if (S && S.mode != 2) {
  if ((F & 4) || !(L.flags2 & 0x40)) { right = cross(L.top, L.front); if (right != L.right) {L.right = right; F |= 0x40;} L.flags2 |= 0x40; }
  if ((F & 0x410001) || (S.flags2 & 0x18000000) != 0x18000000) {
     n = (S.pos.x, 0, S.pos.z); if (S.mode != 1) { n.x -= L.pos.x; n.z -= L.pos.z; }
     d = sqrtf(n.x*n.x + n.z*n.z); if (d) { n.x/=d; n.z/=d; }
     if (n != S.normPos) { S.normPos = n; F |= 0x08000000; }  if (d != S.dist) { S.dist = d; F |= 0x10000000; }  S.flags2 |= 0x18000000; }
  if ((F & 0x18400044) || !(S.flags2 & 0x20000000)) {
     front = S.mode==1 ? (0,0,1) : L.front;  rightv = S.mode==1 ? (1,0,0) : L.right;
     f = dot(front, S.normPos); r = dot(rightv, S.normPos);
     if (S.dist == 0) az = 0; else if (fabs(f) > fabs(r)) az = 45*fabs(r)/fabs(f); else if (r != 0) az = 90 - 45*fabs(f)/fabs(r); else az = 0;
     if (f < 0) az = 180 - az;  if (r < 0) az = -az;   el = 0;
     if (az != S.az || el != S.el) { S.az = az; S.el = el; F |= 0x20000000; }  S.flags2 |= 0x20000000; }
  if ((F & 0x08080000) || !(S.flags2 & 0x40000000)) {
     m1 = |S.coneOrient - S.normPos|; m2 = |S.coneOrient + S.normPos|;
     angle = (m2 < m1) ? 180*m2/m1 : 360 - 180*m1/m2;
     if (angle != S.coneAngle) { S.coneAngle = angle; F |= 0x40000000; }  S.flags2 |= 0x40000000; }
  if ((F & 0x08420002) || !(S.flags2 & 0x80000000)) {
     v = S.mode==1 ? dot(S.vel, S.normPos) : dot(S.vel - L.vel, S.normPos);
     if (v != S.relVel) { S.relVel = v; F |= 0x80000000; }  S.flags2 |= 0x80000000; }
}
if (F & 0x7f) L.flags = F & 0x7f;  if (L2 & 0x7f) L.flags2 = ...;  if (F & 0xffff0000) S.flags = F & 0xffff0000;  (0x1d45d3..0x1d45fd)

// ---- GetVoiceData(L, S, I3L, I3S, VD)  (section 5)
F = L.flags | S.flags; if (F & 0x400000) F |= 0xffff0000;
if (F & 0x15200010) { v = S.mode==2 ? 0 : DistanceVolume(L.rolloff*S.rolloff, S.minD, S.maxD, S.curve, S.nPts, S.dist, (S.flags58>>2)&1);
                      if (v != VD.distance) { VD.distance = v; VD.flags |= 1; } }
if (F & 0x40140000) { v = S.mode==2 ? 0 : ConeVolume(S.inside, S.outside, S.outVol, S.coneAngle);   if (v != VD.cone) {...; VD.flags |= 2;} }
if (F & 0x34800008) { (fr,re) = S.mode==2 ? (0,0) : FrontRear(L.distF*S.distF*S.dist, S.az, S.flags58&1);  ... VD.flags |= 4; }
if (F & 0x24000000) { (adj,c) = (S.mode==2 || !(S.flags58&2)) ? (0,0) : Center(S.az);  ... VD.flags |= 8; }
if (F & 0x82800028) { p = S.mode==2 ? 0 : Doppler((L.distF*S.distF)*(L.dopF*S.dopF)*S.relVel);  ... VD.flags |= 0x20; }
if (F & 0x20000000) { (az,el) = S.mode==2 ? (0,0) : (S.az,S.el); if changed { VD.az=az; VD.el=el; VD.flags |= 0x40; } }
if (I3L && I3S && ((F & 0x14000000) || ((I3L.flags|I3S.flags) & 0x7f0804))) { ... I3DL2 (5.7) ... VD.flags |= 0x10 / 0x80; }

DistanceVolume(rolloff,minD,maxD,curve,n,dist,mute):
  if (dist <= minD) return 0;  if (mute && dist >= maxD) return -10000;  if (dist > maxD) dist = maxD;
  if (curve && n) { seg=(maxD-minD)/n; i=FloatToLong((dist-minD)/seg); if (i>=n) i=n-1; y0 = i?curve[i-1]:1; y1=curve[i];
                    return AmplitudeToVolume(y0 + ((dist-minD) - i*seg)/seg * (y1-y0)); }
  return MetersToVolume((dist/minD - 1) * rolloff);
ConeVolume(in,out,outVol,angle): if (angle <= in) return 0; if (in < out && angle < out) return FloatToLong((angle-in)*outVol/max(1,out-in)); return outVol;
FrontRear(x,az,hasRear): if (!hasRear) return (0,-10000); t = clamp(fabs(az)/90 - 0.5, 0, 1); if (x < 0.5) t = 0.5 + 0.5*x*(t-0.5); return (PowerToVolume(1-t), PowerToVolume(t));
Center(az): i = FloatToLong(fabs(az)); if (i >= 45) return (adj 0, centre -10000); return (AmplitudeToVolume(1 - B[i]), AmplitudeToVolume(A[i]));
Doppler(v): if (v == 0) return 0; if (v >= 342) return -32767; if (v <= -342) return 4096; return RatioToPitch(1 - v/342);

// ---- Set3DVoiceData / apply  (section 6)
copy flagged fields into the voice's block; l3D = clamp(cone+dist); per-bin V(b) as 6.2; stereo fold as 7.
```

---

## 10. Everything the emulator needs, in one place

- Structs: listener 2.1 (0x50 bytes, flags at +0/+4), source 2.2 (0xa4), voice data 2.3 (0x38).
- Distance: `20 log10(minD / (minD + rolloff*(dist-minD)))` mB; dist = XZ-plane distance;
  clamp at maxD, mute only with MUTE3DATMAXDISTANCE; optional piecewise-linear curve.
- Cone: 0 inside, `outVol` outside, linear-in-dB between, on the 0..360 tan-approximated angle.
- Azimuth: tangent-ratio approximation of atan2 in the XZ plane, (-180,180], + = right; elevation 0.
- Front/rear: constant power, `t = clamp(|az|/90 - 0.5, 0, 1)`, near-field blend under 0.5 m; front only when no rear speakers.
- Centre: tables A/B at 0x1dd018/0x1dcf60, muted beyond 45 deg; only if the voice's 5th bin is 2.
- Doppler: `round(4096*log2(1 - v/342))`, clamps -32767/+4096, gated by flag 0x01000000.
- Per-bin: `V(b) = mixbinVol[b] + volume - headroom + {centre | l3D | front | rear | room}` clamped per 6.2; hw floor -6398 mB.
- L/R: identical volumes on each 3D pair; pan = HRTF kernel energy (table 5.6) or the kernels themselves.

## 11. Open questions / UNVERIFIED

- XDK names of bins 6..10 (believed `DSMIXBIN_XTALK_LEFT/RIGHT/REAR_LEFT/REAR_RIGHT`,
  `DSMIXBIN_I3DL2`); L/R identity of 6 vs 7 and 8 vs 9 (INFERRED by convention; the
  CPU side cannot tell and does not need to).
- The HRTF tap encoding (sign-magnitude, INFERRED) and how the GP mixes bins 6..9 to
  the stereo DAC in stereo mode (possible crosstalk-cancellation stage: not visible
  from DSOUND code). The pan table is therefore "what the voice FIR does", not
  "what leaves the jack".
- Which public creation flag is 0x01000000 (doppler gate) and 0x200000/0x400000
  (companions of CTRL3D in `settings+8` tests at 0x1d3a92/0x1d48d5).
- The near-field front/rear discontinuity at exactly 0.5 m (5.3) looks like a bug in
  the library; kept as-is.
- Hardware meaning of a low-pass coefficient of 0 (5.7): the library's default for
  every voice, so "bypass" is the only sensible reading, but it is not provable from
  the CPU side. The host ignores the filter anyway.
