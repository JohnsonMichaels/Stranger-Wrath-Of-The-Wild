# Running the 2004 beta on PC — status and road to playable

Living status document. See BETA_BUILD.md for what the archive is,
CXBX_STUB_AUDIT.md for the emulator gap analysis, and BETA_PC_PLAN.md for the
strategy that led here.

---

## Where we are in one line

The beta boots under our own fork of Cxbx-Reloaded with hardware Direct3D
active and no assertion failures, and dies part-way through renderer start-up
on a small, enumerable set of LTCG calling-convention mismatches. **It does not
render yet.**

## The single most important discovery

**We spent hours debugging the wrong executable.**

The archive ships four builds. We picked `Debug` at the start because it was the
only one that got past init, and that choice quietly poisoned everything after.

| build | link date | graphics lib | asserts |
|---|---|---|---|
| `Debug\SteefDebug.exe` | **2004-04-29** | `D3D8D` | compiled IN |
| `Release\Steef.exe` | 2004-05-22 | `D3D8I` | out |
| `Final\SteefFinal.exe` | 2004-05-22 | `D3D8LTCG` | out |
| `ReleaseXACT\SteefXACT.exe` | 2004-05-22 | - | out |

**The data is from May; the Debug build is from April.** The game says so
itself in `Game.log`:

```
eApp | Build: debug : Apr 29 2004, 05:57:40, XTL ver = 5788
eApp | Build: Change 46300 on 2004/04/28 by mlee@mlee 'Up the max size of the queue.'
*** WARNING *** Unrecognized variable name (m_audioMasterVolume) in GamePrefs.txt
*** WARNING *** Unrecognized variable name (m_audioMusicVolume)
*** WARNING *** Unrecognized variable name (m_I3DL2Enabled)
Encountered unknown var during load (trying m_gibOnKill)! in global_prefs.smb
```

The data carries audio settings the April exe has never heard of, so
`audio_master.clr` fails its `0xdeadbeef` magic check, `SentenceMgr::AppInit`
then walks lip-sync data that was never loaded, reads a garbage count of ~78.7
million, and asks for **2 GB**. That OOM triggers `Assert::SystemHalted`, whose
halt screen cannot draw because fonts are not loaded that early - producing the
endless `sptr.h(114) : m_ptr` + `gamefont.cpp` loop we chased for hours.

**Those asserts were the error message, not the error.** Decoding the backtrace
against the PDB is what settled it:

```
Game::AppInit -> Singletons::AppInit -> GameAudioMgr::AppInit
  -> SentenceMgr::AppInit -> operator new[](2,002,321,108)
    -> OutOfMemory -> Assert::SystemHalted
      -> GameFont::Printf -> SPtr<FontResource> (null)
```

The April build cannot be made to work against May data. **Final and Release are
the real targets.**

## Fixes made to the fork (all root-caused, none worked around)

1. **Debug-library names were unrecognised.** `Intercept.cpp:306` forces
   software rendering unless the scan reports `D3D8`/`D3D8LTCG`, and
   `XbSDB_LibraryToFlag` compares 8 bytes against the retail literals - so
   `D3D8D` fails at byte 5, and later `D3D8I` (Release) failed the same way.
   Mapped the debug/instrumented names onto retail flags. Note `XACTENID` is
   irregular rather than `XACTENGD`.
   *Result: HLE patches 137 -> 224; hardware D3D initialises.*

2. **`D3D_g_DeferredTextureState` has no public symbol in any PDB.** Cxbx
   normally recovers it by pattern-scanning retail D3D8. Recovered per build by
   disassembly instead - `SetTextureState_TexCoordIndex` ends
   `shl reg,7 / mov [reg+BASE],val`, and `ColorKeyColor` writes `BASE+8` with
   the same stride, which cross-checks the answer:
   - Debug `0x012EDA00`, Release `0x0056A2C0` (derived twice independently,
     agreeing), Final `0x00215798`.

3. **GPU fences were `// TODO` stubs.** Disassembling the title's own XDK copy
   gave the real contract:
   ```
   D3D::SetFence           -> returns D3D__Device.m_Fence, then m_Fence += 2 (odd)
   D3DResource_IsBusy(pRes)-> pRes->Lock != 0 &&
                              (m_Fence - pRes->Lock) < (m_Fence - *m_pGpuFence)
   ```
   `D3DResource_IsBusy` is **not patched** - the engine reaches it inline and
   reads `m_Fence` at `D3D__Device+0x2C` and a pointer to the GPU fence at
   `+0x30` directly, so faking `InsertFence`'s return could never have worked.
   Measured: `m_Fence`=7, GPU fence frozen at 3, stuck resource `Lock`=7.

4. **Infinite recursion in `GetBackBuffer`.** The Xbox wrapper tail-calls
   `GetBackBuffer2`; Cxbx patches both, so the trampoline re-entered the patch.
   ~680 bytes of stack per cycle until the 1 MB stack died.

5. **`EmuInstallPatch` failed SILENTLY** on a table miss. Now logs the symbol -
   which exposed **407 unpatched symbols** and one genuine mismatched pair
   (`BeginPush` unpatched while `EndPush` was patched).

6. **Push-buffer group patched** (`KickOffAndWaitForIdle`, `KickPushBuffer`);
   `MakeRequestedSpace` deliberately NOT patched - it returns the live FIFO
   write cursor that the title's unpatched `SetRenderState_*` family writes
   through, so a wrong pointer there is the most dangerous change available.

7. **PDB-derived symbol caches.** Cxbx's byte-pattern database is built from
   retail libraries and cannot match these builds. We extract from the beta's
   own PDBs via DbgHelp; `XBE_VA = PDB_RVA + delta`. Validated against Cxbx's
   own independent scan: **372 exact matches** on Release, 290 on Final.

Also fixed: the exception handler faulted on its own `Eip-2` peek and destroyed
crash reports; `DEBUG_PRINT` used `%s` on Xbox counted strings that are not
NUL-terminated.

## Two mistakes worth recording

**Merging PDB symbols into an LTCG title made things worse.** My merge added
plain names like `D3DDevice_SetTransform` that shadowed Cxbx's correct
`__LTCG_eax1_edx2` variants, so Cxbx patched the stack-based form while the
title passes arguments in registers - producing
`Unknown Transform State Type (2199536)`, where the value is an *address*
(`0x218FF0`) rather than an enum. Pre-merge caches are kept as `.prePDB`.

**A metric I wrote reported progress that did not exist.** Counting bare API
names across the whole log scored the `SymbolCache:`/`HLE: ... Patched`
inventory lines as if they were calls, so `betacheck` claimed "2 Swap calls /
6 draw calls" for runs that never presented a frame. Fixed to exclude those
lines. Real counts are still **0**.

## Current failure

Final and Release now reach the renderer with hardware D3D, zero asserts, and
real NV2A command traffic, then stop on LTCG calling-convention mismatches:

```
LOG_TEST_CASE: Unassigned Xbox vertex shader!
LOG_TEST_CASE: Xbox should always have a VertexShader set (even for FVF's)
```

The remaining variants are enumerable - these are the ones Cxbx's scan knows
that a plain PDB extraction does not produce:

```
D3DDevice_SetTransform_0__LTCG_eax1_edx2
D3DDevice_LoadVertexShader_4__LTCG_eax1
D3DDevice_SelectVertexShader_0__LTCG_eax1_ebx2
D3DDevice_GetViewportOffsetAndScale_0__LTCG_edx1_ecx2
CDevice_SetStateUP_0__LTCG_esi1
CDevice_InitializeFrameBuffers_4__LTCG_edi1
CDevice_SetStateVB_8
CDevice_FreeFrameBuffers_4
```

## Road to playable

| step | state |
|---|---|
| Build the fork from source | done |
| Hardware D3D instead of software fallback | done |
| Boot without halting | done for the May builds |
| **LTCG calling-convention variants** | **in progress - current blocker** |
| First presented frame | not yet |
| Menus / UI | not yet |
| In-game rendering | not yet |
| Playable frame rate | unknown; retail-style builds should be far lighter than the debug one |
| xbdm fixes in source, not XBE patches | task #22 - lets the installer use Beta.rar unmodified |
| Standalone packaging | `tools\make_oddbeta.ps1` written, waiting on a build that renders |

**Input is already wired** - all seven `XInput*` functions patched and
`xinput1_4.dll` loaded, so a controller should work once there is a picture.

## Tools built for this

- `tools\betacheck.ps1` - one command, reports the numbers that actually track
  progress (patches, real Swap/draw calls, asserts, whether the *render* window
  is visible, whether the software fallback engaged)
- `tools\make_oddbeta.ps1` - packages Beta.rar + fork into a standalone folder
- `tools\pdb_dump.ps1`, `make_symbol_cache.py`, `pdb_to_cxbx.py` - PDB to
  SymbolCache pipeline
- `tools\xbe_xref.py` and friends - call-site analysis that resolves MSVC
  incremental-link thunks, without which every function looks uncalled

## Honest assessment

Every blocker so far has been a gap in an emulator that had never been handed a
devkit build - not an architectural impossibility - and each was diagnosable to
a specific line. The LTCG convention problem is the first that is *inherent* to
the build rather than an oversight, but it is bounded: a known list of entry
points needing the register-passing variant rather than the stack-based one.

The risk that would change this assessment is a failure we cannot diagnose. We
have not hit one; the game's own assertions and its PDBs have answered every
question so far.
