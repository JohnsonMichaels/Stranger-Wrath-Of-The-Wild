# The May 2004 Xbox beta ("Steef")

Source: `Beta.rar` (2.1 GB) supplied by the owner; extracted to
`C:\Users\<you>\SWBeta\Game` (3.92 GB, 11,757 files).

Extraction note: Windows' bsdtar SILENTLY LOSES the archive - it stops at an
unsupported RAR filter after 54 files while still exiting "done". AMD's driver
suite bundles a full 7-Zip (`C:\Program Files\AMD\CNext\CNext\7z.exe`, with the
codec DLL) which extracts everything. Unity's bundled `7z.exe` has no `7z.dll`
and cannot read RAR at all.

## What it is

**An original-Xbox devkit build, not a PC game.** Every "exe" is an Xbox
PE image:

| build | link date | notes |
|---|---|---|
| `Debug\SteefDebug.exe` | 2004-04-29 | + 63 MB PDB |
| `Final\SteefFinal.exe` | 2004-05-22 | + 25 MB PDB |
| `Release\Steef.exe` | 2004-05-22 | + 46 MB PDB |
| `ReleaseXACT\SteefXACT.exe` | 2004-05-22 | + 47 MB PDB |

Evidence: PE subsystem 14 (`IMAGE_SUBSYSTEM_XBOX`), imports `xboxkrnl.exe`
(161 ordinals) and `xbdm.dll` (7 ordinals - notification tier only), statically
linked Xbox sections (`D3D`, `XGRPH`, `XACTENG`, `BINK*`, `DOLBY`, `.XBLD`).
Each folder also carries the converted `.xbe`; all three XBEs use the DEBUG
entry-point key and a live non-kernel import table -> **devkit XBEs**, which a
retail-emulating setup will not run unpatched.

The filesystem dates say 2000; the LINK timestamps say May 2004. Trust the
linker - "Steef" was the project codename and this is Stranger's Wrath eight
months before its January 2005 release.

**The PDBs are the prize.** Full symbols for the same engine family the HD
remaster ports. Cross-referencing `Steef.pdb` names against `stranger.exe` can
name functions in `ALL_FUNCTIONS.tsv` that we have been reversing blind.

## Beta-only content (folder census vs HD)

Characters that exist here and not in the HD install: `GutLips` (73 files),
`narc` (59), `outhouse_bandit` (28), `scubatoad`/`scubatoadNative`/
`scubatoadRebel` (51), `sektoLightArmor`/`sektoFullArmor`, `wolvarkleader`,
`townsfolkDusty`, `gibs`, plus dev stand-ins `test_ajb`, `test_dummy`,
`wolvarkTest`, and `geometry/work/charlesNutz.geo`.

Minion identity notes ship as text files: minionA "Known As Shooter",
minionB "Known as Mortar", minionC "Known as Nailer".

Levels: regions 01-05 only (no 00/06 yet), each in BOTH pre-lightmap
(`level_XX.lvl`) and lightmapped (`lm_level_XX.lvl`) form - the HD ships only
`lm_`. Plus the dev test level `WORK\test_cb\test_aaron_2.lvl`.

## test_aaron_2 = the Steef-boat test level

Its tag bundle carries the whole boat kit: `SteefBoatRow{Left,Right,IdleLeft,
IdleRight}.gr2`, `steefboat.geo`, `boatcover.geo`, `steefboat_turret.geo` with
`activate/fire/deactivated` anims, `WaterMarkers.tga`, `star.geo` collectables.
The `.lvl` object graph lists a water mesh (`test2_Watermesh_1`), 15
collectables, 5 spawn points (one prefixed `aaa_` to win a sort), 12 PipeTags -
and `test2_Tag_Decorator_couch1` (`couch.geo`, textured `puke_couch_01.bmp`).

## Format distance from HD, measured

| container | beta | HD | note |
|---|---|---|---|
| `.smb` | magic `3A4B5C6D`, mostly **v5** (CORRECTED - see below) | same magic **v5** | level bundles (tag, zone) pass the v5 structural invariant `A+C+D == size` UNCHANGED; the GUI/system bundles (AppGlobal, *_swf, prefs) do NOT - different sub-layout |

**Correction (2026-08-03):** this table previously said the beta's `.smb` files are
v4. A version count over the whole beta tree gives **2031 v5, 19 v4, 34 v1** - v5 is
the norm, not the exception.

### Beta `.smb` record layout - fully decoded and cross-checked

Everything lives in the header block (`A` = filesize, `C` = `D` = 0). After the
`BEEF1234` sentinel come the entries:

```
entry  = u32 0x4DFAA77E   u32 7   u32 entryId
         u32 0   u32 0   u32 payloadSize
         u32 pathLen + path        e.g. "\data\prefs\Effects\ParticleSystemDef\walk_metal_dust.txt"
         u32 clsLen  + class       e.g. "class ParticleSystemDef"
         record*                   terminated by a u32 0
record = u32 size (INCLUDING this field)   u32 tag_hash(fieldName)   u32 typeHash   payload[size-12]
```

* `entryId == tag_hash(path)` - 245 of 254 exact in `global_prefs.smb`, and the ids
  ascend strictly, so it is a binary-search key. **This independently confirms the
  recovered hash in `tools\swse_hash.py` is correct.**
* Field names use the **`tag_hash`** variant (UPPERCASE + trailing length byte):
  388 / 388 resolved against strings from the shipped exe. `name_hash` and
  `path_hash` resolve 0 / 388.
* Type hashes seen: `0xF866FCB4` bool (1 byte, `00`/`01`, whole record 13 bytes),
  `0xEA9BCD54` float (4B), `0x3CA53410` Vec3 (12B), `0x017E87A7` RGBA (16B),
  `0x3D0D975A` string (variable).
* **No checksum anywhere** - not in the container header, not per entry. The only
  integrity fields are sizes (`A`/`B`, each record's leading `size`, each entry's
  `payloadSize = 4+clsLen + sum(record sizes) + 4`). A same-length in-place byte
  edit invalidates nothing, so single-value patching is safe.

Validated against ground truth: every entry in `global_prefs.smb` also exists as a
loose `.txt`, giving **244 of 254** entries to check. Result: **1038 / 1038 booleans
matched** across 117 distinct class+field pairs in 15 classes, and 2749 / 2750
floats (the one miss was a 1-ULP artifact of the text parser, not the decoder).
Parser lands exactly on `B` after all 254 entries with no slack.

Tools: `tools\smb_entries.py` (parser), `tools\smb_xcheck.py` (compiled-vs-loose
validator).

**`0x4DFAA77E` is a CONSTANT, not a timestamp.** `FORMAT.md` reads it as a June-2011
build time; it is byte-identical in this 2004 data, so that reading is wrong.
| `.lvl` | magic `2BAD4700` **v5** | same magic **v6** | object graph inside; also an inner field 1 vs 2 |

Xbox and PC are both little-endian x86 - no byte-order wall anywhere.

## The hybrid experiment: HD engine, beta geometry - WORKS

1. Beta level copied verbatim, warped -> loader hangs on the loading screen
   (alive, console dead to warps: "could not hook ctx vtable").
2. Version bytes bumped (smb 4->5, lvl 5->6) -> same hang. The wrapper is the
   gate, not just the number.
3. **Hybrid**: HD `utility\empty.lvl` + `empty_blockmap.smh` kept as the
   wrapper; beta `test_aaron_2_tag.smb` -> renamed `empty_tag.smb`, beta
   `zonebundle_0.smb` alongside, versions bumped, all under
   `data\bundles\utility2\`. **Loads and plays.**

Proof it is beta geometry and not the HD level: harvest census, same wrapper,
same spot -

| | HD empty | hybrid |
|---|---|---|
| world draws with extent | 4 | 3 |
| grid-area draws | 3 | **2** |

test_aaron_2 has exactly TWO area meshes (`test_aaron_2_area_0_0/0_1.geo`).
The count matches the beta manifest, not the HD level.

**Limit found:** the couch, water, collectables and spawn points did NOT
appear, because the object graph lives in the `.lvl` we replaced with HD's.
The v5 -> v6 `.lvl` delta is the gate to whole beta levels.

Operational note: warping into these hacked levels works on a FRESH boot;
the second warp of a session tends to hang the loader. Reboot between
experiments.

## Cxbx-Reloaded attempt - how far it gets, and exactly what stops it

Installed CI-585c49a (Apr 2026, 4.6 MB) to `C:\Users\<you>\SWBeta\cxbx`,
portable mode. Logging must be switched on in `settings.ini`:
`KrnlDebugMode = 0x2` plus a `KrnlDebugLogFile` path (0x1 is console-only and
writes nothing).

**The Xbox reads its data from `D:\`, which Cxbx maps to the XBE's own folder.**
The builds live in `Game\Release\` etc. while `data\` sits in `Game\`, so an
XBE run in place finds no data. Copy the chosen XBE to `Game\default.xbe`.

### The library table is the whole story

| build | graphics lib | result |
|---|---|---|
| Release / Final / ReleaseXACT | **`D3D8LTCG`** (XDK 5849) | dies during init, never reaches the entry point |
| Debug | **`D3D8D`** (XDK 5788) | **reaches the XBE entry point and runs engine code** |

`D3D8LTCG` is link-time-code-generation Direct3D: calls are inlined and
mangled, which defeats the symbol scanning Cxbx relies on to hook graphics. It
is Cxbx's worst-supported case. The Debug build links plain `D3D8D`, so it is
the only one worth pursuing. XDK build numbers also pin the SDK: **5849** for
the May 2004 builds, **5788** for the April Debug build.

### The exact blocker

```
DEBUG_PRINT: --- ASSERT ---
d:\exoddus\code\engine\debugport\debugport.cpp (346) :
    DmOpenNotificationSession(DM_PERSISTENT, &m_session) == XBDM_NOERR
[MAIN] Received Breakpoint Exception (int 3)
```

A devkit build demanding the debug monitor that Cxbx does not implement. Note
the source path: the engine codebase was internally called **exoddus**, carried
over from the Abe games.

Disassembled at `0xc149f3`:

```
  0xc149f6  ff 15 4c3f6301    call [DmOpenNotificationSession]
  0xc149fc  3d 0000db02       cmp  eax, 0x02DB0000      ; XBDM_NOERR
  0xc14a01  74 23             je   0xc14a23             ; success -> skip
  0xc14a03  0f b6 05 50ca5201 movzx eax,[0x0152ca50]    ; asserts-enabled flag
  0xc14a0a  85 c0 / 74 18     test/je -> 0xc14a23
  0xc14a0c  push msg, push 346, push file, call assert
  0xc14a22  cc                int 3                     ; the kill
```

### Why patching it did NOT work (yet)

Three attempts, all blocked by XBE integrity, which is worth recording so it is
not retried blindly:

1. **Clear the global assert flag** (`.data` byte 1 -> 0) - loads only with
   `IgnoreInvalidXbeSec = true`, and in that mode Cxbx dies EARLIER than the
   unpatched build (14 KB of log, mid rdtsc-scan, never reaching the entry
   point). The bypass changes section handling.
2. **`je` -> `jmp` at 0xc14a01** plus a recomputed SHA-1 section digest -
   still rejected with verification on, because the XBE ALSO carries a header
   signature covering the section headers. Recomputing one digest is not
   enough.
3. Section preload flags - already set; not the issue.

So the honest state: **the unpatched Debug XBE gets furthest** and is what
`Game\default.xbe` currently holds. Getting past the assert needs either a
full XBE re-sign (header signature + all section digests), or Cxbx built with
an xbdm stub that returns `XBDM_NOERR`, or its debugger frontend
(`cxbxr-debugger.exe`, shipped) attached to step over the `int 3`.

## The fork: building our own runtime

Cxbx-Reloaded is GPL-2.0, so the practical route to a standalone is to fork it,
fix what this title needs, and ship our own executable rather than reimplement
an Xbox translation layer from scratch.

**The toolchain was already on the machine**: git, VS 18 Community with the C++
workload, and - not obvious - CMake 4.2.3 plus Ninja bundled INSIDE Visual
Studio at
`Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe`.

```
source : C:\Users\<you>\SWBeta\src\Cxbx-Reloaded   (19 submodules)
build  : cmake -S . -B build -G "Visual Studio 18 2026" -A Win32
         cmake --build build --config Release --parallel
run    : C:\Users\<you>\SWBeta\oddbeta\            (flattened, + our SymbolCache)
```

### Why a DEBUG build is both the only option and the hardest one

Release/Final/ReleaseXACT link `D3D8LTCG`, whose inlined and mangled calls
defeat Cxbx's symbol scanning outright. Only the Debug build links plain
`D3D8D`. But a debug devkit build violates four assumptions Cxbx is built on:

| retail titles | this beta |
|---|---|
| link retail libraries (`D3D8`) | link **debug** libraries (`D3D8D`) |
| never call the debug monitor | calls `xbdm` from 12 sites |
| assertions compiled out | **asserts on everything**, including emulator inaccuracy |
| Cxbx's OOVPA database matches them | built from retail patterns, cannot match debug code |

The asserts turn out to be an ADVANTAGE. A retail title fails silently; this one
prints `device.cpp (2598) : pResource->IsBusy() == FALSE` - file, line and
condition - so every fix has been surgical instead of guesswork.

### Fixes made, in the order they were needed

1. **Forced software rendering** - `Intercept.cpp:306` sets `bLLE_GPU` when the
   library scan reports neither `D3D8` nor `D3D8LTCG`, and
   `XbSDB_LibraryToFlag` compares 8 bytes against the retail literals, so
   `"D3D8D"` fails at byte 5. Mapped the six debug names onto retail flags in
   `libXbSymbolDatabase.c` - note `XACTENID`, which is irregular rather than
   `XACTENGD`. **HLE patches 137 -> 224; hardware D3D initialises; a render
   window exists for the first time.** Editing the XBE's library table to say
   `D3D8` instead does NOT work - the decision lives in Cxbx.

2. **`D3D_g_DeferredTextureState` missing** - `TextureStates.cpp:84` aborts
   without it, and no such public symbol exists in the PDBs (Cxbx normally
   recovers it by pattern-scanning retail D3D8). Recovered by disassembly:
   `D3DDevice_SetTextureState_TexCoordIndex` ends
   `mov ecx,ebx / shl ecx,7 / mov [ecx+0x012EDA00],edi`, and ColorKeyColor
   stores to `[esi+0x012EDA08]` with the same `stage<<7` - so base
   **`0x012EDA00`**, 128-byte stride (32 states x 4 bytes).

3. **GPU fences were `// TODO` stubs.** The real contract, disassembled from the
   title's own XDK copy:
   ```
   D3D::SetFence(Flags)     -> returns D3D__Device.m_Fence, then m_Fence += 2  (odd)
   D3DResource_IsBusy(pRes) -> pRes->Lock != 0 &&
                               (m_Fence - pRes->Lock) < (m_Fence - *m_pGpuFence)
   ```
   **`D3DResource_IsBusy` is not patched at all** - the engine reaches it inline
   and reads `m_Fence` at `D3D__Device+0x2C` and a POINTER to the GPU-written
   fence at `+0x30` directly, so faking `InsertFence`'s return value could never
   have worked. Measured: `m_Fence`=7, GPU fence frozen at 3, stuck resource
   `Lock`=7. Fixed by keeping the title's own fields consistent, validated
   before writing and degrading gracefully on mismatch.

4. **Infinite recursion in `GetBackBuffer`.** The Xbox `GetBackBuffer` is a thin
   wrapper that tail-calls `GetBackBuffer2`; Cxbx patches BOTH, so running the
   `GetBackBuffer` trampoline re-enters the patched `GetBackBuffer2` and lands
   back in `CxbxrImpl_GetBackBuffer2`. ~680 bytes of stack per cycle until the
   1 MB stack dies. Fixed by preferring the `GetBackBuffer2` trampoline.

Also fixed along the way: `EmuTryHandleException` was itself faulting on a
`*(uint16_t*)(Eip-2)` peek and destroying crash reports, and the `int 2Dh`
DEBUG_PRINT used `%s` on Xbox counted strings that are not NUL-terminated.

### Where it stands

Boots, initialises hardware D3D, streams resources, issues draw calls and
**presents 2 frames** - then traps in a loop of 134 `sptr.h : m_ptr` (null
resource) and 134 `gamefont.cpp : s_bInsideBeginEnd` asserts. Log grew from
99 KB to 306 KB across these fixes.

### What the stub audit found next

See CXBX_STUB_AUDIT.md. The load-bearing discovery: the engine reads raw GPU
FIFO pointers out of the device struct at `s_pD3DDevice + 0x00/0x24/0x28` and
asserts on them four times in `Renderer::AppInit` and **twice every frame in
`Renderer::Swap`** - so fence fields alone are not enough, three more must stay
self-consistent. Plus: the `GetBackBuffer` recursion has untouched siblings in
`GetRenderTarget`/`GetRenderTarget2` and
`GetDepthStencilSurface`/`GetDepthStencilSurface2`; the push-buffer group
(`D3D_KickOffAndWaitForIdle`, `D3DDevice_KickPushBuffer`,
`D3D_MakeRequestedSpace`) spins forever because the FIFO GET pointer never
advances under HLE; and `EmuInstallPatch` fails SILENTLY on a table miss, which
is why `D3DDevice_BeginPush` is unpatched while `EndPush` is patched.

## Routes to "playable", assessed

1. **Beta content inside the HD game (main line).** Proven for geometry
   tonight. Ladder: level bundles (done) -> assets via OddForge repack ->
   spawnable beta characters (spawn hook + hash table already exist). Risks:
   Xbox texture swizzle, 2004 Granny vs HD Granny runtime. Everything reuses
   SWSE/OddForge/Oddview tooling.
2. **Cxbx-Reloaded** (owner approved the download): HLE-run the devkit XBE on
   Windows; reference footage for how the beta actually behaves. Only 7 xbdm
   imports to stub if its loader balks.
3. **xemu**: parked - owner has no BIOS/MCPX dumps, and these are devkit XBEs
   (would need retail-patching: entry/thunk re-key + xbdm strip).
4. **Native port**: no source in the archive; a bespoke translation layer is
   possible-with-PDBs but a multi-month project. Not the near-term path.
5. **XDK 2004**: not on the machine; it is Microsoft proprietary and will not
   be downloaded by us. If the owner sources it, devkit routes open.
