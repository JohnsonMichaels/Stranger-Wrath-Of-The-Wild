#!/usr/bin/env python3
"""Generate tools/lvl_schema.json - the HD release's ParamIO schema.

Everything here is READ OUT of stranger.exe, not guessed.  The release keeps its
reflection tables as code: one static initialiser per class fills a NULL-
terminated array of heap param descriptors

    param descriptor  { +0 vftable , +4 nameHash , +8 member offset ,
                        +0xc elem-def , +0x10 elem-param }     (0xc or 0x14 bytes)
    class descriptor  { +0 nameHash , +4 param array , +8 count , +0xc spare }

and the descriptor's vftable resolves through RTTI to the concrete
CScalarDef<>/CBasicDef<>/CClassDef<>/CVectorDef<>/CEnumDef<>/CParentDef<>
template instantiation, which names the serialised type exactly.

Serialisation order is the ORDER OF THE PARAM ARRAY (verified against shipped
levels), not the member offsets - a record is the 8-byte object header followed
by every param packed back to back, parents first.

    python make_lvl_schema.py [path\\to\\stranger.exe] [out.json]
"""
import json
import os
import re
import struct
import sys

try:
    import capstone
except ImportError:
    sys.exit('capstone is required: py -m pip install capstone')

DEFAULT_EXE = (r"C:\Program Files (x86)\Steam\steamapps\common"
               r"\Stranger's Wrath\bin\stranger.exe")


class PE:
    def __init__(self, path):
        self.data = d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3C)[0]
        assert d[pe:pe + 4] == b'PE\0\0', 'not a PE image'
        nsec = struct.unpack_from('<H', d, pe + 6)[0]
        optsz = struct.unpack_from('<H', d, pe + 20)[0]
        self.imagebase = struct.unpack_from('<I', d, pe + 24 + 28)[0]
        so = pe + 24 + optsz
        self.sections = []
        for i in range(nsec):
            o = so + i * 40
            name = d[o:o + 8].rstrip(b'\0').decode('latin1')
            vsz, va, rsz, ra = struct.unpack_from('<IIII', d, o + 8)
            self.sections.append((name, va, vsz, ra, rsz))

    def off2va(self, off):
        for _n, va, _vs, ra, rs in self.sections:
            if ra <= off < ra + rs:
                return self.imagebase + va + (off - ra)
        return None

    def va2off(self, va):
        rva = va - self.imagebase
        for _n, va0, vs, ra, rs in self.sections:
            if va0 <= rva < va0 + max(vs, rs):
                o = ra + (rva - va0)
                if o < ra + rs:
                    return o
        return None

    def cstr(self, va):
        o = self.va2off(va)
        if o is None:
            return None
        e = self.data.find(b'\0', o)
        if e < 0 or e - o > 300:
            return None
        try:
            return self.data[o:e].decode('ascii')
        except UnicodeDecodeError:
            return None


def rtti_name(pe, vt):
    """vftable[-1] -> CompleteObjectLocator -> TypeDescriptor -> decorated name"""
    o = pe.va2off(vt - 4)
    if o is None:
        return None
    col = struct.unpack_from('<I', pe.data, o)[0]
    o2 = pe.va2off(col)
    if o2 is None:
        return None
    td = struct.unpack_from('<I', pe.data, o2 + 0xC)[0]
    o3 = pe.va2off(td)
    if o3 is None:
        return None
    e = pe.data.find(b'\0', o3 + 8)
    if e < 0 or e - o3 > 400:
        return None
    try:
        s = pe.data[o3 + 8:e].decode('ascii')
    except UnicodeDecodeError:
        return None
    return re.sub(r'@+$', '', s.replace('.?AV', '').replace('.?AU', ''))


IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_:]{1,60}$')


def scan_slots(pe):
    """functions in .text are int3-padded; disassemble from each boundary so the
    linear sweep can never desync, and run the descriptor state machine."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    txt = [s for s in pe.sections if s[0] == '.text'][0]
    lo, hi = txt[3], txt[3] + txt[4]
    d = pe.data
    starts, i = [], lo
    while True:
        j = d.find(b'\xcc\xcc\xcc', i, hi)
        if j < 0:
            break
        k = j
        while k < hi and d[k] == 0xCC:
            k += 1
        starts.append(k)
        i = k
    slots = {}
    for st in starts:
        cur = {}
        for ins in md.disasm(d[st:min(hi, st + 0x4000)], pe.off2va(st)):
            if ins.mnemonic == 'int3':
                break
            if ins.mnemonic != 'mov':
                continue
            o = ins.op_str
            m = re.match(r'^(e[a-z]{2}), (0x[0-9a-f]+)$', o)
            if m:
                s = pe.cstr(int(m.group(2), 16))
                if s and IDENT.match(s):
                    cur['name'] = s
                continue
            m = re.match(r'^dword ptr \[e[a-z]{2} \+ 8\], (0x[0-9a-f]+|\d+|e[a-z]{2})$', o)
            if m:
                v = m.group(1)
                cur['off'] = 0 if v.startswith('e') else int(v, 0)
                continue
            m = re.match(r'^dword ptr \[e[a-z]{2}\], (0x[0-9a-f]+)$', o)
            if m:
                cur.setdefault('vt', []).append(int(m.group(1), 16))
                continue
            m = re.match(r'^dword ptr \[e[a-z]{2} \+ 0xc\], (0x[0-9a-f]+)$', o)
            if m:
                cur.setdefault('elem', []).append(int(m.group(1), 16))
                continue
            m = re.match(r'^dword ptr \[(0x[0-9a-f]+)\], e[a-z]{2}$', o)
            if m:
                if 'name' in cur and 'vt' in cur:
                    slots[int(m.group(1), 16)] = {
                        'name': cur['name'], 'off': cur.get('off'),
                        'type': rtti_name(pe, cur['vt'][-1]),
                        'elemtype': rtti_name(pe, cur['elem'][-1]) if cur.get('elem') else None,
                    }
                cur = {}
    return slots


NAMEPAT = re.compile(rb'([\xb8-\xbf])(....)([\xb8-\xbf])(....)\xe8(....)', re.S)


def scan_class_names(pe):
    """mov <reg>,"ClassName" ; mov <reg>,<descriptor> ; call CRC-string"""
    txt = [s for s in pe.sections if s[0] == '.text'][0]
    lo, hi = txt[3], txt[3] + txt[4]
    out = {}
    for m in NAMEPAT.finditer(pe.data, lo, hi):
        a = struct.unpack('<I', m.group(2))[0]
        b = struct.unpack('<I', m.group(4))[0]
        for sv, dv in ((a, b), (b, a)):
            s = pe.cstr(sv)
            if s and IDENT.match(s) and 0x700000 <= dv < 0xB00000 \
                    and pe.va2off(dv) is not None:
                out.setdefault(dv, s)
    return out


def kind_of(t):
    if not t:
        return None
    if t.startswith('?$CParentDef'):
        return 'parent'
    if t.startswith('?$CVectorDef'):
        return 'vector'
    if t.startswith('?$CArrayDef'):
        return 'array'
    if t.startswith('?$CEnumDef'):
        return 'u32'
    if t.startswith('?$CBasicDef@_N'):
        return 'bool'
    if t.startswith(('?$CBasicDef@H', '?$CScalarDef@H', '?$CScalarDef@K',
                     '?$CScalarDef@I', '?$CScalarDef@J')):
        return 'u32'
    if t.startswith('?$CScalarDef@M'):
        return 'f32'
    if t.startswith('?$CClassDef@VToken'):
        return 'token'
    if t.startswith('?$CClassDef@VStringBuffer'):
        return 'string'
    if t.startswith('?$CClassDef@VColorDW'):
        return 'u32'
    if t.startswith('?$CClassDef@VColorF'):
        return 'b16'
    if t.startswith('?$CClassDef@VVec2'):
        return 'b8'
    if t.startswith('?$CClassDef@VVec3'):
        return 'b12'
    if t.startswith('?$CClassDef@VFrame3Scaled'):
        return 'b52'
    if t.startswith('?$CClassDef@VFrame3'):
        return 'b48'
    if t.startswith('?$CClassDef@VAxialBox'):
        return 'b24'
    if t.startswith('?$CClassDef@VRectI'):
        return 'b16'
    if t.startswith('?$CClassDef'):
        return 'object'
    return None


def elem_of(et):
    if not et:
        return None
    for pat, k in (('CClassDef@VVec3', 'b12'), ('CClassDef@VVec2', 'b8'),
                   ('CClassDef@VStringBuffer', 'string'),
                   ('CClassDef@VToken', 'token'), ('CBasicDef@H', 'u32'),
                   ('CScalarDef@H', 'u32'), ('CScalarDef@M', 'f32'),
                   ('CBasicDef@_N', 'bool'), ('CClassDef', 'object')):
        if pat in et:
            return k
    return None


def main(argv):
    exe = argv[1] if len(argv) > 1 else DEFAULT_EXE
    out = argv[2] if len(argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'lvl_schema.json')
    pe = PE(exe)
    slots = scan_slots(pe)
    names = scan_class_names(pe)

    ks = sorted(slots)
    arrays, cur = [], [ks[0]]
    for a, b in zip(ks, ks[1:]):
        if b == a + 4:
            cur.append(b)
        else:
            arrays.append(cur)
            cur = [b]
    arrays.append(cur)

    arr_list = []
    for r in arrays:
        ps = []
        for s in r:
            v = slots[s]
            ps.append({'name': v['name'], 'kind': kind_of(v['type']),
                       'elem': elem_of(v['elemtype']), 'type': v['type']})
        arr_list.append({'va': r[0], 'params': ps})
    start_index = {a['va']: i for i, a in enumerate(arr_list)}

    classes = {}
    for dest, nm in names.items():
        o = pe.va2off(dest)
        if o is None:
            continue
        arr = struct.unpack_from('<I', pe.data, o + 4)[0]
        if arr in start_index:
            classes.setdefault(nm, start_index[arr])

    json.dump({'arrays': arr_list, 'classes': classes},
              open(out, 'w'), indent=0)
    print('%s: %d param slots, %d arrays, %d named classes -> %s'
          % (os.path.basename(exe), len(slots), len(arr_list), len(classes), out))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
