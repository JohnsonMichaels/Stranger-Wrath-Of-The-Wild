import re, sys, collections, os

INI = r"C:\Users\<you>\SWBeta\oddbeta\SymbolCache\-2946c8408876b876.ini"
PATCHES = r"C:\Users\<you>\SWBeta\src\Cxbx-Reloaded\src\core\hle\Patches.cpp"

# ---- parse symbol cache ----
syms = {}
in_syms = False
for line in open(INI, encoding="utf-8", errors="replace"):
    s = line.strip()
    if s.startswith("["):
        in_syms = (s.lower() == "[symbols]")
        continue
    if not in_syms or "=" not in s:
        continue
    name, val = s.split("=", 1)
    name = name.strip(); val = val.strip()
    try:
        addr = int(val, 16) if val.lower().startswith("0x") else int(val, 16)
    except ValueError:
        continue
    syms[name] = addr

# ---- parse Patches.cpp active entries ----
active = {}     # name -> emupatch func
commented = {}
line_no = {}
for i, line in enumerate(open(PATCHES, encoding="utf-8", errors="replace"), 1):
    m = re.search(r'PATCH_ENTRY\(\s*"([^"]+)"\s*,\s*([^,]+),', line)
    if not m:
        continue
    stripped = line.lstrip()
    is_comment = stripped.startswith("//") or stripped.startswith("/*")
    name = m.group(1); func = m.group(2).strip()
    if is_comment:
        commented[name] = func
    else:
        active[name] = func
        line_no[name] = i

byaddr = collections.defaultdict(list)
for n, a in syms.items():
    byaddr[a].append(n)

print("=== ALL ADDRESSES WITH >1 SYMBOL NAME ===")
print()
rows = []
for a in sorted(byaddr):
    names = sorted(byaddr[a])
    if len(names) < 2:
        continue
    patched = [n for n in names if n in active]
    rows.append((a, names, patched))

def simulate(patched):
    """Replicate EmuInstallPatches dedupe: iterate names in alphabetical order
    (g_SymbolAddresses is a std::map keyed by name), keep incumbent unless
    candidate is LTCG and incumbent is not."""
    winner = None
    for n in sorted(patched):
        if winner is None:
            winner = n; continue
        cand_l = "__LTCG_" in n
        inc_l = "__LTCG_" in winner
        if cand_l != inc_l and cand_l:
            winner = n
    return winner

n_multi = 0
n_patched_collide = 0
for a, names, patched in rows:
    n_multi += 1
    tag = ""
    if len(patched) >= 2:
        n_patched_collide += 1
        tag = "  <<< REAL COLLISION (>=2 active patches)"
    elif len(patched) == 1:
        tag = "  (only 1 patched -> no collision)"
    else:
        tag = "  (none patched)"
    has_ltcg = any("__LTCG_" in n for n in names)
    print(f"0x{a:08x}{tag}")
    for n in names:
        mark = "ACTIVE " if n in active else ("cmted  " if n in commented else "       ")
        print(f"    [{mark}] {n}")
    if len(patched) >= 2:
        w = simulate(patched)
        print(f"    -> INSTALLED: {w}   (losers: {[x for x in sorted(patched) if x != w]})")
    print()

print(f"total addresses with >1 name: {n_multi}")
print(f"addresses where >=2 names have ACTIVE patches: {n_patched_collide}")
