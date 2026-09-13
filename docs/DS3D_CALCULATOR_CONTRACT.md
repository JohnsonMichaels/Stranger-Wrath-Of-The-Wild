# Xbox DirectSound 3D calculator - API contract as used by the May-2004 beta XBE

Scope: the exact calling conventions, structures and data flow of `IDirectSound3DCalculator`
and the `Set3DVoiceData` / `Use3DVoiceData` family in **this** build, so Cxbx-Reloaded's stubs
can be replaced by a real implementation. Everything below is taken from the XBE's own code
and the PDB's type records; nothing is from memory of the XDK. Anything not backed by one of
those two is marked UNVERIFIED.

Sources (read-only):

- XBE `C:\Users\<you>\SWBeta\Game\default.xbe`, guest VA = PDB RVA + 0x10920.
- PDB `C:\Users\<you>\SWBeta\Game\Final\SteefFinal.pdb` (full type info: every struct
  named below is a real UDT in it, dumped with DbgHelp's `SymGetTypeInfo`).
- Tools written for this (all under `tools/`, all read-only):
  - `ds3d_pdb_types.ps1` - UDT layouts (`-Types`), function signatures + the compiler's
    recorded parameter LOCATIONS (`-Funcs`/`-FuncRva`), name listing (`-ListTypes`).
  - `ds3d_funcs.py` - disassemble whole functions by PDB name with call targets AND
    bracketed absolute data references named (`--data` dumps tables, `--emit5` writes the
    5-column TSV `xbe_xref.py`/`xbe_callees.py` need).

Reproduce any quoted listing with, e.g.:

```
python tools/ds3d_funcs.py C:/Users/<you>/SWBeta/Game/default.xbe C:/Users/<you>/AppData/Local/Temp/swsyms.tsv 10920 "DirectSound::CDirectSound3DCalculator::Calculate3D"
powershell -File tools/ds3d_pdb_types.ps1 -Pdb C:\Users\<you>\SWBeta\Game\Final\SteefFinal.pdb -Types "_DS3DCALCVOICEDATA" -Funcs "DirectSound::CDirectSound3DCalculator::GetVoiceData"
```

---

## 0. The short version

- The "calculator" has **no object**. `DirectSound::CDirectSound3DCalculator` is a class of
  size 1 whose two entry points are **static stdcall functions**: nothing is ever passed in
  a register, there is no `this`. Cxbx's existing stub arity (2 and 5 dwords, `WINAPI`) is
  correct; only the parameter *types* were unknown.
- `Calculate3D(pListener, pBuffer)` turns the app-owned listener/source parameters
  (stage 0) into derived geometry (stage 1: normalised direction, distance, azimuth,
  elevation, cone angle, relative velocity), tracked by change/valid bit masks.
- `GetVoiceData(pListener, pBuffer, pI3DL2Listener, pI3DL2Buffer, pVoiceData)` turns
  stage 1 into a 0x38-byte `_DS3DCALCVOICEDATA` of volumes (in mB), a Doppler pitch, an
  HRTF filter azimuth/elevation and two IIR words, setting a change bit per field that
  actually changed.
- The title never calls these itself; the statically linked **XACT** engine does
  (`XACT::CSoundSource::Update3DProperties`), then hands the result to DSOUND with
  `IDirectSoundBuffer_Set3DVoiceData` / `IDirectSoundStream_Set3DVoiceData`, which copy
  the flagged fields into the voice's `CDirectSoundVoiceSettings::m_p3dVoiceData` and
  apply them to the MCPX voice (volume per mixbin, pitch, HRTF FIR pair, IIR filter).
- DSOUND's own internal 3D path (`CMcpxVoiceClient::Commit3dSettings`) only runs for
  `DSBCAPS_CTRL3D` (0x10) voices; XACT creates its wave voices with flag 0x400000 and its
  app sound sources' mixin buffers with 0x2000|0x600000 (`CreateDSoundVoice`, 0x1fb059),
  never 0x10, so in this title **the explicit calculator + Set3DVoiceData is the only 3D
  path**.
- Left/right lateralisation on the Xbox comes from the per-voice HRTF **FIR filter** chosen
  by `flFIRFilterAzimuth`, not from any volume field; the volume fields are pair levels
  (front pair, rear pair, centre). A host implementation has to derive pan from the azimuth.

---

## 1. Entry points

All addresses: `rva` is the PDB RVA, `guest` = rva + 0x10920. "size" = distance to the next
PDB symbol (exact in the LTCG DSOUND/XACT sections).

| function | rva | guest | size | convention |
|---|---|---|---|---|
| `DirectSound::CDirectSound3DCalculator::Calculate3D` | 0x1c3ab1 | **0x1d43d1** | 0x240 | stdcall, 2 stack args, `ret 8`, no `this` |
| `IDirectSound3DCalculator_Calculate3D` | 0x1c3dd2 | 0x1d46f2 | 5 | `jmp` thunk to the above |
| `DirectSound::CDirectSound3DCalculator::GetVoiceData` | 0x1c22b2 | **0x1d2bd2** | 0x2e1 | stdcall, 5 stack args, `ret 0x14`, no `this` |
| `IDirectSound3DCalculator_GetVoiceData` | 0x1c2f16 | 0x1d3836 | 9 | `push ebp/mov ebp,esp/pop ebp/jmp` thunk to the above |
| `DirectSound::CDirectSound3DCalculator::CalculateI3DL2Reverb` | 0x1c2593 | 0x1d2eb3 | 0x37 | stdcall, 2 stack args, `ret 8`, no `this` |
| `DirectSound::CDirectSoundVoice::Set3DVoiceData` | 0x1c191f | 0x1d223f | 0x131 | stdcall, `(this, pVoiceData)` both on the stack, `ret 8` |
| `DirectSound::CDirectSoundVoice::Use3DVoiceData` | 0x1c1a50 | 0x1d2370 | 0x1d | stdcall, `(this, fUse)` on the stack, `ret 8` |
| `DirectSound::CDirectSoundBuffer::Set3DVoiceData` | 0x1c287f | **0x1d319f** | 0x4e | stdcall `(this, pVoiceData)`, `ret 8` |
| `DirectSound::CDirectSoundBuffer::Use3DVoiceData` | 0x1c28cd | **0x1d31ed** | 0x4c | stdcall `(this, fUse)`, `ret 8` |
| `IDirectSoundBuffer_Set3DVoiceData` | 0x1c2e8f | 0x1d37af | 0x1c | stdcall `(IDirectSoundBuffer*, pVoiceData)`, `ret 8` |
| `IDirectSoundBuffer_Use3DVoiceData` | 0x1c2eab | 0x1d37cb | 0x1c | stdcall `(IDirectSoundBuffer*, fUse)`, `ret 8` |
| `DirectSound::CDirectSoundStream::Set3DVoiceData` | 0x1c2ba9 | 0x1d34c9 | 0x52 | stdcall `(this, pVoiceData)`, `ret 8` |
| `DirectSound::CDirectSoundStream::Use3DVoiceData` | 0x1c2bfb | 0x1d351b | 0x50 | stdcall `(this, fUse)`, `ret 8` |
| `IDirectSoundStream_Set3DVoiceData` / `_Use3DVoiceData` | 0x1c2f0c / 0x1c2f11 | 0x1d382c / 0x1d3831 | 5 | `jmp` thunks to the stream methods |

Not present in this XBE (searched the full 43k-symbol PDB function list): `GetPanData`,
`GetMixBinVolumes`, `DirectSoundCreate3DCalculator`, any `IDirectSound3DCalculator`
vtable. The PDB's *type* record for the interface does declare four methods
(`Calculate3D, GetVoiceData, GetPanData, GetMixBinVolumes`) but only the first two were
compiled into this build, so Cxbx's TODO for the other two does not apply here. There is
no calculator instance anywhere: XACT calls the C functions directly (see 3.3).

### 1.1 `Calculate3D` - prologue and PDB record

```
001d43d1  55            push ebp
001d43d2  8d6c2490      lea ebp, [esp - 0x70]        ; frame sits BELOW esp (LTCG frame trick)
001d43d6  81ecd4000000  sub esp, 0xd4
...
001d43de  8d4d78        lea ecx, [ebp + 0x78]        ; CAutoFpState (KeSaveFloatingPointState) - see note
001d43e1  e883d7ffff    call 0x1d1b69                ; DirectSound::CFpState::Save
001d43e6  8b7578        mov esi, dword ptr [ebp + 0x78]   ; = [esp_entry+4]  -> pListener
001d43e9  8b5d7c        mov ebx, dword ptr [ebp + 0x7c]   ; = [esp_entry+8]  -> pBuffer
...
001d460e  c20800        ret 8
```

With `ebp = esp_after_push_ebp - 0x70`, `[ebp+0x78]` is `[esp_entry+4]` and `[ebp+0x7c]` is
`[esp_entry+8]`: two stack parameters, nothing read from any register before it is
overwritten. The PDB agrees:

```
FUNCTION DirectSound::CDirectSound3DCalculator::Calculate3D
  declared: NEAR_STD(stdcall)  returns void  class=DirectSound::CDirectSound3DCalculator  thisadjust=0
    arg0: _DS3DCALCLISTENER*      arg1: _DS3DCALCBUFFER*
    param pListener  _DS3DCALCLISTENER*  [ebp+78]
    param pBuffer    _DS3DCALCBUFFER*    [ebp+7c]
    local dwChangeMask ulong [ebp+6c]   local dwValidMask ulong [ebp+68]
```

(`CFpState::Save/Restore` take `this` in ecx but the object is empty - class size 1, only
static members `m_dwRefCount` / `m_fps` - so pointing ecx at the argument slot is harmless.)

Call sites (all three in the XBE, none in the title's `.text`):

```
XACT::CSoundSource::Update3DProperties   0x1fbede push eax(&m_3DBuffer) / push 0x2026e0(&m_3DListener) / call 0x1d46f2 (x2)
DirectSound::CMcpxVoiceClient::Commit3dSettings  0x1d96d6 push [settings+0xb4] / push CDirectSoundSettings+0x30 / call 0x1d43d1
DirectSound::CDirectSound::CommitDeferredSettings                                   call 0x1d43d1
```

### 1.2 `GetVoiceData`

```
001d2bd2  55        push ebp
001d2bd3  8bec      mov ebp, esp
...
001d2be3  8b5d08    mov ebx, dword ptr [ebp + 8]     ; pListener  (_DS3DCALCLISTENER*)
001d2be8  8b750c    mov esi, dword ptr [ebp + 0xc]   ; pBuffer    (_DS3DCALCBUFFER*)
001d2c04  8b7d18    mov edi, dword ptr [ebp + 0x18]  ; pVoiceData (_DS3DCALCVOICEDATA*, OUT)
001d2df1  8b4510    mov eax, dword ptr [ebp + 0x10]  ; pI3DL2Listener (may be NULL)
001d2dfe  8b4d14    mov ecx, dword ptr [ebp + 0x14]  ; pI3DL2Buffer   (may be NULL)
...
001d2eb0  c21400    ret 0x14
```

PDB: `NEAR_STD(stdcall) returns void`, params `p3DListener [vframe+8]`, `p3DBuffer [+c]`,
`pI3DL2Listener [+10]`, `pI3DL2Buffer [+14]`, `pVoiceData [+18]`. XACT's call site:

```
001fbeea  lea ebx, [esi + 0xe8]        ; &m_voiceData
001fbef0  push ebx
001fbef1  lea eax, [esi + 0xc0]        ; &m_I3DL2Buffer
001fbef7  push eax
001fbef8  push 0x202498                ; &XACT::CSoundSource::m_I3DL2Listener
001fbefd  lea eax, [esi + 0x44]        ; &m_3DBuffer
001fbf00  push eax
001fbf01  push edi                     ; 0x2026e0 = &XACT::CSoundSource::m_3DListener
001fbf02  call 0x1d3836                ; IDirectSound3DCalculator_GetVoiceData
```

### 1.3 `CalculateI3DL2Reverb(pI3DL2Listener, pReverb)`

stdcall, `(const _DS3DCALCI3DL2LISTENER*, _DSFX_I3DL2REVERB_PARAMS* /*0x220 bytes*/)`,
`ret 8`. Builds a `CI3DL2Listener{ m_pReverb = pReverb }` on the stack (calls
`CI3DL2Listener::Initialize` first if `g_fDirectSoundI3DL2Overdelay`), then
`CI3DL2Listener::CalculateI3DL2(pListener)`. Only caller: `CMcpxAPU::CommitI3DL2Listener`
(internal, reached from `IDirectSound_SetI3DL2Listener`, which Cxbx already patches). Not
needed for audibility.

### 1.4 `Set3DVoiceData` / `Use3DVoiceData` chain

`IDirectSoundBuffer_Set3DVoiceData(pBuffer, pVoiceData)` converts the interface pointer to
the object (`IDirectSoundBuffer` is the base at **+0x1c** of `CDirectSoundBuffer`) and
tail-calls the method, which is `CDirectSoundVoice::Set3DVoiceData` under the global lock:

```
001d37af  mov eax, [esp+4]     ; pBuffer
001d37b3  push [esp+8]         ; pVoiceData
001d37b9  add eax, -0x1c       ; CDirectSoundBuffer* = pBuffer - 0x1c (NULL stays NULL)
...       push ecx / call 0x1d319f / ret 8
```

`CDirectSoundStream::Set3DVoiceData` does `add eax, 4` instead (the `CDirectSoundVoice`
sub-object sits at +4 of `CDirectSoundStream`; `IDirectSoundStream` is at +0, so the
interface pointer *is* the object).

`CDirectSoundVoice::Set3DVoiceData(this, pVoiceData)` (0x1d223f) - `this` from
`[esp+4]`, `pVoiceData` from `[esp+8]`:

```
001d223f  mov ecx, [esp+4]                 ; this (CDirectSoundVoice*)
001d2243  mov eax, [ecx+0x10]              ; this->m_pSettings
001d2247  mov esi, [eax+0xb8]              ; settings->m_p3dVoiceData (_DS3DCALCVOICEDATA*, may be NULL)
001d224f  mov eax, [esp+0xc]               ; pVoiceData (after push esi)
001d2259  mov edx, [eax]  ; test dl,dl ; je -> no copy if low byte of dwChangeMask is 0
001d2263  mov [esi], edx                   ; dest.dwChangeMask = src.dwChangeMask (whole dword)
001d2265  test byte [eax],1  -> copy +4               (lDistanceVolume)
001d2279  test byte [eax],2  -> copy +8               (lConeVolume)
001d228d  test byte [eax],4  -> copy +0xc,+0x10       (lFrontVolume, lRearVolume)
001d22b0  test byte [eax],8  -> copy +0x14,+0x18      (lLeftRightVolume, lCenterVolume)
001d22d3  test byte [eax],0x10 -> copy +0x1c,+0x20    (lDirectVolume, lReverbVolume)
001d22f6  test byte [eax],0x20 -> copy +0x24          (lDopplerPitch)
001d230a  test byte [eax],0x40 -> copy +0x28,+0x2c    (flFIRFilterAzimuth, flFIRFilterElevation)
001d232d  test byte [eax],0x80 -> copy +0x30,+0x34    (dwIIRFilterDirect, dwIIRFilterReverb)
001d2350  mov ecx, [ecx+0xc]               ; this->m_pVoice (CMcpxVoiceClient*)
001d2353  call CMcpxVoiceClient::Apply3dSettings     ; thiscall, ecx = voice client
001d235a  ; else-branch (no 3D data on this voice, or mask low byte 0):
          test byte [eax],0x40 -> CMcpxVoiceClient::LoadHRTFFilter(this->m_pVoice, 0, pVoiceData)
001d236a  xor eax,eax ; ret 8               ; always DS_OK
```

`CDirectSoundVoice::Use3DVoiceData(this, fUse)` (0x1d2370): sets/clears bit 0 of byte
`settings+0xb`, i.e. `m_pSettings->m_dwFlags` bit **0x01000000**; returns DS_OK. That bit
is what `ConvertVolumeValues` / `ConvertPitchValue` / `SetFilter` test before using
`m_p3dVoiceData` (see 3.5).

A note on the PDB's register records: for these stdcall functions the PDB's parameter
locations matched the code exactly; for LTCG thiscall helpers (`Apply3dSettings`,
`XACT::CSoundSource::Update3DProperties`) the PDB says `this in edx` / `in edi` while the
code uses ecx / esi. Trust the disassembly for registers, the PDB for stack slots.

---

## 2. Structures

All from `ds3d_pdb_types.ps1 -Types`, cross-checked against the field accesses quoted in
section 3. Offsets are absolute within the outer struct.

### 2.1 `_DS3DCALCLISTENER` (0x50 bytes) - app-owned listener

```
+0x00  ulong   dwChangeMask     bits the app/engine sets when it changes a stage-0 field; low 7 bits
+0x04  ulong   dwValidMask      bits the calculator sets when a stage-1 field is current
+0x08  Stage0  (_DS3DCALCLISTENER_STAGE0, 0x3c)
   +0x08  D3DVECTOR vPosition
   +0x14  D3DVECTOR vVelocity
   +0x20  D3DVECTOR vOrientFront
   +0x2c  D3DVECTOR vOrientTop
   +0x38  float flDistanceFactor
   +0x3c  float flRolloffFactor
   +0x40  float flDopplerFactor
+0x44  Stage1  (_DS3DCALCLISTENER_STAGE1, 0xc)
   +0x44  D3DVECTOR vNormOrient    = vOrientTop x vOrientFront ("right" vector), written by Calculate3D
```

Stage 0 is exactly `_DS3DLISTENER` (0x40) minus `dwSize`, shifted to +8.

Listener change bits (from the XACT setters and `Calculate3D`'s tests):

| bit | field | evidence |
|---|---|---|
| 0x01 | vPosition | `IXACTEngine_SetListenerPosition`: `or dword [0x2026e0], 1` |
| 0x02 | vVelocity | `IXACTEngine_SetListenerVelocity`: `or ..., 2` |
| 0x04 | vOrientFront/Top | `IXACTEngine_SetListenerOrientation`: `or ..., 4`; `Calculate3D` `test al, 4` gates CalcNormOrient |
| 0x08 | flDistanceFactor | in the front/rear (0x34800008) and Doppler (0x82800028) gates, which are the consumers of the distance factor |
| 0x10 | flRolloffFactor | in the distance-volume gate 0x15200010 |
| 0x20 | flDopplerFactor | `IXACTEngine_SetListenerDopplerFactor`: `or ..., 0x20`; Doppler gate |
| 0x40 | vNormOrient (stage 1) | `Calculate3D` 0x1d4432 `or dword [ebp+0x6c], 0x40` after recomputing it; valid-mask bit 0x40 likewise |

`Calculate3D` writes back only the low 7 bits into the listener's masks and only the high 16
into the buffer's (0x1d45d3-0x1d45fd): listener bits live in 0..6, buffer bits in 16..31.

### 2.2 `_DS3DCALCBUFFER` (0x7c bytes) - app-owned source ("buffer")

```
+0x00  ulong dwChangeMask                bits 16..31
+0x04  ulong dwValidMask
+0x08  Stage0 (_DS3DCALCBUFFER_STAGE0, 0x54)
   +0x08  D3DVECTOR vPosition
   +0x14  D3DVECTOR vVelocity
   +0x20  ulong dwInsideConeAngle        degrees, full angle
   +0x24  ulong dwOutsideConeAngle
   +0x28  D3DVECTOR vConeOrientation
   +0x34  long  lConeOutsideVolume       mB
   +0x38  float flMinDistance
   +0x3c  float flMaxDistance
   +0x40  ulong dwMode                   0 normal, 1 head-relative, 2 = disabled (every output becomes 0)
   +0x44  float flDistanceFactor
   +0x48  float flRolloffFactor
   +0x4c  float flDopplerFactor
   +0x50  float* paflRolloffPoints       optional piecewise-linear amplitude curve (XACT: 10 floats, byte/255)
   +0x54  ulong dwRolloffPointCount
   +0x58  ulong dwFlags                  bit0 surround (compute rear), bit1 centre speaker present, bit2 mute beyond flMaxDistance
+0x5c  Stage1 (_DS3DCALCBUFFER_STAGE1, 0x20) - written by Calculate3D
   +0x5c  D3DVECTOR vNormPosition        unit vector listener->source (Light HRTF: XZ plane only, y forced to 0)
   +0x68  float flRelativeDistance       metres (Light: XZ-plane distance)
   +0x6c  float flRelativeVelocity       (vSrc - vListener) . vNormPosition   (>0 = receding)
   +0x70  float flConeTheta              2 x angle(vConeOrientation, direction source->listener), degrees
   +0x74  float flAzimuth                degrees, (-180,180], + = right of the listener
   +0x78  float flElevation              degrees (always 0 with the Light HRTF)
```

Stage 0 bytes +8..+0x4f are byte-for-byte `_DS3DBUFFER` (0x4c) minus `dwSize`;
`CDirectSoundVoiceSettings::Initialize` (0x1d489a) copies `DirectSoundDefault3DBuffer+4`
(18 dwords) straight into blob+8, and sets `dwChangeMask = 0x07ff0000`.

Buffer change bits:

| bit | field | evidence |
|---|---|---|
| 0x00010000 | vPosition | `XACT::CSoundSource::SetPosition`: `mov ebx,0x10000; or [esi+0x44],ebx`; `Get3DProperties` `or byte [eax+0x46],1` |
| 0x00020000 | vVelocity | `SetVelocity`, `Get3DProperties` `or edx,0x20000` |
| 0x00040000 | dwInside/OutsideConeAngle | `CSoundCue::Update3DProperties` `or byte [esi+0x46],4` after writing +0x64/+0x68 |
| 0x00080000 | vConeOrientation | `SetConeOrientation`: `mov ebx,0x80000; or [esi+0x44],ebx` |
| 0x00100000 | lConeOutsideVolume | `CSoundCue::Update3DProperties` `or byte [esi+0x46],0x10` after +0x78 |
| 0x00200000 | flMinDistance / flMaxDistance | `SetMaxDistance` and the min-distance store both `or byte [esi+0x46],0x20` |
| 0x00400000 | dwMode | `SetMode`: `mov edx,0x400000; or [esi+0x44],edx` |
| 0x00800000 | flDistanceFactor | `or byte [esi+0x46],0x80` after +0x88 |
| 0x01000000 | flRolloffFactor | `or byte [esi+0x47],1` after +0x8c; `SetRolloffCurve` also sets it (0x1fbc0c) |
| 0x02000000 | flDopplerFactor | `or byte [esi+0x47],2` after +0x90 |
| 0x04000000 | dwFlags (and rolloff points) | `CreateDSoundVoice` `or byte [esi+0x47],4` after `or [esi+0x9c],eax`; `CDirectSoundVoice::Initialize` ORs 0x4000000 after setting bits 1/4 of blob+0x58 |
| 0x08000000 | vNormPosition (stage 1) | `Calculate3D` 0x1d44b3 `or byte [ebp+0x6f],8` |
| 0x10000000 | flRelativeDistance | 0x1d44d1 `or byte [ebp+0x6f],0x10` |
| 0x20000000 | flAzimuth/flElevation | 0x1d453a `or [ebp+0x6c], edi(=0x20000000)` |
| 0x40000000 | flConeTheta | 0x1d457f `or ..., 0x40000000` |
| 0x80000000 | flRelativeVelocity | 0x1d45ca `or ..., 0x80000000` |

"Mode changed" (0x400000) with `dwMode != 2` expands to `0x07ff0000` (0x1d4455
`or word [ebp+0x6e], 0x7ff`): every stage-0 bit.

### 2.3 `_DS3DCALCI3DL2LISTENER` (0x34) and `_DS3DCALCI3DL2BUFFER` (0x28)

```
_DS3DCALCI3DL2LISTENER            _DS3DCALCI3DL2BUFFER
+0x00 ulong dwChangeMask (bits 0..11) +0x00 ulong dwChangeMask (bits 16..22, XACT sets 0x7f0000)
+0x04 long  lRoom                     +0x04 long  lDirect
+0x08 long  lRoomHF                   +0x08 long  lDirectHF
+0x0c float flRoomRolloffFactor       +0x0c long  lRoom
+0x10 float flDecayTime               +0x10 long  lRoomHF
+0x14 float flDecayHFRatio            +0x14 float flRoomRolloffFactor
+0x18 long  lReflections              +0x18 _DSI3DL2OBSTRUCTION { long lHFLevel; float flLFRatio }
+0x1c float flReflectionsDelay        +0x20 _DSI3DL2OCCLUSION   { long lHFLevel; float flLFRatio }
+0x20 long  lReverb
+0x24 float flReverbDelay
+0x28 float flDiffusion
+0x2c float flDensity
+0x30 float flHFReference
```

i.e. `dwChangeMask` + `_DSI3DL2LISTENER` (0x30) / `_DSI3DL2BUFFER` (0x24). GetVoiceData's
I3DL2 gate is `(L.dwChangeMask | B.dwChangeMask) & 0x7f0804`: listener bits 0x4
(flRoomRolloffFactor) and 0x800 (flHFReference) are the only listener fields it reads.

### 2.4 `_DS3DCALCVOICEDATA` (0x38) - the calculator's output / the voice's 3D settings

```
+0x00 ulong dwChangeMask          0x01 .. 0x80, one per row below (set only when the value changed)
+0x04 long  lDistanceVolume       mB  (0 = no attenuation, -10000 = DSBVOLUME_MIN)   bit 0x01
+0x08 long  lConeVolume           mB                                                   bit 0x02
+0x0c long  lFrontVolume          mB, level of the FRONT pair (mixbins 6/7)            bit 0x04
+0x10 long  lRearVolume           mB, level of the REAR pair  (mixbins 8/9)            bit 0x04
+0x14 long  lLeftRightVolume      mB, extra level on the front pair vs centre          bit 0x08
+0x18 long  lCenterVolume         mB, level of mixbin 2                                bit 0x08
+0x1c long  lDirectVolume         mB, I3DL2 direct path (added to every 3D bin)        bit 0x10
+0x20 long  lReverbVolume         mB, I3DL2 send (mixbin 10)                           bit 0x10
+0x24 long  lDopplerPitch         4096 * log2(ratio), same units as SetPitch           bit 0x20
+0x28 float flFIRFilterAzimuth    degrees                                              bit 0x40
+0x2c float flFIRFilterElevation  degrees                                              bit 0x40
+0x30 ulong dwIIRFilterDirect     MCPX 1-pole low-pass coefficient word                bit 0x80
+0x34 ulong dwIIRFilterReverb     MCPX 1-pole low-pass coefficient word                bit 0x80
```

The same layout is used in three places: XACT's `CSoundSource::m_voiceData` (+0xe8), the
0x38-byte block `CDirectSoundVoiceSettings::m_p3dVoiceData` points at (allocated zeroed by
`CDirectSoundVoiceSettings::Initialize` at 0x1d48e0 when `dwFlags & 0x400010`), and the
`VoiceDataTemp` local of `GetVoiceData`.

### 2.5 Blobs and the voice/settings objects DSOUND keeps

```
DS3DBUFFERBLOB   (0xa4): +0x00 _DS3DCALCBUFFER HRTF; +0x7c _DS3DCALCI3DL2BUFFER I3DL2
DS3DLISTENERBLOB (0x84): +0x00 _DS3DCALCLISTENER HRTF; +0x50 _DS3DCALCI3DL2LISTENER I3DL2
                         (one lives at CDirectSoundSettings+0x30, used only by the internal path)

DirectSound::CDirectSoundVoiceSettings (0xbc)
 +0x08 ulong m_dwFlags        DSBCAPS-style flags; bit 0x01000000 = "apply m_p3dVoiceData" (Use3DVoiceData)
 +0x18 long  m_lPitch         +0x1c long m_lVolume   +0x20 ulong m_dwHeadroom
 +0x24 ulong m_dwMixBinCount  +0x28 uint8 m_abMixBins[8]   +0x30 long m_alMixBinVolumes[32]
 +0xb0 CDirectSoundBuffer* m_pMixinBuffer
 +0xb4 DS3DBUFFERBLOB*     m_p3dParams      (only for DSBCAPS_CTRL3D 0x10 voices)
 +0xb8 _DS3DCALCVOICEDATA* m_p3dVoiceData   (for flags 0x10 or 0x400000)

DirectSound::CDirectSoundVoice (0x1c): +0x0c CMcpxVoiceClient* m_pVoice, +0x10 CDirectSoundVoiceSettings* m_pSettings
DirectSound::CDirectSoundBuffer (0x24): CDirectSoundVoice at +0, IDirectSoundBuffer vtable at +0x1c
DirectSound::CDirectSoundStream (0x28): IDirectSoundStream at +0, CDirectSoundVoice at +4
DirectSound::CMcpxVoiceClient  (0x80): +0x12 uint16 m_dwStatus (bit1 = active), +0x64 uint8 m_bVoiceCount, +0x70 m_pSettings
```

### 2.6 XACT's side (`XACT::CSoundSource`, 0x164)

```
+0x14  ulong m_dwFlags        bit 0x2 = 3D; bit 0x20000000 = created by IXACTEngine_CreateSoundSource (app sound source)
+0x1c  IDirectSoundBuffer* m_pBuffer   +0x20 IDirectSoundStream* m_pStream
+0x44  _DS3DCALCBUFFER      m_3DBuffer
+0xc0  _DS3DCALCI3DL2BUFFER m_I3DL2Buffer
+0xe8  _DS3DCALCVOICEDATA   m_voiceData
+0x120 float m_afl3DRolloffCurve[10]   (paflRolloffPoints points here; bytes * 1/255)
+0x150 CSoundSource* m_pSubmixDestination
static _DS3DCALCLISTENER      m_3DListener     @ guest 0x2026e0
static _DS3DCALCI3DL2LISTENER m_I3DL2Listener  @ guest 0x202498
static int m_fSurround @ 0x202480
```

Defaults: the ctor (0x1fadb2) writes `m_3DListener = { 0x7f, 0, pos 0, vel 0, front (0,0,1),
top (0,1,0), 1, 1, 1, normOrient 0 }`. `Reset3DProperties` (0x1fbfbd) writes
`m_3DBuffer = { 0xffff0000, 0, pos 0, vel 0, 360, 360, cone (0,0,1), 0 mB, min 1.0,
max 1e9, mode 0, 1, 1, 1, NULL, 0, dwFlags = (m_fSurround?1:0) | (bit1 if
m_dwFlags&0x20000000 && m_dwFlags&2) }` and `m_I3DL2Buffer = { 0x7f0000, 0,0,0,0, 0.0,
{0,0.0}, {0,0.25} }`.

### 2.7 Supporting tables

```
HRTFSOURCEVECTORTABLE (0x28) = DirectSound::CHRTFSource::m_vtable @ 0x1dea7c  (zero in the XBE image;
  filled at runtime by CHRTFSource::SetLightHRTF5Channel, 0x1d59eb, which AudioMgr::AppInit reaches
  through DirectSoundUseLightHRTF; m_nAlgorithm @ 0x1deaa4 = 4)
  [0] CalcNormPos          = CLightHRTFSource::CalcNormPos          0x1d572f
  [1] CalcPolarCoords      = CLightHRTFSource::CalcPolarCoords      0x1d5769
  [2] CalcConeAngle        = CFullHRTFSource::CalcConeAngle         0x1d5434
  [3] CalcRelativeVelocity = CFullHRTFSource::CalcRelativeVelocity  0x1d54ca
  [4] GetDistanceVolume    = CFullHRTFSource::GetDistanceVolume     0x1d5515
  [5] GetConeVolume        = CFullHRTFSource::GetConeVolume         0x1d5626
  [6] GetFrontRearVolume   = CLightHRTFSource::GetFrontRearVolume   0x1d5851
  [7] GetCenterVolume      = CLightHRTFSource::GetCenterVolume      0x1d5908
  [8] GetDopplerPitch      = CFullHRTFSource::GetDopplerPitch       0x1d56b6
  [9] GetFilterPair        = CLightHRTFSource::GetFilterPair        0x1d5971
FIRFILTER8 (0x20): uint8 Coeff[31], uint8 Delay.  HRTFFILTERPAIR: { FIRFILTER8* pLeftFilter, *pRightFilter }
Light FIR table @ 0x1dd0d0: 61 entries x 64 bytes (left @+0, right @+0x20), index = (180 - |az| snapped to 3 deg)/3
Mixbin sets used for 3D voices (DSMIXBINS {count, pairs}):
  DirectSoundRequiredMixBins_5Channel3D  @0x1de11c = {6,8,7,9,2}      DefaulMixBins_5Channel3D_PlusLFE @0x1de114 = {6,8,7,9,2,10,3}
  DirectSoundRequiredMixBins_3D          @0x1de12c = {6,8,7,9}        DefaultMixBins_3D               @0x1de124 = {6,8,7,9,10}
```

No other `SetFullHRTF*/SetLightHRTF4Channel` body exists in this XBE (the PDB type lists them,
the function table does not), so the algorithm is always Light-HRTF-5-channel here.

---

## 3. Data flow

### 3.1 `Calculate3D(pListener, pBuffer)` - stage 0 -> stage 1

With `A = L.dwChangeMask | B.dwChangeMask` (B may be NULL; then `A = L.dwChangeMask`) and
`V = L.dwValidMask | B.dwValidMask` (0x1d43ec-0x1d4403):

1. If `(A & 0x4) || !(V & 0x40)`: `vNormOrient = CalcNormOrient(&vOrientFront, &vOrientTop)`
   (0x1d441c). `CFullHRTFListener::CalcNormOrient` (0x1d53f7) computes
   `out = top x front` (out.x = top.y*front.z - top.z*front.y, ...). Stored into +0x44 only if
   different (`repe cmpsd`, 0x1d442e); then `A |= 0x40`; always `V |= 0x40`.
2. If `A & 0x400000` (mode changed): `dwMode != 2 ? A |= 0x07ff0000 : A &= 0x400000`.
3. If `pBuffer && dwMode != 2`:
   - if `(A & 0x410001) || (V & 0x18000000) != 0x18000000`:
     `vt[0] CalcNormPos(dwMode, &L.vPosition, &B.vPosition, &normPos, &dist)` (0x1d449c);
     store `vNormPosition` if changed (`A |= 0x08000000`), `flRelativeDistance` if changed
     (`A |= 0x10000000`); `V |= 0x18000000`.
   - if `(A & 0x18400044) || !(V & 0x20000000)`:
     `CalcPolarCoords(dwMode, &L.vOrientFront, &L.vOrientTop, &L.vNormOrient, &B.vNormPosition,
     B.flRelativeDistance, &az, &el)` (0x1d4514, through vt[1]); store az/el if either
     changed (`A |= 0x20000000`); `V |= 0x20000000`.
   - if `(A & 0x08080000) || !(V & 0x40000000)`:
     `vt[2] CalcConeAngle(&B.vConeOrientation, &B.vNormPosition, &theta)` (0x1d4567);
     `A |= 0x40000000` if changed; `V |= 0x40000000`.
   - if `(A & 0x08420002) || !(V & 0x80000000)`:
     `vt[3] CalcRelativeVelocity(dwMode, &L.vVelocity, &B.vVelocity, &B.vNormPosition, &v)`
     (0x1d45b2); `A |= 0x80000000` if changed; `V |= 0x80000000`.
4. Write back (0x1d45d3-0x1d45fd): `L.dwChangeMask = A & 0x7f` (only if non-zero),
   `L.dwValidMask = V & 0x7f` (if non-zero), `B.dwChangeMask = A & 0xffff0000` (if
   non-zero), `B.dwValidMask = V & 0xffff0000` (if non-zero).

Light-HRTF geometry actually executed (constants read from `.rdata`, values verified):

- `CalcNormPos` (0x1d572f): `out.x = B.x; out.z = B.z;` (**out.y is never written**); if
  `dwMode != 1`: `out.x -= L.x; out.z -= L.z`; `dist = |out.xz|` via
  `Math::NormalizeVector2` (which also sets out.y = 0 - so y ends up 0 after all), out
  normalised in the XZ plane. Height is ignored entirely.
- `CalcPolarCoords` (0x1d5769): with `front = (mode==1 ? (0,0,1) : vOrientFront)`,
  `right = (mode==1 ? (1,0,0) : vNormOrient)`: `a = front . normPos`, `b = right . normPos`,
  `*pel = 0`; if `dist == 0`: az = 0; else if `|a| > |b|`: `az = 45 * |b|/|a|` else
  `az = 90 - 45 * |a|/|b|` (piecewise-linear atan); then `if (a < 0) az = 180 - az;
  if (b < 0) az = -az`.
- `CalcConeAngle` (0x1d5434): `m = |cone - normPos|, p = |cone + normPos|`;
  `theta = (p > m) ? 4*(90 - 45*m/p) : 180 * p/m` = `2 * angle(vConeOrientation,
  direction source->listener)` in degrees (a full-cone angle, comparable with
  dwInsideConeAngle directly).
- `CalcRelativeVelocity` (0x1d54ca): `mode==1 ? B.vel . normPos : (B.vel - L.vel) . normPos`.

### 3.2 `GetVoiceData(pListener, pBuffer, pI3DL2Listener, pI3DL2Buffer, pVoiceData)`

`m = L.dwChangeMask | B.dwChangeMask; if (m & 0x400000) m |= 0xffff0000` (0x1d2beb-
0x1d2bfc). Each output below is computed only when its gate is set, forced to 0 when
`dwMode == 2`, then **compared with the existing field in `*pVoiceData`**; the field and its
change bit are written only if the value differs (e.g. 0x1d2c53-0x1d2c5b). The
change bits are ORed into `pVoiceData->dwChangeMask` (never cleared by the calculator).

| gate on `m` | vt slot called (arguments in C order) | writes |
|---|---|---|
| `0x15200010` | `GetDistanceVolume(L.roll*B.roll, flMin, flMax, paflRolloffPoints, dwRolloffPointCount, flRelativeDistance, (dwFlags>>2)&1, &out)` | lDistanceVolume, bit 0x01 |
| `0x40140000` | `GetConeVolume(dwInsideConeAngle, dwOutsideConeAngle, lConeOutsideVolume, flConeTheta, &out)` | lConeVolume, bit 0x02 |
| `0x34800008` | `GetFrontRearVolume(L.dist*B.dist, flRelativeDistance, flAzimuth, flElevation, dwFlags&1, &front, &rear)` | lFrontVolume, lRearVolume, bit 0x04 |
| `0x24000000` (byte +0xb & 0x24) and `dwFlags & 2` | `GetCenterVolume(flAzimuth, flElevation, &leftRight, &center)` (else both 0) | lLeftRightVolume, lCenterVolume, bit 0x08 |
| I3DL2 ptrs non-NULL and (`m & 0x14000000` or `(L2.mask|B2.mask) & 0x7f0804`) | `CI3DL2Source::CalculateI3DL2(flRelativeDistance, L2.flRoomRolloffFactor, L2.flHFReference, pI3DL2Buffer, &direct, &room, &iirDirect, &iirReverb)`; if `dwFlags & 2`: iirReverb = iirDirect | lDirectVolume, lReverbVolume, bit 0x10; dwIIRFilterDirect/Reverb, bit 0x80 |
| `0x82800028` | `GetDopplerPitch(L.dist*B.dist, L.dop*B.dop, flRelativeVelocity, &out)` | lDopplerPitch, bit 0x20 |
| `0x20000000` | copy `flAzimuth, flElevation` | flFIRFilterAzimuth/Elevation, bit 0x40 |

Formulas of the slot implementations (all volumes in mB, `FloatToLong` = `cvttss2si`):

- `Math::AmplitudeToVolume(a)`: `a <= 0 -> -10000; a >= 1 -> 0; else (long)(2000*log10(a))`.
  `PowerToVolume(p)`: same with 1000. `MetersToVolume(x)`: `x <= 0 -> 0; else
  (long)(-2000*log10(1+x))`. `RatioToPitch(r) = (long)(4096*log2(r))` (0x45800000).
- `GetDistanceVolume` (0x1d5515): `if (dist <= min) 0; else if (mute && dist > max) -10000;
  else { dist = min(dist,max); if (pts && n) { seg=(max-min)/n; i=clamp((int)((dist-min)/seg),0,n-1);
  a0 = i ? pts[i-1] : 1.0; a1 = pts[i]; f=(dist-min)/seg - i; AmplitudeToVolume(a0 + f*(a1-a0)) }
  else MetersToVolume((dist/min - 1) * rolloff) }` - i.e. inverse-distance, 20*log10(min/dist) for rolloff 1.
- `GetConeVolume` (0x1d5626): `theta <= inside -> 0; else if (inside < outside && theta >= outside)
  -> lConeOutsideVolume; else (long)((theta - inside) * lConeOutsideVolume / max(1, outside - inside))`.
- `GetFrontRearVolume` (Light, 0x1d5851): `if (!surround) { front = 0; rear = -10000 } else {
  d = distFactor*dist; t = clamp(|az|/90 - 0.5, 0, 1); if (d <= 0.5) t = 0.5 + (t - 0.5)*d;
  front = PowerToVolume(1 - t); rear = PowerToVolume(t) }`.
- `GetCenterVolume` (Light, 0x1d5908): `n = (int)|az|; if (n < 45) { center =
  AmplitudeToVolume(tabC[n]); leftRight = AmplitudeToVolume(1 - tabLR[n]) } else { leftRight = 0;
  center = -10000 }`, tabC @0x1dd018 = 0.7071..0 (46 floats), tabLR @0x1dcf60 = 0.5..0.
- `GetDopplerPitch` (0x1d56b6): `v = distF*dopF*relVel; v == 0 -> 0; v >= 342 -> -32767;
  v <= -342 -> 4096; else RatioToPitch(1 - v/342)`.
- `CalculateI3DL2` (0x1dad7d): `lDirect = B2.lDirect + (long)(obs.lHFLevel*obs.flLFRatio +
  occ.lHFLevel*occ.flLFRatio)`; `lRoom = B2.lRoom + (long)(x >= 1 ? 2000*log10(x) : x >= 1e-5 ?
  ... : -10000) + ...` with `x = dist * B2.flRoomRolloffFactor * L2.flRoomRolloffFactor`; the two IIR
  words come from `CI3DL2Source::Get1PoleLoPass(0, level, flHFReference, 48000)`. (Exact room
  arithmetic left as the listing; not needed for audibility.)

### 3.3 Who calls the calculator, and with what

Only `XACT::CSoundSource::Update3DProperties` (0x1fbebb; `this` in esi) - reached from
`XACT::CSoundSource::CommitDeferredSettings` (0x1fbce0, called after every 3D setter whose
`fDefer` argument is 0, and for every 3D source when the speaker config changes) and from
`SetOutputBuffer`. Two classes of source:

- **App sound sources** (`m_dwFlags & 0x20000000`, set only by `IXACTEngine_CreateSoundSource`
  0x1f7cfe; these are what the title positions with `IXACTSoundSource_SetPosition/SetVelocity/
  SetConeOrientation` and they own a DSBCAPS_MIXIN buffer, see `CreateDSoundVoice`):
  ```
  001fbed8  and dword [esi+0x44], 0x10000      ; keep only "position changed"
  ...       Calculate3D(&m_3DListener, &m_3DBuffer); GetVoiceData(..., &m_voiceData)
  001fbf07  and dword [ebx], 0xdc              ; drop bits 0x01,0x02,0x20 (distance, cone, doppler)
  001fbf2a  push ebx; push m_pBuffer; call IDirectSoundBuffer_Set3DVoiceData
  ```
  So the mixin voice receives only the *panning* bits (0x04 front/rear, 0x08 LR/centre,
  0x10 I3DL2, 0x40 FIR, 0x80 IIR).
- **Wave voices routed into such a source** (`m_pSubmixDestination` set and the destination has
  `m_dwFlags & 0x2`): `Get3DProperties` (0x1fc0dc) copies the destination's position,
  velocity, cone orientation and mode into their own `m_3DBuffer` (bits 0x10000|0x20000|
  0x80000|0x400000), their own distance/cone/rolloff parameters come from the sound bank
  (`CSoundCue::Update3DProperties`, 0x20066f), then:
  ```
  001fbf69  and byte [ebx], 0x33               ; keep 0x01 distance, 0x02 cone, 0x10 I3DL2, 0x20 doppler
  001fbf71  if (bit 0x10) m_voiceData.lReverbVolume = dest->m_voiceData.lReverbVolume
            IDirectSoundBuffer_Set3DVoiceData(m_pBuffer,...)  or  IDirectSoundStream_Set3DVoiceData(m_pStream,...)
  ```
- Afterwards (0x1fbfa3): `m_3DBuffer.dwChangeMask = 0; m_voiceData.dwChangeMask = 0`.
  `CommitDeferredSettings` clears `m_3DListener.dwChangeMask` and `m_I3DL2Listener.dwChangeMask`
  once all sources are updated (0x1fbea5). Nothing in XACT ever reads the volume fields back
  except `IXACTSoundSource_GetProperties` (title: `SoundSource::GetHighestPriorityCue`).

`SetOutputBuffer` (0x1fb200) calls `IDirectSoundBuffer_Use3DVoiceData(m_pBuffer, this_is_3D
|| dest_is_3D)` (or the stream variant) every time the routing changes, and assigns the
child voice's mixbins to `{10, 3}` (0x1fb2f4-0x1fb305) when it feeds a 3D destination.

How the two classes are created (`CreateDSoundVoice`, 0x1fb059; the DSBUFFERDESC is the
local at `[ebp-0x2c]`: `dwSize = 0x18` at 0x1fb07c, `lpMixBins` at `[ebp-0x1c]`):

- `m_dwFlags & 0x20000000` (app sound source): `dwFlags |= 0x2000` (DSBCAPS_MIXIN, 0x1fb090),
  `lpMixBins = DefaulMixBins_5Channel3D_PlusLFE`; if also `m_dwFlags & 2` (3D):
  `dwFlags |= 0x600000` (0x1fb09f), `lpMixBins = RequiredMixBins_5Channel3D = {6,8,7,9,2}`,
  `Stage0.dwFlags |= 2 | (m_fSurround ? 1 : 0)`, change bit 0x4000000; linked into
  `m_3DSubmixSources` (0x1fb13d).
- otherwise (wave voice): a PCM format is built (`XAudioCreatePcmFormat`, 48000 Hz, 16-bit)
  and `dwFlags |= 0x400000` on both the buffer and the stream descriptor (0x1fb0e1-0x1fb0e9);
  `m_dwFlags & 0x40000000` selects `IDirectSound_CreateSoundStream` (with stream flag
  0x20000000) over `IDirectSound_CreateSoundBuffer`; linked into `m_3DNormalSources`.

Neither class ever carries `DSBCAPS_CTRL3D` (0x10).

### 3.4 What DSOUND does with the voice data (`Apply3dSettings`, 0x1d94b3, thiscall)

```
if (mask & 0x1f) CMcpxVoiceClient::SetVolume()        ; -> ConvertVolumeValues, per-mixbin 12-bit attenuation
if (mask & 0x20) SetPitch()                           ; -> ConvertPitchValue
if (mask & 0x40) LoadHRTFFilter(0, 0)                 ; -> GetFilterPair(az, el, surround) -> FIR pair into the voice
if (mask & 0x80) SetFilter(0)                         ; IIR words
m_p3dVoiceData->dwChangeMask = 0
```

`ConvertVolumeValues` (0x1d8220), only when `m_p3dVoiceData && (settings.m_dwFlags & 0x01000000)`:

```
base   = clamp(lConeVolume + lDistanceVolume, -10000, 0)
front  = clamp(lDirectVolume + lLeftRightVolume + lFrontVolume + base, -10000, 0)
rear   = clamp(lDirectVolume + lRearVolume + base, -10000, 0)
center = clamp(lDirectVolume + lCenterVolume + lFrontVolume + base, -10000, 0)
reverb = clamp(lReverbVolume + base, -10000, 0)
for each of the voice's mixbins k (m_abMixBins[k]):
    att = -(m_alMixBinVolumes[bin] + m_lVolume)
    bin == 2       : att -= center
    bin in 0,1,3,4,5: att -= base
    bin in 6,7     : att -= front          (front L/R pair)
    bin in 8,9     : att -= rear           (rear L/R pair)
    bin == 10      : att -= reverb
    reg = min(att * 64 / 100, 0xfff)        (1/64 dB steps, 12 bits)
```

`ConvertPitchValue` (0x1d8474): `p = m_lPitch + (m_p3dVoiceData && flags&0x01000000 ?
lDopplerPitch : 0) + (mixin chain: destination's m_lPitch + its lDopplerPitch)`, clamped to
`[-32767, 8191]`, `<< 16` into the pitch register. So `lDopplerPitch` is in the units of
`IDirectSoundBuffer::SetPitch` (4096 per octave).

`LoadHRTFFilter` (0x1d8969): picks `HRTFFILTERPAIR` via `GetFilterPair(flFIRFilterAzimuth,
flFIRFilterElevation, speakerConfig is surround/AC3)`, Light version: index
`(180 - |az| snapped to 3 deg)/3` into the 61x64-byte table, left/right swapped when `az < 0`,
`Delay` byte negated when `az < 0`; if the voice has no 3D data or `dwMode == 2`, the flat
filter @0x1de27c is loaded for both ears. The table is a genuine per-azimuth pair of 31-tap
FIRs (L != R except at 0 and 180 deg), which is where left/right localisation happens on the
console.

### 3.5 When DSOUND's *internal* calculator runs

`CMcpxVoiceClient::Commit3dSettings` (0x1d96bb) runs `Calculate3D(CDirectSoundSettings+0x30,
settings->m_p3dParams)` and `GetVoiceData(+0x30, blob, +0x80, blob+0x7c, m_p3dVoiceData)`,
then `Apply3dSettings`; it is invoked from `ActivateVoice` only when
`settings.m_dwFlags & 0x10` (DSBCAPS_CTRL3D, 0x1d99bc) and from
`CDirectSoundVoice::CommitDeferredSettings`. `m_p3dParams` is only allocated for flag 0x10
(0x1d4862). XACT's voices use 0x400000 (wave voices) or 0x2000|0x600000 (mixin sources) -
values verified in `CreateDSoundVoice`, flag *names* UNVERIFIED - so for this title the
internal path never touches the app-supplied voice data. `Use3DVoiceData(FALSE)` disables all 3D processing on
a voice by clearing the 0x01000000 flag that `ConvertVolumeValues`/`ConvertPitchValue`/
`SetFilter` test.

---

## 4. Implementing it in Cxbx-Reloaded

Patch the two class methods (Cxbx already binds them: `CDirectSound3DCalculator_Calculate3D`
= 0x1d43d1, `CDirectSound3DCalculator_GetVoiceData` = 0x1d2bd2); the C thunks jump there,
so both the XACT calls and any internal call are covered. Signatures:

```cpp
void WINAPI CDirectSound3DCalculator_Calculate3D(X_DS3DCALCLISTENER* pListener, X_DS3DCALCBUFFER* pBuffer);      // ret 8
void WINAPI CDirectSound3DCalculator_GetVoiceData(X_DS3DCALCLISTENER* pListener, X_DS3DCALCBUFFER* pBuffer,
        X_DS3DCALCI3DL2LISTENER* pI3DL2Listener /*nullable*/, X_DS3DCALCI3DL2BUFFER* pI3DL2Buffer /*nullable*/,
        X_DS3DCALCVOICEDATA* pVoiceData);                                                                         // ret 0x14
HRESULT WINAPI IDirectSoundBuffer_Set3DVoiceData(X_CDirectSoundBuffer* pThis, X_DS3DCALCVOICEDATA* pVoiceData);   // ret 8
HRESULT WINAPI IDirectSoundBuffer_Use3DVoiceData(X_CDirectSoundBuffer* pThis, BOOL fUse);                          // ret 8
HRESULT WINAPI IDirectSoundStream_Set3DVoiceData(X_CDirectSoundStream* pThis, X_DS3DCALCVOICEDATA* pVoiceData);
HRESULT WINAPI IDirectSoundStream_Use3DVoiceData(X_CDirectSoundStream* pThis, BOOL fUse);
```

Struct definitions for `XbDSoundTypes.h` follow 2.1-2.4 verbatim (`X_DS3DCALCBUFFER` =
`{DWORD dwChangeMask, dwValidMask; X_DS3DBUFFER-without-dwSize; float* paflRolloffPoints;
DWORD dwRolloffPointCount, dwFlags; D3DVECTOR vNormPosition; float flRelativeDistance,
flRelativeVelocity, flConeTheta, flAzimuth, flElevation;}` = 0x7c bytes).

**Minimum that makes a positional voice audible at full volume, centred** (the fallback the
owner accepts):

- `Calculate3D`: may do nothing. If you want the masks coherent, set
  `pBuffer->dwValidMask |= 0xf8000000`, `pListener->dwValidMask |= 0x40`.
- `GetVoiceData`: write into `*pVoiceData`: `lDistanceVolume = lConeVolume = lFrontVolume =
  lLeftRightVolume = lCenterVolume = lDirectVolume = lReverbVolume = 0; lRearVolume =
  (pBuffer->dwFlags & 1) ? 0 : -10000; lDopplerPitch = 0; flFIRFilterAzimuth =
  flFIRFilterElevation = 0; dwIIRFilterDirect = dwIIRFilterReverb = 0;` and
  `dwChangeMask |= 0xff` (XACT masks it down itself; the Xbox code sets a bit only when a
  value changed, but over-reporting is harmless).
- `Set3DVoiceData` patches: keep a `X_DS3DCALCVOICEDATA` per hybrid buffer/stream, merge the
  fields whose bits are set exactly like 0x1d2265-0x1d234d, and (for the fallback) apply
  nothing further. With the merged copy all zero the voice plays at its XACT volume.

**Adding real attenuation** (fields that carry it, all in mB unless stated):

- distance + cone: `base = clamp(lDistanceVolume + lConeVolume, -10000, 0)` -> add to the
  host buffer volume (the fork's `HybridDirectSoundBuffer_SetVolume` comment already
  reserves a "3D volume" term: `real volume = mixbins volume + 3D volume + volume - headroom`).
  On the console this lands on the *wave* voices (the XACT children), never on the mixin.
- Doppler: `lDopplerPitch` -> add to the value the pitch patch converts (4096/octave).
- Pan: the console pans with the FIR pair selected by `flFIRFilterAzimuth` (+ = right);
  on the host use `SetPan` from the azimuth (constant power, `pan = sin(az)`), which is what
  the app-sound-source (mixin) voice would carry - see the caveat below.
- Front/rear: `lFrontVolume`/`lRearVolume` are pair levels for mixbins 6-7 / 8-9; on a
  stereo host `lFrontVolume` (plus `lLeftRightVolume`) is the only one that matters.
- Faithful `Calculate3D`/`GetVoiceData`: follow 3.1/3.2 literally (XZ-plane distance,
  piecewise-linear atan, 342 m/s Doppler, tables at 0x1dd018/0x1dcf60) or use exact maths -
  the differences are inaudible.

**Caveat that decides whether any of this is audible - not part of the calculator, verify
before blaming it.** On the console the 3D chain is *wave voice -> (mixbins {10,3}) ->
mixin buffer of the app sound source -> (mixbins {6,8,7,9,2})*. The fork's
`IDirectSoundBuffer_SetOutputBuffer` is `LOG_NOT_SUPPORTED` (DirectSoundBuffer.cpp:1351),
`GenerateMixBinDefault` maps custom bins straight through, and
`HybridDirectSoundBuffer_SetMixBinVolumes_8` (DirectSoundInline.hpp:1394-1404) derives the
host volume as the max over bins `< XDSMIXBIN_SPEAKERS_MAX (6)` excluding LFE, starting
from `DSBVOLUME_MIN`: a wave voice whose only bins are `{10, 3}` therefore gets
`Xb_volumeMixBin = -10000` the first time any `SetMixBinVolumes` reaches it, and the mixin
voice (bins `{6,8,7,9,2}`) contributes nothing on the host at all. Log `SetMixBins` /
`SetMixBinVolumes` / `SetOutputBuffer` on the silent voices first; if that is the mute, the
fix is in the mixbin mapping (treat 6/7 as front L/R, 8/9 as rear, 10 as a pass-through send
to the destination's host buffer), and the calculator work above is what gives them
position afterwards.

---

## 5. Open questions / UNVERIFIED

- Names of DSBUFFERDESC flags 0x400000 / 0x200000 (XACT's 3D voices) and 0x20000 (sets
  `dwFlags` bit 2 "mute at max distance" in `CDirectSoundVoice::Initialize`): values are
  from code, names are not in the PDB.
- Mixbin 10's XDK name (the fork's `GenerateMixBinDefault` also uses 10 for 3D; DSOUND's
  `ConvertVolumeValues` treats it as the reverb/send bin).
- `_DS3DCALCBUFFER_STAGE0.dwFlags` bit 1 is set by XACT only for app sound sources with the
  0x2 flag; whether the title ever creates such sources without surround (bit 0) is a runtime
  question (`m_fSurround` follows `g_dwDirectSoundSpeakerConfig`).
- The I3DL2 room arithmetic in `CI3DL2Source::CalculateI3DL2` was only summarised.
- `GetVoiceData` compares against the *previous* contents of `*pVoiceData`; the Cxbx stub
  currently leaves XACT's `m_voiceData` at its zeroed initial state, which by itself is
  "full volume, centred" - so the calculator stubs cannot be the sole cause of silence
  unless something on the Cxbx side mutes voices with no voice data (see the caveat in 4).

## 6. Evidence files

Scratch listings used for this document (regenerable with the two tools):
`ds3d_disasm.txt` (the 15 entry points), `ds3d_disasm2.txt` (HRTF/I3DL2/MCPX helpers, XACT
3D methods), `ds3d_disasm3.txt` (SetVolume/SetPitch/Math), `ds3d_mcpx.txt`,
`ds3d_xact.txt`, `ds3d_types.txt` (PDB layouts), `xrefs*.tsv` (callers) in the session
scratchpad `C:\Users\<you>\AppData\Local\Temp\claude\C--Users-<you>-New-folder\f670e163-c51f-42f1-a2bf-849855c8a75c\scratchpad\`.
