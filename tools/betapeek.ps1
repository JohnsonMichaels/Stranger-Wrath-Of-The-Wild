# betapeek.ps1 - read guest memory out of the running beta under our Cxbx fork.
#
#   powershell -ExecutionPolicy Bypass -File tools\betapeek.ps1 -Addr 0x29ACB0 -Count 8
#
# Cxbx maps the Xbox address space into the emulator process at the SAME virtual
# addresses, so an Xbox VA can be read directly with ReadProcessMemory. That makes
# it possible to inspect engine globals without rebuilding the emulator or adding
# a printf - which matters when a rebuild-and-relaunch cycle costs a minute and
# the question is just "what is this DWORD right now".
#
# The guest runs inside cxbxr-ldr.exe (cxbx.exe is the front end), but both are
# tried so the caller does not have to care.

param(
    [Parameter(Mandatory = $true)][string]$Addr,   # hex, e.g. 0x29ACB0
    [int]$Count = 4,                               # DWORDs to read
    [string]$Label = ""
)

if (-not ('Peek' -as [type])) {
Add-Type @"
using System; using System.Runtime.InteropServices;
public class Peek {
 [DllImport("kernel32.dll", SetLastError=true)]
 public static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
 [DllImport("kernel32.dll", SetLastError=true)]
 public static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int size, out int read);
 [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
}
"@
}

$PROCESS_VM_READ = 0x0010
$PROCESS_QUERY_INFORMATION = 0x0400

$target = [Convert]::ToInt64($Addr, 16)
$bytes = $Count * 4

foreach ($name in @("cxbxr-ldr", "cxbx")) {
    $proc = Get-Process $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $proc) { continue }

    $h = [Peek]::OpenProcess($PROCESS_VM_READ -bor $PROCESS_QUERY_INFORMATION, $false, $proc.Id)
    if ($h -eq [IntPtr]::Zero) { continue }

    $buf = New-Object byte[] $bytes
    $read = 0
    $ok = [Peek]::ReadProcessMemory($h, [IntPtr]$target, $buf, $bytes, [ref]$read)
    [void][Peek]::CloseHandle($h)

    if ($ok -and $read -eq $bytes) {
        $tag = if ($Label) { "$Label " } else { "" }
        Write-Output "$tag[$name pid $($proc.Id)] at ${Addr}"
        for ($i = 0; $i -lt $Count; $i++) {
            $v = [BitConverter]::ToUInt32($buf, $i * 4)
            $a = "0x{0:X8}" -f ($target + $i * 4)
            Write-Output ("  {0} = 0x{1:X8}  ({2})" -f $a, $v, $v)
        }
        exit 0
    }
}

Write-Output "UNREADABLE $Addr (process not running, or address not mapped)"
exit 1
