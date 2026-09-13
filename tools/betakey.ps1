# betakey.ps1 - send keystrokes to the beta running under our Cxbx fork.
#
#   powershell -ExecutionPolicy Bypass -File tools\betakey.ps1 -Keys s
#   powershell -ExecutionPolicy Bypass -File tools\betakey.ps1 -Keys "down,down,enter" -DelayMs 250
#
# Why SendInput with SCANCODES and not SendKeys / WM_KEYDOWN: the emulator reads
# the keyboard through DirectInput, which ignores posted window messages and reads
# scancodes rather than virtual keys. SendKeys reaches the window and does nothing.
# The window must also be foreground for SendInput to land, so it is raised first.
#
# Key names are the ones in $ScanCodes below; anything else is treated as a single
# character and looked up with VkKeyScan + MapVirtualKey.

param(
    [Parameter(Mandatory = $true)][string]$Keys,   # comma-separated
    [int]$DelayMs = 200,                           # between keys
    [int]$HoldMs = 60                              # key down -> up
)

if (-not ('BetaKey' -as [type])) {
Add-Type @"
using System; using System.Runtime.InteropServices;
public class BetaKey {
 [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT {
   public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
 [StructLayout(LayoutKind.Explicit, Size=28)] public struct INPUT {
   [FieldOffset(0)] public uint type;
   [FieldOffset(4)] public KEYBDINPUT ki; }
 [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint n, INPUT[] p, int size);
 [DllImport("user32.dll")] public static extern short VkKeyScan(char c);
 [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code, uint mapType);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc e, IntPtr p);
 public delegate bool EnumProc(IntPtr h, IntPtr p);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint id);
 [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
 [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr SetActiveWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr h);
 [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@
}

# Set 1 scancodes. Extended keys (arrows etc.) need KEYEVENTF_EXTENDEDKEY.
$ScanCodes = @{
    'a'=0x1E; 'b'=0x30; 'c'=0x2E; 'd'=0x20; 'e'=0x12; 'f'=0x21; 'g'=0x22; 'h'=0x23
    'i'=0x17; 'j'=0x24; 'k'=0x25; 'l'=0x26; 'm'=0x32; 'n'=0x31; 'o'=0x18; 'p'=0x19
    'q'=0x10; 'r'=0x13; 's'=0x1F; 't'=0x14; 'u'=0x16; 'v'=0x2F; 'w'=0x11; 'x'=0x2D
    'y'=0x15; 'z'=0x2C
    'enter'=0x1C; 'esc'=0x01; 'space'=0x39; 'tab'=0x0F; 'backspace'=0x0E
    'up'=0x48; 'down'=0x50; 'left'=0x4B; 'right'=0x4D
    'f1'=0x3B; 'f2'=0x3C; 'f3'=0x3D; 'f4'=0x3E
    '1'=0x02; '2'=0x03; '3'=0x04; '4'=0x05; '5'=0x06
}
$Extended = @('up','down','left','right')

$KEYEVENTF_KEYUP = 0x0002
$KEYEVENTF_SCANCODE = 0x0008
$KEYEVENTF_EXTENDEDKEY = 0x0001

# --- raise the render window so SendInput reaches it -------------------------
$procIds = @(Get-Process cxbx, cxbxr-ldr -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
if ($procIds.Count -eq 0) { Write-Output "NOT RUNNING"; exit 1 }

$target = [IntPtr]::Zero
$best = 0
$cb = [BetaKey+EnumProc]{
    param($h, $p)
    $id = 0
    [void][BetaKey]::GetWindowThreadProcessId($h, [ref]$id)
    if (($procIds -contains $id) -and [BetaKey]::IsWindowVisible($h)) {
        $r = New-Object BetaKey+RECT
        [void][BetaKey]::GetClientRect($h, [ref]$r)
        $area = $r.R * $r.B
        if ($area -gt $script:best) { $script:best = $area; $script:target = $h }
    }
    return $true
}
[void][BetaKey]::EnumWindows($cb, [IntPtr]::Zero)
if ($target -eq [IntPtr]::Zero) { Write-Output "NO RENDER WINDOW"; exit 2 }

# Windows refuses SetForegroundWindow from a process that does not already own the
# foreground, and Cxbx acquires its DirectInput keyboard at DISCL_FOREGROUND - so
# an unfocused window simply has no acquired device and every injected key is
# dropped. Attaching our input queue to the current foreground thread lifts that
# restriction for the duration of the call, which is the standard workaround.
[void][BetaKey]::ShowWindow($target, 5)   # SW_SHOW

$fg = [BetaKey]::GetForegroundWindow()
$fgThread = 0
[void][BetaKey]::GetWindowThreadProcessId($fg, [ref]$fgThread)
$ourThread = [BetaKey]::GetCurrentThreadId()

$attached = $false
if ($fgThread -ne 0 -and $fgThread -ne $ourThread) {
    $attached = [BetaKey]::AttachThreadInput($ourThread, $fgThread, $true)
}
[void][BetaKey]::BringWindowToTop($target)
[void][BetaKey]::SetForegroundWindow($target)
[void][BetaKey]::SetActiveWindow($target)
[void][BetaKey]::SetFocus($target)
if ($attached) { [void][BetaKey]::AttachThreadInput($ourThread, $fgThread, $false) }

Start-Sleep -Milliseconds 400
$nowFg = [BetaKey]::GetForegroundWindow()
if ($nowFg -ne $target) {
    Write-Output "WARNING: render window did not become foreground (fg=$nowFg target=$target) - DirectInput will drop these keys"
} else {
    Write-Output "focused render window"
}

function Send-Scan([uint16]$scan, [bool]$ext, [bool]$up) {
    $i = New-Object BetaKey+INPUT
    $i.type = 1  # INPUT_KEYBOARD
    $i.ki.wVk = 0
    $i.ki.wScan = $scan
    $f = $KEYEVENTF_SCANCODE
    if ($ext) { $f = $f -bor $KEYEVENTF_EXTENDEDKEY }
    if ($up)  { $f = $f -bor $KEYEVENTF_KEYUP }
    $i.ki.dwFlags = $f
    $i.ki.time = 0
    $i.ki.dwExtraInfo = [IntPtr]::Zero
    [void][BetaKey]::SendInput(1, @($i), [Runtime.InteropServices.Marshal]::SizeOf($i))
}

foreach ($nameRaw in ($Keys -split ',')) {
    $name = $nameRaw.Trim().ToLower()
    if ($name -eq '') { continue }

    if ($ScanCodes.ContainsKey($name)) {
        $scan = [uint16]$ScanCodes[$name]
    }
    elseif ($name.Length -eq 1) {
        $vk = [BetaKey]::VkKeyScan($name[0]) -band 0xFF
        $scan = [uint16][BetaKey]::MapVirtualKey([uint32]$vk, 0)
    }
    else {
        Write-Output "UNKNOWN KEY: $name"; continue
    }

    $ext = $Extended -contains $name
    Send-Scan $scan $ext $false
    Start-Sleep -Milliseconds $HoldMs
    Send-Scan $scan $ext $true
    Write-Output ("sent {0} (scan 0x{1:X2})" -f $name, $scan)
    Start-Sleep -Milliseconds $DelayMs
}
