# Sanctuary Studio

**Sanctuary Studio 2.2.10 Preview** is a Blender add-on for creating and editing assets and mods for **Sanctuary: Shattered Sun**. It was previously called Sanctuary Modding Toolkit.

## Install

Download the installable ZIP under **Assets** on the [Studio 2.2.10 Preview release](https://github.com/Killercat4444/Sanctuary-Modding-Toolkit/releases/tag/v2.2.10), or [build from source](CONTRIBUTING.md).

Blender 4.5 on Windows is recommended. In Blender, use **Preferences → Add-ons → Install from Disk** and select the ZIP without extracting it. Then open **3D View → Sidebar → Sanctuary**. The legacy asset export tools are part of this add-on under **Advanced / Legacy Tools**.

## Features

### Unit Browser and Importer

Browse vanilla Sanctuary units and import their models into Blender along with their armatures. You can also include textures and animation clips when available.

![Unit Browser and Importer](docs/demos/Unit%20Browser%20and%20Importer.gif)

### Animation Previewer

Select an imported animation clip and play it on the unit’s armature in Blender to check how the model moves.

![Animation Previewer](docs/demos/Animation%20Previewer.gif)

### Import Mods With The Unit Browser

Browse units from installed mods and import supported models directly into Blender. The browser can load their armatures, textures, and animations when those files are available.

![Mod Importing using unit browser](docs/demos/Mod%20Importing%20using%20unit%20browser.gif)

### Import Shields with their VFX - WIP

Import a supported shield generator with its shield geometry and materials attached to the unit. The Blender preview is a work in progress and can look different in-game.

![Shield Importing WIP](docs/demos/Shield%20Importing%20WIP.gif)

### Create Icons for mod Units

Set up a camera for a unit, frame the model, and render an icon. Studio saves the PNG, DDS, and sprite files into your project.

![Unit Icon Creator](docs/demos/Unit%20Icon%20Creator.gif)

### Create Lua's or edit existing LUA's for Vanilla or Modded Units

Generate unit Lua from templates, then edit unit settings and behavior through form controls or the source editor inside Blender.

![Lua Creator and editor](docs/demos/Lua%20Creator%20and%20editor.gif)

### Models, textures, and animation export

- **Model and skeleton export** — Export an LOD0 mesh as `.sanmodel` and its armature as `.sanskeleton`. Export complete unit assets together or use the individual tools under **Advanced / Legacy Tools**.
- **PNG to DDS texture converter** — Convert albedo, mask or ambient occlusion, and normal PNGs to Sanctuary DDS files. Choose the texture size and mipmap mode. Studio uses the unit material’s texture filenames and refreshes its `.sanmaterial`.
- **Faction color preview** — Preview team colors on the model in Blender. Use an explicit team mask, preserve existing albedo alpha, or export without a team color mask.
- **Animation exporting** — Add one or more Blender Actions, set each clip’s frame range and FPS, and export `.sananimation` for a target armature. Baked Pose mode preserves previously baked bone poses. The animation assignment panel can bind clips to unit animation slots.
- **Model and rig validation** — Check the model and armature, then preview exported assets in a separate Blender scene to inspect the result and replay animation clips before packaging.

### Unit dimensions and behavior

- **Visual size editor** — See collision bounds, footprints, selection boxes, and skirts around the model in the viewport. Fit a shape to the export mesh, copy dimensions from an existing unit, or match the tarmac skirt to the selection size. Apply the changes to the unit’s Lua draft when ready.
- **Unit template and behavior tools** — Start from a working template for the unit’s role, generate separate host and client behavior scripts, and copy compatible components from a donor unit. Search for named values such as projectile IDs instead of typing them from memory.
- **Unit sounds and rotating bones** — Preview and assign audio to supported unit fields and configure rotating bones. In-game sound events still need to exist in a sound bank.

### VFX, projectiles, and shields

- **VFX Browser (WIP)** — Search effects by name, ID, category, or the units that use them. Preview supported effects in Blender, browse bundled stock GIFs, or generate a local GIF gallery. See [Known issues](#known-issues) for preview and import limitations.
- **Effect and projectile editing (WIP)** — Clone an effect and its dependencies into the project, edit settings such as color, intensity, lifetime, or size, and save a private copy. Edit projectile Lua and assign the new projectile or effect to a specific weapon.
- **Shield material authoring (WIP)** — Edit the shield body and rim materials, preview colors and energy scrolling, and replace pattern or normal textures. Export the shield under a new ID and bind its materials to a unit.

### Mod projects, validation, and packaging

- **Mod project management** — Create, open, and share editable projects as `.SMT` archives. Import existing project archives and configure a custom faction’s tag, commander, icon, and publishing metadata.
- **Project preflight** — Compile Lua without running it and check references to unit IDs, classes, models, bones, projectiles, effects, and upgrade targets before exporting.
- **Remmy’s Mod Manager export** — Package project Lua, assets, and declared plugins as a Mod API ZIP. Use this path for new custom factions.
- **Standalone export and direct install** — Stage a standalone ZIP or install supported unit, art, and behavior projects directly for testing. Compatibility depends on the game build and loader setup.

## Known issues

**The Blender add-on is still in development.** The newer mod export and packaging workflows are unfinished. Some mods can fail to export or produce a package that still needs changes before it works in-game. If the new workflow blocks an asset export, try **Advanced / Legacy Tools** inside Studio. Complete mod packaging can still require additional work.

- Static validation catches some missing files, IDs, and Lua errors. Confirm the exported mod in-game before sharing it.
- **VFX Browser:** Still a work in progress. Many GIF previews fail to load or play, and some effects may fail to import into Blender or look wrong after import.
- Effects and shields are Blender previews. Their lighting, distortion, and other visual details can differ from Sanctuary.
- The source checkout uses your local game assets. The pre-rendered preview library and compiled optional runtime helper are separate from the source files.

## Documentation

- [Creator guide](docs/creator-guide.md)
- [Build from source and contribute](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md) and [validation notes](docs/validation.md)

Report problems in [GitHub Issues](https://github.com/Killercat4444/Sanctuary-Modding-Toolkit/issues). Include the Studio and Blender versions, the game build, the export target, and the exact error or steps to reproduce it.

The current extension declares **GPL-3.0-or-later**. See [LICENSE](LICENSE).
