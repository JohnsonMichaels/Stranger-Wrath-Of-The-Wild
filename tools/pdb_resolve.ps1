# pdb_resolve.ps1 - turn a module-relative RVA (e.g. the "Fault offset" printed by
# Windows Error Reporting) into "function + source file : line" using the module's PDB.
#
#   powershell -File pdb_resolve.ps1 -Image <path to .exe/.dll> -Rva 0x553fcd[,0x...]
#
# Why this exists: when the emulator dies without flushing its kernel log, the WER
# Application Error event is the only surviving evidence of where it died. It gives a
# faulting module and a fault offset; this converts that pair into a source line.
# The module is loaded at a synthetic base of 0x10000000 so address = BASE + RVA.
param(
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string[]]$Rva,
    [string]$SearchPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $SearchPath) { $SearchPath = Split-Path -Parent $Image }

$src = @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

public static class PdbResolve
{
    [DllImport("dbghelp.dll", SetLastError = true, CharSet = CharSet.Ansi)]
    static extern bool SymInitialize(IntPtr hProcess, string UserSearchPath, bool fInvadeProcess);
    [DllImport("dbghelp.dll", SetLastError = true)]
    static extern bool SymCleanup(IntPtr hProcess);
    [DllImport("dbghelp.dll")]
    static extern uint SymSetOptions(uint SymOptions);
    [DllImport("dbghelp.dll", SetLastError = true, CharSet = CharSet.Ansi)]
    static extern ulong SymLoadModuleEx(IntPtr hProcess, IntPtr hFile, string ImageName,
        string ModuleName, ulong BaseOfDll, uint DllSize, IntPtr Data, uint Flags);
    [DllImport("dbghelp.dll", SetLastError = true, CharSet = CharSet.Ansi)]
    static extern bool SymFromAddr(IntPtr hProcess, ulong Address, out ulong Displacement, IntPtr Symbol);
    [DllImport("dbghelp.dll", SetLastError = true, CharSet = CharSet.Ansi)]
    static extern bool SymGetLineFromAddr64(IntPtr hProcess, ulong Address, out uint Displacement, IntPtr Line);

    [StructLayout(LayoutKind.Sequential)]
    struct IMAGEHLP_LINE64
    {
        public uint SizeOfStruct; public IntPtr Key; public uint LineNumber;
        public IntPtr FileName; public ulong Address;
    }

    const uint SYMOPT_LOAD_LINES  = 0x00000010;
    const uint SYMOPT_LOAD_ANYTHING = 0x00000040;
    const uint SYMOPT_NO_PROMPTS  = 0x00080000;
    const uint SYMOPT_UNDNAME     = 0x00000002;
    const ulong BASE = 0x10000000UL;

    public static string Run(string image, ulong[] rvas, string searchPath)
    {
        var sb = new StringBuilder();
        IntPtr hProc = new IntPtr(0x4321);
        SymSetOptions(SYMOPT_LOAD_LINES | SYMOPT_LOAD_ANYTHING | SYMOPT_NO_PROMPTS | SYMOPT_UNDNAME);
        if (!SymInitialize(hProc, searchPath, false))
            throw new Exception("SymInitialize failed: " + Marshal.GetLastWin32Error());

        long fsize = new FileInfo(image).Length;
        uint dllSize = (uint)Math.Min(fsize * 8, 0x40000000L);
        ulong b = SymLoadModuleEx(hProc, IntPtr.Zero, image, null, BASE, dllSize, IntPtr.Zero, 0);
        if (b == 0) { int e = Marshal.GetLastWin32Error(); SymCleanup(hProc);
            throw new Exception("SymLoadModuleEx failed: " + e); }

        int symBufSize = 88 + 1024;
        IntPtr symBuf = Marshal.AllocHGlobal(symBufSize);
        IntPtr lineBuf = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(IMAGEHLP_LINE64)));

        foreach (ulong rva in rvas)
        {
            ulong addr = b + rva;
            for (int i = 0; i < symBufSize; i++) Marshal.WriteByte(symBuf, i, 0);
            // 64-bit SYMBOL_INFO: NameLen@76, MaxNameLen@80, Name@84, sizeof=88.
            Marshal.WriteInt32(symBuf, 0, 88);        // SizeOfStruct
            Marshal.WriteInt32(symBuf, 80, 1024);     // MaxNameLen
            ulong disp;
            string name = "<no symbol>";
            if (SymFromAddr(hProc, addr, out disp, symBuf))
            {
                int nameLen = Marshal.ReadInt32(symBuf, 76);
                name = Marshal.PtrToStringAnsi(new IntPtr(symBuf.ToInt64() + 84), nameLen)
                     + " + 0x" + disp.ToString("x");
            }
            else { name = "<no symbol> (err " + Marshal.GetLastWin32Error() + ")"; }
            string loc = "";
            var l = new IMAGEHLP_LINE64();
            l.SizeOfStruct = (uint)Marshal.SizeOf(typeof(IMAGEHLP_LINE64));
            Marshal.StructureToPtr(l, lineBuf, false);
            uint ldisp;
            if (SymGetLineFromAddr64(hProc, addr, out ldisp, lineBuf))
            {
                l = (IMAGEHLP_LINE64)Marshal.PtrToStructure(lineBuf, typeof(IMAGEHLP_LINE64));
                loc = "   " + Marshal.PtrToStringAnsi(l.FileName) + " : " + l.LineNumber
                    + " (+" + ldisp + " bytes)";
            }
            sb.AppendLine("RVA 0x" + rva.ToString("x") + "  ->  " + name + loc);
        }
        Marshal.FreeHGlobal(symBuf); Marshal.FreeHGlobal(lineBuf);
        SymCleanup(hProc);
        return sb.ToString();
    }
}
'@

Add-Type -TypeDefinition $src -Language CSharp

# -File passes "a","b" through as the single token a,b - split it back apart.
$vals = @()
foreach ($r in ($Rva -split ',')) {
    $s = $r.Trim().Trim('"').Trim("'")
    if (-not $s) { continue }
    if ($s.StartsWith("0x") -or $s.StartsWith("0X")) { $s = $s.Substring(2) }
    $vals += [uint64]::Parse($s, [System.Globalization.NumberStyles]::HexNumber)
}
Write-Output ([PdbResolve]::Run($Image, $vals, $SearchPath))
