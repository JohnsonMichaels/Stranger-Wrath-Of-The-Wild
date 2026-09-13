# Stranger's Wrath of the Wild

A modified emulator that runs the **May 2004 Xbox devkit beta** of *Oddworld:
Stranger's Wrath* on Windows. The beta was never a PC game: the executable is an
Xbox image. This project supplies a compatibility layer — a modified build of
[Cxbx-Reloaded](https://github.com/Cxbx-Reloaded/Cxbx-Reloaded) — so it runs
natively on your CPU and translates the Xbox Direct3D and DirectSound calls to
your graphics card and sound device.

PC port by JohnsonMichaels.

## Download

Grab the latest package from the
[Releases](https://github.com/JohnsonMichaels/Stranger-Wrath-Of-The-Wild/releases)
page, unpack it anywhere, run `Stranger's Wrath Beta.exe`, choose a resolution,
press Play.

**You need your own copy of the beta.** The package contains no game data. On
first run the launcher asks for the folder that holds the beta's `Final` build and
sets it up; your files are not modified.

Requirements: 64-bit Windows, a Direct3D 9 capable graphics card.

## What works

- All six levels load, including Mongo Valley. The beta ships with an engine
  memory pool too small for that level; the emulator enlarges it in memory when
  the game starts.
- Music, cutscene audio and sound effects, including positional effects such as
  footsteps and gunfire — the Xbox's own 3D audio calculator runs natively and
  the emulator applies its output.
- Keyboard and mouse controls matching the PC release.

Known issues: some characters render darker than they should, and some textures
may flicker. It is a beta; expect rough edges.

## Controls

| Action | Key |
|---|---|
| Move | W A S D |
| Look | Mouse |
| Fire / Aim | Left / Right mouse |
| Jump | Enter |
| Use | E |
| Melee | F |
| Change view | V |
| Crouch | Left Ctrl |
| Pause | Esc |
| Map | Tab |
| Menus | Arrow keys, Enter |

The mouse is held inside the window while the game has focus; Alt-Tab releases
it, and F3 toggles the behaviour.

## Repository layout

- `main` (this branch)
  - `launcher/` — the launcher's source (C#, WinForms) and icon
  - `tools/` — the scripts used to build, package and investigate the port
    (packaging, XBE/PDB analysis, log parsers). They reference the developer's
    directory layout as `C:\Users\<you>\...`; adjust the paths at the top of each
    script.
  - `docs/` — the engineering notes: what the beta does, what was fixed and how
    it was found
- `emulator` branch — the complete source of the modified Cxbx-Reloaded, with
  history, based on upstream commit `585c49a`. The symbol-database submodule
  carries local modifications that are compiled into the shipped DLL; they are in
  `import/XbSymbolDatabase-oddworld-beta.patch` — apply it inside
  `import/XbSymbolDatabase` before building.

## Building

```
git clone -b emulator --recurse-submodules https://github.com/JohnsonMichaels/Stranger-Wrath-Of-The-Wild.git cxbx
cd cxbx/import/XbSymbolDatabase && git apply ../XbSymbolDatabase-oddworld-beta.patch && cd ../..
```

Then build as upstream Cxbx-Reloaded does (CMake, Visual Studio, x86, Release).
`tools/make_installer.ps1` assembles the distributable from the build output, the
launcher and a scrubbed settings file; it never bundles game data.

## Credits and licence

Cxbx-Reloaded is licensed GPL-2.0; the modifications in the `emulator` branch are
likewise GPL-2.0, and that branch is the complete corresponding source of the
shipped emulator.

*Oddworld: Stranger's Wrath* is the property of Oddworld Inhabitants. This project
does not distribute any game data.
