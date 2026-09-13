#!/usr/bin/env python3
"""Calibrate class -> ParamIO array against the SHIPPED v6 levels.

make_lvl_schema.py recovers all 200 param arrays exactly, but the class-name
initialisers in stranger.exe use several different code idioms, so mapping a
name onto its array by disassembly alone is unreliable.  This does it the
self-checking way instead: for every class that appears in a beta v5 file, try
each candidate array and keep the one whose schema consumes the release's own
records EXACTLY, byte for byte, with nothing left over.

Writes tools/lvl_class_map.json  { "PipeTag": <array index>, ... }.

    python calibrate_lvl_schema.py [--quick]
"""
import collections
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lvl_v5_to_v6 as L                                        # noqa: E402
from swse_hash import tag_hash                                  # noqa: E402

RETAIL = r"C:\Program Files (x86)\Steam\steamapps\common\Stranger's Wrath\data\bundles"
BETA = r"C:\Users\<you>\SWBeta\Game\data\bundles"

V6_FILES = [
    os.path.join(RETAIL, 'utility', 'empty.lvl'),
    os.path.join(RETAIL, 'region_00', 'lm_level_00.lvl'),
    os.path.join(RETAIL, 'region_01', 'lm_level_01.lvl'),
    os.path.join(RETAIL, 'region_02a', 'lm_level_02a.lvl'),
    os.path.join(RETAIL, 'region_05', 'lm_level_05.lvl'),
]
V5_FILES = [
    os.path.join(BETA, 'WORK', 'test_cb', 'test_aaron_2.lvl'),
    os.path.join(BETA, 'Region_01', 'lm_level_01.lvl'),
    os.path.join(BETA, 'Region_02a', 'lm_level_02a.lvl'),
    os.path.join(BETA, 'Region_05', 'lm_level_05.lvl'),
    os.path.join(BETA, 'Region_03', 'lm_level_03.lvl'),
]
MAX_SAMPLES = 40


def _weight(o):
    """richest instance wins: most fields, then most bytes, so that nested
    vectors are non-empty and their element schemas can be resolved"""
    return (len(o.fields), sum(len(f.payload) for f in o.fields))


def templates(paths):
    tmpl = {}
    for p in paths:
        if not os.path.exists(p):
            continue
        lvl = L.parse(open(p, 'rb').read())
        if lvl.version != 5:
            continue
        for grp in ([lvl.root] + [n for _o, n in lvl.nodes]):
            for o in (grp or []):
                cur = tmpl.get(o.bare)
                if cur is None or _weight(o) > _weight(cur):
                    tmpl[o.bare] = o
    return tmpl


def samples(paths, tmpl=None, deep=False):
    """classId -> [(body, start offset of that object inside the body)]

    Without a schema only the first object of a record can be located.  Once a
    first pass has mapped the common classes, `deep` walks past them with the
    mapping in hand and picks up the second-position objects (NPCTag behind a
    SpawnPoint::Tag, SimpleInstancedObject behind an InstancedObjectTag).
    """
    sc = L.schema()
    byid = {tag_hash(b): b for b in (tmpl or {})}
    out = collections.defaultdict(list)
    for p in paths:
        if not os.path.exists(p):
            continue
        d = open(p, 'rb').read()
        if L._u32(d, 4) != 6:
            continue
        for _off, body in L.v6_records(d):
            i = 0
            while i + 8 <= len(body) and L._u32(body, i) == L.V6_OBJ_CONST:
                cid = L._u32(body, i + 4)
                if len(out[cid]) < MAX_SAMPLES:
                    out[cid].append((body, i))
                if not deep:
                    break
                name = byid.get(cid)
                idx = sc.classes.get(name) if name else None
                if idx is None:
                    break
                j = L.walk(L.resolve(idx, tmpl[name].fields), body, i + 8)
                if j is None or j <= i:
                    break
                i = j
    return out


def main(argv):
    sc = L.schema()
    tmpl = templates(V5_FILES)
    files = V6_FILES[:2] if '--quick' in argv else V6_FILES
    samp = samples(files)
    print('templates=%d  classIds with samples=%d  arrays=%d'
          % (len(tmpl), len(samp), len(sc.arrays)))

    byid = {}
    for bare in tmpl:
        byid[tag_hash(bare)] = bare

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'lvl_class_map.json')
    mapping, report = {}, []
    # three passes: a subclass (SpotLightTag) can only be resolved once its
    # parent (PointLightTag) is, because the parent link is expanded during
    # resolve(); and second-position objects only become visible once the
    # classes in front of them can be walked past.
    for _pass in (1, 2, 3):
        if _pass == 3:
            samp = samples(files, tmpl, deep=True)
        mapping, report = {}, []
        for cid, recs in sorted(samp.items(), key=lambda kv: -len(kv[1])):
            bare = byid.get(cid)
            if bare is None:
                report.append(('0x%08X' % cid, 'no beta class with this id', 0, 0, 0))
                continue
            t = tmpl[bare]
            tset = set(L.ALIAS.get(f.fname, f.fname) for f in t.fields)
            cands = [i for i, a in enumerate(sc.arrays)
                     if (sc.hashsets[i] & tset)
                     or any(p['kind'] == 'parent' for p in a['params'])]
            best, bestkey = None, None
            for i in cands:
                tree = L.resolve(i, t.fields)
                ok = 0
                for body, start in recs:
                    j = L.walk(tree, body, start + 8)
                    if j is None:
                        continue
                    # exact: ends the record, or lands on the next object header
                    if j == len(body) or (j + 8 <= len(body)
                                          and L._u32(body, j) == L.V6_OBJ_CONST):
                        ok += 1
                # tie-break on how well the expanded schema matches the beta
                # object's own field set - several classes share a record shape
                hs = set(n.h for n in tree)
                fit = len(hs & tset) - 0.5 * len(hs - tset) - 0.5 * len(tset - hs)
                key = (ok, fit)
                if bestkey is None or key > bestkey:
                    best, bestkey = i, key
            n = len(recs)
            if best is not None and bestkey[0] == n and n:
                mapping[bare] = best
            report.append((bare, 'arr#%s' % best, bestkey[0] if bestkey else 0,
                           n, bestkey[1] if bestkey else 0))
        json.dump(mapping, open(out, 'w'), indent=0, sort_keys=True)
        sc.classes.update(mapping)      # feed pass 2 (parent-link resolution)
    print('\n%-34s %-8s %-12s %s' % ('class', 'array', 'exact', 'schema fit'))
    for name, arr, ok, n, fit in report:
        flag = '' if (n and ok == n) else '   <-- NOT RESOLVED'
        print('%-34s %-8s %-12s %+.1f%s' % (name, arr, '%d/%d' % (ok, n), fit, flag))
    print('\nresolved %d/%d classes -> %s' % (len(mapping), len(report), out))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
