# Sanctuary Studio

**Sanctuary Studio 2.2.10 Preview** is a Blender add-on for creating and editing assets and mods for **Sanctuary: Shattered Sun**. It was previously called Sanctuary Modding Toolkit.

## Choose a version

| Version | Use it for |
| --- | --- |
| **Studio 2.2.10 Preview** | The current project-based tools for browsing, importing, editing, validating, and exporting Sanctuary mods. See [Build from source](CONTRIBUTING.md). |
| **[Legacy Toolkit v1.4.9](Sanctuary_Modding_Toolkit_v1.4.9.zip)** | The included, known-working fallback for the established asset export workflow. [Legacy instructions](docs/legacy/README.md) |

Blender 4.5 on Windows is recommended for the preview. Keep one version enabled at a time. For an extension ZIP you build yourself, use **Preferences → Add-ons → Install from Disk**, then open **3D View → Sidebar → Sanctuary**.

## What it does

- Imports and exports native models, skeletons, animations, materials, and textures.
- Browses assets from your local game and mod projects, with unit, shield, and effect previews.
- Helps author unit templates and Lua, check project references, and package mods for Remmy’s Mod Manager.
- Includes an **Advanced / Legacy Tools** panel for low-level asset export controls.

## Known issues

**The Blender add-on is still in development.** The newer mod export and packaging workflows are unfinished. Some mods can fail to export or produce a package that still needs changes before it works in-game. If the new workflow blocks an asset export, use the included legacy version for that export. Complete mod packaging can still require additional work.

- Static validation catches some missing files, IDs, and Lua errors. Confirm the exported mod in-game before sharing it.
- Effects and shields are Blender previews. Their lighting, distortion, and other visual details can differ from Sanctuary.
- The source checkout uses your local game assets. The pre-rendered preview library and compiled optional runtime helper are separate from the source files.

## Documentation

- [Creator guide](docs/creator-guide.md)
- [Build from source and contribute](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md) and [validation notes](docs/validation.md)

Report problems in [GitHub Issues](https://github.com/Killercat4444/Sanctuary-Modding-Toolkit/issues). Include the Studio and Blender versions, the game build, the export target, and the exact error or steps to reproduce it.

The current extension declares **GPL-3.0-or-later**. See [LICENSE](LICENSE).
