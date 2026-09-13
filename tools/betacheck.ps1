# betacheck.ps1 - run the 2004 beta under our Cxbx fork and report the numbers
# that actually say whether it is rendering.
#
#   powershell -ExecutionPolicy Bypass -File tools\betacheck.ps1 [-Wait 50] [-Tag before]
#
# Why this exists: judging progress by "did a window appear" is unreliable - the
# render window can exist but stay hidden at 136x39 when rendering is broken, and
# the kernel log is block-buffered so its tail vanishes when the process dies.
# These five numbers have tracked real progress all the way through:
#
#   Swap calls      frames actually presented. Was 0, then 2. Hundreds = rendering.
#   draw calls      geometry submitted at all.
#   HLE patches     how much of Direct3D got hooked (137 -> 224 after the debug
#                   library-name fix; a drop means symbols regressed).
#   assert loop     the engine's own assertions. 269 = trapped in the null-resource
#                   /font loop. Falling to ~0 is the win condition.
#   window visible  the real test. TRUE with a sane size means pixels.

param(
    [int]$Wait = 50,
    [string]$Tag = "",
    [string]$Run = "C:\Users\<you>\SWBeta\oddbeta",
    [string]$Xbe = "C:\Users\<you>\SWBeta\Game\default.xbe"
)

$ErrorActionPreference = "Continue"
$log = Join-Path $Run "KrnlDebug.txt"

Get-Process cxbx, cxbxr-ldr -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Remove-Item $log -Force -ErrorAction SilentlyContinue

# file logging must be on (0x2); 0x1 is console-only and writes nothing
$ini = Join-Path $Run "settings.ini"
$cfg = Get-Content $ini -Raw
if ($cfg -notmatch 'KrnlDebugMode = 0x2') {
    $cfg = $cfg -replace 'KrnlDebugMode = 0x\d', 'KrnlDebugMode = 0x2'
    $cfg = $cfg -replace 'KrnlDebugLogFile = .*', "KrnlDebugLogFile = $log"
    Set-Content $ini $cfg -Encoding ascii
}

Write-Output "launching (waiting ${Wait}s)..."
Start-Process -FilePath (Join-Path $Run "cxbx.exe") -ArgumentList "`"$Xbe`""
Start-Sleep -Seconds $Wait

$procs = Get-Process | Where-Object { $_.Name -match "cxbx" }
$emuAlive = [bool]($procs | Where-Object { $_.Name -eq "cxbxr-ldr" })

# --- window visibility: the render window belongs to cxbxr-ldr ---------------
if (-not ('BetaWin' -as [type])) {
    Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class BetaWin {
  delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc e, IntPtr p);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint id);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll",CharSet=CharSet.Auto)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  public struct RECT { public int L,T,R,B; }
  public static List<string> Get(uint[] ids) {
    var res = new List<string>();
    EnumWindows((h,p) => { uint w; GetWindowThreadProcessId(h, out w);
      foreach (var id in ids) if (id == w) {
        RECT r; GetWindowRect(h, out r);
        var c = new StringBuilder(256); GetClassName(h, c, 256);
        // #32770 is the dialog class - that is a modal error popup, not pixels.
        if (IsWindowVisible(h) && (r.R-r.L) > 200 && c.ToString() != "#32770")
          res.Add(string.Format("{0} {1}x{2}", c, r.R-r.L, r.B-r.T)); }
      return true; }, IntPtr.Zero);
    return res; } }
"@
}
# The render surface is the GUI process's WndMain, NOT a window of cxbxr-ldr:
# Cxbx-Reloaded draws the title into the shell window and resizes it to the
# title's resolution (640x480 client = 656x539 framed). cxbxr-ldr owns only
# DIEmWin, a hidden 136x39 DirectInput helper, so filtering to cxbxr-ldr reports
# "no window" even while the game is visibly rendering - which it did, for a run
# that was presenting 1895 frames. Scan both processes and name the window class
# so the shell, the render surface and an error dialog are told apart on sight.
$ids = @($procs | Select-Object -ExpandProperty Id | ForEach-Object { [uint32]$_ })
$vis = if ($ids.Count) { [BetaWin]::Get($ids) } else { @() }

function Count($pat) {
    if (-not (Test-Path $log)) { return 0 }
    (Select-String -Path $log -Pattern $pat -AllMatches |
        ForEach-Object { $_.Matches.Count } | Measure-Object -Sum).Sum
}

# The symbol scanner prints one "SymbolCache: <addr> -> <name>" and one
# "HLE: <name> Patched" line per symbol, so counting bare API names over the whole
# log scores those inventory lines as if they were calls. That reported "2 Swap
# calls / 6 draw calls" for a run that never presented a single frame. Only count
# names on lines that are neither of those.
function CountCalls($pat) {
    if (-not (Test-Path $log)) { return 0 }
    (Select-String -Path $log -Pattern $pat -AllMatches |
        Where-Object { $_.Line -notmatch '^\s*(SymbolCache:|HLE:)' } |
        ForEach-Object { $_.Matches.Count } | Measure-Object -Sum).Sum
}

$size = if (Test-Path $log) { (Get-Item $log).Length } else { 0 }
Write-Output ""
Write-Output ("=== betacheck {0} ===" -f $Tag)
Write-Output ("  emulation alive : {0}" -f $emuAlive)
Write-Output ("  log size        : {0:N0} bytes" -f $size)
Write-Output ("  HLE patches     : {0}" -f (Count "Patched"))
Write-Output ("  CreateDevice    : {0}" -f (CountCalls "CreateDevice"))
# Swap and draw counts only appear at LogLevel 0 with the D3D8 module enabled;
# at LogLevel 1 they read 0 whatever the title is doing. This build draws through
# the immediate-mode Begin/SetVertexData/End path, not DrawIndexedVertices, so
# counting only the Draw* entry points scores a rendering title as zero geometry.
Write-Output ("  Swap calls      : {0}" -f (CountCalls "D3DDevice_Swap"))
Write-Output ("  draw calls      : {0}" -f (CountCalls "DrawIndexedVertices|DrawVertices|D3DDevice_Begin\("))
Write-Output ("  assert lines    : {0}" -f (Count "ASSERT"))
Write-Output ("  window visible  : {0}" -f $(if ($vis.Count) { "YES  " + ($vis -join ", ") } else { "no" }))

$fallback = Count "Fallback to LLE GPU"
Write-Output ("  LLE GPU fallback: {0}" -f $(if ($fallback) { "YES - no HLE graphics" } else { "no (HLE graphics active)" }))

# A crash is invisible in every number above: cxbxr-ldr stays alive holding the modal
# "unhandled exception" dialog, and that dialog is class #32770 which the window scan
# deliberately skips - so a crashed run reports "emulation alive TRUE, window no" and
# looks identical to a slow load. Surface the exception explicitly, with the faulting
# address, because that is the line that names the next bug.
$crash = Count "Received Exception"
Write-Output ("  CRASHED         : {0}" -f $(if ($crash) { "YES" } else { "no" }))
if ($crash -and (Test-Path $log)) {
    Select-String -Path $log -Pattern "Received Exception|EIP := " |
        Select-Object -First 2 |
        ForEach-Object { Write-Output ("    " + $_.Line.Trim()) }
}
if (Test-Path $log) {
    Write-Output ""
    Write-Output "  last 6 log lines:"
    Get-Content $log -Tail 6 | ForEach-Object { Write-Output ("    " + $_) }
}

if (Test-Path $log) {
    Write-Output ""
    Write-Output "  top engine asserts:"
    Select-String -Path $log -Pattern "^DEBUG_PRINT: d:" |
        ForEach-Object { ($_.Line -replace 'DEBUG_PRINT: ', '') } |
        Group-Object | Sort-Object Count -Descending | Select-Object -First 4 |
        ForEach-Object { Write-Output ("    {0,5}  {1}" -f $_.Count, $_.Name) }
}
