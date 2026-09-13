#!/usr/bin/env python3
"""
lvl_v5_to_v6.py - Oddworld: Stranger's Wrath .lvl object-graph tool.

Reads the 2004 Xbox beta's version-5 .lvl files and converts them to the Steam
HD release's version-6 format.

CONTAINER (identical in v5 and v6)
----------------------------------
    0x00  u32  0x2BAD4700          magic
    0x04  u32  5 (beta) | 6 (HD)   version   (the HD loader accepts ONLY 6)
    0x08  u32  0x1A2B3C4D          section marker
    0x0C  u32  1 (v5) | 2 (v6)
    0x10  u32  0x1A2B3C4D          section marker
    0x14  u32  0x7A60600D          node-marker constant
    0x18  u32  file offset of the FIRST list node
    0x1C  ...  root object (WorldTag), inline
          nodes: [u32 0x7A60600D][u32 next_offset][object+]  absolute offsets
          u32 0x601307A6                                     end-of-list sentinel
          zero pad to 2048

v5 OBJECT ENCODING - self-describing / reflective
-------------------------------------------------
    object := u32 len, char typename[len]   # MSVC typeid().name()
              field*
              u32 0                         # end-of-object terminator
    field  := u32 size                      # INCLUDES this 12-byte header
              u32 fieldNameHash             # tag_hash of the reflected name
              u32 fieldTypeHash
              byte payload[size-12]

    A container field's payload is itself
        object  := field* , u32 0
        vector  := u32 count , count x (field* , u32 0)
    and a POD vector's payload is  u32 count , count x <element bytes>.

    A node may hold more than one object back to back (an InstancedObjectTag
    followed by the runtime class it instantiates).

v6 OBJECT ENCODING - schema-driven, NOT self-describing
-------------------------------------------------------
    object := u32 0x000B4265           # constant on every object in every v6 file
              u32 classId              # tag_hash(bare class name)
              <every param of the class, packed, parents first>

Each object in a node carries its own 8-byte header - a node with an
InstancedObjectTag plus a SimpleInstancedObject is two headers.

THE SCHEMA IS READ OUT OF THE SHIPPING EXE, NOT GUESSED
-------------------------------------------------------
stranger.exe keeps its ParamIO reflection tables as code: one static initialiser
per class fills a NULL-terminated array of param descriptors
{vftable, nameHash, memberOffset}, and the vftable resolves through RTTI to the
concrete CScalarDef<>/CBasicDef<>/CClassDef<>/CVectorDef<>/CEnumDef<>/
CParentDef<> instantiation, which names the serialised type exactly.
`make_lvl_schema.py` extracts all 2557 of them into lvl_schema.json; this tool
emits fields in that order, with those types.

Proof the order is the param-array order and not the member order: the release's
GameTagBase array is
    m_tagName m_tagZone m_isInProxyZone m_tagTransform m_illuminationColorDW
    m_snapToGround m_groupName m_startsInPurgatory m_castLightmapShadows
    m_scriptArgs m_scriptResource m_pathToken m_originalTagFile
whose member offsets are 8,0x14,0x51,0x18,0x4c,0x50,0x10,0x52,0x53,0x58,0x54,
0xc,0x5c - out of order - yet every shipped record matches the array order
byte for byte (a NamedLocation::Tag is exactly 8+88 = 96 bytes, m_groupName's
empty-string hash sits at 0x4a, and the 13-float identity Frame3Scaled sits at
0x11).

WHAT THE RELEASE ADDED AFTER THE BETA (all of it recovered from the exe)
------------------------------------------------------------------------
  GameTagBase   + m_originalTagFile (Token) after m_pathToken
  WorldTag      + m_areaAdjacency (vector) after m_proxyVisibility
  WorldTag::Zone+ m_fromTagfile, m_exportInfo (StringBuffer) after m_zoneAABB
  LevelPrefs    + m_flowMarkerPrefsFile (Token), m_townsfolkAnnoyedCue (String)
  GameControl   + 16 params (water murk, surface sparkle, shadow fades, ...)
  DamageVolumeTag + m_startsActive; CameraVolumeTag + nothing; ...
A Token with no value is the engine's empty-string hash 0x2DFD1072, NOT zero -
no shipped record of the 43,758 sampled carries a zero there.

m_originalTagFile is tag_hash of the authoring .tag file's path in BACKSLASH
form.  utility/empty.lvl has exactly one node and it carries 0x97EEF3D8 ==
tag_hash('\\data\\levels\\utility\\empty.tag'); 40 further distinct values across
the eight region levels each equal the hash of an '\\data\\levels\\Region_NN\\
lm_*.tag' string that the same level's own root spells out in its zones'
m_fromTagfile.  Every shipped ROOT carries the empty hash there instead.

SELF-CHECK (the `check` command)
--------------------------------
The same schema that emits also parses.  Run against the release's own levels
it consumes every WorldTag root of all nine shipped .lvl files exactly - 255,617
bytes with nothing left over - and 4,627 of the 4,674 records of region_01.
The stragglers are NPCTag, GameplayCameraTag and WaterCurrentSplineProperties,
whose nested LinearSpline / attachment-list element schemas are still unmapped.

Usage:
    python lvl_v5_to_v6.py analyze  <in.lvl>
    python lvl_v5_to_v6.py convert  <in_v5.lvl> <out_v6.lvl>
    python lvl_v5_to_v6.py verify   <file.lvl>
    python lvl_v5_to_v6.py check    <v6.lvl> [<v5 template.lvl> ...]
"""

import collections
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swse_hash import tag_hash                                   # noqa: E402

MAGIC          = 0x2BAD4700
SECTION_MARKER = 0x1A2B3C4D
NODE_MARKER    = 0x7A60600D
LIST_END       = 0x601307A6
V6_OBJ_CONST   = 0x000B4265
PAD_ALIGN      = 2048
EMPTY_HASH     = 0x2DFD1072          # tag_hash('') - the engine's own constant

T_STRING = 0x3D0D975A
T_BOOL   = 0xF866FCB4
T_INT    = 0x1FB8C028
T_NAMEID = 0xFDA8D766
T_MATRIX = 0x1A0850BC
T_COLOUR = 0xE11A3AEA

# The beta's WORK/test_cb level predates the colour packing: it carries
# m_illuminationColor, a float RGBA quad, where the release has
# m_illuminationColorDW, the same colour in one DWORD (B,G,R,A in memory -
# confirmed by the alpha, 0.9961 float vs 0xFE packed, and by the exact x255
# scale of every sampled value).
ALIAS = {tag_hash('m_illuminationColor'): tag_hash('m_illuminationColorDW')}

FIXED = {'bool': 1, 'u32': 4, 'f32': 4, 'token': 4,
         'b8': 8, 'b12': 12, 'b16': 16, 'b24': 24, 'b48': 48, 'b52': 52}

# A nested struct's param array is recovered from stranger.exe but nothing in
# the array names the struct, so a container whose beta counterpart is empty
# (or absent) has no example to match against.  These pin the few that matter,
# each by a set of param names that occurs in exactly one array.
# keyed by (param name, param kind): LightmapperLMInfoTag reuses the name
# m_lmInfo for both the InstanceInfo struct and the RenderableInfo vector
# inside it.
ELEM_HINT = {
    ('m_zones', 'vector'):           ('m_zoneName', 'm_exportInfo', 'm_zoneAABB'),
    ('m_portals', 'vector'):         ('m_portalName', 'm_frontZone', 'm_backZone'),
    ('m_teleportPortals', 'vector'): ('m_fromZone', 'm_toZone', 'm_frameFrom',
                                      'm_frameTo'),
    ('m_proxyVisibility', 'vector'): ('m_visibleFrom', 'm_proxyZone',
                                      'm_proxyZoneName'),
    ('m_areaAdjacency', 'vector'):   ('m_zone', 'm_adjacentZone'),
    ('m_worldGeometries', 'vector'): ('m_geometryToken', 'm_zone'),
    ('m_lmInfo', 'object'):          ('m_lightmapID', 'm_lmInfo'),
    ('m_lmInfo', 'vector'):          ('m_packedResource', 'm_packingInfo'),
    ('m_infos', 'vector'):           ('m_packedResource', 'm_packingInfo'),
}


_ARRLEN = re.compile(r'\$0(\d)$')


def array_shape(t):
    """CArrayDef<...> -> (element kind, element count); MSVC mangles the
    length as $0<n-1>, so array<Token,2> ends '...V?$array@VToken@@$01'."""
    m = _ARRLEN.search(t or '')
    n = int(m.group(1)) + 1 if m else 0
    if 'CClassDef@VToken' in (t or ''):
        return 'token', n
    if 'CScalarDef@M' in (t or ''):
        return 'f32', n
    return 'u32', n


# Values the release itself writes for params the beta never had.  Lifted from
# data/bundles/utility/empty.lvl - the smallest shipping level - by walking its
# WorldTag root with this schema and recording each param's bytes.  Zero is a
# poor default for a fade distance (m_cloudShadowEndFade 0 with StartFade 0 is a
# zero-width ramp), so prefer what the engine's own data uses.
RELEASE_DEFAULTS = {
    'm_surfaceSparkleNear':           '00002041',   # 10.0
    'm_surfaceSparkleFar':            '00002041',   # 10.0
    'm_surfaceSparkleRadius':         '00004040',   # 3.0
    'm_surfaceSparkleColor':          '0000803f0000803f0000803f0000803f',
    'm_cloudShadowEndFade':           '0000a042',   # 80.0
    'm_shadowMapEndFade':             '0000c041',   # 24.0
    'm_critterCuePrefs':              '45e0b0a4',
    'm_townsfolkAnnoyedCue':          '17000000' + b'speak_while_investigate'.hex(),
}
RELEASE_DEFAULTS = {k: bytes.fromhex(v) for k, v in RELEASE_DEFAULTS.items()}


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def _pack_colour(p):
    r, g, b, a = struct.unpack('<4f', p)
    q = lambda v: max(0, min(255, int(round(v * 255.0))))
    return struct.pack('<4B', q(b), q(g), q(r), q(a))


# --------------------------------------------------------------------------
# the release's schema
# --------------------------------------------------------------------------

class Schema:
    def __init__(self, path=None):
        path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    'lvl_schema.json')
        d = json.load(open(path))
        self.arrays = d['arrays']
        self.classes = dict(d['classes'])
        self.hashsets = []
        for a in self.arrays:
            self.hashsets.append(set(tag_hash(p['name']) for p in a['params']))
        # The class-name initialisers are emitted next to, not inside, the array
        # initialisers, so a couple of names land on the neighbouring
        # descriptor.  Re-anchor the ones we can identify from their contents.
        self._fix('WorldTag', ('m_worldIsSection', 'm_zones', 'm_areaAdjacency'))
        self._fix('WorldTag::Zone', ('m_zoneName', 'm_exportInfo', 'm_zoneAABB'))
        self._fix('VolumeTag', ('m_width', 'm_depth', 'm_height'))
        self.base = self._find(('m_tagName', 'm_originalTagFile', 'm_tagTransform'))
        # calibrate_lvl_schema.py resolves the rest by making the schema consume
        # the release's own records exactly; that file wins where it exists.
        cmap = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'lvl_class_map.json')
        if os.path.exists(cmap):
            self.classes.update(json.load(open(cmap)))

    def _find(self, names):
        want = set(tag_hash(n) for n in names)
        for i, hs in enumerate(self.hashsets):
            if want <= hs:
                return i
        return None

    def _fix(self, cls, names):
        i = self._find(names)
        if i is not None:
            self.classes[cls] = i

    def array_for(self, names):
        """locate the param array of a nested struct from its v5 field names"""
        sn = set(tag_hash(n) if isinstance(n, str) else n for n in names)
        if not sn:
            return None
        best, bs = None, 0.0
        for i, hs in enumerate(self.hashsets):
            inter = len(hs & sn)
            if not inter:
                continue
            score = inter - 0.3 * len(hs ^ sn)
            if score > bs:
                best, bs = i, score
        return best

    def params(self, idx):
        return self.arrays[idx]['params']


_SCHEMA = None


def schema():
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = Schema()
    return _SCHEMA


# --------------------------------------------------------------------------
# v5 parsing
# --------------------------------------------------------------------------

class Field:
    __slots__ = ('size', 'fname', 'ftype', 'payload')

    def __init__(self, size, fname, ftype, payload):
        self.size, self.fname, self.ftype, self.payload = size, fname, ftype, payload

    @property
    def is_string(self):
        return self.ftype == T_STRING

    def string_value(self):
        n = _u32(self.payload, 0)
        return self.payload[4:4 + n]


class Obj:
    __slots__ = ('typename', 'fields')

    def __init__(self, typename):
        self.typename = typename
        self.fields = []

    @property
    def bare(self):
        t = self.typename
        return t.split(' ', 1)[1] if ' ' in t else t


def _field_run(b, i, end):
    """field* terminated by a u32 0; returns (fields, pos_after) or None"""
    out = []
    while i < end:
        if i + 4 > end:
            return None
        size = _u32(b, i)
        if size == 0:
            return out, i + 4
        if size < 12 or i + size > end:
            return None
        out.append(Field(size, _u32(b, i + 4), _u32(b, i + 8), b[i + 12:i + size]))
        i += size
    return None


def as_object(b):
    r = _field_run(b, 0, len(b))
    return r[0] if r and r[1] == len(b) else None


def as_vector(b):
    if len(b) < 4:
        return None
    n = _u32(b, 0)
    if n > 1 << 20:
        return None
    i, elems = 4, []
    for _ in range(n):
        r = _field_run(b, i, len(b))
        if not r:
            return None
        elems.append(r[0])
        i = r[1]
    return elems if i == len(b) else None


def _typename_len(b, i, end):
    if i + 4 > end:
        return 0
    n = _u32(b, i)
    if not (4 <= n <= 64) or i + 4 + n > end:
        return 0
    s = b[i + 4:i + 4 + n]
    if not (s.startswith(b'class ') or s.startswith(b'struct ')):
        return 0
    return n if all(32 <= c < 127 for c in s) else 0


def parse_object(b, i, end):
    n = _typename_len(b, i, end)
    if not n:
        return None, None
    obj = Obj(b[i + 4:i + 4 + n].decode('latin1'))
    i += 4 + n
    while i < end:
        if i + 4 > end:
            return None, None
        size = _u32(b, i)
        if size == 0:
            return obj, i + 4
        if _typename_len(b, i, end):
            return None, None
        if size < 12 or i + size > end:
            return None, None
        obj.fields.append(Field(size, _u32(b, i + 4), _u32(b, i + 8),
                                b[i + 12:i + size]))
        i += size
    return None, None


def parse_span(b, start, end):
    objs, i = [], start
    while i < end:
        o, i2 = parse_object(b, i, end)
        if o is None or i2 is None or i2 <= i:
            return None
        objs.append(o)
        i = i2
    return objs if i == end else None


class Level:
    pass


def parse(data, descend=True):
    if _u32(data, 0) != MAGIC:
        raise ValueError('bad magic 0x%08X' % _u32(data, 0))
    lvl = Level()
    lvl.raw = data
    lvl.version = _u32(data, 4)
    lvl.stream_count = _u32(data, 12)
    head = _u32(data, 0x18)

    offs, cur, seen = [], head, set()
    while cur and cur + 8 <= len(data) and cur not in seen and _u32(data, cur) == NODE_MARKER:
        seen.add(cur)
        offs.append(cur)
        cur = _u32(data, cur + 4)
    lvl.list_end = cur
    lvl.node_offsets = offs

    if lvl.version != 5:
        lvl.root, lvl.nodes = None, [(o, None) for o in offs]
        return lvl

    lvl.root = parse_span(data, 0x1C, head)
    lvl.nodes = []
    for k, o in enumerate(offs):
        end = offs[k + 1] if k + 1 < len(offs) else cur
        lvl.nodes.append((o, parse_span(data, o + 8, end)))
    return lvl


# --------------------------------------------------------------------------
# resolved schema tree  (release param list + the beta's nesting)
# --------------------------------------------------------------------------

class Node:
    __slots__ = ('name', 'h', 'kind', 'elem', 'sub', 'count')

    def __init__(self, name, kind, elem, sub=None):
        self.name, self.kind, self.elem, self.sub = name, kind, elem, sub
        self.count = 0
        self.h = tag_hash(name)


def _byhash(fields):
    d = {}
    for f in fields:
        d.setdefault(ALIAS.get(f.fname, f.fname), f)
    return d


def resolve(arr_idx, fields, warn=None, path='', have=None, seen=()):
    """expand a release param array against a v5 field list, inlining parents"""
    sc = schema()
    out = []
    top = have is None
    if top:
        have = _byhash(fields)
    # LightmapperLMInfoTag's InstanceInfo holds a vector of itself, so a schema
    # can be cyclic; only expand a container as deep as the data actually goes.
    seen = tuple(seen) + (arr_idx,)
    for p in sc.params(arr_idx):
        kind = p['kind']
        if kind == 'parent':
            par = _parent_name(p['type'])
            idx = sc.classes.get(par)
            if idx is None:
                idx = sc.base
            out.extend(resolve(idx, fields, warn, path, have, seen))
            continue
        n = Node(p['name'], kind, p.get('elem'))
        if kind == 'array':
            n.elem, n.count = array_shape(p.get('type'))
        f = have.pop(n.h, None)
        if kind == 'object' and f is not None:
            kids = as_object(f.payload)
            if kids is not None:
                i = sc.array_for([k.fname for k in kids])
                if i is not None and i not in seen:
                    n.sub = resolve(i, kids, warn, path + '/' + n.name, None, seen)
        elif kind == 'vector' and p.get('elem') == 'object' and f is not None:
            elems = as_vector(f.payload)
            if elems:
                i = sc.array_for([k.fname for k in elems[0]])
                if i is not None and i not in seen:
                    n.sub = resolve(i, elems[0], warn, path + '/' + n.name, None, seen)
        if n.sub is None and (n.name, kind) in ELEM_HINT:
            i = sc._find(ELEM_HINT[(n.name, kind)])
            if i is not None and i not in seen:
                n.sub = resolve(i, [], warn, path + '/' + n.name, None, seen)
        out.append(n)
    if top and warn is not None:
        for h, f in have.items():
            warn.append('%s: v5 field 0x%08X (%d bytes) has no release param'
                        % (path or '<root>', h, len(f.payload)))
    return out


def _parent_name(t):
    """?$CParentDef@VPipeTag@@UGameTagBase       -> GameTagBase
       ?$CParentDef@VCameraVolumeTag@@VVolumeTag -> VolumeTag

    Drop exactly ONE leading class/struct tag character - 'VVolumeTag' has two
    Vs and stripping both loses the first letter of the name."""
    parts = (t or '').split('@@')
    if len(parts) >= 2 and parts[-1]:
        s = parts[-1]
        return s[1:] if s[0] in 'VU' else s
    return None


# --------------------------------------------------------------------------
# v6 emission
# --------------------------------------------------------------------------

def default_bytes(n, over=None):
    """what the release writes for a param the beta never had"""
    if over and n.name in over:
        return over[n.name]
    d = RELEASE_DEFAULTS.get(n.name)
    if d is not None and len(d) == FIXED.get(n.kind, len(d)):
        return d
    if n.kind == 'token':
        return struct.pack('<I', EMPTY_HASH)     # never zero - see the header
    if n.kind in FIXED:
        return b'\0' * FIXED[n.kind]
    if n.kind in ('string', 'vector', 'array'):
        return b'\0' * 4                          # length 0 / count 0
    if n.kind == 'object':
        return b''.join(default_bytes(k, over) for k in (n.sub or []))
    return b''


def _strip_headers(p):
    """v5 object payload -> just the leaf bytes, headers and terminators gone"""
    kids = as_object(p)
    if kids is None:
        return None
    out = []
    for k in kids:
        inner = _strip_headers(k.payload)
        out.append(k.payload if inner is None else inner)
    return b''.join(out)


def encode(n, f, warn, over=None):
    k = n.kind
    p = f.payload
    if k == 'bool':
        return p[:1] if p else b'\0'
    if k in ('u32', 'f32'):
        if len(p) == 16:                      # ColorF -> ColorDW
            return _pack_colour(p)
        return (p + b'\0' * 4)[:4]
    if k == 'token':
        if f.ftype == T_STRING:
            return struct.pack('<I', tag_hash(f.string_value()))
        return (p + b'\0' * 4)[:4]
    if k == 'string':
        if f.ftype == T_STRING:
            return p                          # u32 len + chars, unchanged
        return b'\0' * 4
    if k in FIXED:
        want = FIXED[k]
        if len(p) == want:
            return p
        # The beta writes some PODs reflectively: WorldTag::Zone's m_zoneAABB is
        # 52 bytes = two 12-byte-headed Vec3 fields plus the u32 0 terminator,
        # where the release stores a bare 24-byte AxialBox.  Strip the headers.
        flat = _strip_headers(p)
        if flat is not None and len(flat) == want:
            return flat
        warn.append('%s: expected %d bytes, v5 has %d' % (n.name, want, len(p)))
        return (p + b'\0' * want)[:want]
    if k == 'object':
        if n.sub is None:
            return p                          # leaf struct (RectI, ...) or unknown
        kids = as_object(p)
        if kids is None:
            warn.append('%s: object payload did not parse' % n.name)
            return p
        return emit(n.sub, kids, warn, over)
    if k == 'vector':
        if n.elem != 'object':
            return p                          # u32 count + POD elements, identical
        elems = as_vector(p)
        if elems is None:
            warn.append('%s: vector payload did not parse' % n.name)
            return p
        out = [struct.pack('<I', len(elems))]
        for e in elems:
            out.append(emit(n.sub or [], e, warn, over))
        return b''.join(out)
    if k == 'array':
        want = n.count * FIXED.get(n.elem, 4)
        if len(p) == want:
            return p
        warn.append('%s: fixed array is %d bytes in the release, %d in the beta'
                    % (n.name, want, len(p)))
        return (p + b'\0' * want)[:want]
    warn.append('%s: unknown param kind %r' % (n.name, k))
    return p


def emit(nodes, fields, warn, over=None):
    have = _byhash(fields)
    out = []
    for n in nodes:
        f = have.get(n.h)
        out.append(default_bytes(n, over) if f is None
                   else encode(n, f, warn, over))
    return b''.join(out)


def object_v6(obj, warn, over=None):
    sc = schema()
    idx = sc.classes.get(obj.bare)
    if idx is None:
        if obj.fields:
            idx = sc.array_for([f.fname for f in obj.fields])
        if idx is None:
            if obj.fields:
                warn.append('%s: class not in the release schema, %d fields dropped'
                            % (obj.bare, len(obj.fields)))
            return struct.pack('<II', V6_OBJ_CONST, tag_hash(obj.bare))
    tree = resolve(idx, obj.fields, warn, obj.bare)
    return (struct.pack('<II', V6_OBJ_CONST, tag_hash(obj.bare))
            + emit(tree, obj.fields, warn, over))


def node_v6(objs, warn, over=None):
    return b''.join(object_v6(o, warn, over) for o in objs)


def tagfile_path(root_objs):
    """The release records, per tag, the authoring .tag file it came from.

    m_originalTagFile is tag_hash of that path in BACKSLASH form - proved on
    utility/empty.lvl, whose single node carries 0x97EEF3D8 ==
    tag_hash('\\data\\levels\\utility\\empty.tag'), and again on 40 further
    distinct values across the eight region levels, every one of which equals
    the hash of an '\\data\\levels\\Region_NN\\lm_*.tag' string that the same
    level's own root spells out.  The beta has no such field, so reconstruct
    the path from the level's own m_smbFileNames, whose first entry is the same
    stem with a .smb extension.
    """
    h = tag_hash('m_smbFileNames')
    for o in root_objs:
        for f in o.fields:
            if f.fname != h or len(f.payload) < 8:
                continue
            n = _u32(f.payload, 0)
            if n < 1:
                continue
            ln = _u32(f.payload, 4)
            s = f.payload[8:8 + ln].decode('latin1')
            if s.lower().endswith('.smb'):
                s = s[:-4] + '.tag'
            return s
    return None


def convert(lvl):
    if lvl.version != 5:
        raise ValueError('input is version %d, expected 5' % lvl.version)
    if lvl.root is None:
        raise ValueError('root WorldTag did not parse')
    warn = []
    tagpath = tagfile_path(lvl.root)
    node_over = {}
    if tagpath:
        node_over['m_originalTagFile'] = struct.pack('<I', tag_hash(tagpath))
        node_over['m_fromTagfile'] = (struct.pack('<I', len(tagpath))
                                      + tagpath.encode('latin1'))
    # every shipped root carries the EMPTY hash there, only nodes name a file
    root_body = node_v6(lvl.root, warn, {'m_fromTagfile':
                                         node_over.get('m_fromTagfile', b'\0' * 4)})
    bodies = []
    for _o, objs in lvl.nodes:
        if objs is None:
            raise ValueError('a node failed to parse')
        bodies.append(node_v6(objs, warn, node_over))

    head = 0x1C + len(root_body)
    out = bytearray()
    out += struct.pack('<7I', MAGIC, 6, SECTION_MARKER, 2, SECTION_MARKER,
                       NODE_MARKER, head)
    out += root_body
    off = head
    for body in bodies:
        nxt = off + 8 + len(body)
        out += struct.pack('<II', NODE_MARKER, nxt)
        out += body
        off = nxt
    out += struct.pack('<I', LIST_END)
    while len(out) % PAD_ALIGN:
        out += b'\0'
    return bytes(out), warn


# --------------------------------------------------------------------------
# v6 walking - the self-check: a schema must consume a record exactly
# --------------------------------------------------------------------------

def _walk_string(b, i):
    return i + 4 + _u32(b, i)


def walk(nodes, b, i):
    for n in nodes:
        k = n.kind
        if i is None or i > len(b):
            return None
        if k in FIXED:
            i += FIXED[k]
        elif k == 'string':
            if i + 4 > len(b):
                return None
            i = _walk_string(b, i)
        elif k == 'vector':
            if i + 4 > len(b):
                return None
            cnt = _u32(b, i)
            i += 4
            if cnt > (len(b) - i) + 4:
                return None
            if n.elem == 'object':
                for _ in range(cnt):
                    i = walk(n.sub or [], b, i)
                    if i is None:
                        return None
            elif n.elem == 'string':
                for _ in range(cnt):
                    if i + 4 > len(b):
                        return None
                    i = _walk_string(b, i)
            else:
                i += cnt * FIXED.get(n.elem or 'u32', 4)
        elif k == 'object':
            if n.sub is None:
                return None
            i = walk(n.sub, b, i)
        elif k == 'array':
            i += n.count * FIXED.get(n.elem, 4)
        else:
            return None
    return i if (i is not None and i <= len(b)) else None


def v6_records(data):
    """[(offset, body)] for the root and every node of a v6 file"""
    head = _u32(data, 0x18)
    offs, cur, seen = [], head, set()
    while cur and cur + 8 <= len(data) and cur not in seen and _u32(data, cur) == NODE_MARKER:
        seen.add(cur)
        offs.append(cur)
        cur = _u32(data, cur + 4)
    out = [(0x1C, data[0x1C:head])]
    for k, o in enumerate(offs):
        end = offs[k + 1] if k + 1 < len(offs) else cur
        out.append((o, data[o + 8:end]))
    return out


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_analyze(path):
    data = open(path, 'rb').read()
    lvl = parse(data)
    print('%s  %d bytes  version=%d  streams=%d  nodes=%d' % (
        os.path.basename(path), len(data), lvl.version, lvl.stream_count,
        len(lvl.node_offsets)))
    if lvl.version != 5:
        return
    ok = sum(1 for _o, n in lvl.nodes if n is not None)
    print('root parsed: %s   nodes parsed exactly: %d/%d' % (
        lvl.root is not None, ok, len(lvl.nodes)))
    warn = []
    per = collections.defaultdict(list)
    for _o, objs in lvl.nodes:
        if objs:
            per[' + '.join(o.bare for o in objs)].append(len(node_v6(objs, warn)))
    print('\n%-40s %6s %-12s %s' % ('node classes', 'count', 'v6 classId',
                                    'v6 record size'))
    for tn, sizes in sorted(per.items(), key=lambda kv: -len(kv[1])):
        c = collections.Counter(sizes)
        print('%-40s %6d 0x%08X  %s' % (
            tn[:40], len(sizes), tag_hash(tn.split(' + ')[0]),
            dict(c.most_common(3))))
    if warn:
        print('\n%d warnings:' % len(warn))
        for w in sorted(set(warn))[:20]:
            print('  ' + w)


def cmd_verify(path):
    data = open(path, 'rb').read()
    lvl = parse(data, descend=False)
    print('%s version=%d size=%d streams=%d' % (
        os.path.basename(path), lvl.version, len(data), lvl.stream_count))
    errs = []
    if _u32(data, 8) != SECTION_MARKER or _u32(data, 0x10) != SECTION_MARKER:
        errs.append('section markers wrong')
    if _u32(data, 0x14) != NODE_MARKER:
        errs.append('node-marker constant wrong')
    n = len(lvl.node_offsets)
    marks = data.count(struct.pack('<I', NODE_MARKER))
    print('  nodes walked=%d  marker occurrences=%d (expect nodes+1)' % (n, marks))
    if marks != n + 1:
        errs.append('marker count != nodes+1')
    if lvl.list_end >= len(data) or _u32(data, lvl.list_end) != LIST_END:
        errs.append('sentinel 0x%08X missing at 0x%X' % (LIST_END, lvl.list_end))
    else:
        print('  end-of-list sentinel present at 0x%X' % lvl.list_end)
    if len(data) % PAD_ALIGN:
        print('  note: not padded to %d' % PAD_ALIGN)
    if lvl.version == 6:
        sc = schema()
        known = {tag_hash(c): c for c in sc.classes}
        if _u32(data, 0x1C) == V6_OBJ_CONST:
            print('  root: const OK, classId=0x%08X %s' % (
                _u32(data, 0x20), '(WorldTag)'
                if _u32(data, 0x20) == tag_hash('WorldTag') else '(?)'))
        else:
            errs.append('root missing the v6 object constant')
        bad = sum(1 for o in lvl.node_offsets if _u32(data, o + 8) != V6_OBJ_CONST)
        print('  object constant on %d/%d nodes' % (n - bad, n))
        if bad:
            errs.append('%d nodes missing the object constant' % bad)
        ids = collections.Counter(_u32(data, o + 12) for o in lvl.node_offsets)
        unknown = sum(c for i, c in ids.items() if i not in known)
        print('  class ids: %d distinct, %d records with an unrecognised id'
              % (len(ids), unknown))
        for cid, c in ids.most_common(8):
            print('     0x%08X x%-6d %s' % (cid, c, known.get(cid, '???')))
        zeros = 0
        for o in lvl.node_offsets:
            body = data[o + 8:o + 8 + 0x60]
            if len(body) >= 0x60 and _u32(body, 0x5c) == 0:
                zeros += 1
        if zeros:
            print('  %d records carry a ZERO m_originalTagFile '
                  '(no shipped record ever does)' % zeros)
    print('  RESULT: %s' % ('OK' if not errs else 'PROBLEMS: ' + '; '.join(errs)))
    return not errs


def _templates(paths):
    """class -> a v5 object of that class, used as the nesting template"""
    tmpl = {}
    for p in paths:
        lvl = parse(open(p, 'rb').read())
        if lvl.version != 5:
            continue
        for grp in ([lvl.root] + [n for _o, n in lvl.nodes]):
            for o in (grp or []):
                if o.bare not in tmpl or len(o.fields) > len(tmpl[o.bare].fields):
                    tmpl[o.bare] = o
    return tmpl


def cmd_check(v6path, v5paths):
    """parse a v6 file with the recovered schema; every record must be consumed
    exactly.  Run against the shipped levels this proves the schema."""
    sc = schema()
    tmpl = _templates(v5paths) if v5paths else {}
    data = open(v6path, 'rb').read()
    if _u32(data, 4) != 6:
        print('%s is version %d' % (v6path, _u32(data, 4)))
        return False
    byid = {tag_hash(c): c for c in sc.classes}
    stats = collections.Counter()
    fails = collections.Counter()
    for off, body in v6_records(data):
        i = 0
        okrec = True
        while i + 8 <= len(body):
            if _u32(body, i) != V6_OBJ_CONST:
                okrec = False
                break
            cid = _u32(body, i + 4)
            cname = byid.get(cid)
            if cname is None:
                # a runtime class with no reflected params (e.g.
                # SimpleInstancedObject) is just its 8-byte header
                if i + 8 == len(body):
                    i += 8
                    break
                stats['unknown class'] += 1
                okrec = False
                break
            idx = sc.classes[cname]
            t = tmpl.get(cname)
            tree = resolve(idx, t.fields if t else [])
            j = walk(tree, body, i + 8)
            if j is None:
                fails[cname] += 1
                okrec = False
                break
            i = j
        if okrec and i == len(body):
            stats['exact'] += 1
        elif okrec:
            stats['short/long'] += 1
            fails['<residue at 0x%x>' % off] += 1
        else:
            stats['failed'] += 1
    print('%-24s %s' % (os.path.basename(v6path), dict(stats)))
    if fails:
        for k, v in fails.most_common(10):
            print('    %-32s x%d' % (k, v))
    return stats['exact'] == sum(stats.values())


def cmd_convert(src, dst):
    data = open(src, 'rb').read()
    lvl = parse(data)
    bad = [i for i, (_o, n) in enumerate(lvl.nodes) if n is None]
    if bad:
        print('ERROR: %d/%d nodes did not parse' % (len(bad), len(lvl.nodes)))
        return 1
    out, warn = convert(lvl)
    open(dst, 'wb').write(out)
    print('wrote %s  %d bytes  (from %s, %d bytes)' % (
        dst, len(out), os.path.basename(src), len(data)))
    if warn:
        print('%d warnings:' % len(warn))
        for w in sorted(set(warn))[:20]:
            print('  ' + w)
    print()
    cmd_verify(dst)
    print()
    cmd_check(dst, [src])
    return 0


def main(argv):
    a = argv[1:]
    if len(a) == 2 and a[0] == 'analyze':
        cmd_analyze(a[1])
    elif len(a) == 2 and a[0] == 'verify':
        return 0 if cmd_verify(a[1]) else 1
    elif len(a) == 3 and a[0] == 'convert':
        return cmd_convert(a[1], a[2])
    elif len(a) >= 2 and a[0] == 'check':
        return 0 if cmd_check(a[1], a[2:]) else 1
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
