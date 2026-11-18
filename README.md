# Sanctuary Studio

**Sanctuary Studio 2.2.10 Preview** is a Blender add-on for creating and editing assets and mods for **Sanctuary: Shattered Sun**. It was previously called Sanctuary Modding Toolkit.

## Install

Download the installable ZIP under **Assets** on the [Studio 2.2.10 Preview release](https://github.com/Killercat4444/Sanctuary-Modding-Toolkit/releases/tag/v2.2.10), or [build from source](CONTRIBUTING.md).

Blender 4.5 on Windows is recommended. In Blender, use **Preferences → Add-ons → Install from Disk** and select the ZIP without extracting it. Then open **3D View → Sidebar → Sanctuary**. The legacy asset export tools are part of this add-on under **Advanced / Legacy Tools**.

## Features

- [Mod Importing using unit browser](#mod-importing-using-unit-browser) — Browse and import units from installed mods.
- [Shield Importing WIP](#shield-importing-wip) — Preview imported shield geometry and materials on supported units.
- [Unit Browser and Importer](#unit-browser-and-importer) — Import vanilla units with their armatures and, where available, textures and animation clips.
- [Unit Icon Creator](#unit-icon-creator) — Frame a unit and render its in-game icon.
- [Animation Previewer](#animation-previewer) — Play imported animation clips on a unit’s armature in Blender.
- [Lua Creator and editor](#lua-creator-and-editor) — Generate unit Lua from templates and edit it inside Blender.

Studio also includes project validation, packaging tools for Remmy’s Mod Manager, and low-level export controls under **Advanced / Legacy Tools**.

## Demos

### Mod Importing using unit browser

Browse units from installed mods and import supported models directly into Blender. The browser can load their armatures, textures, and animations when those files are available.

![Mod Importing using unit browser](docs/demos/Mod%20Importing%20using%20unit%20browser.gif)

### Shield Importing WIP

Import a supported shield generator with its shield geometry and materials attached to the unit. The Blender preview is a work in progress and can look different in-game.

![Shield Importing WIP](docs/demos/Shield%20Importing%20WIP.gif)

### Unit Browser and Importer

Browse vanilla Sanctuary units and import their models into Blender along with their armatures. You can also include textures and animation clips when available.

![Unit Browser and Importer](docs/demos/Unit%20Browser%20and%20Importer.gif)

### Unit Icon Creator

Set up a camera for a unit, frame the model, and render an icon. Studio saves the PNG, DDS, and sprite files into your project.

![Unit Icon Creator](docs/demos/Unit%20Icon%20Creator.gif)

### Animation Previewer

Select an imported animation clip and play it on the unit’s armature in Blender to check how the model moves.

![Animation Previewer](docs/demos/Animation%20Previewer.gif)

### Lua Creator and editor

Generate unit Lua from templates, then edit unit settings and behavior through form controls or the source editor inside Blender.

![Lua Creator and editor](docs/demos/Lua%20Creator%20and%20editor.gif)

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
