# pdb_syms.ps1 - dump every function symbol from a PDB as name<TAB>rva.
#
#   powershell -File tools\pdb_syms.ps1 -Pdb "...\SteefFinal.pdb" -Out syms.tsv
#   powershell -File tools\pdb_syms.ps1 -Pdb "...\SteefFinal.pdb" -Rva 0x9BF47
#
# Why this exists: pdb_resolve.ps1 and pdb_dump.ps1 both take an IMAGE and call
# SymLoadModuleEx on it. That works for a PE, but the beta ships an XBE, which
# DbgHelp refuses ("SymLoadModuleEx failed: 0") - and the original SteefFinal.exe
# the PDB was built against does not exist. So guest addresses from a stall or crash
# could not be turned into function names at all.
#
# DbgHelp will load a .pdb directly if it is passed as the module image with an
# explicit base and size, which is what this does. The result is the missing half of
# the toolchain: xbe_symbolize.py already maps guest VAs to names given a TSV and a
# delta, it just had no way to get the TSV.

param(
    [Parameter(Mandatory = $true)][string]$Pdb,
    [string]$Out = "",
    [string[]]$Rva = @(),
    [int]$Base = 0x10000000
)

if (-not (Test-Path $Pdb)) { Write-Output "NOT FOUND: $Pdb"; exit 1 }

if (-not ('PdbSyms' -as [type])) {
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class PdbSyms {
    [DllImport("dbghelp.dll", SetLastError=true)]
    public static extern bool SymInitialize(IntPtr h, string path, bool invade);
    [DllImport("dbghelp.dll", SetLastError=true)]
    public static extern ulong SymLoadModuleEx(IntPtr h, IntPtr file, string img, string mod,
        ulong baseAddr, uint size, IntPtr data, uint flags);
    [DllImport("dbghelp.dll", SetLastError=true)]
    public static extern uint SymSetOptions(uint opts);
    [DllImport("dbghelp.dll", SetLastError=true)]
    public static extern bool SymCleanup(IntPtr h);

    [StructLayout(LayoutKind.Sequential)]
    public struct SYMBOL_INFO {
        public uint SizeOfStruct, TypeIndex;
        public ulong Reserved0, Reserved1;
        public uint Index, Size;
        public ulong ModBase;
        public uint Flags;
        public ulong Value, Address;
        public uint Register, Scope, Tag, NameLen, MaxNameLen;
        public byte NameFirst;
    }

    public delegate bool EnumProc(IntPtr sym, uint size, IntPtr ctx);
    [DllImport("dbghelp.dll", SetLastError=true)]
    public static extern bool SymEnumSymbols(IntPtr h, ulong modBase, string mask, EnumProc cb, IntPtr ctx);

    public static List<string> Names = new List<string>();
    public static List<ulong> Addrs = new List<ulong>();

    public static bool Collect(IntPtr symPtr, uint size, IntPtr ctx) {
        SYMBOL_INFO si = (SYMBOL_INFO)Marshal.PtrToStructure(symPtr, typeof(SYMBOL_INFO));
        // Name is a char[] immediately after the fixed fields.
        IntPtr namePtr = (IntPtr)((long)symPtr + (long)Marshal.OffsetOf(typeof(SYMBOL_INFO), "NameFirst"));
        string n = Marshal.PtrToStringAnsi(namePtr, (int)si.NameLen);
        if (!string.IsNullOrEmpty(n)) { Names.Add(n); Addrs.Add(si.Address); }
        return true;
    }
}
"@
}

$SYMOPT_UNDNAME = 0x00000002
$SYMOPT_LOAD_LINES = 0x00000010
[void][PdbSyms]::SymSetOptions($SYMOPT_UNDNAME -bor $SYMOPT_LOAD_LINES)

$h = [IntPtr]12345   # any unique pseudo-handle; we never touch a real process
if (-not [PdbSyms]::SymInitialize($h, (Split-Path $Pdb), $false)) {
    Write-Output "SymInitialize failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    exit 2
}

# Size must be large enough to cover the whole image; the PDB itself carries the
# section layout, so an over-estimate is harmless.
$modBase = [PdbSyms]::SymLoadModuleEx($h, [IntPtr]::Zero, $Pdb, "beta", [uint64]$Base, 0x02000000, [IntPtr]::Zero, 0)
if ($modBase -eq 0) {
    Write-Output "SymLoadModuleEx failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    [void][PdbSyms]::SymCleanup($h)
    exit 3
}

$cb = [PdbSyms+EnumProc]{ param($s,$z,$c) return [PdbSyms]::Collect($s,$z,$c) }
[void][PdbSyms]::SymEnumSymbols($h, $modBase, "*", $cb, [IntPtr]::Zero)
[void][PdbSyms]::SymCleanup($h)

$n = [PdbSyms]::Names.Count
Write-Output "symbols: $n"
if ($n -eq 0) { exit 4 }

# Build (rva, name) sorted once, reused for both output modes.
$rows = New-Object 'System.Collections.Generic.List[object]'
for ($i = 0; $i -lt $n; $i++) {
    $rows.Add([pscustomobject]@{ Rva = [int64]([PdbSyms]::Addrs[$i] - $Base); Name = [PdbSyms]::Names[$i] })
}
$rows = $rows | Where-Object { $_.Rva -gt 0 } | Sort-Object Rva

if ($Out) {
    $sb = New-Object Text.StringBuilder
    foreach ($r in $rows) { [void]$sb.AppendLine(('{0:x}' -f $r.Rva) + "`t" + $r.Name) }
    [IO.File]::WriteAllText($Out, $sb.ToString())
    Write-Output "wrote $Out"
}

foreach ($t in $Rva) {
    $target = [Convert]::ToInt64(($t -replace '^0x',''), 16)
    $best = $null
    foreach ($r in $rows) { if ($r.Rva -le $target) { $best = $r } else { break } }
    if ($best) { Write-Output ("0x{0:X}  ->  {1}+0x{2:X}" -f $target, $best.Name, ($target - $best.Rva)) }
    else       { Write-Output ("0x{0:X}  ->  (below first symbol)" -f $target) }
}
