# pdb_dump.ps1 - enumerate every symbol in a PDB via the DbgHelp API and emit
# TAB-separated "rva<TAB>size<TAB>tag<TAB>flags<TAB>name" to stdout.
#
#   powershell -File pdb_dump.ps1 -Image <path to .exe or .pdb> -Out <tsv path> [-Mask *]
#
# The module is loaded at a synthetic base of 0x10000000 so that RVA = addr - base.
param(
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Mask = "*",
    [string]$SearchPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $SearchPath) { $SearchPath = Split-Path -Parent $Image }

$src = @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

public static class PdbDump
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

    delegate bool SymEnumSymbolsProc(IntPtr pSymInfo, uint SymbolSize, IntPtr UserContext);

    [DllImport("dbghelp.dll", SetLastError = true, CharSet = CharSet.Ansi)]
    static extern bool SymEnumSymbols(IntPtr hProcess, ulong BaseOfDll, string Mask,
        SymEnumSymbolsProc EnumSymbolsCallback, IntPtr UserContext);

    [StructLayout(LayoutKind.Sequential)]
    struct SYMBOL_INFO
    {
        public uint SizeOfStruct;
        public uint TypeIndex;
        public ulong Reserved0;
        public ulong Reserved1;
        public uint Index;
        public uint Size;
        public ulong ModBase;
        public uint Flags;
        public ulong Value;
        public ulong Address;
        public uint Register;
        public uint Scope;
        public uint Tag;
        public int NameLen;
        public int MaxNameLen;
        // Name follows inline
    }

    const uint SYMOPT_CASE_INSENSITIVE     = 0x00000001;
    const uint SYMOPT_UNDNAME              = 0x00000002;
    const uint SYMOPT_DEFERRED_LOADS       = 0x00000004;
    const uint SYMOPT_LOAD_LINES           = 0x00000010;
    const uint SYMOPT_OMAP_FIND_NEAREST    = 0x00000020;
    const uint SYMOPT_LOAD_ANYTHING        = 0x00000040;
    const uint SYMOPT_NO_UNQUALIFIED_LOADS = 0x00000100;
    const uint SYMOPT_DEBUG                = 0x80000000;
    const uint SYMOPT_NO_PROMPTS           = 0x00080000;

    const ulong BASE = 0x10000000UL;

    public static int Run(string image, string outPath, string mask, string searchPath, out string diag)
    {
        var sb = new StringBuilder();
        IntPtr hProc = new IntPtr(0x1234);   // any unique pseudo-handle
        // Do NOT set SYMOPT_UNDNAME: we want the raw decorated names so we can
        // recover the __stdcall/__fastcall arg-byte suffix ourselves.
        SymSetOptions(SYMOPT_LOAD_ANYTHING | SYMOPT_NO_PROMPTS | SYMOPT_OMAP_FIND_NEAREST);

        if (!SymInitialize(hProc, searchPath, false))
            throw new Exception("SymInitialize failed: " + Marshal.GetLastWin32Error());

        long fsize = new FileInfo(image).Length;
        uint dllSize = (uint)Math.Min(fsize * 4, 0x40000000L);
        ulong b = SymLoadModuleEx(hProc, IntPtr.Zero, image, null, BASE, dllSize, IntPtr.Zero, 0);
        if (b == 0)
        {
            int err = Marshal.GetLastWin32Error();
            SymCleanup(hProc);
            throw new Exception("SymLoadModuleEx failed: " + err);
        }
        sb.AppendLine("loaded at 0x" + b.ToString("x"));

        int count = 0;
        var w = new StreamWriter(outPath, false, new UTF8Encoding(false));
        SymEnumSymbolsProc cb = delegate(IntPtr p, uint sz, IntPtr ctx)
        {
            SYMBOL_INFO si = (SYMBOL_INFO)Marshal.PtrToStructure(p, typeof(SYMBOL_INFO));
            int nameOff = (int)Marshal.OffsetOf(typeof(SYMBOL_INFO), "MaxNameLen") + 4;
            string name = Marshal.PtrToStringAnsi(new IntPtr(p.ToInt64() + nameOff), si.NameLen);
            long rva = (long)(si.Address - b);
            w.Write(rva.ToString("x"));  w.Write('\t');
            w.Write(si.Size.ToString()); w.Write('\t');
            w.Write(si.Tag.ToString());  w.Write('\t');
            w.Write(si.Flags.ToString("x")); w.Write('\t');
            w.Write(name); w.Write('\n');
            count++;
            return true;
        };
        bool ok = SymEnumSymbols(hProc, b, mask, cb, IntPtr.Zero);
        int err2 = Marshal.GetLastWin32Error();
        w.Flush(); w.Close();
        GC.KeepAlive(cb);
        SymCleanup(hProc);
        sb.AppendLine("SymEnumSymbols returned " + ok + " lastError=" + err2 + " count=" + count);
        diag = sb.ToString();
        return count;
    }
}
'@

Add-Type -TypeDefinition $src -Language CSharp

$diag = ""
$n = [PdbDump]::Run($Image, $Out, $Mask, $SearchPath, [ref]$diag)
Write-Output $diag
Write-Output ("symbols written: {0} -> {1}" -f $n, $Out)
