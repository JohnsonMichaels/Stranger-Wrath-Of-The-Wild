import collections, re
TSV = r"C:\Users\<you>\AppData\Local\Temp\claude\C--Users-<you>-New-folder\c07f577f-7365-43c8-8b76-ba84acd7f8f6\scratchpad\final_syms.tsv"
INI = r"C:\Users\<you>\SWBeta\oddbeta\SymbolCache\-2946c8408876b876.ini"

pdb = {}          # name -> (rva, size, tag)
pdb_by_rva = collections.defaultdict(list)
for line in open(TSV, encoding="utf-8", errors="replace"):
    p = line.rstrip("\n").split("\t")
    if len(p) < 5: continue
    try: rva = int(p[0], 16)
    except ValueError: continue
    size = int(p[1]) if p[1].isdigit() else 0
    tag = p[2]; name = p[4]
    pdb.setdefault(name, (rva, size, tag))
    pdb_by_rva[rva].append((name, size, tag))

syms = {}
in_s = False
for line in open(INI, encoding="utf-8", errors="replace"):
    s = line.strip()
    if s.startswith("["):
        in_s = s.lower() == "[symbols]"; continue
    if not in_s or "=" not in s: continue
    n, v = [x.strip() for x in s.split("=", 1)]
    try: syms[n] = int(v, 16)
    except ValueError: pass

deltas = collections.Counter()
matched = []
for n, a in syms.items():
    if n in pdb:
        d = a - pdb[n][0]
        deltas[d] += 1
        matched.append((n, a, pdb[n][0], d))

print("delta histogram (symbolcache_addr - pdb_rva):")
for d, c in deltas.most_common(12):
    print(f"  0x{d & 0xffffffff:08x} ({d:+d})  x{c}")
print()
for n, a, r, d in sorted(matched, key=lambda x: x[1])[:25]:
    print(f"  {n:55s} cache=0x{a:06x} pdb=0x{r:06x} delta=0x{d & 0xffffffff:x}")
