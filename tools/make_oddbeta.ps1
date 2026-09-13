# make_oddbeta.ps1 - build a standalone, ready-to-run Stranger's Wrath 2004 beta
# from the original Beta.rar plus our Cxbx-Reloaded fork.
#
# The point of this script: the beta is an original-Xbox devkit build. It was
# never a PC game and cannot be made into one without a translation layer. What
# it CAN be is a self-contained folder holding that layer, pre-configured, with
# one executable to double-click - no emulator install, no settings to find, no
# game picker.
#
#   powershell -ExecutionPolicy Bypass -File tools\make_oddbeta.ps1 `
#       -Rar "C:\path\to\Beta.rar" -Out "C:\OddBeta"
#
# Idempotent: re-running skips extraction if the game data is already there.

param(
    [string]$Rar   = "C:\Users\<you>\OneDrive\Desktop\Beta.rar",
    [string]$Out   = "C:\OddBeta",
    [string]$Fork  = "C:\Users\<you>\SWBeta\src\Cxbx-Reloaded\build\bin\Release",
    [string]$Cache = "C:\Users\<you>\SWBeta\oddbeta\SymbolCache",
    # Reuse an existing extraction instead of unpacking the 2 GB archive again.
    [string]$FromGameDir = "",
    [switch]$SkipExtract
)

$ErrorActionPreference = "Stop"
function Say($m) { Write-Output "  $m" }

Write-Output "=== Oddbeta packager ==="

# ---- 1. locate an extractor -------------------------------------------------
# Windows' bundled bsdtar SILENTLY TRUNCATES this archive - it stops at an
# unsupported RAR filter after ~54 of 11,757 files while still exiting 0. Only a
# real 7-Zip (with its codec DLL beside it) reads it correctly. AMD's driver
# suite happens to ship one, which is why that path is in the list.
$sevenZip = @(
    "C:\Program Files\7-Zip\7z.exe",
    "C:\Program Files\AMD\CNext\CNext\7z.exe",
    "C:\Program Files\AMD\CIM\Bin64\7z.exe"
) | Where-Object { (Test-Path $_) -and (Test-Path (Join-Path (Split-Path $_) "7z.dll")) } |
    Select-Object -First 1

if (-not $sevenZip) { throw "No 7-Zip with codec DLL found. Install 7-Zip; bsdtar CANNOT read this archive correctly." }
Say "extractor : $sevenZip"

# ---- 2. extract the game ----------------------------------------------------
$gameDir = Join-Path $Out "Game"
if ($FromGameDir) {
    if (-not (Test-Path (Join-Path $FromGameDir "data\bundles"))) {
        throw "-FromGameDir has no data\bundles: $FromGameDir"
    }
    Say "game data : reusing existing extraction at $FromGameDir"
    $gameDir = $FromGameDir
} elseif ($SkipExtract -or (Test-Path (Join-Path $gameDir "data\bundles"))) {
    Say "game data : already present, skipping extraction"
} else {
    if (-not (Test-Path $Rar)) { throw "Beta.rar not found at: $Rar" }
    Say "extracting $([math]::Round((Get-Item $Rar).Length / 1GB, 2)) GB (this takes a few minutes)..."
    New-Item -ItemType Directory -Force $Out | Out-Null
    & $sevenZip x -y -o"$Out" "$Rar" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "extraction failed ($LASTEXITCODE)" }
}

# ---- 3. choose the executable ----------------------------------------------
# FINAL, and the choice matters more than it looks.
#
# The archive ships four builds. Debug is dated 2004-04-29; Release, Final and
# ReleaseXACT are all 2004-05-22 - and THE DATA IS FROM MAY. Running the April
# build against May data means it does not recognise the newer audio prefs
# (m_audioMasterVolume, m_I3DL2Enabled, ...), so audio_master.clr fails its
# 0xdeadbeef magic check, SentenceMgr then walks lip-sync data that was never
# loaded, reads a garbage count of ~78.7 million entries and asks for 2 GB. The
# out-of-memory halt screen cannot even draw itself, because fonts are not
# loaded that early. That is a dead end no emulator fix can reach.
#
# Of the three May builds, Final declares D3D8LTCG - which Cxbx recognises - and
# has assertions compiled out. Release declares D3D8I and needs the extra
# library-name mapping our fork adds; it gets further than it used to but still
# faults. Final is the one that renders.
$xbe = Join-Path $gameDir "Final\SteefFinal.xbe"
if (-not (Test-Path $xbe)) { throw "SteefFinal.xbe not found - is this the right archive?" }

# The Xbox reads its data from D:, which maps to the folder holding the XBE.
# The builds live in Game\Debug\ while data\ is in Game\, so the XBE must be
# copied up beside the data or it finds nothing and quits.
Copy-Item $xbe (Join-Path $gameDir "default.xbe") -Force
Say "runtime   : Final build, 2004-05-22 (matches the data; Debug is 3 weeks older)"

# ---- 4. lay down the fork ---------------------------------------------------
$runtime = Join-Path $Out "runtime"
New-Item -ItemType Directory -Force $runtime | Out-Null
foreach ($f in @("cxbx.exe","cxbxr-emu.dll","cxbxr-ldr.exe","glew32.dll","SDL2.dll","subhook.dll")) {
    $src = Join-Path $Fork $f
    if (-not (Test-Path $src)) { throw "fork binary missing: $src  (build it first)" }
    Copy-Item $src $runtime -Force
}
# capstone ships with the upstream release rather than our build
foreach ($f in @("capstone.dll","cs_x86.dll")) {
    $src = Join-Path "C:\Users\<you>\SWBeta\cxbx" $f
    if (Test-Path $src) { Copy-Item $src $runtime -Force }
}
$hlsl = Join-Path $Fork "hlsl"
if (Test-Path $hlsl) { Copy-Item $hlsl $runtime -Recurse -Force }
Say "runtime   : fork binaries copied"

# ---- 4b. remove stale generated state -------------------------------------
# These three directories are GENERATED at runtime and must never be shipped, or
# carried across a rebuild. Each one has already caused a bug that looked exactly
# like an emulator fault:
#
#   EmuDisk     the virtual Xbox HDD (T:/U:). The title caches processed level data
#               here. A poisoned cache made Tutorial Town load with NO TERRAIN -
#               no ground drawn AND no ground collision, so the player and every
#               NPC fell through the world. Draw rate collapsed from ~7,200 to 680
#               per frame. Nothing in the emulator was at fault and no source change
#               could fix it; moving EmuDisk aside fixed it instantly.
#   ShaderCache compiled host shaders, keyed by Xbox bytecode, PERSISTED TO DISK and
#               reused across rebuilds. A bad build cached wrong shaders for the
#               skinned character programs, so Stranger and the Clakkerz stayed
#               invisible through several correct fixes. Deleting it fixed them.
#   EEPROM.bin  Xbox settings (video mode, region). Harmless to regenerate.
#
# The rule these share: when a fix that should work has no effect, suspect
# persistent state before suspecting the code.
foreach ($stale in @("EmuDisk", "ShaderCache", "EmuMu", "EmuMediaBoard", "EEPROM.bin")) {
    $path = Join-Path $runtime $stale
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}
Say "runtime   : cleared generated state (EmuDisk/ShaderCache/EEPROM) - these must be built fresh"

# ---- 5. the symbol cache ----------------------------------------------------
# This is the part that makes the whole thing work. Cxbx locates statically
# linked Xbox library functions by scanning for byte patterns from RETAIL
# libraries - which cannot match this debug build, so it finds almost nothing
# (9 symbols, zero Direct3D). We extracted 628 from the beta's own PDBs, plus
# D3D_g_DeferredTextureState recovered by disassembly because no public symbol
# for it exists at all. Without this file the emulator has no graphics.
if (Test-Path $Cache) {
    Copy-Item $Cache $runtime -Recurse -Force
    $n = (Select-String -Path (Join-Path $runtime "SymbolCache\*.ini") -Pattern "^\w+ = 0x").Count
    Say "symbols   : $n entries pre-seeded (PDB-derived; the emulator cannot find these itself)"
} else {
    Write-Warning "  symbol cache not found at $Cache - graphics will not work"
}

# ---- 6. settings ------------------------------------------------------------
# Copy a KNOWN-GOOD settings.ini rather than writing a minimal one from scratch.
# A hand-written file that only carried the flags we care about omitted the
# [audio], [network], [input-general], [input-port-0..3], [overlay] and [hack]
# sections - Cxbx reads all of those at start-up and crashes when they are
# absent. Take the working file and adjust only what needs to differ.
$srcIni = Join-Path (Split-Path $Cache -Parent) "settings.ini"
if (-not (Test-Path $srcIni)) { throw "no reference settings.ini beside the symbol cache: $srcIni" }
$ini = Get-Content $srcIni -Raw

# Silence logging for an end-user build, and drop absolute paths that only make
# sense on the development machine.
$ini = $ini -replace 'KrnlDebugMode = 0x\d',  'KrnlDebugMode = 0x0'
$ini = $ini -replace 'CxbxDebugMode = 0x\d',  'CxbxDebugMode = 0x0'
$ini = $ini -replace 'KrnlDebugLogFile = .*', 'KrnlDebugLogFile = '
$ini = $ini -replace 'CxbxDebugLogFile = .*', 'CxbxDebugLogFile = '
$ini = $ini -replace 'RecentXbeFiles = .*',   'RecentXbeFiles = '
# LoggedModules is a per-subsystem DEBUG mask. The development config has it at
# 0xffffffff, which includes per-instruction x86 disassembly and NV2A register
# traces - that produced a 632 MB log in fifty seconds and stalls the emulator
# badly enough to look like a crash. An end-user build logs nothing.
$ini = $ini -replace 'LoggedModules = 0x[0-9a-fA-F]+', 'LoggedModules = 0x0'
# These two are what let an unsigned, PDB-symbol-driven devkit XBE load at all.
$ini = $ini -replace 'IgnoreInvalidXbeSig = false', 'IgnoreInvalidXbeSig = true'
$ini = $ini -replace 'LogPopupTestCase = true',     'LogPopupTestCase = false'
$ini | Set-Content (Join-Path $runtime "settings.ini") -Encoding ascii
Say "settings  : derived from the working config (all sections preserved)"

# ---- 7. the launcher --------------------------------------------------------
# The XBE path must be absolute and correct. With -FromGameDir the game data
# deliberately stays where it already is rather than being copied again, so
# "%~dp0Game" would be wrong; resolve it from what we actually used.
$xbePath = Join-Path $gameDir "default.xbe"
$inPkg   = $gameDir.TrimEnd('\').StartsWith($Out.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
$xbeArg  = if ($inPkg) { '"%~dp0Game\default.xbe"' } else { "`"$xbePath`"" }
@"
@echo off
REM Oddworld: Stranger's Wrath - May 2004 Xbox beta, on PC.
cd /d "%~dp0"
start "" "%~dp0runtime\cxbx.exe" $xbeArg
"@ | Set-Content (Join-Path $Out "Play Beta.bat") -Encoding ascii
Say "launcher  : points at $xbePath"

@"
Oddworld: Stranger's Wrath - 2004 beta
======================================

Double-click "Play Beta.bat".

What this is
------------
An original-Xbox development build from May 2004, eight months before release.
It was never a PC game: the executables are Xbox images that import the Xbox
kernel. This folder carries a compatibility layer - a fork of Cxbx-Reloaded -
so it runs on Windows.

The Xbox is x86, so the game's own code runs natively on your CPU. The layer
supplies the kernel functions and translates Xbox Direct3D to your GPU.

Why a fork was needed
---------------------
This is a 2004 devkit build, which stock Cxbx-Reloaded cannot run:

  * it declares link-time-optimised and instrumented libraries (D3D8LTCG,
    D3D8I, D3D8D) that were not recognised as Direct3D at all, so the
    emulator fell back to software rendering
  * GPU fences were unimplemented stubs, so the engine stalled forever
    waiting on a fence that never advanced
  * an xbdm ordinal past the end of an emulator table corrupted one of the
    game's own function pointers
  * Direct3D wrapper functions recursed until the stack died
  * some entry points were patched twice - once for the normal calling
    convention and once for the register-passing LTCG variant - and the
    wrong one won, so arguments were read from the wrong place
  * symbols could not be found at all, because the emulator's pattern
    database is built from RETAIL libraries

Fixes for these are compiled into runtime\cxbxr-emu.dll, and the symbol table
recovered from the build's own debug files is pre-seeded in
runtime\SymbolCache\. The game files themselves are used UNMODIFIED.

Controls
--------
An XInput controller (Xbox pad) is picked up automatically. Keyboard mapping
is available through the emulator window's Settings menu.

Credits
-------
Cxbx-Reloaded (GPL-2.0) - https://github.com/Cxbx-Reloaded/Cxbx-Reloaded
Modifications are likewise GPL-2.0; source is available on request.
Oddworld: Stranger's Wrath is the property of Oddworld Inhabitants.
You must own the game. Nothing here is redistributed with it.
"@ | Set-Content (Join-Path $Out "README.txt") -Encoding ascii

Write-Output ""
Write-Output "=== done ==="
Write-Output "  $Out"
Write-Output "  run: `"$Out\Play Beta.bat`""
