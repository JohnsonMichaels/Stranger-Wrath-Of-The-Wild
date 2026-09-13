# make_installer.ps1 - build a clean, portable, self-extracting installer for the
# Stranger's Wrath 2004 beta standalone.
#
#   powershell -ExecutionPolicy Bypass -File tools\make_installer.ps1
#
# Produces:  <Out>\StrangersWrathBetaSetup.exe   (self-extracting, no dependencies)
#
# The point of this script over the dev folder: C:\OddBeta is a WORKING directory.
# It points at game data outside itself, its settings.ini carries absolute paths and
# a recent-files list from this machine, and it accumulates generated state. None of
# that can go to anyone else - some of it would not even work on another PC, and the
# rest just leaks the developer's directory layout. This assembles a fresh tree and
# scrubs it.

param(
    [string]$GameData = "C:\Users\<you>\SWBeta\Game",
    [string]$Fork     = "C:\Users\<you>\SWBeta\src\Cxbx-Reloaded\build\bin\Release",
    [string]$Launcher = "C:\Users\<you>\New folder\launcher",
    [string]$Cache    = "C:\Users\<you>\SWBeta\oddbeta\SymbolCache",
    [string]$RefIni   = "C:\Users\<you>\SWBeta\oddbeta\settings.ini",
    [string]$Stage    = "C:\OddBetaDist\Stranger's Wrath Beta",
    [string]$Out      = "C:\OddBetaDist",
    [switch]$IncludeGameData  # normally OFF - the recipient supplies their own copy of the beta
)

$ErrorActionPreference = "Stop"
function Say($m) { Write-Output "  $m" }
Write-Output "=== building installer ==="

# ---- 1. staging tree --------------------------------------------------------
New-Item -ItemType Directory -Force $Stage | Out-Null
$runtime = Join-Path $Stage "runtime"
New-Item -ItemType Directory -Force $runtime | Out-Null

# ---- 2. runtime binaries ----------------------------------------------------
# Only what the import tables actually require. cxbx.exe (the emulator's own GUI) is
# NOT included: the launcher runs cxbxr-ldr.exe /load directly, which is headless.
# capstone.dll / cs_x86.dll are imported by nothing.
foreach ($f in @("cxbxr-emu.dll", "cxbxr-ldr.exe", "SDL2.dll", "glew32.dll", "subhook.dll")) {
    $src = Join-Path $Fork $f
    if (-not (Test-Path $src)) { throw "missing runtime binary: $src (build the fork first)" }
    Copy-Item $src $runtime -Force
}
$hlsl = Join-Path $Fork "hlsl"
if (Test-Path $hlsl) {
    Copy-Item $hlsl $runtime -Recurse -Force
    # The build tree keeps an hlsl\backup\ copy of every shader. It is a build
    # artifact, byte-identical to the live set, and shipping it doubles the shader
    # payload for no reason.
    $backup = Join-Path $runtime "hlsl\backup"
    if (Test-Path $backup) { Remove-Item $backup -Recurse -Force }
}
Say "runtime binaries copied"

# ---- 3. launcher + icon -----------------------------------------------------
Copy-Item (Join-Path $Launcher "Stranger's Wrath Beta.exe") $Stage -Force
Copy-Item (Join-Path $Launcher "stranger.ico") (Join-Path $runtime "game.ico") -Force
Say "launcher and window icon copied"

# ---- 4. symbol cache --------------------------------------------------------
# Without this the emulator cannot locate the title's Direct3D functions and there
# are no graphics at all - its pattern database is built from RETAIL libraries and
# cannot match this devkit build. Ship only the live .ini files, not the .bak /
# .prePDB / .freshscan working copies lying around the dev folder.
if (Test-Path $Cache) {
    $dst = Join-Path $runtime "SymbolCache"
    New-Item -ItemType Directory -Force $dst | Out-Null
    Get-ChildItem $Cache -Filter "*.ini" | Where-Object { $_.Name -notmatch '\.(bak|pre|fresh)' } |
        ForEach-Object { Copy-Item $_.FullName $dst -Force }
    Say "symbol cache: $((Get-ChildItem $dst).Count) file(s)"
}
else { Write-Warning "  symbol cache missing - the build will have no graphics" }

# ---- 5. settings, scrubbed --------------------------------------------------
# Derived from the working config so every section survives (a hand-written minimal
# settings.ini crashed the emulator once - it reads [audio], [network],
# [input-port-*], [input-profile-*], [overlay] and [hack] at start-up), then cleaned
# of anything machine-specific.
if (-not (Test-Path $RefIni)) { throw "no reference settings.ini at $RefIni" }
$ini = Get-Content $RefIni -Raw
$ini = $ini -replace '(?m)^(KrnlDebugLogFile|CxbxDebugLogFile|DataCustomLocation)\s*=.*$', '$1 = '
$ini = $ini -replace '(?m)^RecentXbeFiles\s*=.*$', 'RecentXbeFiles = '
$ini = $ini -replace '(?m)^(KrnlDebugMode|CxbxDebugMode)\s*=.*$', '$1 = 0x0'
$ini = $ini -replace '(?m)^LoggedModules\s*=.*$', 'LoggedModules = 0x0'
$ini = $ini -replace '(?m)^IgnoreInvalidXbeSig\s*=.*$', 'IgnoreInvalidXbeSig = true'
$ini = $ini -replace '(?m)^LogPopupTestCase\s*=.*$', 'LogPopupTestCase = false'
$ini | Set-Content (Join-Path $runtime "settings.ini") -Encoding ascii
Say "settings.ini derived and scrubbed of local paths"

# Sanity check: nothing machine-specific may survive into the shipped file.
$leaks = Select-String -Path (Join-Path $runtime "settings.ini") -Pattern '<you>|SWBeta|OddBeta|C:\\Users' -AllMatches
if ($leaks) {
    $leaks | ForEach-Object { Write-Warning "  LEAK: $($_.Line.Trim())" }
    throw "settings.ini still contains local paths - fix the scrub rules above"
}
Say "settings.ini verified clean"

# ---- 6. game data -----------------------------------------------------------
# Copied IN, so the result is portable. The dev folder deliberately points at an
# external extraction to avoid duplicating 2 GB on every iteration; a distributable
# build cannot do that.
$gameDst = Join-Path $Stage "Game"
if (-not $IncludeGameData) { Say "game data: NOT bundled - the launcher asks the user for their own copy" }
else {
    if (-not (Test-Path (Join-Path $GameData "default.xbe"))) { throw "no default.xbe in $GameData" }
    Say "copying game data (about 2 GB, this takes a while)..."
    New-Item -ItemType Directory -Force $gameDst | Out-Null
    robocopy $GameData $gameDst /E /NFL /NDL /NJH /NJS /NP /XD "Debug" "Release" "Final" | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
    # The build sub-folders are not needed - default.xbe at the root is the Final build.
    Say "game data copied"
}

# game.txt is a DEV convenience pointing at an external path. A portable build finds
# Game\default.xbe beside the launcher, so the pointer file must not ship.
Remove-Item (Join-Path $Stage "game.txt") -Force -ErrorAction SilentlyContinue

# ---- 7. generated state must never ship -------------------------------------
# Both of these caused bugs that looked exactly like emulator faults: a stale
# ShaderCache left characters invisible, and a stale EmuDisk made terrain fail to
# load so the player and NPCs fell through the world.
foreach ($stale in @("EmuDisk", "ShaderCache", "EmuMu", "EmuMediaBoard", "EEPROM.bin", "KrnlDebug.txt", "_unused")) {
    Remove-Item (Join-Path $runtime $stale) -Recurse -Force -ErrorAction SilentlyContinue
}
Say "generated state excluded"

# ---- 8. readme --------------------------------------------------------------
@"
Oddworld: Stranger's Wrath - 2004 Beta
======================================

Run "Stranger's Wrath Beta.exe", choose a resolution, press Play.

Controls
--------
  Move            W A S D
  Look            Mouse
  Fire            Left mouse
  Aim             Right mouse
  Jump            Enter
  Use             E
  Melee           F
  Change view     V
  Crouch          Left Ctrl
  Pause           Esc
  Map             Tab
  Menus           Arrow keys, Enter to select

The mouse is held inside the window while the game has focus. Alt-Tab releases it.
F3 toggles that if you prefer it off.

What this is
------------
An Xbox development build from May 2004, months before release. It was never a PC
game - the executable is an Xbox image. This folder includes a compatibility layer,
a modified build of Cxbx-Reloaded, so it runs on Windows. The Xbox is x86, so the
game's own code runs natively on your CPU; the layer supplies the Xbox system
functions and translates Xbox Direct3D to your graphics card.

It is a beta. Expect rough edges.

What works
----------
All six levels load, including Mongo Valley (the beta ships with an engine memory
pool too small for it; this build enlarges it automatically when the game starts -
your game files are not modified). Music, cutscene audio, and sound effects play,
including positional effects such as footsteps and gunfire.

Known issues
------------
Some characters render darker than they should, and some textures may flicker.

Requirements
------------
64-bit Windows and a Direct3D 9 capable graphics card.

Credits and licence
-------------------
Cxbx-Reloaded, licensed GPL-2.0 - https://github.com/Cxbx-Reloaded/Cxbx-Reloaded
This build includes modifications to it, which are likewise GPL-2.0. Source is
available on request.

Oddworld: Stranger's Wrath is the property of Oddworld Inhabitants.
"@ | Set-Content (Join-Path $Stage "README.txt") -Encoding ascii
Say "readme written"

# ---- 9. self-extracting installer -------------------------------------------
# Windows' bundled bsdtar cannot produce an SFX, so a real 7-Zip is required. AMD's
# driver suite happens to ship one, which is why that path is in the list.
$sevenZip = @(
    "C:\Program Files\7-Zip\7z.exe",
    "C:\Program Files\AMD\CNext\CNext\7z.exe",
    "C:\Program Files\AMD\CIM\Bin64\7z.exe"
) | Where-Object { (Test-Path $_) -and (Test-Path (Join-Path (Split-Path $_) "7z.dll")) } | Select-Object -First 1

$archive = Join-Path $Out "StrangersWrathBeta.7z"
Remove-Item $archive -Force -ErrorAction SilentlyContinue

if ($sevenZip) {
    Say "compressing (this takes a few minutes)..."
    & $sevenZip a -t7z -mx3 -mmt=on $archive "$Stage" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "7-Zip failed ($LASTEXITCODE)" }

    $sfx = Join-Path (Split-Path $sevenZip) "7z.sfx"
    $setup = Join-Path $Out "StrangersWrathBetaSetup.exe"
    if (Test-Path $sfx) {
        Remove-Item $setup -Force -ErrorAction SilentlyContinue
        cmd /c copy /b "`"$sfx`"" + "`"$archive`"" "`"$setup`"" | Out-Null
        Say "installer: $setup"
    }
    else {
        Say "no 7z.sfx module beside 7-Zip - shipping the .7z archive instead"
        Say "archive: $archive"
    }
}
else {
    Say "no 7-Zip found - falling back to a plain .zip"
    $zip = Join-Path $Out "StrangersWrathBeta.zip"
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path $Stage -DestinationPath $zip
    Say "archive: $zip"
}

Write-Output ""
Write-Output "=== done ==="
Write-Output "  staged at: $Stage"


