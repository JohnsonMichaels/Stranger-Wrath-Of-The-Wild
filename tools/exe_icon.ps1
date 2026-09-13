# exe_icon.ps1 - extract an executable's icon resource into a real multi-size .ico
#
#   powershell -File tools\exe_icon.ps1 -Exe "C:\path\game.exe" -Out "C:\path\game.ico"
#
# Why not ExtractAssociatedIcon: that returns a single 32x32 image, which looks
# blurry the moment Windows wants a 48 or 256 pixel version (taskbar, alt-tab,
# large icon view). Executables store a RT_GROUP_ICON directory plus one RT_ICON
# per size; this reads those resources and reassembles a proper .ico containing
# every size the original had.
#
# The .ico container and the RT_GROUP_ICON resource are almost the same format -
# the only difference is the last field of each directory entry: the file form
# stores a 4-byte OFFSET to the image, the resource form stores a 2-byte resource
# ID. So the conversion is: read the group, fetch each RT_ICON by id, then rewrite
# the directory with computed offsets.

param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$Out
)

if (-not (Test-Path $Exe)) { Write-Output "NOT FOUND: $Exe"; exit 1 }

if (-not ('IconRes' -as [type])) {
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class IconRes {
 [DllImport("kernel32.dll", SetLastError=true)]
 public static extern IntPtr LoadLibraryEx(string f, IntPtr h, uint flags);
 [DllImport("kernel32.dll")] public static extern bool FreeLibrary(IntPtr h);
 [DllImport("kernel32.dll", SetLastError=true)]
 public static extern IntPtr FindResource(IntPtr h, IntPtr name, IntPtr type);
 [DllImport("kernel32.dll")] public static extern IntPtr LoadResource(IntPtr h, IntPtr res);
 [DllImport("kernel32.dll")] public static extern IntPtr LockResource(IntPtr data);
 [DllImport("kernel32.dll")] public static extern uint SizeofResource(IntPtr h, IntPtr res);
 [DllImport("kernel32.dll", SetLastError=true)]
 public static extern bool EnumResourceNames(IntPtr h, IntPtr type, EnumResNameProc cb, IntPtr param);
 public delegate bool EnumResNameProc(IntPtr h, IntPtr type, IntPtr name, IntPtr param);
}
"@
}

$LOAD_LIBRARY_AS_DATAFILE = 0x00000002
$RT_ICON       = [IntPtr]3
$RT_GROUP_ICON = [IntPtr]14

$h = [IconRes]::LoadLibraryEx($Exe, [IntPtr]::Zero, $LOAD_LIBRARY_AS_DATAFILE)
if ($h -eq [IntPtr]::Zero) { Write-Output "could not load $Exe as a resource file"; exit 2 }

# Take the first icon group - for a game executable that is the application icon.
$script:groupName = [IntPtr]::Zero
$cb = [IconRes+EnumResNameProc]{
    param($hm, $type, $name, $p)
    $script:groupName = $name
    return $false   # stop after the first
}
[void][IconRes]::EnumResourceNames($h, $RT_GROUP_ICON, $cb, [IntPtr]::Zero)

if ($script:groupName -eq [IntPtr]::Zero) {
    [void][IconRes]::FreeLibrary($h)
    Write-Output "no RT_GROUP_ICON in $Exe"
    exit 3
}

function Get-ResBytes([IntPtr]$hMod, [IntPtr]$name, [IntPtr]$type) {
    $r = [IconRes]::FindResource($hMod, $name, $type)
    if ($r -eq [IntPtr]::Zero) { return $null }
    $size = [IconRes]::SizeofResource($hMod, $r)
    $d = [IconRes]::LoadResource($hMod, $r)
    $p = [IconRes]::LockResource($d)
    if ($p -eq [IntPtr]::Zero -or $size -eq 0) { return $null }
    $buf = New-Object byte[] $size
    [Runtime.InteropServices.Marshal]::Copy($p, $buf, 0, [int]$size)
    return $buf
}

$group = Get-ResBytes $h $script:groupName $RT_GROUP_ICON
if ($null -eq $group) { [void][IconRes]::FreeLibrary($h); Write-Output "group icon unreadable"; exit 4 }

# GRPICONDIR: reserved(2) type(2) count(2), then count * GRPICONDIRENTRY(14)
$count = [BitConverter]::ToUInt16($group, 4)
$images = New-Object 'System.Collections.Generic.List[byte[]]'
for ($i = 0; $i -lt $count; $i++) {
    $id = [BitConverter]::ToUInt16($group, 6 + $i * 14 + 12)
    $img = Get-ResBytes $h ([IntPtr]$id) $RT_ICON
    $images.Add($img)
}
[void][IconRes]::FreeLibrary($h)

# ICONDIR + ICONDIRENTRY(16 each), then the images
$headerSize = 6 + $count * 16
$ms = New-Object IO.MemoryStream
$bw = New-Object IO.BinaryWriter($ms)
$bw.Write([uint16]0); $bw.Write([uint16]1); $bw.Write([uint16]$count)

$offset = $headerSize
for ($i = 0; $i -lt $count; $i++) {
    $src = 6 + $i * 14
    $bw.Write($group[$src + 0])                       # width
    $bw.Write($group[$src + 1])                       # height
    $bw.Write($group[$src + 2])                       # colour count
    $bw.Write($group[$src + 3])                       # reserved
    $bw.Write([BitConverter]::ToUInt16($group, $src + 4))  # planes
    $bw.Write([BitConverter]::ToUInt16($group, $src + 6))  # bit count
    $len = if ($images[$i]) { $images[$i].Length } else { 0 }
    $bw.Write([uint32]$len)
    $bw.Write([uint32]$offset)                        # <- offset, not resource id
    $offset += $len
}
foreach ($img in $images) { if ($img) { $bw.Write($img) } }
$bw.Flush()
[IO.File]::WriteAllBytes($Out, $ms.ToArray())
$bw.Dispose(); $ms.Dispose()

$sizes = @()
for ($i = 0; $i -lt $count; $i++) {
    $w = $group[6 + $i * 14 + 0]; if ($w -eq 0) { $w = 256 }
    $sizes += "$w"
}
Write-Output ("SAVED {0} ({1} images: {2})" -f $Out, $count, ($sizes -join ", "))
