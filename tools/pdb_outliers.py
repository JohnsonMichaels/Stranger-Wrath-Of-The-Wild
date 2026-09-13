import collections
TSV = r"C:\Users\<you>\AppData\Local\Temp\claude\C--Users-<you>-New-folder\c07f577f-7365-43c8-8b76-ba84acd7f8f6\scratchpad\final_syms.tsv"
INI = r"C:\Users\<you>\SWBeta\oddbeta\SymbolCache\-2946c8408876b876.ini"
DELTA = 0x10920

pdb = {}
pdb_by_va = collections.defaultdict(list)
for line in open(TSV, encoding="utf-8", errors="replace"):
    p = line.rstrip("\n").split("\t")
    if len(p) < 5: continue
    try: rva = int(p[0], 16)
    except ValueError: continue
    size = int(p[1]) if p[1].isdigit() else 0
    tag = p[2]; name = p[4]
    pdb.setdefault(name, (rva, size, tag))
    pdb_by_va[rva + DELTA].append((name, size, tag))

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

print("=== NAME PRESENT IN BOTH, ADDRESS DISAGREES ===")
for n, a in sorted(syms.items()):
    if n in pdb:
        exp = pdb[n][0] + DELTA
        if exp != a:
            print(f"  {n}")
            print(f"     cache = 0x{a:08x}   pdb-derived = 0x{exp:08x}   diff {a-exp:+d}")
            print(f"     what PDB says lives at cache addr 0x{a:08x}: {pdb_by_va.get(a)}")
            print()

print()
print("=== FOR EVERY SYMBOL-CACHE ENTRY: WHAT DOES THE PDB CALL THAT ADDRESS? ===")
print("(only D3D/graphics-relevant ones; '??' = no PDB function starts there)")
for n, a in sorted(syms.items(), key=lambda kv: kv[1]):
    if not (n.startswith("D3D") or n.startswith("CDevice") or n.startswith("CMiniport")
            or n.startswith("Get2D") or n.startswith("Lock2D") or n.startswith("XG")
            or n.startswith("Direct3D")):
        continue
    here = pdb_by_va.get(a)
    if here is None:
        # find containing function
        best = None
        for va, lst in pdb_by_va.items():
            for nm, sz, tg in lst:
                if tg == "5" and va <= a < va + max(sz, 1):
                    best = (nm, va, a - va)
        desc = f"?? inside {best[0]}+0x{best[2]:x}" if best else "?? (nothing)"
    else:
        desc = ", ".join(f"{nm}[sz={sz},tag={tg}]" for nm, sz, tg in here)
    flag = ""
    if here:
        pdbnames = {nm for nm, _, _ in here}
        base = n
        if base not in pdbnames:
            flag = "   <-- cache name NOT among PDB names here"
    print(f"0x{a:08x}  cache={n}")
    print(f"              pdb={desc}{flag}")
