"""Cross-check: for every entry in global_prefs.smb that also exists as a loose
.txt under data\prefs, verify that the compiled record for each field carries
the value the text file states.  Focus on booleans."""
import sys, os, re, struct, collections
sys.path.insert(0, r'C:\Users\<you>\New folder\tools')
from swse_hash import tag_hash
from gp_entries import parse

ROOT = r'C:\Users\<you>\SWBeta\Game'
BUNDLE = os.path.join(ROOT, r'data\global\global_prefs.smb')

def read_txt(path):
    """Return list of (name, rawvalue) in file order, flattening nested blocks."""
    out = []
    try:
        txt = open(path, 'r', errors='replace').read()
    except OSError:
        return None
    for line in txt.splitlines():
        line = line.strip()
        if not line or line in ('begin', 'end'):
            continue
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        out.append((k.strip(), v.strip()))
    return out

D, hdr, ents = parse(BUNDLE)

BOOL_T = 0xF866FCB4
FLOAT_T = 0xEA9BCD54

tot = {'bool': [0, 0], 'float': [0, 0], 'other': [0, 0]}
missing_files = 0
detail = []

for e in ents:
    disk = os.path.join(ROOT, e['path'].lstrip('\\'))
    txt = read_txt(disk)
    if txt is None:
        missing_files += 1
        continue
    byhash = {}
    for k, v in txt:
        byhash.setdefault(tag_hash(k) & 0xFFFFFFFF, (k, v))
    for (off, sz, nh, th, pay) in e['recs']:
        if nh not in byhash:
            continue
        k, v = byhash[nh]
        if th == BOOL_T and len(pay) == 1:
            got = 'true' if pay[0] else 'false'
            exp = v.split()[0].lower()
            if exp in ('true', 'false'):
                ok = (got == exp)
                tot['bool'][ok] += 1
                detail.append((ok, 'bool', e['path'], k, exp, got, off))
        elif th == FLOAT_T and len(pay) == 4:
            got = struct.unpack('<f', pay)[0]
            m = re.match(r'^(-?[0-9.eE+-]+)\s*(\[([0-9A-Fa-f]{8})\])?', v)
            if m and m.group(3):
                exp = struct.unpack('<f', bytes.fromhex(m.group(3))[::-1])[0]
                ok = abs(exp - got) < 1e-6 or (exp == got)
                tot['float'][ok] += 1
                detail.append((ok, 'float', e['path'], k, exp, got, off))

print('entries=%d  loose .txt missing for %d' % (len(ents), missing_files))
for t, (bad, good) in tot.items():
    if bad + good:
        print('  %-6s matched %d / %d' % (t, good, good + bad))

print('\n--- first 30 BOOL comparisons ---')
n = 0
for ok, t, p, k, exp, got, off in detail:
    if t != 'bool':
        continue
    print('  %s %-58s %-34s txt=%-6s smb=%-6s @%#08x' %
          ('OK ' if ok else 'BAD', p.split('\\')[-1], k, exp, got, off))
    n += 1
    if n >= 30:
        break

print('\n--- MISMATCHES (any type) ---')
bad = [d for d in detail if not d[0]]
for ok, t, p, k, exp, got, off in bad[:25]:
    print('  %-5s %-58s %-34s txt=%-14s smb=%-14s @%#08x' % (t, p.split('\\')[-1], k, exp, got, off))
print('  total mismatches:', len(bad))
