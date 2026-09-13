# ds3d_pdb_types.ps1 - dump struct layouts and function signatures straight from a
# PDB through DbgHelp's type API (SymEnumTypes / SymGetTypeInfo / SymSetContext).
#
#   powershell -File tools\ds3d_pdb_types.ps1 -Pdb <SteefFinal.pdb> -Types "DS3D*","*3DCalculator*"
#   powershell -File tools\ds3d_pdb_types.ps1 -Pdb <SteefFinal.pdb> -Funcs "DirectSound::CDirectSound3DCalculator::Calculate3D"
#   powershell -File tools\ds3d_pdb_types.ps1 -Pdb <SteefFinal.pdb> -FuncRva 0x1c3ab1,0x1c22b2
#   powershell -File tools\ds3d_pdb_types.ps1 -Pdb <SteefFinal.pdb> -ListTypes "*Sound*"
#
# Why this exists: pdb_syms.ps1 only enumerates function symbols. The beta's DSOUND
# is an LTCG build whose entry points take arguments in registers, and the 3D
# calculator's structs (DS3DVOICEDATA and friends) exist nowhere in Cxbx. The PDB
# carries full type info, so this is the authoritative way to get:
#   -Types    every member of a UDT with its byte offset, size and type, nested UDTs
#             expanded inline with absolute offsets (so vtable slots / embedded
#             objects show up where they really are)
#   -Funcs /  the function's declared calling convention, return type, parameter
#   -FuncRva  types, and - via SymSetContext - each parameter's actual LOCATION
#             (enregistered: which register; regrel: [reg+off]) as recorded by the
#             compiler, which is what settles the LTCG question per entry point
#   -ListTypes just the names/sizes of matching UDTs (to find what to dump)
#
# Loads the PDB as the module image exactly like pdb_syms.ps1 (base 0x10000000, so
# rva = addr - base). Read-only; writes nothing.

param(
    [Parameter(Mandatory = $true)][string]$Pdb,
    [string[]]$Types = @(),
    [string[]]$Funcs = @(),
    [string[]]$FuncRva = @(),
    [string[]]$ListTypes = @(),
    [int]$Depth = 3,
    [int]$Base = 0x10000000
)

if (-not (Test-Path $Pdb)) { Write-Output "NOT FOUND: $Pdb"; exit 1 }

if (-not ('Ds3dPdb' -as [type])) {
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;

public static class Ds3dPdb {
    [DllImport("dbghelp.dll", SetLastError=true, CharSet=CharSet.Ansi)]
    public static extern bool SymInitialize(IntPtr h, string path, bool invade);
    [DllImport("dbghelp.dll", SetLastError=true, CharSet=CharSet.Ansi)]
    public static extern ulong SymLoadModuleEx(IntPtr h, IntPtr file, string img, string mod,
        ulong baseAddr, uint size, IntPtr data, uint flags);
    [DllImport("dbghelp.dll")]
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
    [StructLayout(LayoutKind.Sequential)]
    public struct IMAGEHLP_STACK_FRAME {
        public ulong InstructionOffset, ReturnOffset, FrameOffset, StackOffset, BackingStoreOffset, FuncTableEntry;
        public ulong P0, P1, P2, P3;
        public ulong R0, R1, R2, R3, R4;
        public int Virtual;
        public uint Reserved2;
    }

    public delegate bool EnumProc(IntPtr sym, uint size, IntPtr ctx);
    [DllImport("dbghelp.dll", SetLastError=true)]
    public static extern bool SymEnumTypes(IntPtr h, ulong modBase, EnumProc cb, IntPtr ctx);
    [DllImport("dbghelp.dll", SetLastError=true, CharSet=CharSet.Ansi)]
    public static extern bool SymEnumSymbols(IntPtr h, ulong modBase, string mask, EnumProc cb, IntPtr ctx);
    [DllImport("dbghelp.dll", SetLastError=true, CharSet=CharSet.Ansi)]
    public static extern bool SymFromName(IntPtr h, string name, IntPtr sym);
    [DllImport("dbghelp.dll", SetLastError=true)]
    public static extern bool SymFromAddr(IntPtr h, ulong addr, out ulong disp, IntPtr sym);
    [DllImport("dbghelp.dll", SetLastError=true)]
    public static extern bool SymGetTypeInfo(IntPtr h, ulong modBase, uint typeId, int getType, IntPtr outp);
    [DllImport("dbghelp.dll", SetLastError=true)]
    public static extern bool SymSetContext(IntPtr h, ref IMAGEHLP_STACK_FRAME frame, IntPtr ctx);
    [DllImport("kernel32.dll")]
    public static extern IntPtr LocalFree(IntPtr p);

    const int TI_GET_SYMTAG = 0, TI_GET_SYMNAME = 1, TI_GET_LENGTH = 2, TI_GET_TYPE = 3, TI_GET_TYPEID = 4,
        TI_GET_BASETYPE = 5, TI_FINDCHILDREN = 7, TI_GET_DATAKIND = 8, TI_GET_OFFSET = 10, TI_GET_VALUE = 11,
        TI_GET_COUNT = 12, TI_GET_CHILDRENCOUNT = 13, TI_GET_BITPOSITION = 14, TI_GET_CLASSPARENTID = 18,
        TI_GET_UDTKIND = 24, TI_GET_CALLING_CONVENTION = 26, TI_GET_IS_REFERENCE = 31, TI_GET_THISADJUST = 23;

    public static IntPtr H = (IntPtr)4242;
    public static ulong Mod;
    public static StringBuilder Out = new StringBuilder();

    static bool GetDw(uint ti, int what, out uint v) {
        IntPtr p = Marshal.AllocHGlobal(8); Marshal.WriteInt64(p, 0);
        bool ok = SymGetTypeInfo(H, Mod, ti, what, p);
        v = (uint)Marshal.ReadInt32(p); Marshal.FreeHGlobal(p); return ok;
    }
    static uint Dw(uint ti, int what) { uint v; GetDw(ti, what, out v); return v; }
    static ulong U64(uint ti, int what) {
        IntPtr p = Marshal.AllocHGlobal(8); Marshal.WriteInt64(p, 0);
        SymGetTypeInfo(H, Mod, ti, what, p);
        ulong v = (ulong)Marshal.ReadInt64(p); Marshal.FreeHGlobal(p); return v;
    }
    static string Name(uint ti) {
        IntPtr p = Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(p, IntPtr.Zero);
        string s = "";
        if (SymGetTypeInfo(H, Mod, ti, TI_GET_SYMNAME, p)) {
            IntPtr sp = Marshal.ReadIntPtr(p);
            if (sp != IntPtr.Zero) { s = Marshal.PtrToStringUni(sp); LocalFree(sp); }
        }
        Marshal.FreeHGlobal(p); return s;
    }
    static uint[] Children(uint ti) {
        uint n = Dw(ti, TI_GET_CHILDRENCOUNT);
        if (n == 0) return new uint[0];
        IntPtr p = Marshal.AllocHGlobal((int)(8 + 4 * n));
        Marshal.WriteInt32(p, 0, (int)n); Marshal.WriteInt32(p, 4, 0);
        uint[] r = new uint[0];
        if (SymGetTypeInfo(H, Mod, ti, TI_FINDCHILDREN, p)) {
            r = new uint[n];
            for (int i = 0; i < n; i++) r[i] = (uint)Marshal.ReadInt32(p, 8 + 4 * i);
        }
        Marshal.FreeHGlobal(p); return r;
    }
    public static string TypeName(uint ti) {
        if (ti == 0) return "?";
        uint tag = Dw(ti, TI_GET_SYMTAG);
        switch (tag) {
            case 16: {
                uint bt = Dw(ti, TI_GET_BASETYPE); ulong len = U64(ti, TI_GET_LENGTH);
                switch (bt) {
                    case 1: return "void"; case 2: return "char"; case 3: return "wchar_t";
                    case 6: return len == 1 ? "int8" : len == 2 ? "int16" : len == 8 ? "int64" : "int";
                    case 7: return len == 1 ? "uint8" : len == 2 ? "uint16" : len == 8 ? "uint64" : "uint";
                    case 8: return len == 8 ? "double" : "float";
                    case 10: return "bool"; case 13: return "long"; case 14: return "ulong"; case 31: return "HRESULT";
                    default: return "bt" + bt + "_" + len;
                }
            }
            case 14: { uint t = Dw(ti, TI_GET_TYPE); uint r; bool isref = GetDw(ti, TI_GET_IS_REFERENCE, out r) && r != 0; return TypeName(t) + (isref ? "&" : "*"); }
            case 15: { uint t = Dw(ti, TI_GET_TYPE); uint n = Dw(ti, TI_GET_COUNT); return TypeName(t) + "[" + n + "]"; }
            case 11: case 12: case 17: return Name(ti);
            case 13: return "<func>";
            default: return "<tag" + tag + ">";
        }
    }
    static string CallConv(uint cc) {
        switch (cc) {
            case 0: return "NEAR_C(cdecl)"; case 4: return "NEAR_FAST(fastcall)"; case 7: return "NEAR_STD(stdcall)";
            case 11: return "THISCALL"; case 22: return "CLRCALL"; default: return "cc" + cc;
        }
    }
    // Dump one UDT; nested UDT members are expanded inline (depth-limited) with absolute offsets.
    public static void DumpUdt(uint ti, uint baseOff, int depth, int maxDepth, string indent) {
        ulong len = U64(ti, TI_GET_LENGTH);
        uint kind = Dw(ti, TI_GET_UDTKIND);
        Out.AppendFormat("{0}{1} {2}  size=0x{3:x}\n", indent, kind == 0 ? "struct" : kind == 1 ? "class" : kind == 2 ? "union" : "udt?", Name(ti), len);
        uint[] kids = Children(ti);
        int nfunc = 0;
        var fnames = new List<string>();
        foreach (uint c in kids) {
            uint tag = Dw(c, TI_GET_SYMTAG);
            if (tag == 5) { nfunc++; fnames.Add(Name(c)); continue; }
            if (tag == 18) { // base class
                uint off = Dw(c, TI_GET_OFFSET); uint bt = Dw(c, TI_GET_TYPE);
                Out.AppendFormat("{0}  +0x{1:x3}  base {2}  size=0x{3:x}\n", indent, baseOff + off, TypeName(bt), U64(bt, TI_GET_LENGTH));
                if (depth < maxDepth) DumpUdt(bt, baseOff + off, depth + 1, maxDepth, indent + "      ");
                continue;
            }
            if (tag == 25) { Out.AppendFormat("{0}  +0x{1:x3}  vftable*\n", indent, baseOff + Dw(c, TI_GET_OFFSET)); continue; }
            if (tag == 7) {
                uint dk = Dw(c, TI_GET_DATAKIND); uint t = Dw(c, TI_GET_TYPE); string tn = TypeName(t);
                if (dk == 7) {
                    uint off = Dw(c, TI_GET_OFFSET); ulong tl = U64(t, TI_GET_LENGTH);
                    uint bp; bool isbit = GetDw(c, TI_GET_BITPOSITION, out bp);
                    Out.AppendFormat("{0}  +0x{1:x3}  {2,-34} {3}  size=0x{4:x}{5}\n", indent, baseOff + off, Name(c), tn, tl, isbit ? "  bit" + bp + "x" + U64(c, TI_GET_LENGTH) : "");
                    uint ttag = Dw(t, TI_GET_SYMTAG);
                    uint inner = t; int guard = 0;
                    while (guard++ < 4 && (ttag == 17)) { inner = Dw(inner, TI_GET_TYPE); ttag = Dw(inner, TI_GET_SYMTAG); }
                    if (ttag == 11 && depth < maxDepth) DumpUdt(inner, baseOff + off, depth + 1, maxDepth, indent + "      ");
                    else if (ttag == 15 && depth < maxDepth) {
                        uint et = Dw(inner, TI_GET_TYPE); uint etag = Dw(et, TI_GET_SYMTAG);
                        while (etag == 17) { et = Dw(et, TI_GET_TYPE); etag = Dw(et, TI_GET_SYMTAG); }
                        if (etag == 11) { Out.AppendFormat("{0}      [element 0 of {1}:]\n", indent, Dw(inner, TI_GET_COUNT)); DumpUdt(et, baseOff + off, depth + 1, maxDepth, indent + "      "); }
                    }
                } else if (dk == 8) {
                    Out.AppendFormat("{0}  static  {1,-34} {2}\n", indent, Name(c), tn);
                } else if (dk == 9) {
                    Out.AppendFormat("{0}  const   {1,-34} {2}\n", indent, Name(c), tn);
                }
                continue;
            }
        }
        if (nfunc > 0) Out.AppendFormat("{0}  ({1} member functions: {2})\n", indent, nfunc, string.Join(", ", fnames.ToArray()));
    }

    public static List<uint> FoundTypes = new List<uint>();
    public static List<string> FoundNames = new List<string>();
    static Regex Want;
    public static bool CollectType(IntPtr symPtr, uint size, IntPtr ctx) {
        SYMBOL_INFO si = (SYMBOL_INFO)Marshal.PtrToStructure(symPtr, typeof(SYMBOL_INFO));
        IntPtr namePtr = (IntPtr)((long)symPtr + (long)Marshal.OffsetOf(typeof(SYMBOL_INFO), "NameFirst"));
        string n = Marshal.PtrToStringAnsi(namePtr, (int)si.NameLen);
        if (si.Tag == 11 && Want.IsMatch(n)) { FoundTypes.Add(si.TypeIndex); FoundNames.Add(n); }
        return true;
    }
    public static void DumpTypes(string mask, int maxDepth, bool listOnly) {
        Want = new Regex("^" + Regex.Escape(mask).Replace("\\*", ".*").Replace("\\?", ".") + "$", RegexOptions.IgnoreCase);
        FoundTypes.Clear(); FoundNames.Clear();
        SymEnumTypes(H, Mod, CollectType, IntPtr.Zero);
        var seen = new HashSet<string>();
        for (int i = 0; i < FoundTypes.Count; i++) {
            if (!seen.Add(FoundNames[i])) continue;
            if (listOnly) Out.AppendFormat("{0}  size=0x{1:x}  members={2}\n", FoundNames[i], U64(FoundTypes[i], TI_GET_LENGTH), Dw(FoundTypes[i], TI_GET_CHILDRENCOUNT));
            else { DumpUdt(FoundTypes[i], 0, 0, maxDepth, ""); Out.Append("\n"); }
        }
        if (FoundTypes.Count == 0) Out.AppendFormat("(no UDT matches {0})\n", mask);
    }

    // ---- functions ----
    public static List<string> Locals = new List<string>();
    static string Reg(uint r) {
        switch (r) { case 17: return "eax"; case 18: return "ecx"; case 19: return "edx"; case 20: return "ebx"; case 21: return "esp"; case 22: return "ebp"; case 23: return "esi"; case 24: return "edi"; default: return "reg" + r; }
    }
    public static bool CollectLocal(IntPtr symPtr, uint size, IntPtr ctx) {
        SYMBOL_INFO si = (SYMBOL_INFO)Marshal.PtrToStructure(symPtr, typeof(SYMBOL_INFO));
        IntPtr namePtr = (IntPtr)((long)symPtr + (long)Marshal.OffsetOf(typeof(SYMBOL_INFO), "NameFirst"));
        string n = Marshal.PtrToStringAnsi(namePtr, (int)si.NameLen);
        string loc;
        if ((si.Flags & 0x08) != 0) loc = "in " + Reg(si.Register);
        else if ((si.Flags & 0x10) != 0) loc = "[" + Reg(si.Register) + (((long)si.Address) >= 0 ? "+" : "") + ((long)si.Address).ToString("x") + "]";
        else if ((si.Flags & 0x20) != 0) loc = "[frame" + (((long)si.Address) >= 0 ? "+" : "") + ((long)si.Address).ToString("x") + "]";
        else loc = "flags=0x" + si.Flags.ToString("x") + " addr=0x" + si.Address.ToString("x");
        Locals.Add(string.Format("    {0,-8} {1,-28} {2,-30} {3}", (si.Flags & 0x40) != 0 ? "param" : "local", n, TypeName(si.TypeIndex), loc));
        return true;
    }
    static void DumpFuncSym(SYMBOL_INFO si, string n) {
        Out.AppendFormat("FUNCTION {0}\n  rva=0x{1:x} guest=0x{2:x} size=0x{3:x} tag={4}\n", n, si.Address - Mod, si.Address - Mod + 0x10920, si.Size, si.Tag);
        uint ft = si.TypeIndex;
        if (ft != 0) {
            uint cc = Dw(ft, TI_GET_CALLING_CONVENTION);
            uint rt = Dw(ft, TI_GET_TYPE);
            uint cp; bool hasCp = GetDw(ft, TI_GET_CLASSPARENTID, out cp);
            uint ta; bool hasTa = GetDw(ft, TI_GET_THISADJUST, out ta);
            Out.AppendFormat("  declared: {0}  returns {1}{2}{3}\n", CallConv(cc), TypeName(rt), hasCp ? "  class=" + TypeName(cp) : "", hasTa ? "  thisadjust=" + ta : "");
            uint[] args = Children(ft); int i = 0;
            foreach (uint a in args) { if (Dw(a, TI_GET_SYMTAG) == 20) Out.AppendFormat("    arg{0}: {1}\n", i++, TypeName(Dw(a, TI_GET_TYPE))); }
        }
        IMAGEHLP_STACK_FRAME f = new IMAGEHLP_STACK_FRAME(); f.InstructionOffset = si.Address;
        Locals.Clear();
        if (SymSetContext(H, ref f, IntPtr.Zero)) {
            SymEnumSymbols(H, 0, "*", CollectLocal, IntPtr.Zero);
            Out.Append("  locals/params as recorded by the compiler:\n");
            foreach (string l in Locals) Out.Append(l + "\n");
        } else Out.AppendFormat("  SymSetContext failed: {0}\n", Marshal.GetLastWin32Error());
        Out.Append("\n");
    }
    public static void DumpFunc(string name) {
        int sz = 88 + 2048; IntPtr p = Marshal.AllocHGlobal(sz);
        for (int i = 0; i < sz; i += 8) Marshal.WriteInt64(p, i, 0);
        Marshal.WriteInt32(p, 0, 88); Marshal.WriteInt32(p, 80, 2000);
        if (!SymFromName(H, name, p)) { Out.AppendFormat("FUNCTION {0}: SymFromName failed {1}\n\n", name, Marshal.GetLastWin32Error()); Marshal.FreeHGlobal(p); return; }
        SYMBOL_INFO si = (SYMBOL_INFO)Marshal.PtrToStructure(p, typeof(SYMBOL_INFO));
        DumpFuncSym(si, name); Marshal.FreeHGlobal(p);
    }
    public static void DumpFuncRva(ulong rva) {
        int sz = 88 + 2048; IntPtr p = Marshal.AllocHGlobal(sz);
        for (int i = 0; i < sz; i += 8) Marshal.WriteInt64(p, i, 0);
        Marshal.WriteInt32(p, 0, 88); Marshal.WriteInt32(p, 80, 2000);
        ulong disp;
        if (!SymFromAddr(H, Mod + rva, out disp, p)) { Out.AppendFormat("rva 0x{0:x}: SymFromAddr failed {1}\n\n", rva, Marshal.GetLastWin32Error()); Marshal.FreeHGlobal(p); return; }
        SYMBOL_INFO si = (SYMBOL_INFO)Marshal.PtrToStructure(p, typeof(SYMBOL_INFO));
        IntPtr namePtr = (IntPtr)((long)p + (long)Marshal.OffsetOf(typeof(SYMBOL_INFO), "NameFirst"));
        string n = Marshal.PtrToStringAnsi(namePtr, (int)si.NameLen);
        if (disp != 0) Out.AppendFormat("(rva 0x{0:x} is {1}+0x{2:x})\n", rva, n, disp);
        DumpFuncSym(si, n); Marshal.FreeHGlobal(p);
    }
}
"@
}

$SYMOPT_UNDNAME = 0x00000002
$SYMOPT_LOAD_LINES = 0x00000010
[void][Ds3dPdb]::SymSetOptions($SYMOPT_UNDNAME -bor $SYMOPT_LOAD_LINES)

if (-not [Ds3dPdb]::SymInitialize([Ds3dPdb]::H, (Split-Path $Pdb), $false)) {
    Write-Output "SymInitialize failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"; exit 2
}
[Ds3dPdb]::Mod = [Ds3dPdb]::SymLoadModuleEx([Ds3dPdb]::H, [IntPtr]::Zero, $Pdb, "beta", [uint64]$Base, 0x02000000, [IntPtr]::Zero, 0)
if ([Ds3dPdb]::Mod -eq 0) {
    Write-Output "SymLoadModuleEx failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    [void][Ds3dPdb]::SymCleanup([Ds3dPdb]::H); exit 3
}

# `powershell -File` hands a comma list over as ONE literal string, so split here
# too; that lets both "-Types a,b" and -Types "a","b" work.
function Split-List($list) { @($list | ForEach-Object { $_ -split ',' } | Where-Object { $_ -ne '' }) }

foreach ($m in (Split-List $ListTypes)) { [Ds3dPdb]::DumpTypes($m, $Depth, $true) }
foreach ($m in (Split-List $Types))     { [Ds3dPdb]::DumpTypes($m, $Depth, $false) }
foreach ($f in (Split-List $Funcs))     { [Ds3dPdb]::DumpFunc($f) }
foreach ($r in (Split-List $FuncRva))   { [Ds3dPdb]::DumpFuncRva([Convert]::ToUInt64(($r -replace '^0x',''), 16)) }

[void][Ds3dPdb]::SymCleanup([Ds3dPdb]::H)
Write-Output ([Ds3dPdb]::Out.ToString())
