# Cxbx-Reloaded stub audit vs. Stranger's Wrath 2004 debug devkit build

Static, read-only prediction of the remaining emulator blockers, so we stop
discovering them one crash at a time.

Target: `C:\Users\<you>\SWBeta\Game\Debug\SteefDebug.xbe` (XDK 5788, debug libs
`D3D8D` / `XAPILIBD` / `LIBCMTD`), running under the fork at
`C:\Users\<you>\SWBeta\src\Cxbx-Reloaded` (HEAD `585c49a`).

Nothing here was built or executed. All conclusions come from the XBE, its PDB,
the symbol cache, and the fork's source.

---

## Method — how "CONFIRMED called" is established

Three independent sources were joined:

1. **PDB** — `tools\pdb_dump.ps1` over `Debug\SteefDebug.exe` yields 124,715
   symbols. The statically linked `D3D8D` library is fully covered, so every
   D3D entry point has a name and an extent. XBE VA = PDB RVA + `0x118E0`
   (verified: `D3DDevice_Swap` PDB `0x1264130` -> cache `0x1275a10`).
2. **XBE call graph** — new tool `tools\xbe_xref.py` scans every executable
   section for `E8`/`E9 rel32` and maps call site and target back to PDB
   function names.

   **This build routes every call through an MSVC incremental-link thunk
   table** (a packed run of 5-byte `E9` stubs in `.text`). A naive scan reports
   exactly one caller for every function — its own thunk — and makes the whole
   title look dead. The tool resolves thunks before counting; do not trust any
   earlier xref that did not.

   Because the target's own section is known, calls are split into
   `n_calls_from_title` (caller in `.text`) versus library-internal ones. Only
   the former proves the *title* calls it.
3. **Patch reality** — a function is only HLE-patched if it has an *active*
   `PATCH_ENTRY` in `src\core\hle\Patches.cpp`. Two traps here:
   `Direct3D9.cpp.unused-patches` is **not compiled** (grepping for `EMUPATCH`
   across the folder gives false positives), and 29 `PATCH_ENTRY` lines are
   commented out. Active D3D surface: **141 entries**.

Companion tools added (read-only, in `C:\Users\<you>\New folder\tools\`):
`xbe_xref.py`, `xbe_callees.py` (what does X call — reads a startup path
without a disassembler), `xbe_krnl_imports.py`, `xbe_krnl_xref.py`.

### The engine's call chain

`Renderer::` and `Device::` are `engine\renderer\renderer.cpp` /
`device.cpp` — the files whose asserts we keep hitting. The chain is:

```
Renderer::AppInit / Renderer::Swap        (engine)
  -> Device::Xxx                          (engine wrapper, device.cpp)
    -> IDirect3DDevice8_Xxx               (inline d3d8.h wrapper, COMDAT in .text)
      -> D3DDevice_Xxx                    (D3D8D library, section "D3D")
```

The middle layer matters: **the inline `d3d8.h` wrappers are emitted into the
title's own `.text`, and Cxbx's scanner finds them there as well as in the
library.** That is the root of blocker #4 — `D3DDevice_GetBackBuffer` exists
twice (`0x00ee3f10` in `.text`, `0x0128a320` in `D3D`), and patching both ends
of an inline forwarder produces infinite recursion. **Any other `Xxx` /`Xxx2`
pair where the non-suffixed form is an inline wrapper has the same hazard.**
From the xref, the pairs this title instantiates in `.text` are:
`GetBackBuffer`/`GetBackBuffer2`, `GetRenderTarget`/`GetRenderTarget2`,
`GetDepthStencilSurface`/`GetDepthStencilSurface2`. The first is the one being
fixed; **check the other two before they bite.**

### Startup path (from `xbe_callees.py`)

```
Renderer::AppInit
  +0x00bb Direct3DCreate8
  +0x00cf Direct3D_SetPushBufferSize        <-- not known to Cxbx at all
  +0x0160 Direct3D_CreateDevice
  +0x01cd Renderer::GetPushBufferStartAddress
  +0x01d7 Renderer::GetPushBufferEndAddress
  +0x020b .. +0x02b5  FOUR Assert::Message  <-- asserts on the push buffer range
  +0x02c7 Device::AppInit
  +0x02d3 RenderContext::Create
  +0x0311 Renderer::Reset_Internal
  +0x09ca Device::SetWaitCallback           <-- not known to Cxbx at all
```

Per frame:

```
GameLoop::EndRender -> Renderer::Swap
  +0x0699 Device::Swap
  +0x06d6 Renderer::SynchronizeCPUWithGPU  = InsertFence  then  BlockOnFence
  +0x06db Renderer::GetCurrentPushBufferAddress
  +0x0740, +0x0778  TWO Assert::Message    <-- asserts on the push buffer pointer
```

`Renderer::SynchronizeCPUWithGPU` being exactly `InsertFence` + `BlockOnFence`
is blocker #3, confirming the method reproduces known bugs.

The three push-buffer accessors are 23–24 byte leaf functions that read
`[[0x015BB414] + 0x24 / +0x28 / +0x00]`. `0x015BB414` is the engine global
`s_pD3DDevice`. So **the engine reads the raw GPU FIFO pointers straight out of
the Xbox D3D device structure and asserts on them, at startup and every
frame.** Cxbx must keep those three fields plausible (start < current < end),
not merely keep the fence fields in sync.

---

## TIER 0 — confirmed called, hang class, on the startup or per-frame path

### 1. `D3DDevice_KickPushBuffer` — no patch, tail-jumps into the real NV2A kick

- **Evidence**: 6 calls from the title. `Device::KickPushBuffer` is called by
  `LoadingAnimation::ShowLoadingScreen`, `Device::ClearResourceFromDevice`,
  `ClearPalettePointers`, `ContiguousPoolInstance::Condense`.
- **Status**: in the symbol cache (`0x1276b80`), **no active `PATCH_ENTRY`**.
  The only implementation is dead code in `.unused-patches` carrying
  `// TODO -oDxbx : Locate the current PushBuffer address...`.
- 22 bytes, disassembled:
  ```
  mov eax,[0x015775F0]      ; bump a counter
  mov ecx,[0x012EDE28]      ; D3D_g_pDevice  -> thiscall
  inc eax
  mov [0x015775F0],eax
  jmp D3D::CDevice::KickOff ; tail call
  ```
  `D3D::CDevice::KickOff` writes the NV2A PUT register and polls
  `D3DDevice_IsBusy` and `D3D::DXGRIP`.
- **What breaks**: `Device::ClearResourceFromDevice` is
  `Flush -> InsertFence -> KickPushBuffer`, then the engine spins waiting for
  the resource to go un-busy. This is precisely the
  `device.cpp (2598) : pResource->IsBusy() == FALSE` assert. The fork's own
  source names this title in the comment at `Direct3D9.cpp:9652-9662`
  ("Timed out while attempting to clear resource! (VERY BAD!)").
- **Severity**: hang / assert storm on every texture and vertex buffer
  destruction, and during loading screens.
- **Fix**: add `PATCH_ENTRY("D3DDevice_KickPushBuffer", ...)` implemented as
  "advance the title's GPU fence to the current fence, then return" — the same
  `CxbxrImpl_CatchUpXboxFence()` the `InsertFence`/`BlockOnFence` fix already
  uses. It must not reach `CDevice::KickOff`.

### 2. `D3DDevice_BlockUntilIdle` -> `D3D::KickOffAndWaitForIdle` — unknown to Cxbx

- **Evidence**: 4 calls from the title (`LoadingAnimation::Stop` x2,
  `ContiguousPoolInstance::Condense`, plus debug paths), 21 total.
- **Status**: the name appears **nowhere in the entire Cxbx source tree** and
  is **not in the symbol cache**. It is a debug-XDK export. Unpatched, so the
  real library code runs:
  ```
  D3DDevice_BlockUntilIdle -> D3D::KickOffAndWaitForIdle
                              -> D3D::BlockOnTime   (patched: no-op)
                              -> D3D::DXGRIP        (debug deadlock reporter)
  ```
- `D3D_KickOffAndWaitForIdle` is in the symbol cache (`0x12945d0`) but is also
  **unpatched** — its only implementation is dead code reading
  `// TODO: Actually do something here?`.
- **What breaks**: the loop's exit condition reads GPU state (`D3D__GpuReg`).
  `D3D_BlockOnTime` is patched to a pure `LOG_UNIMPLEMENTED()` no-op, so the
  "wait" degenerates into an unthrottled busy spin that never terminates.
  `D3D__DeadlockTimeOutVal` exists in this build, so the debug library has its
  own deadlock watchdog that will fire through `DXGRIP`.
- **Severity**: hang, first hit when a loading screen ends.
- **Fix**: patch `D3D_KickOffAndWaitForIdle` (already a known symbol) to
  catch the fence up and return. Add an OOVPA/alias for
  `D3DDevice_BlockUntilIdle` too, or rely on it tail-calling the patched
  `KickOffAndWaitForIdle`. **Patching `KickOffAndWaitForIdle` alone fixes both**
  and also covers `D3D::BlockOnResource`, which calls it.

### 3. `D3DDevice_MakeSpace` -> `D3D_MakeRequestedSpace` — no patch, FIFO wait

- **Evidence**: 1 title call (`D3DDevice_BeginState`), 132 library-internal.
- **Status**: `MakeSpace` is in the symbol cache; its Cxbx implementation is
  dead code inside a `#if 0 // patch disabled`. `D3D_MakeRequestedSpace` is
  also cached and also unpatched (`// NOTE: This function is ignored, as we
  currently don't emulate the push buffer`).
- 15 bytes, disassembled:
  ```
  mov eax,[0x012F4810]      ; push buffer size
  push eax
  shr eax,1
  push eax
  call D3D_MakeRequestedSpace
  ```
- **What breaks**: `MakeRequestedSpace` waits for FIFO room by comparing GET
  against PUT. Under HLE, PUT advances and GET never does, so once the ring
  appears full this never returns.
- **Severity**: hang. Lower immediate probability than #1/#2 because most
  callers are library functions that are themselves patched — but **every
  unpatched entry point above reaches it**, so it is the common failure mode
  behind them.
- **Fix**: patch `D3D_MakeRequestedSpace` to return immediately. Cheap
  insurance regardless of the others.

### 4. `D3DDevice_BeginPush` — patch name mismatch, so it is silently unpatched

- **Evidence**: symbol cache has plain `D3DDevice_BeginPush = 0x1294940`.
  `Patches.cpp` only registers `D3DDevice_BeginPush_4` and
  `D3DDevice_BeginPush_8`. `EmuInstallPatch` does a `g_PatchTable.find(name)`
  and **returns silently on a miss** — no warning.
- Meanwhile `D3DDevice_EndPush` **is** patched.
- **What breaks**: an unpatched `BeginPush` hands the title a pointer into the
  real Xbox FIFO. The patched `EndPush` then finds
  `g_pXbox_BeginPush_Buffer == nullptr`, logs
  `"D3DDevice_EndPush called without preceding D3DDevice_BeginPush?!"`, and
  drops the batch. Everything pushed that way is never drawn.
- **Severity**: mismatched pair — either a lost batch or a bad pointer walk.
  1 title call (`Device::BeginPush`), so low frequency but easy to fix.
- **Fix**: add `PATCH_ENTRY("D3DDevice_BeginPush", ... BeginPush_4 ...)`, or
  confirm the 5788 signature and alias it. **Also worth adding a one-line
  `EmuLog` in `EmuInstallPatch`'s not-found branch** — that silent `return` is
  why this class of bug is invisible.

### 5. `D3DDevice_SetWaitCallback` — unknown to Cxbx, last call in `Renderer::AppInit`

- **Evidence**: 1 title call, from `Renderer::AppInit +0x09ca`.
- **Status**: appears **nowhere in the Cxbx tree**, not in the symbol cache.
  Debug-XDK-only export.
- **What breaks**: unpatched, the library just stores a function pointer, so
  this will not crash. But the engine installs the callback expecting to be
  called back whenever D3D blocks; under HLE nothing ever blocks, so the
  callback never fires. If the engine uses it to service a watchdog, pump IO,
  or keep the loading animation alive, that work silently stops.
- **Severity**: low crash risk, medium behavioural risk. Listed at Tier 0 only
  because it is in the startup path and costs one line to log.
- **Fix**: no patch needed initially — add logging to confirm what the title
  registers, then decide.

---

## TIER 1 — confirmed called, wrong rendering or assert class

| # | Function | Title calls | Status | Consequence |
|---|---|---|---|---|
| 6 | `D3DDevice_SetDepthClipPlanes` | 3 (`IDirect3DDevice8_SetDepthClipPlanes` x2, `LoadingAnimation::InitLoadingAnimation`) | **Patched but hollow** — `Direct3D9.cpp:9558-9615`, every `switch` case empty, five bare `// TODO`, `Near`/`Far` read and discarded, returns `D3D_OK` | Depth range never applied. Directly implicates the known assert `rendercontext.cpp (838) : m_viewportDepthRange.x < m_viewportDepthRange.y`. **Highest-value Tier 1 item.** |
| 7 | `D3DDevice_SetScissors` | 1 direct, plus `D3DDevice_SetViewport` calls it internally | No patch; Cxbx *also* force-sets `SetScissorRect(&viewportRect)` on every draw (`Direct3D9.cpp:8285`, `:8312`) | Title's scissor rect is ignored and overwritten each draw. Wrong clipping for HUD/overlay/split rendering. |
| 8 | `D3DDevice_GetVisibilityTestResult` | 1 (`Visibility::Update`, `Visibility::FlushLimbo`) | Patched but `*pResult = 640*480 // TODO : Use actual backbuffer dimensions` and `*pTimeStamp = sizeof(DWORD)` (literally `4`) | `pTimeStamp` is garbage. If the engine orders or expires queries by timestamp, occlusion goes wrong or the query queue never drains. Also contains a blocking `while (S_FALSE == GetData())` spin. |
| 9 | `D3DDevice_GetBackBuffer2` | 6 | Patched, but the CPU readback is entirely inside `#if 0` (`Direct3D9.cpp:4089-4120`, "no known games depend on backbuffer readback") | This title **does** read the backbuffer: `ScreenShot::SaveBackBuffer`, `DoCubeFaceScreenShot`, `BackBufferPlayer::DisplayFrame`, `SimpleBinkPlayer::PlayMovie`, `LoadingAnimation::Start`. Bink playback and the loading animation write *through* the returned surface. Stale/blank video and loading screens. Two `CxbxrAbort` paths at `:4043` and `:4082`. |
| 10 | `D3DVertexBuffer_Lock2` | 1 (`D3DVertexBuffer::Lock`) | No patch, **and no internal primitive covers it** (unlike textures, which are covered by `Lock2DSurface`/`Lock3DSurface`) | Vertex buffer writes detected only by per-draw resource hashing. Stale geometry when hashing misses. |
| 11 | `D3DPalette_Lock2` | 1 (`D3DPalette::Lock`) | Same gap | A rewritten palette does not invalidate P8 textures. Confirmed relevant: `ClearPalettePointers` and `Device::SetPalette` (11 engine sites) are live. |
| 12 | `D3DDevice_SetGammaRamp` | 1 | Patched, but the only real call is inside `#if 0 // TODO : Why is this disabled?` (`Direct3D9.cpp:3943`) | Gamma fully discarded. `GetGammaRamp` is worse — it casts host 16-bit ramp entries with `(BYTE)`, keeping the **low** byte instead of `>> 8`, so reads return garbage. A title that does get/modify/set will corrupt its own ramp. |
| 13 | `D3DDevice_SetFlickerFilter`, `D3DDevice_SetSoftDisplayFilter` | 2 each, from `Renderer::Reset_Internal` (**startup**) and `DebugKeys::Tick` | Patches **deliberately commented out** (`Patches.cpp:146,147,167`) | Unpatched Xbox code calls `AvSendTVEncoderOption(AV_OPTION_FLICKER_FILTER)`, whose kernel handler is a stub (`EmuKrnlAv.cpp:215`). Survivable, but it is on the startup path — verify it returns rather than looping. |

---

## TIER 2 — confirmed called, debug-XDK-only, entirely unknown to Cxbx

These 23 functions are called by the title but are **not in the symbol cache and
not in the Cxbx source at all**, because they do not exist in retail D3D8.
Retail-focused Cxbx has never needed them. All run unpatched.

**Parameter validation** (these read Xbox device/state that Cxbx now manages):
`D3DDevice_SetRenderState_ParameterCheck` (3 calls),
`D3DDevice_SetTextureState_ParameterCheck` (2),
`D3DDevice_BeginStateParameterCheck` (1), `D3DDevice_EndStateParameterCheck` (1).
They validate arguments and report via the debug channel. Because
`DbgBreakPoint` is a Cxbx no-op stub (`EmuKrnlDbg.cpp:52`), a failed check
**logs and continues** rather than stopping — so the eventual crash is
downstream of the real cause. Worth watching the log for these firing.

**Performance counters**: `D3DPERF_GetStatistics` (3 calls), `D3DPERF_Reset`,
`D3DPERF_Dump`, `D3DPERF_DumpFrameRateInfo`, `D3DPERF_DumpPerfEvents`,
`D3DPERF_DumpPerfProfCounts`, `D3DPERF_GetPushBufferInfo`,
`D3DPERF_StartPerfProfile`, `D3DPERF_StopPerfProfile` (1 each). These read NV2A
performance registers. `D3DPERF_DumpFrameRateInfo` also calls the kernel's
`MmQueryStatistics`. `D3DPERF_PerfEventStart`/`End` are invoked from inside
`D3DDevice_BlockUntilIdle` and `CDevice::KickOff`, so they are on the Tier 0
paths.

**Others**: `D3DDevice_SetDebugMarker` (5 calls), `D3DDevice_CreateSurface2`
(1 title call, via `IDirect3DDevice8_CreateImageSurface`),
`D3DDevice_GetTileCompressionTags` and `D3DDevice_GetTile` (1 each, but only
from `Renderer::DumpTileInfo`, a debug command — low),
`D3DDevice_SetOverscanColor`, `D3DDevice_GetPalette2`,
`D3DTexture_GetLevelDesc` / `D3DCubeTexture_GetLevelDesc` /
`D3DVolumeTexture_GetLevelDesc` (these read Xbox resource headers directly,
which Cxbx does populate, so probably benign).

**Recommendation**: rather than patching these individually, add a
**catch-all log** for symbol-cache entries with no patch table match (see item
#4). For a debug-XDK title this class is large and currently invisible.

---

## Kernel side

Kernel imports were extracted from the XBE thunk table at `0x01633f94`
(163 ordinals) and cross-referenced to call sites via `FF 15`/`FF 25` indirect
calls (`tools\xbe_krnl_xref.py`).

**Good news first** — everything the GPU-wait path needs is implemented:
`KeSetTimerEx`, `KeWaitForSingleObject`, `KeDelayExecutionThread` (with a
fork-specific `SleepPrecise` fix for vblank timing), `KeQueryPerformanceCounter`,
`KeTickCount`/`KeInterruptTime`/`KeSystemTime` (advanced by `KiClockIsr` from
`common\Timer.cpp:106`), `KeInitializeDpc`, `KeInsertQueueDpc`, the DPC dispatch
loop, NV2A vblank interrupt (`devices\video\nv2a.cpp:1111`), and all of
`MmAllocateContiguousMemory*` / `MmPersistContiguousMemory` /
`MmSetAddressProtect`. The kernel thunk table has no null entries.

**Confirmed-called kernel stubs, ranked:**

| Ordinal | Export | Status | Called from | Assessment |
|---|---|---|---|---|
| 139 / 142 | `KeRestoreFloatingPointState` / `KeSaveFloatingPointState` | **Pure stubs**, `LOG_UNIMPLEMENTED()` | `DirectSound::CFpState::Save`/`Restore`, `XACT::CEngine::DispatchEvent` x3, `XACT::CEngine::EndTimer`, `CalculateRecurringVolume` — 7 calls each | DirectSound saves the x87 control word, changes precision/rounding, and relies on Restore to put it back. With both no-ops the control word stays changed on whichever thread ran the audio callback. **Speculative but testable link to `viewer.cpp (61) : m_fovRad > 0.f && m_fovRad < PI`** — a float that goes wrong rather than a pointer that goes null. Cheap to test: log the x87 control word around those calls. |
| 2 | `AvSendTVEncoderOption` | **Partial** — `AV_QUERY_ENCODER_TYPE` and `AV_QUERY_MODE_TABLE_VERSION` are stubbed **and never write `*Result`** (`EmuKrnlAv.cpp:231,234`) | `D3D::CMiniport::GetDisplayCapabilities`, `D3DDevice_SetFlickerFilter`, `D3DDevice_SetSoftDisplayFilter`, `CDevice::FreeFrameBuffers` | Caller reads an **uninitialised** `Result`. `GetDisplayCapabilities` feeding garbage into mode selection is a plausible source of a bad viewport/fov. **Fix is two lines** — write a sane value in both cases. Best effort-to-risk ratio on the kernel side. |
| 109 | `KeInitializeInterrupt` | **Partial** — `DispatchCode` never populated, `LOG_INCOMPLETE()` (`EmuKrnlKe.cpp:1015`) | `D3D::CMiniport::InitHardware`, `CMcpxAPU::Initialize`, `CAc97Device::Initialize`, `HCD_NewHostController` | Fields are set correctly so `KeConnectInterrupt` + HLE trigger still works; only raw `DispatchCode` execution breaks. Monitor, do not pre-emptively fix. |
| 153 | `KeSynchronizeExecution` | **Pure stub**, returns TRUE without ever calling `SynchronizeRoutine` | **`DirectSound::CAc97Device::ServiceAciInterruptDpc` only** | The kernel audit flagged this as its top risk; the xref **downgrades it** — it is on the audio DPC path, not the renderer. Audio glitches, not a renderer blocker. |
| 5 | `DbgBreakPoint` | Pure stub | reached via import stub | Engine asserts log and continue instead of stopping. Not a bug to fix — but it means **the first assert in the log is the real one; later crashes are consequences.** |
| 49 | `HalReturnToFirmware` | `CxbxrAbort("Emulated Xbox is halted")` on `ReturnFirmwareHalt`; registered shutdown routines never run (`#if 0` at `EmuKrnlHal.cpp:510`) | `XapiBootToDash`, `XWriteTitleInfoAndRebootA` | Only on the reboot path (`Singletons::PrepareForReboot`). Not a startup concern. |
| 76, 81, 83, 87, 359 | `IoQueryVolumeInformation`, `IoStartNextPacket`, `IoStartPacket`, `IofCompleteRequest`, `IoMarkIrpMustComplete` | Pure stubs | `MU_*` (memory unit) and `XGetFilePhysicalSortKey` | Memory-unit/save paths. Irrelevant until saves are exercised. |

Also worth knowing: `DbgPrint` (`EmuKrnlDbg.cpp:94-134`) has a real off-by-one —
`malloc(size)` where `size` excludes the NUL that `vsnprintf` writes. For a
debug XDK title this is called constantly. One-character truncation plus a
1-byte heap overrun.

**Blocker #1 status**: the debug library-name mapping is already fixed in
`import\XbSymbolDatabase\src\lib\libXbSymbolDatabase.c:438`, which maps
`D3D8D`/`DSOUNDD`/`XAPILIBD` onto their retail equivalents. The symbol cache
(628 symbols, 224 D3D) confirms it works. The name list in
`libXbSymbolDatabase.h:21-56` still has no debug entries, which is misleading
when reading the code but is not the live path.

---

## Recommended order of work

1. **`D3D_KickOffAndWaitForIdle`** — one patch kills `D3DDevice_BlockUntilIdle`
   and `D3D::BlockOnResource`. (Tier 0 #2)
2. **`D3DDevice_KickPushBuffer`** — same fence-catch-up treatment. (Tier 0 #1)
3. **`D3D_MakeRequestedSpace`** — return immediately. (Tier 0 #3)
4. **Log unmatched patch names** in `EmuInstallPatch`'s silent `return`, then
   fix `D3DDevice_BeginPush`. This converts the whole Tier 2 class from
   invisible to visible and is the single highest-leverage change here. (#4)
5. **`AvSendTVEncoderOption`** — write `*Result` for `AV_QUERY_ENCODER_TYPE`
   and `AV_QUERY_MODE_TABLE_VERSION`. Two lines.
6. **`D3DDevice_SetDepthClipPlanes`** — fill in the switch; it is the closest
   match to a live assert. (Tier 1 #6)
7. Check `GetRenderTarget`/`GetRenderTarget2` and
   `GetDepthStencilSurface`/`GetDepthStencilSurface2` for the same inline
   recursion being fixed in `GetBackBuffer`.
8. Keep the push-buffer fields at `s_pD3DDevice + 0x00/0x24/0x28` self-consistent
   — the engine asserts on them at startup and every frame.
9. Tier 1 #7–#13 as rendering correctness work, after boot is stable.

## Explicitly *not* a problem for this title

Ruled out by the xref, despite looking alarming in a generic stub audit:

- **State blocks** (`BeginStateBlock`, `EndStateBlock`, `ApplyStateBlock`,
  `CaptureStateBlock`, `CreateStateBlock`, `DeleteStateBlock`) — the entire
  family is unpatched, but **0 calls from the title**. Every call site is inside
  D3DX (`CD3DXSprite`, `CD3DXRenderToSurface`, `CD3DXRenderToEnvMap`) or
  `D3DDevice_Resume`. Only becomes relevant if the title uses those D3DX
  helpers or the suspend/resume path.
- **`D3DDevice_BlockUntilVerticalBlank` / `SetVerticalBlankCallback`** — patches
  are deliberately disabled and were flagged as the top hang risk in the generic
  audit, but **0 calls from the title**. The only callers are
  `D3D::SwapFirstFlip` and `D3DTest_GetScreenChecksum` inside the library, and
  `D3DDevice_Swap` is patched so `SwapFirstFlip` should not run.
- **`SetRenderState_*` and `SetTextureState_*` (35 functions)** — unpatched, but
  **by design**. The title writes `D3D__RenderState` / `D3D__TextureState` and
  Cxbx reads those arrays at draw time via the `D3D_g_RenderState` and
  `D3D_g_DeferredTextureState` symbols (`RenderStates.cpp:40`,
  `TextureStates.cpp:84`). This is why blocker #2 mattered so much.
- **`D3DDevice_CreateTexture2` / `CreateVertexBuffer2` / `CreatePalette2`** —
  unpatched by design; host resources are created lazily by `CreateHostResource`
  at draw time.
- **`D3DDevice_SetPixelShaderProgram`** (4 title calls, 42 engine sites via
  `Device::SetPixelShaderProgram`) — unpatched, but the Xbox implementation
  writes the shader def into the `X_D3DRS_PS*` render states, which is exactly
  where Cxbx reads it from (`Direct3D9.cpp:8496`,
  `XboxRenderStates.GetPixelShaderRenderStatePointer()`). Probably benign.
  Caveat: `g_pXbox_PixelShader` will stay stale, so anything keying off that
  handle rather than the render states would be wrong. Worth a log line, not a
  patch.
- **Public lock wrappers** (`D3DSurface_LockRect`, `D3DTexture_LockRect`,
  `D3DCubeTexture_LockRect`, `D3DVolumeTexture_LockBox`) — unpatched but
  correctly covered by the patched internal `Lock2DSurface` / `Lock3DSurface`
  primitives. Only the vertex-buffer and palette locks are genuinely uncovered
  (Tier 1 #10, #11).

## Confidence

- **Tier 0 #1–#4 and Tier 1 #6–#13**: call counts and call sites come from
  resolved-thunk cross-references against the real XBE; patch status from
  `Patches.cpp`. High confidence on *reachability*. The predicted *symptom* is
  inference from disassembly and the fork's own comments.
- **Tier 0 #5 and Tier 2**: reachability is certain (direct call sites);
  severity is genuinely unknown because Cxbx has no implementation to compare
  against.
- **Kernel `KeSaveFloatingPointState` -> fov assert**: explicitly a hypothesis,
  flagged because it is cheap to test, not because the evidence is strong.
- The `.textbss` section (VA `0x12000`–`0x65AF8C`) has zero raw size, so nothing
  is resolvable there; no call targets landed in it, so this does not affect the
  results.
