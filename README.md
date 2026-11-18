# Sanctuary Studio

**Sanctuary Studio 2.2.10 Preview** is a Blender add-on for creating and editing assets and mods for **Sanctuary: Shattered Sun**. It was previously called Sanctuary Modding Toolkit.

## Install

Download the installable ZIP under **Assets** on the [Studio 2.2.10 Preview release](https://github.com/Killercat4444/Sanctuary-Modding-Toolkit/releases/tag/v2.2.10), or [build from source](CONTRIBUTING.md).

Blender 4.5 on Windows is recommended. In Blender, use **Preferences → Add-ons → Install from Disk** and select the ZIP without extracting it. Then open **3D View → Sidebar → Sanctuary**. The legacy asset export tools are part of this add-on under **Advanced / Legacy Tools**.

## What it does

- Imports and exports native models, skeletons, animations, materials, and textures.
- Browses assets from your local game and mod projects, with unit, shield, and effect previews.
- Helps author unit templates and Lua, check project references, and package mods for Remmy’s Mod Manager.
- Includes an **Advanced / Legacy Tools** panel for low-level asset export controls.

## Known issues

**The Blender add-on is still in development.** The newer mod export and packaging workflows are unfinished. Some mods can fail to export or produce a package that still needs changes before it works in-game. If the new workflow blocks an asset export, try **Advanced / Legacy Tools** inside Studio. Complete mod packaging can still require additional work.

- Static validation catches some missing files, IDs, and Lua errors. Confirm the exported mod in-game before sharing it.
- Effects and shields are Blender previews. Their lighting, distortion, and other visual details can differ from Sanctuary.
- The source checkout uses your local game assets. The pre-rendered preview library and compiled optional runtime helper are separate from the source files.

## Documentation

- [Creator guide](docs/creator-guide.md)
- [Build from source and contribute](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md) and [validation notes](docs/validation.md)

Report problems in [GitHub Issues](https://github.com/Killercat4444/Sanctuary-Modding-Toolkit/issues). Include the Studio and Blender versions, the game build, the export target, and the exact error or steps to reproduce it.

The current extension declares **GPL-3.0-or-later**. See [LICENSE](LICENSE).
