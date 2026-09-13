# The 2004 beta on PC — where it actually stands

Written 2026-08-03, end of session. Blunt version first.

## It is NOT playable

It renders menus. It does not render a world. Pressing X for level select wedges
it in a load that never completes. That is the truth, and no amount of listing
what does work changes it.

What it does do:

* boots from an **unmodified** game file
* renders the title screen and menus, hardware Direct3D, locked **30 FPS**
* responds to a controller
* exposes the developer level-select that shipped in the build
* launches from a folder you double-click (`C:\OddBeta\Play Beta.bat`)

That is a real distance from "does not launch", but it is not the goal.

## The one thing in the way

`GetXboxVertexShader` (`XbVertexShader.cpp:295`) returns null, always:

```cpp
if (g_Xbox_VertexShader_Handle == 0) {
    LOG_TEST_CASE("Unassigned Xbox vertex shader!");
    return nullptr;
}
```

`g_Xbox_VertexShader_Handle` is a **Cxbx-side shadow variable** populated only by
our own patches on `SetVertexShader` / `SelectVertexShader`. It never gets set,
so every attempt to build 3D geometry returns nothing. One cause, three symptoms:

| symptom | why |
|---|---|
| no world geometry | `GetXboxVertexShader` returns null |
| `FVF without position` | an FVF of 0 has no position bits |
| level load never finishes | it is waiting on geometry that never arrives |

**Why the variable stays zero.** The assignment in `CxbxImpl_SelectVertexShader`
(line ~1423) is guarded by `if (Handle)`. This is a `D3D8LTCG` build, so several
entry points take arguments in REGISTERS rather than on the stack. If our patch
reads the handle from the wrong register it gets 0, the guard skips silently, and
nothing reports an error. We have already been bitten by exactly this class of
bug four times — `SetTransform`, `LoadVertexShader`, `SelectVertexShader` and
`D3D_DestroyResource` were all double-patched with the stack-based variant
winning, which is why `SetTransform` once received an *address* where it expected
an enum.

## The fix, already specified

`GetXboxVertexShader` contains a better path that is disabled:

```cpp
#if 0 // TODO : Retrieve vertex shader from actual Xbox D3D state
    // Only when we're sure of the location of the Xbox Device.m_pVertexShader variable
    if (XboxVertexShaders.g_XboxAddr_pVertexShader) {
        pXboxVertexShader = (X_D3DVertexShader*)(*XboxVertexShaders.g_XboxAddr_pVertexShader);
    }
#endif
```

It was waiting on one thing: knowing where the Xbox device keeps its current
vertex shader. **We know.** The Final build's symbol cache has:

```
D3D_g_pDevice                    = 0x217e48
D3DDevice__m_VertexShader_OFFSET = 0x794
```

so the live pointer is at `*(*(0x217e48) + 0x794)`. Nothing in the codebase ever
computes `g_XboxAddr_pVertexShader` — it appears *only* inside the `#if 0`. Wire
it from those two symbols, enable the block, and the emulator reads the title's
real shader state instead of a variable our patches fail to populate. The code's
own comment says that once this works, the `SetVertexShader` and
`SelectVertexShader` patches become unnecessary — which removes the entire LTCG
argument-passing problem for this path rather than patching around it.

**Validate before enabling.** Read `*(*(0x217e48) + 0x794)` at runtime and check
it looks like a plausible pointer that `VshHandleIsVertexShader` accepts. A wrong
offset trades "no 3D" for a crash.

Fallback if that fails: disassemble the title's own `SelectVertexShader`
(`0x00209580`) and `LoadVertexShader` (`0x002093F0`) and confirm which registers
actually carry the arguments, then fix the patch bodies to match.

## What was fixed to get here

Nine root-caused fixes, every one found by reading the machine code or the
build's own debug symbols rather than guessing:

1. Debug/instrumented/LTCG library names unrecognised → forced software rendering
2. `D3D_g_DeferredTextureState` — no public symbol in any PDB, recovered by
   disassembly three times (once per build)
3. GPU fences were `// TODO` stubs; `D3DResource_IsBusy` is not even patched and
   reads the device struct inline, so faking a return value could never work
4. `GetBackBuffer` recursion ate the 1 MB stack, 680 bytes per cycle
5. `EmuInstallPatch` failed silently — one log line exposed **407** unpatched symbols
6. xbdm ordinal 75 (`DmQueryMemoryStatistics`) wrote out of bounds into one of the
   game's own function pointers
7. LTCG double-patching read arguments from the stack when they were in registers
8. **We were running the wrong executable** — the April Debug build against May data
9. A null `pPSDef` dereference in `SetPixelShader`

Number 8 was the expensive one. Its 2 GB allocation failure produced a halt screen
that could not draw its own fonts, and we spent hours debugging that halt screen.
**The asserts we chased were the error message, not the error.**

## Mistakes I made

Recorded because each cost real time:

* **A metric that lied.** My `betacheck` counted the log's `SymbolCache:` and
  `HLE: ... Patched` inventory lines as if they were calls, so I reported
  "2 frames presented" for runs that presented none.
* **A symbol merge that broke rendering.** Merging PDB names into an LTCG title
  shadowed Cxbx's correct register-passing variants and caused a fatal
  `SetTransform` failure.
* **A settings file written from memory.** I hand-wrote the standalone's
  `settings.ini` and omitted six sections Cxbx reads at start-up, so the package
  crashed on launch — when a known-good file was sitting right there.
* **`LoggedModules = 0xffffffff`** inherited into the shipped package: a 632 MB
  log in fifty seconds, which looks exactly like a crash.
* **A wrong diagnosis.** I was confident `z:\data` was the blocker. It was not —
  the title mounts Z: itself and had already written 40 MB through it. Those
  error lines are benign probe-before-create noise.

The pattern in the first three: I reconstructed something from memory when a
working version was already in front of me.

## Where it stopped, and the one measurement to take next

The crash is a WRITE overrun inside `CxbxVertexBufferConverter::ConvertStream`,
reached via `HLE_draw_inline_elements -> CxbxDrawIndexed -> Apply ->
ConvertStream` (resolved against the PDB; the fault sits at `+0x8bf`/`+0x8df`
of that function, i.e. the conversion loop).

**Three hypotheses were guarded and none of them fired.** Record this so nobody
retries them:

| guess | guard | result |
|---|---|---|
| null vertex declaration reaching `SetVertexDeclaration` | `g_Cxbx_SkipDrawNoVertexDeclaration` | fired **0** times |
| 32-bit `count * stride` overflow sizing the host buffer | 64-bit recompute + sanity limit | fired **0** times |
| null texture from `PS_INPUTTEXTURE(n) uses texture 0` | dummy-texture path | `nullTexStages=0` |

All three were plausible and all three were wrong. The allocation genuinely does
equal `uiVertexCount * uiHostVertexStride`, so the buffer is the right size.

**What that leaves.** The conversion loop is:

```cpp
for (uiVertex = 0; uiVertex < uiVertexCount; uiVertex++) {
    pHostVertexAsByte = &pHostVertexData[uiVertex * uiHostVertexStride];
    for (uiElement = 0; uiElement < pVertexShaderStreamInfo->NumberOfVertexElements; uiElement++) {
```

If the vertex ELEMENTS sum to more bytes than `HostVertexStride`, every vertex
writes slightly past its own slot and the final vertex runs off the end of the
buffer - which is exactly a small overrun rather than a wild pointer, matching
the observed fault addresses (`0x14016278`, `0x13EE6278` - adjacent, not random).

**STOP GUESSING. The next step is one measurement**, not another guard: at the
top of `ConvertStream`, log `uiVertexCount`, `uiXboxVertexStride`,
`uiHostVertexStride`, `NumberOfVertexElements`, and the sum of
`VertexElements[i].HostByteSize`. If that sum exceeds `uiHostVertexStride`, the
bug is proven and the fix is upstream in how the declaration computes the
stride. Three rounds were spent guarding guesses; printing five numbers settles
it.

Everything needed is committed: the fork at `C:\Users\<you>\SWBeta\src\Cxbx-Reloaded`
(branch `master`, commit "Fork fixes for the Oddworld Stranger's Wrath May 2004
devkit build"), the standalone at `C:\OddBeta`, and the symbol pipeline in
`tools\`.

## Session 2 — why the world does not draw (root cause found)

The earlier chapters of this document chase a crash. That was the wrong target.
With the owner's much better test case — **the main menu, which has an animated
backdrop that should be running and is black, and needs no input to reproduce** —
the answer came from five measurements and one disassembly.

### What the measurements ruled out

Every one of these was a live number, not a guess:

| measurement | value at the menu | rules out |
|---|---|---|
| `hostDraws` | ~38 per frame | "nothing is being drawn" |
| `vertexShaderLookups` / `fromDevice` / `missing` | 723 / 723 / **0** | missing shader lookups |
| `nullTexStages` | 0 | unbound textures |
| viewport | `0,0 640x480 minZ=0 maxZ=1` | degenerate viewport / depth range |
| `setVS` `loadVS` `selectVS` | 1.9 / 0.93 / 0.96 per frame | a broken patch chain |

Draws ARE submitted, shaders ARE resolved, the viewport IS correct. So the
problem was never geometry supply.

**A sampling mistake to not repeat.** I first read `mode=FixedFunction` in the
per-swap stats and concluded the mode was stuck. It was not — the mode is
per-draw state and I printed it at swap time, which always reflects the last draw
of the frame, i.e. the UI. The counters disproved my own headline within minutes.

### The root cause

The title does not select vertex shaders the way Cxbx assumes. From disassembly
of the shipping XBE (see `tools\xbe_refs.py`, added for this):

```
SimpleShader::ActivateVertexShader          <- the 3D world path
WaterRenderer::RenderTiles, SkyInstance::*, VertexBuffer::Activate
  -> Device::SetActiveVertexShaderInputs
     -> D3DDevice_SelectVertexShaderDirect (0x00209510)   NOT PATCHED
        -> D3DDevice_SelectVertexShader (0x00209580)  only when pVAF != NULL

Device::ResetDeviceStates, render_handler_owi::begin_display   <- SWF/UI only
  -> D3DDevice_SetVertexShader (0x00209D90)                    PATCHED
```

Three facts follow, each grounded in the machine code:

1. **The world renderer never creates a vertex shader handle.**
   `D3DDevice_CreateVertexShader` has exactly ONE call site, and it belongs to the
   Flash/SWF UI renderer. The 3D pipeline uploads microcode with
   `LoadVertexShaderProgram` and then selects it **by address**. Cxbx's entire
   vertex-shader path is keyed on handles.

2. **`SetActiveVertexShaderInputs` passes `pVertexAttributeFormat = NULL` in
   steady state**, taking the branch at `0x0020953E` that re-points the program
   without calling anything Cxbx patches:
   ```
   0020956C  mov [esi+0x79C], ecx   ; device.m_VertexShaderStartAddress = Address
   ```
   So the world switches vertex programs — skinning, water, sky, foliage — with no
   observable call. `0x00209580` fires at most once per declaration change.

3. Consequently BOTH of Cxbx's shadow variables are stale for every world draw:
   `g_Xbox_VertexShaderMode` (so world geometry ran through the FIXED-FUNCTION
   pipeline, using transforms the title never sets) and
   `g_Xbox_VertexShader_FunctionSlots_StartAddress` (so the wrong uploaded program
   was compiled).

That is a complete explanation of the symptom: 3D disappears, while
pre-transformed 2D UI — which needs neither transforms nor a program — draws
perfectly. It is exactly what is on screen.

### The fix

Same shape as the fix that already worked for the shader pointer: **stop trusting
patch-written shadow variables, read the title's own device structure.** The
layout is proven by three independent writers in the disassembly:

```
device + 0x794   current X_D3DVertexShader*      (already used)
device + 0x798   handle
device + 0x79C   program start address           (added: CxbxrGetXboxVertexShaderStartAddress)
```

and the effective mode is derived from the live shader's own `Flags`
(`X_VERTEXSHADER_FLAG_PROGRAM`, `0x10` — stamped at D3D init on the static shader
that `SelectVertexShaderDirect` uses) rather than from `g_Xbox_VertexShaderMode`.

Offsets are derived from the `D3DDevice__m_VertexShader_OFFSET` symbol, not
hard-coded.

### The menu backdrop — FIXED, it now renders

It is **not** live geometry and **not** a flycam scene. It is
`data\movies\main_screen.bik` — a Bink movie, 640x480, 29.97 fps, 277 frames,
9.24 s, no audio track, looped. The front end asks for it from ActionScript:

```
_root.StartBinkBackgroundLooped('d:\data\movies\main_screen.bik')   ; main_menu.swf const[45]
  -> FSCommand -> StandardFSCommandHandler::SWFCallback (0x107AF3)
  -> GUI::StartBinkBackground (0x106DD0) -> BackBufferPlayer::Open -> BinkOpen
```

The string appears **nowhere in the XBE** — every movie path is data-supplied, from
SWF constant pools and `.foo` scripts inside the bundles.

**Why nothing found it.** The movie is never *drawn*. `BackBufferPlayer::DisplayFrame`
(0x114601) presents it with no draw call, no texture, no quad and no overlay:

```
GetBackBuffer2 -> D3DSurface_LockRect(X_D3DLOCK_TILED)
              -> memset(pBits, 0, Pitch*480)                 ; black, every frame
              -> BinkCopyToBuffer(BINKCOPYALL|BINKSURFACE32) ; software YUV->BGRA
              -> D3DResource_Release
```

`D3DSurface_LockRect` is declared in Direct3D9.h but **never implemented and never
registered** in Patches.cpp, so Cxbx observes nothing. That is why ~38 clean draws
per frame, zero missing shaders and `enableOverlay=0 updateOverlay=0` all looked
healthy while the screen stayed black. (The overlay path *is* used by this title —
but only by `SimpleBinkPlayer::PlayMovie` for cinematics, not by the menu.)

**Proof before the fix.** A pixel census of the Xbox back buffer at swap time:

```
xboxBackBuffer 640x480 pitch=2560 sampled=4800 nonBlack=4646 or=0xFFFFFF
                                               nonBlack=4616
                                               nonBlack=4650
```

~97% non-black, and the count changes every sample — a live moving image sitting
in guest memory being discarded every frame. `HBINK` at `0x29ACB0` was non-zero
with `bLooped = 1`, so Bink had opened and was decoding correctly.

**The fix** (`CxbxUploadXboxBackBufferToHost`, Direct3D9.cpp). The Xbox has unified
memory, so the surface a title locks IS what gets scanned out and software
rendering into it just works. On PC the host render target is a separate GPU
allocation and Cxbx never reads guest back buffer memory back — the copy in
`CxbxrImpl_GetBackBuffer2` is `#if 0`'d out under the comment *"There are currently
no known games that depend on backbuffer readback on the CPU!"*. This title does.

Each frame the guest back buffer is copied row-by-row into a cached
`D3DPOOL_DEFAULT` offscreen surface and `StretchRect`'d onto the host render
target. **Timing is the load-bearing part**: it runs from
`CxbxUpdateNativeD3DResources` — the pre-draw update, armed at swap — not at swap
itself. The title writes the movie in `GUI::Render` and then draws the SWF front
end over it, so uploading at the first draw of a frame reproduces the Xbox
layering. Uploading at swap would paint the movie *over* the menu.

Result: the forest backdrop renders, animating, at 29.99 FPS, `uploads=419 fails=0`.

### A negative result — do not retry

Deriving `g_Xbox_VertexShaderMode` from the live device shader's `Flags` looks
correct by symmetry with the shader-pointer fix, and the device field really is
authoritative for *which shader is bound*. It is **not** authoritative for which
host pipeline to run: it resolved every draw to FixedFunction (`FF=18187 PROG=0`,
disagreeing with the shadow variable 18185 times) and rendered the whole menu
black — the SWF UI genuinely does use a vertex program. Reverted; kept as a
counter only. `g_Xbox_VertexShaderMode` is the right source there.

The start-address fix (`CxbxrGetXboxVertexShaderStartAddress`, device + 0x79C) is
kept: it is correct and guarded, and it will matter as soon as a level loads, but
it changed nothing at the menu because the menu draws no world geometry.

### THE GAME RUNS — Tutorial Town renders, ~30 FPS

World geometry, terrain, buildings and sky all draw, the level bundles stream
(`npc_19.smb`, `npc_22.smb`, ...), and no exception fires. Two fixes got it there.

**1. The crash: an undersized vertex buffer from our own pool.**
`VBPool_BucketIndex` walked to the largest bucket and returned it *even when the
bucket was still smaller than the request*:

```cpp
while (bucketSize < size && idx < VB_POOL_NUM_BUCKETS - 1) { idx++; bucketSize <<= 1; }
```

The biggest bucket is 2 MB, so every request over 2 MB silently got a 2 MB buffer
and `ConvertStream` wrote off the end. 65,535 vertices at a 32-byte host stride is
2.1 MB — completely ordinary for this engine. Fixed: oversize requests get an
exact-sized allocation and are never pooled (pooling a non-power-of-2 buffer would
let a later larger request pop one too small and reintroduce the bug one size class
down). Confirmed by `oversizeVBs=1` and a clean survival run.

All three reported faults resolved to the same function:

```
0x24CA21 -> CxbxVertexBufferConverter::ConvertStream + 0x991 -> XbVertexBuffer.cpp:704
0x24CA2E -> ConvertStream + 0x99E -> XbVertexBuffer.cpp:706
0x24C0FE -> ConvertStream + 0x6E  -> XbVertexBuffer.cpp:317
```

**2. The vertex program address fix is load-bearing.** In-game,
`startAddrCorrected=6011084` with `live=0x6A shadow=0x1C` — six million draws where
the title's real program address differed from the shadow variable. Without
`CxbxrGetXboxVertexShaderStartAddress` the world draws with the wrong shader.

### Sound effects were silent — a C vs I naming mismatch

Owner reported music playing but no sound effects. The split is exact:

```
CDirectSoundStream_*  23 patched,  3 unpatched   (88%)  <- music
CDirectSoundBuffer_*  25 patched, 25 unpatched   (50%)  <- SFX
```

and the unpatched half contains **`CDirectSoundBuffer_Play`**, the call that starts
an in-memory sound, plus `Lock`, `SetBufferData`, `SetFormat`, `GetStatus`.

Cause: Cxbx's patch table is inconsistent about the leading letter. Streams are
registered under BOTH `CDirectSoundStream_*` (38 entries) and `IDirectSoundStream_*`
(10). Buffers are registered ONLY as `IDirectSoundBuffer_*` (46 entries, **zero**
C-prefixed). Our symbols come from the title's own PDB, which names them after the
implementation CLASS (`CDirectSoundBuffer_Play`), so every stream symbol bound and
every buffer symbol silently did not.

That maps exactly onto what was audible: music is streamed (27 `*_stream.xwb`
banks), effects are in-memory banks (16 of them - `steef.xwb`, `townsfolk.xwb`,
`crossbow.xwb`, `native.xwb`, `outlaw.xwb`, `region_0N.xwb`), and the in-memory path
was running unpatched end to end.

Fix: in `EmuInstallPatch`, retry a leading `C` as `I` for `CDirectSound*` names.
`CDirectSoundBuffer` is the class implementing the `IDirectSoundBuffer` interface,
so the entry point is the same function - this is a naming alias, not a behaviour
change, and it avoids duplicating 46 table entries that would then need keeping in
sync.

**Note for the future:** this was only findable because `EmuInstallPatch` prints
"No patch registered" for symbols it cannot bind. That line was added earlier in
this project and has now paid for itself twice. Keep it.

**Music worked from the start** (owner-confirmed in-game). That closes the loop on the most
expensive wrong turn in this project: the April Debug build's audio init against May
data read a garbage lip-sync count, asked for 2 GB, and died on a halt screen it
could not draw. Running the matching Final build, `DSOUND` / `XACTENLT` emulation
works and the `.xwb` banks stream - the ones the opened-file census lists.

### THE LESSON THAT COST THE MOST TIME

**`LoggedModules = 0x0` silences `EmuLog` completely.** The log contains ZERO
WARNING lines. Every diagnostic from earlier sessions used `EmuLog`, so the claim
in this document that four crash hypotheses "each fired zero times" measured
NOTHING — those guards may have been firing constantly with the output discarded.

Anything that must survive a default configuration uses `printf`, like RENDERSTATS.
Rebuilt on printf, the crash named itself in a single run.

Two supporting changes worth keeping:
* The unhandled-exception handler now reports `module.dll+0xOFFSET`, plus READ /
  WRITE / EXECUTE and the target address. A bare EIP is useless across runs because
  ASLR moves module bases — the address from one session cannot be looked up in the
  next. With module+offset, `tools\pdb_resolve.ps1` gives function + source line.
* `tools\betapeek.ps1` reads guest memory live via ReadProcessMemory (Cxbx maps the
  Xbox address space at the same VAs), so engine globals can be inspected without a
  rebuild.

### SOLVED: characters were a POISONED SHADER CACHE, not a code bug

**Deleting `ShaderCache\` fixed it instantly.** Stranger and the NPCs render fully -
skinned meshes, poncho, hat - and the world renders correctly with it.

Cxbx compiles Xbox vertex shaders to host shaders and caches the results ON DISK,
keyed by the Xbox bytecode. **That cache survives rebuilds.** At some point a bad
build compiled and cached wrong host shaders for the skinned programs; from then on
every subsequent run reused those bad shaders, no matter what was fixed in the
source. The emulator was doing exactly what it was told - with poisoned input.

This explains the whole investigation, and why every measurement said the code was
fine:

| measurement | result | and it was RIGHT |
|---|---|---|
| `droppedUnboundStream=0` | draws submitted | yes |
| vertex declaration census | conversion correct | yes |
| `vsConstCalls`, non-zero to reg 190 | bone constants present | yes |
| `MAC_ARL` parsed | relative addressing handled | yes |

All four were accurate. The geometry, declarations and constants really were
correct - they were being fed to a stale compiled shader. No source change could
ever have fixed it, which is exactly why nothing did.

**THE RULE: when a fix that should work doesn't, suspect persistent state before
suspecting the code.** `ShaderCache\` and `SymbolCache\` both survive rebuilds. Clear
`ShaderCache\` before concluding a rendering change had no effect. Cheap to do, and
it would have saved most of an afternoon here.

The same trap nearly produced two wrong "fixes": a DirectSound patch-name change was
reverted for causing a regression it did not cause, and the vertex buffer pool was
rewritten to chase an exhaustion problem that `vbAllocFailures=0` later disproved.
Both were misattributed to a cache that no code change could touch. (The pool
change is worth keeping on its own merits - large buffers are now pooled and reused
instead of created and destroyed hundreds of times per session - but it was not the
fix it was shipped as.)

**Also note: the DirectSound C->I finding was probably valid after all.** It was
reverted on the assumption it broke rendering; the cache did. Re-test it - the
underlying observation stands, that our PDB-derived symbols use the implementation
class name (`CDirectSoundBuffer_Play`) while Cxbx registers buffers only under the
interface name, which is why music played and no sound effect did. It is behind
`#if 0` in Patches.cpp. Verify the LTCG calling convention per function first.

### Previously open, now closed: characters render as eyeballs only

Rigid meshes draw; **skinned** meshes do not. Established:

* `droppedUnboundStream=0` — the character draws are NOT discarded. They are
  submitted and drawn, and land nowhere visible. This is a transform problem.
* The vertex declaration conversion is **correct** — verified, not assumed. A census
  of every distinct (register, Xbox type, host type) shows the skinning register 1
  using Xbox type `0x44`, which decodes as count 4 / type 4 = `UB_OGL`. That format
  IS normalized on the NV2A, so mapping it to `UBYTE4N` is right. Every other row
  checks out too (`0x42`->FLOAT4, `0x21`->SHORT2N, `0x16` compressed normal->FLOAT3,
  `0x40`->D3DCOLOR). **Do not "fix" the packed-byte mapping — it is not the bug.**

So correct vertex data is reaching a shader that collapses it. The remaining
suspect is the skinning constants: this engine uses two-bone quaternion skinning
with each bone as 2 vec4 program locals, and if those constants are absent or
land in the wrong registers every skinned vertex scales to nothing. Note world
geometry renders correctly, so constants are not broken in general — which points
at the specific path the skinning constants are set through, the same way
`SelectVertexShaderDirect` turned out to be a path Cxbx never observed.

### A wrong claim of mine, corrected

I wrote earlier in this session that the loose `d:\data\prefs\GamePrefs.txt` is
never read, on the strength of the `FILEOPEN` census not showing it. **That was
wrong.** `GamePrefs::Get` (RVA `0x1CB8`) unconditionally calls
`PrefsUtil::InitTextPrefs(..., "\data\prefs\GamePrefs.txt")`, and the April build's
own `Game.log_prev` shows it parsing that file and warning about the five fields the
April exe does not recognise. The open goes through the resource manager, which is
presumably why an `IoCreateFile` census missed it — **so the census proves what was
NOT opened through that path, not what was never opened at all.** Worth remembering
before using it as negative evidence again.

`m_skipShellGUI` is genuinely absent from `global_prefs.smb` (all three hash
variants checked against all 388 field hashes, plus a sweep of all 11,758 files
under `Game\data`). That bundle holds 254 *asset* pref objects keyed by path —
particle systems, effect mixes, artifacts — and no engine singletons.

The reason the edit did not take is a boot-mode gate, decoded from `default.xbe`:

```
1CFD  cmp  [ebp+8], ebx        ; mode == 0 ?
1D00  jne  1D18                ; if not, the shell shows regardless
1D02  call GamePrefs::Get
1D07  cmp  byte [eax+0x28], bl ; m_skipShellGUI at struct offset 0x28
1D0A  jne  1D18                ; skip -> boot the level
```

A one-byte fallback exists (`default.xbe` file offset `0x00001D0A`, `75` -> `EB`)
but was NOT applied: it modifies the game image, which the package deliberately
keeps pristine, and the need has passed now that the game reaches a level normally.

Two further corrections this produced: `m_startCines` is not the opening-cinematic
switch (read at exactly one site, `native__StartJobOnSteef`, suppressing script jobs
on Steef), and `m_bootToLevelList` is dead code in this build.

### The standalone: "Stranger's Wrath Beta.exe"

The package is no longer a batch file next to an emulator. `C:\OddBeta` now holds a
compiled launcher plus a 9.4 MB runtime.

**Headless is the important part.** Cxbx has a real no-GUI mode:
`cxbxr-ldr.exe /load <xbe>` runs the emulator with no shell, no menu bar and no
settings dialogs - and `cxbx.exe` *refuses* `/load` outright ("Emulation must be
launched from cxbxr-ldr.exe!"). Launching through the GUI front end was what put the
Cxbx window and menus on screen. Going direct also means the GUI executable does not
ship at all.

Runtime contents, decided by reading IMPORT TABLES rather than guessing:

```
cxbxr-emu.dll  7.9 MB   cxbxr-ldr.exe -> (no local imports)
SDL2.dll       1.5 MB   cxbx.exe      -> SDL2
glew32.dll     0.3 MB   cxbxr-emu.dll -> subhook, SDL2, glew32
subhook.dll     10 KB
```

`capstone.dll` + `cs_x86.dll` (3.95 MB) are imported by nothing and were dropped;
`cxbx.exe` (2 MB) is unused in headless mode. 118 MB -> 9.4 MB of binaries.

Three emulator fixes went in for the standalone:

* **Window size followed nothing.** The render window was hard-coded to 640x480
  regardless of `RenderResolution`, so raising the upscale sharpened the internal
  render and then displayed it in a tiny window. It now scales with the factor,
  uses `AdjustWindowRect` so the CLIENT area is the requested size, clamps to the
  desktop work area by whole steps (4x is 2560x1920 - taller than a 1080p screen),
  and centres itself.
* **Window title** is now "Oddworld: Stranger's Wrath - 2004 Beta". The window
  CLASS stays `CxbxRender`; other code looks it up by name.
* **Window icon.** The class was registered with `0` for hIcon - there was a
  literal `// TODO : LoadIcon(hmodule, ?)`. It now loads `game.ico` from beside
  cxbxr-emu.dll, so branding lives in the package and can change without a rebuild.
  `tools\exe_icon.ps1` extracts a real multi-size icon from an executable's
  RT_GROUP_ICON (ExtractAssociatedIcon only returns a blurry 32x32).

The launcher itself (`launcher\Launcher.cs`, built with the .NET Framework csc that
ships with Windows) offers five resolutions and rewrites only three keys in
`settings.ini`, **in place**. It deliberately does not regenerate the file: a
hand-written minimal `settings.ini` crashed the emulator earlier in this project
because it omitted six sections read at start-up.

**GPL-2.0 note:** Cxbx-Reloaded is GPL-2.0. Rebranding the window and shipping a
modified build is fine, but the copyright notice and source offer must stay in the
package - they are in `README.txt`. Do not strip them.

### Input: PC-style profile

`[input-profile-0]` is now a PC layout: WASD on the left stick, mouse on the right
stick, left/right mouse on the triggers, Space jump, E use, F melee, V view toggle,
LCtrl crouch, Esc pause, Tab map.

The mouse binds `Axis X+/-` and `Axis Y+/-`, which are Cxbx's RELATIVE mouse deltas -
the same thing a thumbstick produces. `Cursor X/Y` is absolute screen position and
would feel like dragging a pointer; do not use it for look. Y is crossed
(`Right Axis Y+ = Axis Y-`) for the PC convention.

Bindings resolve by EXACT string match against a control's `GetName()`, and an
unmatched name is silently bound to `nullptr` - it simply never responds, with
nothing logged. Names are `Click 0/1/2` for mouse buttons and `Axis X+`/`Axis X-`
for movement. Keyboard confirmed working by the owner.

**Open:** mouse look/click unverified in-game. Note the front end has NO mouse
pointer - it is an Xbox menu, so there is nothing to click there; Space selects.
Also note headless mode writes no log, so the `INPUT:` diagnostics added to
`BindHostDevice` have nowhere to print. Route that to a file before relying on them.

### DEAD LEAD: "the in-memory wave banks never load"

**Wrong. They load fine.** Retired by disassembly - do not re-investigate.

The resident banks are packed INSIDE `.smb` bundles, not loose files:

| bank | carried by |
|---|---|
| `steef.xwb`, `crossbow.xwb`, `general.xwb`, `notfound.xwb` | `global\global_audiobanks.smb` (4,431,872 bytes, 4x WBND + 4x SDBK) |
| `region_01.xwb` | `bundles\Region_01\lm_level_01\lm_level_01_tgl.smb` |
| `townsfolk.xwb` | `...\npc_1.smb`  ·  `outlaw.xwb` -> `...\npc_7.smb` |

Every one of those bundles IS in the census. **Zero loose `.xwb` opens is expected**,
because only `XACT::CWaveBank::InitializeStreamingBank` opens banks by filename;
resident banks are read from bundle bytes already in memory.

There is also no stream-vs-resident branch to flip. `WaveBankUtil::GetWaveBankType`
(0x00167F64) substring-matches the NAME only:

```
push 0x250B44 ; "_stream"     -> type 1
push 0x250B38 ; "_dvdstream"  -> type 2
fallthrough                   -> type 0 = in-memory
```

No pref, no free-memory query, no DVD flag. `region_01` and `region_01_stream` are
two SEPARATE banks with different content, not two variants of one. `m_isDVDRun` has
exactly one reference in the image (its CVarDef registration) and is irrelevant.
`m_doAudioIOThread` does not exist as a string in this XBE at all.

**THE TRAP, TWICE NOW.** The `IoCreateFile` census structurally cannot see anything
loaded from a bundle or through the resource manager. It missed `GamePrefs.txt`, and
it missed every resident wave bank. Both times its silence was read as "never
loaded". **Absence from that census is not evidence of absence.** Before using it
negatively again, check whether the asset could be bundle-packed - `smb_entries.py`
will say.

Runtime proof the banks are live: `diagnostics.txt` shows 250 `IDirectSoundBuffer_Play`
calls with decoded peaks of 32767 / 30792 / 22159. Streamed banks produce
`CDirectSoundStream` objects; only a RESIDENT bank produces a `CDirectSoundBuffer`.
So resident audio is loaded, registered with XACT, decoded and started.

Useful for any follow-up: `s_waveBankEntryList` at guest `0x002BD774` (pointer) and
`0x002BD77C` (count), 0x14-byte entries - `+0x00` name ptr, `+0x0C` type, `+0x10`
`StreamedWaveBank*`. Readable live with `tools\betapeek.ps1`, no rebuild. And note a
silent-success trap in `AudioDevice::RegisterWaveBank` (0x00149CE5): if `g_pXACT` is
null it returns S_OK having registered nothing.

### Sound effects — the 3D audio gap

**2D sounds work; 3D positional sounds do not.** Owner-confirmed: moolah pickup and
UI sounds play, music and cutscenes play, but **walking, attacking and impacts are
silent** - i.e. exactly the sounds that have a world position.

Cause: **Cxbx-Reloaded does not implement `IDirectSound3DCalculator` at all.**

```
PATCH_ENTRY("IDirectSound3DCalculator...")  -> 0 entries in Patches.cpp
Calculate3D implementation                  -> 0 matches anywhere in core/hle/DSOUND
```

Unpatched, all one coherent group:
`IDirectSound3DCalculator_Calculate3D`, `IDirectSound3DCalculator_GetVoiceData`,
`CDirectSoundVoice_Set3DVoiceData`, `CDirectSoundVoice_Use3DVoiceData`.

On Xbox, a positional voice gets its per-speaker volumes from that calculator. With
it missing the voice is created, filled with real audio and played successfully -
and never given an audible volume in any speaker. This is an UPSTREAM Cxbx gap, not
a defect in our fork, and fixing it means implementing DirectSound3D emulation.

**Everything cheaper was eliminated by measurement first** - each of these was a
plausible theory that turned out wrong, and each would have been an expensive guess:

| theory | measurement | verdict |
|---|---|---|
| sound patches not binding | 32 mapped, incl. `CDirectSoundBuffer_Play` | wrong |
| the `CDirectSoundVoice_*` family unpatched | `xbe_refs` - ALL callers are inside DSOUND, none in the title's `.text`; the Buffer wrappers are patched, so those inner calls never execute | **wrong - saved writing a 15-function dispatcher** |
| playback failing | 20 Play calls, every one `hRet=0`, valid host buffers, real byte counts | wrong |
| XADPCM codec disabled -> forced to `DSBVOLUME_MIN` | `codecs[pcm=1 xadpcm=1 unknown=1]`, no buffer muted | wrong |
| audio data missing / decoding to silence | decoded peak amplitudes 13034, 26023, 32767 | wrong |
| `MuteOnUnfocus` | disabled it, no change | wrong |

**A measurement mistake worth remembering:** the first amplitude check sampled "the
first 16 buffers of any kind", and the first ten were PCM - so it reported healthy
peaks belonging to the MUSIC and said nothing about the effects. Measuring the wrong
population is the same error as measuring through a logger that is switched off.

The thing that actually cracked it was the owner's observation that moolah pickup
DOES play. "No SFX" and "no 3D SFX" look identical from the emulator side, and only
one of them points at the 3D calculator.

### Superseded: the Voice family theory

The `C`->`I` mapping WORKS - 32 functions bind, including `CDirectSoundBuffer_Play`.
Confirmed from `diagnostics.txt`. Sound effects are still silent, and the diagnostic
now says exactly why: **15 `CDirectSoundVoice_*` functions are unpatched, and Cxbx
has ZERO Voice entries in its patch table.**

```
CDirectSoundVoice_SetVolume      CDirectSoundVoice_SetFormat
CDirectSoundVoice_SetPitch       CDirectSoundVoice_SetFrequency
CDirectSoundVoice_SetMixBins     CDirectSoundVoice_SetOutputBuffer
CDirectSoundVoice_SetEG          CDirectSoundVoice_SetFilter
CDirectSoundVoice_SetLFO         CDirectSoundVoice_SetHeadroom
CDirectSoundVoice_Set3DVoiceData CDirectSoundVoice_Use3DVoiceData
CDirectSoundVoice_GetVoiceProperties  CDirectSoundVoice_CommitDeferredSettings
CDirectSoundVoiceSettings_SetMixBinVolumes  ... plus CDirectSound_DoWork
```

On Xbox a buffer IS a voice - `CDirectSoundVoice_*` is the shared implementation
behind both buffers and streams. Cxbx instead registers `IDirectSoundBuffer_*` and
`IDirectSoundStream_*` as separate patches, so the shared name matches neither.

**DO NOT simply map `CDirectSoundVoice_*` onto `IDirectSoundBuffer_*`.** Those patches
take a buffer-specific pointer and dereference it immediately:

```cpp
IDirectSoundBuffer_SetVolume(XbHybridDSBuffer *pHybridThis, long lVolume)
    EmuDirectSoundBuffer *pThis = pHybridThis->emuDSBuffer;
```

The same Xbox function is called for streams too, so a blanket map would hand a
stream pointer to buffer code - misread struct, likely corruption, and it would
probably kill the music that currently works. That is the same mistake already made
once today with the blanket C->I rename.

**The correct fix** is a dispatching patch: intercept `CDirectSoundVoice_*`, work out
whether the voice belongs to a buffer or a stream, and forward accordingly.

The lookup exists already. `DirectSound.cpp` keeps `g_pDSoundBufferCache` (and the
equivalent for streams), and every hybrid buffer carries `p_CDSVoice` - the Xbox
voice pointer - which is passed to `HybridDirectSoundBuffer_*` throughout
`DirectSoundBuffer.cpp`. So the shape is:

```
CDirectSoundVoice_SetVolume(pVoice, lVolume)
    scan g_pDSoundBufferCache for p_CDSVoice == pVoice  -> IDirectSoundBuffer_SetVolume
    else scan the stream cache                          -> IDirectSoundStream_SetVolume
    else ignore (voice not tracked yet)
```

Fifteen functions need this, so factor the lookup into one helper rather than
repeating it. Verify each entry point's calling convention BEFORE patching - this is
an LTCG build and several D3D entry points here take arguments in registers.

Do this on a branch and check the MUSIC still plays after every step; music currently
works and is the thing most likely to break.

### Diagnostics in a shipped build - THREE layers were swallowing output

Worth stating plainly because it wasted hours across the session. In the shipped
configuration every diagnostic channel was silently discarded:

1. `LoggedModules = 0x0` makes **EmuLog** produce nothing - zero WARNING lines.
2. Headless (`cxbxr-ldr /load`) has **no console**, so printf goes nowhere visible.
3. With DebugMode NONE, `CxbxKrnl.cpp` actively did `freopen("nul", "w", stdout)` -
   **deliberately routing printf to the null device** - and it ran AFTER the redirect
   added in `Emulate()`, silently undoing it. The diagnostics file was created,
   received its header, and stayed empty.

Each layer made a working diagnostic look like the thing being measured was not
happening. Fixed: that branch now redirects stdout to `diagnostics.txt` beside the
emulator DLL, line-buffered so a crash still leaves the run-up on disk. **A shipped
build must remain diagnosable.**

Result: `diagnostics.txt` is ~97 KB per session and carries RENDERSTATS, the
opened-file census, the input control list, the patch mapping and the
unhandled-exception location.

### Input is DONE - verified, not assumed

`diagnostics.txt` confirms the device exposes 162 controls including `Click 0..7` and
`Axis X-/X+/Y-/Y+`, and **every binding in the profile resolved** (zero UNRESOLVED
lines). Owner confirms mouse look and clicking work in-game. Horizontal look was
inverted and is now swapped.

### Input automation does not work — ask the owner to press the key

`tools\betakey.ps1` sends scancodes with `SendInput` and does everything correctly
— it raises the render window, uses `AttachThreadInput` to defeat the foreground
lock, confirms the window really became foreground, and holds each key 150 ms.
Neither process is elevated, so UIPI is not blocking it. The game still does not
react: with `IgnoreKbMoUnfocus = false` and a genuine mouse click to force
activation, `down` never moves the menu highlight (row brightness 14718 vs ~5700
for the unselected rows, unchanged across presses).

A real keyboard works — the owner has driven this menu by hand. So this is a
limitation of injected input reaching Cxbx's DirectInput acquisition, not a bug in
the build. **Do not spend more time on it; ask the owner to press the key.** The
script is kept because the window-focusing half is still useful.

### Reusable tooling added

* `tools\xbe_refs.py` — address-keyed xref finder (`calls` / `ptr` / `memdisp`).
  The existing `xbe_xref.py` only works on PDB-named targets, and the statically
  linked XDK D3D library has no names.
* `FILEOPEN:` census in `IoCreateFile` — every distinct path the title opens,
  printed once.
* `RENDERSTATS:` now also reports viewport, depth range, the vertex-shader call
  chain, overlay calls, and how often the live device state disagreed with the
  shadow variables.

### A latent bug found on the way

`D3DDevice_UpdateOverlay_16__LTCG_eax2` (Direct3D9.cpp) has an **empty body** — it
logs its arguments and drops the frame. Both it and the plain variant are
registered in `Patches.cpp`, the same double-patch pattern that already cost this
title `SetTransform`, `LoadVertexShader`, `SelectVertexShader` and
`D3D_DestroyResource`. The plain variant wins here so it is not the current cause,
but it is now counted rather than silent.

## Honest assessment

The remaining blocker is a single variable with a known fix and a known fallback.
Everything upstream of it works. But "one variable away" has been true for about
an hour, and it is not done until the world draws.

If the `#if 0` path works, the world should appear almost immediately — every
other stage of the pipeline is proven. If it does not, the fallback is a
disassembly job of exactly two functions, which is the kind of work that has
succeeded every time tonight.

## Session 3 — flicker and the Mongo Valley black screen

### Flicker: five agents, four mechanisms, one content fact

Format conversion, resource caching/hashing, render targets, the CPU back-buffer
upload and texture-stage state were investigated in parallel. Three real emulator
defects were found and FIXED, and one non-emulator cause was identified.

**FIXED 1 - texture stage memo keyed on too little.** `CxbxUpdateHostTextures`
caches per stage and skips `SetTexture` when the Xbox texture pointer and `->Data`
are unchanged. The result actually depends on five more things, none of which were
in the key:

* the host texture OBJECT - `FreeHostResource` + `CreateHostResource` makes a NEW
  one, the memo says "unchanged", and D3D9 keeps the ORPHANED texture bound with
  frozen contents;
* `forceRehash` - set when a texture is locked and rewritten, but only consumed via
  `GetHostBaseTexture`, which the memo's fast path skips entirely;
* whether a pixel shader is bound - the dummy-white substitution is conditional on
  it, so the decision got stuck across fixed-function/shader switches;
* the palette (P8 keys embed a palette hash).

Fix: a `g_CxbxHostTextureGeneration` counter bumped by `FreeHostResource`,
`ForceResourceRehash` and `SetPalette`, plus the pixel-shader flag, both added to
the memo key.

**FIXED 2 - point sprites clobber host stage 0 and never restore it.**
`XboxTextureStateConverter::Apply()` copies stage 3's texture onto host stage 0 for
point sprites. Upstream re-set textures unconditionally every draw, which repaired
it; the memo removed that repair, so the next draw with the same Xbox texture
rendered with the PARTICLE texture. Fix: `CxbxInvalidateHostTextureStage(0)`.

Same bug in the state shadow: `PreviousStates` was indexed by XBOX stage while the
write went to HOST stage, so host stage 0 kept the particle stage's sampler state
indefinitely and host stage 3 never received its own. Fix: index by `HostStage`, and
reset the two direct stage-1 writes.

**FIXED 3 - render targets were being destroyed and rebuilt from guest memory.**
`isRenderTarget` (which makes a resource immune to hash-driven re-creation) was set
at only 1 of the 7 `SetHostResource` exits in `CreateHostResource`. Disassembly
shows this title uses one of the six unmarked paths:

```
0014c3c0  call D3DDevice_CreateTexture2   ; Usage = X_D3DUSAGE_RENDERTARGET
0014c3cd  call D3DTexture_GetSurfaceLevel2
0014c23f  call &lt;engine SetRenderTarget wrapper&gt;
```

That path returns as soon as `GetSurfaceLevel` succeeds and never reaches the
marking code, so the RT was periodically destroyed and rebuilt from a hash of memory
the host never writes back - alternating rendered content with a stale CPU upload.
Fix: mark in `SetHostResource` itself, which every creation path funnels through.

Also noted: this engine registers the DEPTH buffer onto the COLOUR buffer's guest
`Data` pointer (`0x14c4bb`-`0x14c4d0`), so two live resources share one address. Any
address-keyed cache must expect that.

**NOT AN EMULATOR BUG - the world textures have no mips.** Every texture under
`data/textures/level_*` declares exactly ONE mip level (1774 of them; the ~1100
prop/character textures are properly mipped). The mip step was never run on the
level sets in this build. Un-mipped surfaces shimmer under camera motion, and far
worse at `RenderResolution` 3x than at 1x. **Test by running at 640x480:** if the
shimmer drops sharply it is aliasing, not conversion. A real fix would mean
generating mip chains at upload time.

**EXONERATED, with evidence - do not re-investigate:**

* *CPU back-buffer upload* - `uploads` frozen at 1015 across 4,620 gameplay frames,
  zero blits. (An RT-identity guard was added anyway: it had no check that the bound
  target was the back buffer, and this engine renders shadow maps to off-screen
  targets, where a StretchRect would succeed SILENTLY.)
* *Cache key / hash collisions / eviction* - the key is a structural identity tuple
  compared field-by-field; XXH3 is only the bucket function. No cap, no LRU.
* *Format conversion* - a static census of 3,078 texture descriptors from the
  bundles, replayed against Cxbx's arithmetic: swizzle offsets are a complete
  permutation for every size used, the mip walk is byte-exact for all 3,078, and
  every format has a correct host mapping. Zero cubemaps, zero volume textures.
* *Render states* - correct XB->PC conversion, `SetDirty()` after every Present, and
  a 64-bit sentinel that a 32-bit state cannot collide with.

### Mongo Valley (region_03): a LOADER wedge, not a crash

Measured, in order, each step killing a theory:

| theory | measurement | verdict |
|---|---|---|
| it crashes | no exception, process alive | wrong |
| bad level data | all six `.lvl`: magic `2BAD4700`, version 5, sane sizes | wrong |
| filename casing | only the unused `.tgl` sources differ; bundles are uniform | wrong |
| the whole emulator hangs | swaps advance at 40fps; **1 draw/frame** | wrong - render loop is FINE |
| slow loading | `FILEOPEN` count frozen 12+ s | wrong - stopped, not slow |
| dropped async I/O completion | `async=0 withEvent=0 apcDelivered=0` | wrong - all reads synchronous |

What IS established: the loader stops issuing file reads after `npc_15..20.smb`
while the renderer keeps drawing the loading screen, and **every thread in the
process is parked in a kernel wait** - 5 in `NtWaitForAlertByThreadId` (one lock),
6 in `NtDelayExecution`, 4 in `ZwWaitForSingleObject`. That is a deadlock.

Tooling added for it (reusable):
* a **stall watchdog** in `D3DDevice_Swap` with TWO modes - a render wedge (Swap
  stops) and a **loader wedge** (Swap keeps running while file reads stop). The
  first version only watched Swap and stayed silent through the exact bug it was
  written for, because Swap is healthy.
* on a wedge it enumerates every thread, samples EIP, and **walks each stack** past
  the ntdll frames to name the first non-system caller. EIP alone is useless - every
  blocked thread shows the same syscall stub.
* `tools/crashwatch.sh` preserves `diagnostics.txt` before the next launch truncates
  it.

Open: the stack walk currently attributes most frames to `cxbxr-ldr.exe`, because
the loader RESERVES THE WHOLE ADDRESS SPACE, so guest addresses resolve to it.
Filter to `cxbxr-emu.dll` frames, or treat a `cxbxr-ldr.exe+&lt;large offset&gt;` frame as
guest code, before reading the next sample.

### Mongo Valley wedge — located: five GUEST threads on one critical section

The stack walk finally named it. Five game threads, all parked in
`NtWaitForAlertByThreadId`, all returning to the SAME guest address:

```
thread 15156 / 54756 / 45932 / 14976   GUEST 0x000AC738
thread 45020                           GUEST 0x000AC73B
```

Guest code does not call Win32 locks; it calls Xbox kernel functions that Cxbx
emulates. Five guest threads blocked on one primitive is a deadlock on a single
critical section.

**The race, in upstream Cxbx** (`core/kernel/exports/EmuKrnlRtl.cpp`,
`RtlEnterCriticalSection`):

```cpp
if (CriticalSection->OwningThread != thread) {
    if (CriticalSection->OwningThread != nullptr) {
        KeWaitForSingleObject(...);          // wait
    }
    CriticalSection->OwningThread = thread;  // claims it WITHOUT re-checking
    CriticalSection->RecursionCount = 1;
}
```

Two defects in one block:

1. **No re-check after waking.** A correct implementation loops - wait, re-test,
   wait again. This one assumes that waking means the section is free.
2. **A window where the wait is skipped entirely.** If `OwningThread` reads as
   `nullptr` while another thread is between decrementing `LockCount` and clearing
   ownership in `RtlLeaveCriticalSection`, this thread takes the section without
   waiting at all.

Either path lets two threads believe they own the section. The bookkeeping
(`LockCount` / `RecursionCount` / `OwningThread`) then desynchronises and a later
waiter blocks on a section whose owner never signals. Region_01 completes; region_03
loads more assets across more threads and loses the race.

NOT YET PROVEN to be the cause - the code defect is real and the symptom matches,
but the two have not been tied together. Next step is a diagnostic, not a rewrite:
count `RtlEnterCriticalSection` waits and print any section waited on for more than
a second, with its `LockCount` / `RecursionCount` / `OwningThread`. That confirms or
kills it in one run. Fixing it means the standard re-check loop, which is a change
to a primitive every title depends on - so verify first.

**Guest addresses could not be symbolized:** `pdb_dump.ps1` and `pdb_resolve.ps1`
both fail on `SteefFinal.xbe` (`SymLoadModuleEx failed`), and the shipped symbol
cache holds only D3D/DSOUND entries, nothing near `0xAC738`. Worth fixing the
symbolizer - it is the difference between "a guest address" and a named engine
function.

**Critical sections RULED OUT.** With the timed re-check loop in place,
`RtlEnterCriticalSection` printed ZERO `CRITSEC:` lines while region_03 wedged. The
five threads are not blocked there. (The re-check loop is kept anyway - the original
single untimed wait with an unconditional ownership claim is genuinely wrong, it
just is not this bug.)

The guest return addresses shifted slightly between runs - `0x000AC738`, `0x000AC867`,
`0x000AC86B` - all within ~300 bytes, so it is one function region reached through
different call sites. `NtWaitForAlertByThreadId` is what SRWLOCK / std::mutex /
WaitOnAddress use, so the guest thread is blocked inside a HOST lock taken by some
Cxbx patch, not inside an Xbox kernel wait. Candidates, in order: the DirectSound
mutex (`DSoundMutexGuardLock`, taken by every DSound patch - and DSOUND.dll frames
appeared on two other blocked threads), the resource-cache mutex, and the file
system's handle lock.

NEXT STEP, and it is a small one: symbolize `0x000AC867`. Both PDB resolvers fail on
`SteefFinal.xbe` (`SymLoadModuleEx failed: 0`) and `pdb_dump.ps1 -Image <xbe>` throws,
so `xbe_symbolize.py` has no TSV to work from. Fixing that toolchain gap turns this
from "a guest address" into a named engine function in one step - and it will pay for
itself repeatedly, since every future guest-side stall lands in the same place.

Tally for this bug so far, all eliminated by measurement: crash, bad level data,
filename casing, whole-emulator hang, dropped async I/O, critical-section deadlock.
What survives: the loader stops while the renderer runs at 1 draw/frame, and the
blocked guest threads are waiting on a HOST lock held by emulator code.

### Symbolizer FIXED — guest addresses can be named again

`pdb_resolve.ps1` / `pdb_dump.ps1` both call `SymLoadModuleEx` on an IMAGE. That
works for a PE; the beta ships an XBE, which DbgHelp refuses
(`SymLoadModuleEx failed: 0`), and the `SteefFinal.exe` the PDB was built against
does not exist. So guest addresses could not be resolved at all.

**`tools\pdb_syms.ps1`** loads the **PDB directly** as the module image with an
explicit base and size, which DbgHelp accepts. It dumps `rva<TAB>name` or resolves
addresses inline:

```
powershell -File tools\pdb_syms.ps1 -Pdb "...\SteefFinal.pdb" -Out syms.tsv
```

43,321 symbols. **Delta verified as 0x10920** (guest VA = PDB RVA + delta), confirmed
against three independent known addresses from the symbol cache:

```
D3DDevice_SetVertexShader  pdbRVA 0x1F9470  xbeVA 0x209D90
D3DDevice_CreateTexture2   pdbRVA 0x1F7600  xbeVA 0x207F20
D3DDevice_SetRenderTarget  pdbRVA 0x1F4310  xbeVA 0x204C30
```

Dump committed at `swse/research/symbols/SteefFinal_syms.tsv`. Note
`xbe_symbolize.py` expects a different TSV shape and reports "before first symbol"
on this one - either adapt it or use `pdb_syms.ps1 -Rva`.

**CAVEAT ON THE STALL ADDRESSES - they are not trustworthy.** The stack walk in the
watchdog SCANS RAW STACK WORDS and treats any value that resolves inside a module as
a caller. That picks up stale leftovers as readily as real return addresses, and the
resolved names show it: `CastToSet + 0x1` and `_tls_end + 0x21709` are not plausible
return sites. The eight threads sharing `0xAC867` are more likely identical garbage
from a common thread-startup path than a real shared wait.

To get real callers this needs proper unwinding - `StackWalk64` with `SymFunctionTableAccess64`
/ `SymGetModuleBase64`, or frame-pointer chasing via EBP - rather than a word scan.
Until then, treat GUEST addresses from the watchdog as a hint, not evidence. The
solid facts about the wedge remain the ones listed above: the loader stops issuing
reads while the renderer runs at 1 draw/frame, and the blocked threads sit in
`NtWaitForAlertByThreadId`, which is a HOST lock, not an Xbox kernel wait.

---

## Mongo Valley: SOLVED — the engine's small-object arena is too small

**Symptom.** Mongo Valley (region_03) showed a black screen forever. Tutorial Town
(region_01) and Gizzard Gulch (region_02) loaded fine.

**Root cause.** Not a crash, not a hang, not missing data. The engine runs out of
pages in its small-object allocator while deserialising the level, fires its own
assertion, and parks in the halt handler *drawing the message to the screen every
frame*. The black screen is the assert screen.

    d:\Exoddus\Code\Engine\Core\SmallAllocator_FixedRestoring.cpp:711
    "Good Lord! Out of Pages in SmallAllocator_FixedRestoring!"

`SmallAllocator_FixedRestoring` serves every allocation of <= 256 bytes from a FIXED
4 MB arena carved into 1024 pages of 4 KB, allocated once at start-up (initialiser at
VA 0x00142EBF). It never grows. The function that looks like a grow path is a
*reclaim* pass: it walks the 15 bucket lists for a page whose live-object count
(page+0xFF4) is zero and returns that page to the free list. If no page is COMPLETELY
empty it returns empty-handed and the next allocation asserts.

**Measured, live from guest memory:**

| level | arena pages in use |
|---|---|
| menu | 104 |
| Mongo Valley, stock 1024-page arena | 1024 / 1024, pinned — assert |
| Mongo Valley, 4096-page arena | 3071, flat across 254 samples |

**Correction (2026-09-12): "3071" was an artefact.** The free-page walk in the
telemetry was capped at 1024 pages (correct for the stock arena) and, on the enlarged
4096-page arena, therefore always read "1025 free, 3071 in use" — the cap, not the
game. The "flat plateau" was the cap too. So what is established is: the stock 1024
run dry, 4096 do not (owner played the level), and the true high-water mark is
somewhere above 1024 and below 3071. The walk is now bounded by the arena's own page
count and the honest number goes here after a re-run. Lesson: a diagnostic's bound
must come from the thing being measured, or it reports its own limit as a result.

**Not about level size.** Gizzard Gulch is larger on every axis — 191 zonebundles to
139, 130 MB to 111 MB, 19,822 level objects to 12,826 — and loads. What matters is how
many small objects are live *simultaneously* during deserialisation, which is a
property of the object graph's shape, not its size.

**Fix.** `tools/patch_smallalloc.py` rewrites the four constants that define the arena
(size twice, last-page index, page count), enforcing size == pages * 0x1000 and
last == pages - 1. Applied at 4096 pages / 16 MB to both `Game/default.xbe` and
`Game/Final/SteefFinal.xbe` (the one the installer copies). Originals kept as
`*.presmallalloc`. `--revert` restores the stock 1024.

### How it was found, and what that cost

Six theories died on measurement before this one: physical memory exhaustion
(`mapFailures=0`, 76 MiB free), `NtReadFileScatter` being a stub that returns SUCCESS
without reading or signalling (a perfect fit for the symptom — but ordinal 220 is not
in the XBE's import table, checked before implementing), a corrupt or missing bundle
(`npc_0.smb` is byte-identical between a working level and this one), an exotic level
class (all five levels parsed: region_03 uses no class the working levels lack), scale
(Gizzard Gulch is bigger), and a duplicate-ID assert in the pointer table (the map is
keyed by pointer and inserts fine; the assert is one frame deeper, in the allocator).

**Three of my own diagnostics were the obstacle, not the bug:**

1. The stall watchdog deliberately **skipped the render thread**, on the assumption
   that a thread busy rendering cannot be the stuck one. The thread census disproved
   it: the renderer IS the title's main thread, and it is the thread that stopped
   queuing I/O. The watchdog was excluding the only thread that mattered.
2. Guest addresses printed as the literal string `"GUEST CODE (no host module)"` with
   **no address**, which cannot be symbolized. They now print as `GUEST 0x...`.
3. The EBP stack walk **invented three of its five guest frames**. Guest code here is
   partly FPO-compiled, so an EBP chain silently skips frames and resumes on whatever
   it lands on. I announced "the game asserted" off that walk, then had to withdraw it
   when disassembly showed 0xD6D8A cannot be a return address inside a function
   starting at 0xFD4BE. The assert turned out to be real — but the walk had not earned
   the claim.

The replacement is a scanner that keeps a stack slot **only if the instruction
immediately before it is a real CALL** (`E8 rel32` with an in-range target, plus the
indirect forms). It cannot fabricate a caller: a false positive has to be a stale
return address, which at least genuinely was one. It recovered the full chain
including the frame the EBP walk had skipped:

    CoreObjectUtil::WritePointer -> ClassFactory::WriteProduct
      -> ConcreteInstancedTag::Write -> MechDoor::IO -> Mechanical::IO
      -> SimpleAnimMixin::IO -> IOZ<AnimationControl>
      -> IOZPointerSelector<AnimationControl,CoreObject>::Smart -> IOZUtil::IOPtr
      -> CoreObjectUtil::WritePointer -> CoreObjectUtil::SetID
      -> map<CoreObject*,unsigned long>::operator[] -> _Rb_tree::_M_insert
      -> _M_create_node -> SmallAllocator_FixedRestoring::Allocate
      -> Assert::SystemHalted -> GameSystemHaltedHandler
      -> GameFont::ScreenSpacePrintRaw -> D3DDevice_SelectVertexShaderDirect

Two frames were then verified byte-for-byte against the call sites by disassembling
`WritePointer` (`call 0x168a93` returning to 0x168B55) and `SetID` (`call 0xfd4be`
returning to 0x168ADE).

**Rule earned.** A stack walk that cannot show its work is worse than none: it costs a
wrong conclusion on top of the time. Validate frames against actual call sites, and
print raw addresses so a claim can be checked against the binary.

**Also: the game was telling us the answer the whole time.** `LOG_UNIMPLEMENTED`,
`EmuLog` warnings and the engine's own assert text are all silenced in the shipped
configuration (`LoggedModules = 0x0`). The `"Out of physical memory!"` warning and this
assert string were both reachable from the start. Four separate times in this project a
silenced diagnostic hid the answer.

### Still open

- **Back-buffer upload gating.** `uploads` freezes while `swaps` keeps climbing: the
  upload is checksum-gated on the guest frame changing, but the host render target is
  cleared every frame, so once the guest image goes static nothing repaints it. This
  would black the window even with a good frame in guest memory. Needs a gate on
  "host RT invalidated" rather than "guest buffer changed".
- Whether Wolvark Docks, Sekto Springs and The De-Pantsing were hitting the same
  arena limit — untested, but they now have 4x the pages.

## 3D sound — SOLVED (owner-verified 2026-09-12)

**DRAFT.** Written before the combined build and the marked run; the coordinator
replaces this section with the verified result. Nothing below is confirmed at runtime
except where it says so.

### Diagnosis (converged, three independent lines)

Four independent investigations (`DS3D_CXBX_SINKS.md`, `DS3D_CALL_PATH.md`,
`DS3D_CALCULATOR_CONTRACT.md`, `DS3D_MATH.md`, summarised in `DS3D_RESOLVE_BRIEF.md`)
arrived at the same place. Positional effects are silent because of ONE sink in the
emulator, reached by ONE trigger from the title's XACT layer:

1. XACT creates a positional effect as an ordinary *track* voice (mixbins `{0,1}`, not
   CTRL3D) and a parent *source* voice (a MIXIN submix, flag 0x2000).
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
which is FULL volume. They are why nothing is *positioned*. (This refines the "3D audio
gap" section above: that gap is real, but it explains the missing position, not the
silence.)

Runtime evidence so far, from the instrumentation already in the tree (`DSMIX:`,
`DSPLAY:`, `DS3D:` lines in `oddbeta/diagnostics.txt`, which is overwritten on every
launch): both sessions seen while writing this were menu-only with no Play, so the sink
itself is not yet observed. The trigger side is: the first session (stubs in place)
showed 40 `DS3D: Use3DVoiceData ... arg 00000001` at source creation and 40
`DS3D: Set3DVoiceData ... = 00000000 ×8` (all-zero voice data); the second, already
carrying Part A's receiver, showed `SetOutputBuffer buffer X parent 00000000`,
`Use3DVoiceData ... use=1` and `Set3DVoiceData ... mask=0x00 ... use=1` for the same
sources and no `Calculate3D a1=` / `GetVoiceData a1=` stub lines at all — the
calculator running natively, with a zero mask that is consistent with every source and
the listener sitting at the origin (the calculator sets a bit only when a value differs
from the zeroed initial data). Expected for a footstep once a level is played, BEFORE
Part B lands: `DSMIX: SetMixBins ... table={10:0 3:0}`, then `DSMIX: SetMixBinVolumes
... -> maxVolume=-10000`, then `DSPLAY: ... hostVol=-10000`. If the marked log
contradicts that, the diagnosis is wrong and the fix below is aimed at the wrong sink
— stop there.

### The fix, in three disjoint parts

- **Part A — the Xbox's own calculator runs natively; the emulator receives its
  output.** Remove exactly three `PATCH_ENTRY` lines from `Patches.cpp`:
  `CDirectSound3DCalculator_Calculate3D`, `CDirectSound3DCalculator_GetVoiceData`,
  `DirectSoundUseLightHRTF` (the calculator is pure CPU code inside the XBE; its only
  externals, `KeSave/RestoreFloatingPointState`, return success in Cxbx). Implement the
  receiver: `IDirectSoundBuffer_Set3DVoiceData` / `Use3DVoiceData` merge the 0x38-byte
  `X_DS3DCALCVOICEDATA` per change bit into a per-voice `Cxbxr3DVoiceState`
  (`DirectSound3DVoice.hpp/.cpp`) and map it to the host: volume offset =
  clamp(distance + cone + direct, -10000, 0); pan from `flFIRFilterAzimuth`
  (`sin(az) × 10000 × k`, k = 0.5 to start, one named constant); pitch delta =
  `lDopplerPitch` (1/4096 octave). `IDirectSoundBuffer_SetOutputBuffer` records the
  parent, and a parent's pan is propagated to its children (XACT gives the parent only
  the panning bits and the child only distance/cone/I3DL2/doppler).
- **Part B — the fold stops muting; the host volume/pan path learns about 3D.**
  `HybridDirectSoundBuffer_SetMixBinVolumes_8` scores `{0,1,2,4,5}` and the 3D speaker
  feeds `{6,7,8,9}`, never 3 (LFE), 10 (I3DL2 send) or 11+; a table with no scored bin
  yields `maxVolume = 0` — it holds only send levels, and the dry level is defined by
  the parent's 3D data. `HybridDirectSoundBuffer_SetVolume` / `SetPitch` gain defaulted
  `lVolume3DMb` / `lPitchDelta3D`; new `HybridDirectSoundBuffer_SetPan3D` guards the
  host `SetPan` (needs `DSBCAPS_CTRLPAN`, which cannot coexist with `CTRL3D`). The
  hybrid buffer and stream structs gain `Xb_3D` and `Xb_OutputParent`.
- **Part C — streams, tooling, this record.** `DirectSoundStream.cpp` gets the same
  receiver for streamed voices (`IDirectSoundStream_Set3DVoiceData` /
  `Use3DVoiceData`, `CDirectSoundStream_SetOutputBuffer`), taking its pan from the
  parent buffer because a routed track never receives an azimuth of its own. Strictly a
  no-op until `Use3DVoiceData(TRUE)`: music streams start and stay with no volume
  offset, no pan and no pitch delta. `tools/ds3d_trail.py` turns `diagnostics.txt` into
  one block per voice per MARK window (SetMixBins table, fold result, 3D fields, DSPLAY
  read-back) with an `AUDIBLE` / `MUTED(n)` verdict per window.

### What to check (the marked run)

NUMPAD1 before and after three footsteps, NUMPAD2 around one attack, NUMPAD3 around a
moolah pickup; then `python tools/ds3d_trail.py`.

- [ ] Footsteps and attacks audible. Music unchanged — the stream path is the one most
      likely to regress; any change in music volume or pitch is a Part C bug.
- [ ] `DSPLAY: ... hostVol` for the footstep/attack voices `> -6400` (window verdict
      `AUDIBLE`).
- [ ] `DSMIX: SetMixBinVolumes in={3:.. 10:..} table={10:.. 3:..} -> maxVolume=0`
      (fold fixed), not `-10000`.
- [ ] `DS3D:` lines carry a non-zero change mask and distance/azimuth values that change
      as the player moves relative to an NPC — the calculator running natively. No
      `DS3D: Calculate3D a1=` / `GetVoiceData a1=` stub lines at all (the trail tool
      counts them per window; a non-zero count means the Part A patches are still in).
- [ ] Pan follows position: an NPC on the left is heard on the left. Too weak or too
      strong: adjust the single named constant k in `DirectSound3DVoice.cpp`.
- [ ] No `DS3D: stream` lines while only music plays. If positional streamed sounds
      exist (ambience), their trail shows `parent=<mixin buffer>` and a non-zero pan.
- [ ] Moolah pickup and UI sounds still play (2D path untouched).
- [ ] No `Return result report` popup from the host `SetPan` (CTRLPAN must be on the
      host buffer, never together with CTRL3D).

### Verified

Owner-confirmed after the marked run: footsteps and attacks audible, positioned,
music unchanged. Log confirmation on a stock XBE: `DSMIX: ... scored=0
fold=sends-only->0` for the `{10,3}` track voices where the old fold produced
`maxVolume=-10000`; `DSPLAY: ... hostVol` near 0 at Play; `HLE: CDirectSound3DCalculator_Calculate3D:
No patch registered ... running unpatched` — the Xbox's own light-HRTF calculator is
producing the voice data. Five investigations converged on one sink and one trigger;
the method that found it was the code audit naming the sink with file:line plus the
static call graph naming the trigger, both read-only, and the confirming measurement
was reading host state BACK at Play rather than trusting what the emulator believed it
had set.

Release: the diagnostic keys (NUMPAD 1-5, F4) are removed; the Mongo Valley arena fix
now lives in the emulator (`CxbxrKrnlApplyTitlePatches`, applied in guest memory at
load, fingerprinted to this title) so a recipient's pristine XBE gets it; both dev XBEs
reverted to stock. Package: `C:\OddBetaDist\StrangersWrathBeta.zip` / `.7z`.
