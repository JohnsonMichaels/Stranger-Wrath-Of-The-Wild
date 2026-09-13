"""Prepare the Cxbx fork for a release build of the 2004 beta.

    python tools/beta_release_prep.py           # apply
    python tools/beta_release_prep.py --check   # report only

Two things, both idempotent (keyed on marker comments), both Edit-style exact
replacements so nothing else in the files moves:

1. REMOVE the diagnostic keys. NUMPAD5 forced every texture to magenta, NUMPAD1-4
   stamped MARK lines into the log, and F4 (WM_KEYDOWN) toggled the same texture
   override. They were investigation tools; a release must not have a key that turns
   the world magenta. The WM_SYSKEYDOWN F4 handler (Alt+F4 = close) is upstream's
   and stays.

2. MOVE the SmallAllocator arena fix INTO the emulator. Until now it lived in the XBE
   *file* (tools/patch_smallalloc.py), which only helps whoever ran that script. The
   launcher makes recipients supply their own copy of the beta, so the enlargement
   has to happen to whatever XBE is loaded: after the sections are in guest memory,
   fingerprint the four instruction sites of SmallAllocator_FixedRestoring's
   initialiser and rewrite the immediates in memory. The fingerprint is the opcode
   bytes AND the stock immediates at four exact addresses, so any other XBE - or an
   already-enlarged one - is left untouched. The file on disk is never modified.
"""
import io
import sys

D3D = r"C:\Users\<you>\SWBeta\src\Cxbx-Reloaded\src\core\hle\D3D8\Direct3D9\Direct3D9.cpp"
KRNL = r"C:\Users\<you>\SWBeta\src\Cxbx-Reloaded\src\core\kernel\init\CxbxKrnl.cpp"
NL = "\\n"


def read(p):
    return io.open(p, encoding="utf-8", errors="surrogateescape").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", errors="surrogateescape").write(s)


def cut_block(text, start_marker, end_marker, what):
    """Remove text from the line containing start_marker through the line containing
    end_marker (inclusive). Both must exist exactly once."""
    i = text.find(start_marker)
    if i == -1:
        return text, False
    line_start = text.rfind("\n", 0, i) + 1
    j = text.find(end_marker, i)
    if j == -1:
        raise SystemExit("%s: end marker missing" % what)
    # Through the end of the line holding the END of the marker, not its start: a
    # multi-line marker whose last lines are the function's closing braces must take
    # those braces with it. The first version stopped after the marker's first line
    # and left two stray '}' behind, which broke the build.
    line_end = text.find("\n", j + len(end_marker)) + 1
    return text[:line_start] + text[line_end:], True


def prep_d3d(text):
    changed = []

    # 1a. the WM_KEYDOWN F4 toggle (keep upstream's WM_SYSKEYDOWN Alt+F4)
    old = (
        "            else if (wParam == VK_F4)\n"
        "            {\n"
        "                g_bForceWhiteTextures = !g_bForceWhiteTextures;\n"
        '                printf("DIAG: force-white textures %s' + NL + '", g_bForceWhiteTextures ? "ON" : "OFF");\n'
        "                fflush(stdout);\n"
        "            }\n"
    )
    if old in text:
        text = text.replace(old, "", 1)
        changed.append("F4 toggle")

    # 1b. the flag and its comment block
    i = text.find("// F4: bind flat white to every texture stage.")
    if i != -1:
        j = text.find("bool                                g_bForceWhiteTextures = false;", i)
        line_end = text.find("\n", j) + 1
        # also drop the blank line that preceded the comment
        line_start = text.rfind("\n", 0, i) + 1
        if text[line_start - 1:line_start] == "\n" and text[line_start - 2:line_start - 1] == "\n":
            line_start -= 1
        text = text[:line_start] + text[line_end:]
        changed.append("flag")

    # 1c. the polled key function and its call
    text, ok = cut_block(text, "// Diagnostic toggles, polled rather than driven from WM_KEYDOWN.",
                         "\t\ts_MarkWasDown[i] = bDown;\n\t}\n}", "poller")
    if ok:
        changed.append("poller")
    if "\tCxbxrPollDiagnosticKeys();\n" in text:
        text = text.replace("\tCxbxrPollDiagnosticKeys();\n", "", 1)
        changed.append("poller call")

    # 1d. the magenta diagnostic texture
    text, ok = cut_block(text, "// Flat magenta, for the force-textures diagnostic only.",
                         "\treturn g_pCxbxDiagTexture;\n}", "diag texture")
    if ok:
        changed.append("diag texture")

    # 1e. the override in the texture stage loop
    old = (
        "\t\t// F4 diagnostic: substitute flat white everywhere, so what remains on screen\n"
        "\t\t// is lighting and shader output with the textures taken out of the picture.\n"
        "\t\tif (g_bForceWhiteTextures && pHostBaseTexture != nullptr) {\n"
        "\t\t\tif (IDirect3DBaseTexture *pDiag = CxbxrGetDiagnosticTexture()) {\n"
        "\t\t\t\tif (bNeedRelease) {\n"
        "\t\t\t\t\tpHostBaseTexture->Release();\n"
        "\t\t\t\t\tbNeedRelease = false;\n"
        "\t\t\t\t}\n"
        "\t\t\t\tpHostBaseTexture = pDiag;\n"
        "\t\t\t\tg_RenderStat_TextureOverrides++;\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "\n"
    )
    if old in text:
        text = text.replace(old, "", 1)
        changed.append("texture override")

    leftovers = [k for k in ("g_bForceWhiteTextures", "CxbxrPollDiagnosticKeys", "CxbxrGetDiagnosticTexture", "MARK: %s")
                 if k in text]
    return text, changed, leftovers


TITLE_PATCH = r'''
// Title patch: Oddworld: Stranger's Wrath, 2004-05 devkit beta.
//
// The engine serves every allocation of <= 256 bytes from SmallAllocator_FixedRestoring,
// a FIXED arena of 1024 4 KB pages built once by its initialiser and never grown.
// Mongo Valley (region_03) runs it dry while deserialising its object graph,
// the engine asserts "Good Lord! Out of Pages in SmallAllocator_FixedRestoring!"
// and parks in its halt handler - a black screen that looks exactly like a hang.
// (Gizzard Gulch is larger on every axis and loads fine: it is the object graph's
// shape, not the level's size.)
//
// The arena is defined by four immediates in the one initialiser. They are rewritten
// here, in guest memory, after the sections are loaded: the XBE on disk is never
// touched, which matters because recipients supply their own copy of the beta. The
// fingerprint is the opcode bytes AND the stock immediates at four exact addresses,
// so any other XBE - or an already-enlarged file - is left alone.
static void CxbxrKrnlApplyTitlePatches()
{
	struct Site { xbox::addr_xt Opcode; const char *Bytes; unsigned OpcodeLen; xbox::addr_xt Imm; uint32_t Stock; uint32_t Patched; const char *What; };
	static const Site Sites[] = {
		{ 0x00142EC7, "\x68",     1, 0x00142EC8, 0x00400000, 0x01000000, "push <arena size>" },
		{ 0x00142ED4, "\x8D\x88", 2, 0x00142ED6, 0x00400000, 0x01000000, "lea ecx,[eax+<arena size>]" },
		{ 0x00142EF1, "\x81\xF9", 2, 0x00142EF3, 0x000003FF, 0x00000FFF, "cmp ecx,<last page>" },
		{ 0x00142F10, "\x81\xF9", 2, 0x00142F12, 0x00000400, 0x00001000, "cmp ecx,<page count>" },
	};
	const size_t Count = sizeof(Sites) / sizeof(Sites[0]);

	// Every site must be readable and match either the stock or the already-patched
	// value, with the right opcode in front of it. Anything else is not this title.
	unsigned Stock = 0, Already = 0;
	for (size_t i = 0; i < Count; ++i) {
		if (IsBadReadPtr((const void *)(uintptr_t)Sites[i].Opcode, Sites[i].OpcodeLen + 4 + 4)) {
			return;
		}
		if (memcmp((const void *)(uintptr_t)Sites[i].Opcode, Sites[i].Bytes, Sites[i].OpcodeLen) != 0) {
			return;
		}
		uint32_t Value;
		memcpy(&Value, (const void *)(uintptr_t)Sites[i].Imm, sizeof(Value));
		if (Value == Sites[i].Stock) { ++Stock; }
		else if (Value == Sites[i].Patched) { ++Already; }
		else { return; }
	}
	if (Already == Count) {
		printf("TITLEPATCH: SmallAllocator arena already 4096 pages / 16 MB in this XBE - nothing to do" "\n");
		return;
	}
	if (Stock != Count) {
		printf("TITLEPATCH: SmallAllocator arena sites are inconsistent (%u stock, %u patched) - left untouched" "\n", Stock, Already);
		return;
	}
	for (size_t i = 0; i < Count; ++i) {
		memcpy((void *)(uintptr_t)Sites[i].Imm, &Sites[i].Patched, sizeof(uint32_t));
	}
	printf("TITLEPATCH: SmallAllocator arena enlarged in memory, 1024 -> 4096 pages (4 -> 16 MB); the stock 1024 run dry loading Mongo Valley" "\n");
	fflush(stdout);
}
'''


def prep_krnl(text):
    if "static void CxbxrKrnlApplyTitlePatches()" in text:
        return text, "already applied"
    anchor = "// HACK: Attempt to patch out XBE header reads\nstatic void CxbxrKrnlXbePatchXBEHSig() {"
    if anchor not in text:
        raise SystemExit("CxbxKrnl.cpp: XbePatchXBEHSig anchor not found")
    text = text.replace(anchor, TITLE_PATCH.lstrip("\n") + "\n" + anchor, 1)
    call = "CxbxrKrnlXbePatchXBEHSig();"
    n = text.count(call)
    if n != 1:
        raise SystemExit("CxbxKrnl.cpp: expected exactly one call to XbePatchXBEHSig, found %d" % n)
    i = text.find(call)
    line_start = text.rfind("\n", 0, i) + 1
    indent = text[line_start:i]
    text = text.replace(call, call + "\n" + indent + "CxbxrKrnlApplyTitlePatches();", 1)
    return text, "applied"


def main():
    check = "--check" in sys.argv
    d3d = read(D3D)
    new_d3d, changed, leftovers = prep_d3d(d3d)
    print("Direct3D9.cpp : removed %s" % (", ".join(changed) if changed else "nothing (already clean)"))
    if leftovers:
        raise SystemExit("Direct3D9.cpp still references: %s" % ", ".join(leftovers))
    krnl = read(KRNL)
    new_krnl, status = prep_krnl(krnl)
    print("CxbxKrnl.cpp  : title patch %s" % status)
    if check:
        return
    if new_d3d != d3d:
        write(D3D, new_d3d)
    if new_krnl != krnl:
        write(KRNL, new_krnl)
    print("done")


if __name__ == "__main__":
    main()
