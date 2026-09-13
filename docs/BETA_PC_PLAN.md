# Playing the beta on PC — the plan

The goal, in the owner's words: "our own build eventually, kind of like the PC
Version, but just the beta." Native PC, no emulator.

## The insight that makes it possible

**The HD PC release is this same engine.** Just Add Water ported the `exoddus`
codebase the beta is built from (see ENGINE_SYMBOLS.md). That is why:

- the archives share magic `0x3A4B5C6D` and sit ONE version apart (v4 vs v5),
- the beta's level bundles pass the HD v5 structural invariant unchanged,
- beta level geometry rendered in the Steam executable on the first real try
  (see BETA_BUILD.md, the `utility2` hybrid).

So "a PC build of the beta" does NOT mean building an engine. It means moving
beta CONTENT into the PC engine we already have. That is a data problem.

## Routes, rated

| route | verdict |
|---|---|
| Port the Xbox exe to PC | **No.** No source in the archive - PDBs are symbols. Would mean reimplementing 161 Xbox kernel calls plus statically-linked Xbox D3D. That is what Cxbx is; multi-year. |
| Emulation (Cxbx/xemu) | **Reference only.** Ground truth for how the beta should look and behave, used to VERIFY conversions. Not the deliverable. |
| **HD exe + converted beta content** | **The plan.** Proven for geometry. Everything left is format conversion, each piece independently testable. |

## What the build actually is

- the owner's retail Steam `stranger.exe`, unmodified
- SWSE (`dinput8.dll`) for any engine-level behaviour the beta content needs
- converted beta data under `data\bundles\...`

Double-click and play. Native performance, and every existing tool - SWSE
console, OddForge, Oddview, the harvest probes - keeps working.

It ships as a **mod/patcher over the retail game**, not a standalone product.
That is both the legal shape and the practical one.

## Ladder

| step | status |
|---|---|
| Beta level geometry loads and plays in the HD engine | **DONE** - `utility2` hybrid, verified by harvest census (2 area draws = the beta's two area meshes, vs the HD level's 3) |
| `.lvl` v5 -> v6 object graph: couch, water, spawn points, collectables | in progress |
| Texture conversion (Xbox swizzle + palettise -> PC) | required, not started. Symbols name `xboxtextureutil`, `palettize`, `palcreate`, `xboxresourcemakertexture`. |
| Beta characters spawnable as NPCs | machinery exists (spawn hook, `npcspy`, CHARACTER_HASHES.tsv) |
| Animations: 2004 Granny vs HD Granny runtime | **untested - the main risk** |
| Whole beta levels end to end | goal |

## Risks, stated up front

**Animations are the one that could bite.** Whether 2004 `.gr2` files load in
the HD's Granny runtime is a version question that cannot be answered without
testing. If they do not, beta characters come across as static meshes. Fallback
already understood: capture bone matrices at runtime (the pose lives in the
vertex program locals - see materials.cpp v4 dump).

**Some things will not convert.** Beta audio is XACT (`.xsb`/`.xwb`), the HD
uses FMOD. The beta's GUI/system bundles (`AppGlobal`, `*_swf`, `prefs`) FAIL
the structural test that its level bundles pass, so their sub-layout differs.

**The beta is a mid-development snapshot, not an alternate complete game.** It
has regions 01-05 only; no region 00 or 06. Expect progressive restoration of
beta content into a working PC game, not a "beta edition" on day one.

## Why this order

Each rung is independently testable and independently valuable. Levels first
because they are proven. Textures next because every later step needs them to
look right. Characters after that because the spawn machinery already exists.
Animations last because they carry the only real unknown, and because a static
beta character in a beta level is already a result worth having.
