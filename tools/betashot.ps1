# betashot.ps1 - capture the beta's render window under our Cxbx fork.
#
#   powershell -ExecutionPolicy Bypass -File tools\betashot.ps1 -Out shot.png
#
# Why a script and not an inline snippet: the Add-Type'd P/Invoke class does not
# survive between tool invocations (each one gets a fresh shell), so pasting the
# interop inline re-declares it every time and fails the moment it is already
# loaded. Keeping it here also means the capture is identical run to run, which
# matters when the whole point is comparing before/after frames.
#
# PrintWindow with PW_RENDERFULLCONTENT (3) is used rather than a screen grab so
# the capture works even when the window is occluded or off-screen.

param(
    [string]$Out = "C:\Users\<you>\New folder\beta_shot.png"
)

Add-Type -AssemblyName System.Drawing

if (-not ('BetaShot' -as [type])) {
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class BetaShot {
 public delegate bool EnumProc(IntPtr h, IntPtr p);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc e, IntPtr p);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint id);
 [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@
}

$procIds = @(Get-Process cxbx, cxbxr-ldr -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
if ($procIds.Count -eq 0) { Write-Output "NOT RUNNING"; exit 1 }

$found = @()
$cb = [BetaShot+EnumProc]{
    param($h, $p)
    $id = 0
    [void][BetaShot]::GetWindowThreadProcessId($h, [ref]$id)
    if (($procIds -contains $id) -and [BetaShot]::IsWindowVisible($h)) {
        $r = New-Object BetaShot+RECT
        [void][BetaShot]::GetClientRect($h, [ref]$r)
        # Skip the tiny tool/message windows; the render surface is the big one.
        if ($r.R -gt 100 -and $r.B -gt 100) {
            $script:found += [pscustomobject]@{ H = $h; W = $r.R; Hh = $r.B }
        }
    }
    return $true
}
[void][BetaShot]::EnumWindows($cb, [IntPtr]::Zero)

if ($found.Count -eq 0) { Write-Output "NO VISIBLE RENDER WINDOW"; exit 2 }

$w = $found | Sort-Object { $_.W * $_.Hh } -Descending | Select-Object -First 1
$bmp = New-Object Drawing.Bitmap $w.W, $w.Hh
$g = [Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[void][BetaShot]::PrintWindow($w.H, $hdc, 3)
$g.ReleaseHdc($hdc)
$bmp.Save($Out, [Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

Write-Output "SAVED $Out ($($w.W)x$($w.Hh))"
