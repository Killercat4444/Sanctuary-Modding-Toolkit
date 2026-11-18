# Sanctuary Studio 2.2 — Creator Guide

START A FACTION

1. Set the Sanctuary engine folder and a writable project library in Project > Locations.
   Keep your editable project OUTSIDE the game installation. Configure the user's Mods folder.
2. Create Project, enable Custom faction, and enter your faction's name and uppercase tag.
3. Workshop > Behaviour > Generate unit Lua: choose Commander, Find Templates, and a working
   vanilla commander. Set a unique custom ID (e.g. uml0000), title and your faction tag.
   Use stock geometry initially by disabling Use current Unit ID for exported model, or export
   your own rigged model with all referenced bones and animations. Generate, inspect, Save.
4. Project > Faction & Release > Load Publishing Settings. Enter your unique mod ID, author,
   description, faction key/name/tag, unit prefix (um in this example), base visuals, saved
   commander ID and faction PNG. Save Faction & Release Setup.
5. Check Entire Project. Fix every error, then Export MM ZIP (Recommended).
6. Install the current Remmy Mod Manager. Put the ZIP's mod folder in engine/SanctuaryMods.
   Enable it in the lobby's Mods panel. Your faction should appear with the selected commander.
   Start a SMALL test match, select/move the commander, build, save/reload and test with AI.

Each custom faction needs its own prefix and tag. ue, uc and ug belong to vanilla factions.
FACTION SLOTS: the base game uses hard-coded faction slots. Two standalone mods claiming
the same slot can hide one another, even if their unit IDs and faction names are different.
Remmy MM assigns custom faction slots dynamically. Publish NEW FACTIONS through MM, not
standalone/direct install. Studio intentionally blocks those paths for new faction projects;
it does not silently reserve or overwrite a vanilla/another mod's slot.
The guided setup registers one commander, not Dycom's special multi-commander lobby plugin.
It does not make an AI magically understand entirely new economy/combat mechanics.
Add working engineers, factories, economy and build menus incrementally. Keep test releases.


UNIT LUA AND RELEASE CHECKS

Generate from a WORKING game template for the correct unit role. This retains a complete
engine schema and working host/client behavior. Enter YOUR unit ID; no vanilla ID is prefilled.
Use current Unit ID for exported model only after exporting its matching assets. Otherwise
the unit may borrow the donor's model. Assign new animations/bones explicitly.

Create paired host/client behavior Lua produces project-owned subclasses of the donor class.
Host code controls simulation; client code controls presentation. Calling inherited setup
is essential when overriding creation/build/upgrade methods. Edit Full Lua in the Text Editor;
save disk-backed behavior scripts with Text Editor > Save. Save blueprint drafts in Studio.
Adding components copies a compatible donor section; it does not redesign custom behavior.

Named fields offer searchable values. On a weapon's projectileTemplate click Choose Value,
search tank/EDA/T3 or an ID, pick an entry, then Apply Field. The picker writes the actual ID,
not its friendly label. Projectiles shared by several units list those real consumers.

Check Entire Project compiles source with the game's Lua compiler WITHOUT executing it.
It checks IDs, duplicate paths, literal imports, host/client class bindings, positive health/
footprint values, model/skeleton/material references, explicit bone names, projectile and
upgrade targets, VFX dependencies, commander ownership and faction tags/build filters.
Release exports stop on errors. Fix the first errors then recheck; later errors may follow
from one missing dependency. Read warnings too.

IMPORTANT: passing static checks is NOT a guarantee against frozen-map/match initialization
bugs. Dynamic Lua, engine API calls, nil values computed at runtime, state transitions, AI,
multiplayer and shader semantics still need an in-game smoke test. Never distribute untested.


VFX, PROJECTILES AND SHIELDS

Workshop > Behaviour > Effects: Index All. Search by readable name, engine ID, path, faction,
tier or unit consumer. Use the category filter. Build All GIFs creates a local searchable
   gallery; selection automatically plays an animation inside the panel. Catalogue loading and
  animation rendering run in the background; rapid selection keeps only the newest result.
   Warm opens reuse the catalogue. Closing the browser cancels its preview work. Stop also cancels a delayed selection.
     Cache access retries briefly if busy; persistent failures show retry instructions.
     Preview ready means the first animation frame has displayed. Only marked abandoned
     cache jobs with inactive owner and worker are recovered; unknown folders remain. Projectile previews travel
in a straight line. These interpret asset textures/meshes/particle settings, NOT Unity's
renderer: shader distortion, collisions, complex subemitters and physics are approximate.
Previews use 256 x 256 pixels and 30 frames, with native flipbook/UV and lifetime data.
Partial previews label omitted shader-only layers. Procedural flares are approximated.
Shield/material/beam swatches do not simulate hits or the complete game scene. A blank
preview can mean unsupported emission or a zero exported trail width. Sampling is bounded;
appearance must be checked in-game. The larger browser poster is simulated, not a raw atlas.
Preview limitations / next steps gives source-backed explanations, shown automatically
for blank entries. Distance-emitting trails and dust use a moving-emitter demonstration:
Demo emitter speed is in source units/second (default 1), not game speed or an export
setting. Press Play to refresh; zero pauses distance-only emission. A verified same-path
particle/beam extension mismatch is resolved for the preview only; Lua remains unchanged.
Unchanged stock images and exact matching bundled fingerprints are reused. Zero-width
trails, unsupported shaders, distortion and shield hits still need authoritative runtime
data or matched in-game captures. No nonzero trail width or shader parity is invented.
Browse Included Stock GIFs opens the shipped offline catalogue immediately. Generate a new
gallery for YOUR project effects or after game updates; included previews are not live captures.

Never edit game assets directly. Clone an effect or projectile into your project first.
Effect cloning includes referenced materials, textures and nested effect dependencies.
Edit typed settings, HDR color/intensity/opacity, lifetime/speed/size, or full JSON/Lua.
Save Custom Asset / Save Custom Projectile, assign it to the intended weapon slot, Save unit.
To avoid changing all weapons which share a stock projectile, use a unique projectile ID.
Full projectile Lua permits any supported field, while the movement form covers common ones.

AUTOMATIC GENERATOR SHIELDS: Import Selected Unit (or a matching loose .sanmodel) reads
that unit's own blueprint and creates every resolved defence.shields sphere beside the
generator. Native radii scale a radius-one sphere directly; offset is applied in the
named bone's local space, and parent/world scale is inherited once. Custom body/glow
references and DDS are resolved from that source project, not your current donor preset.
Select the generator and open Sanctuary > Imported Unit Shields. Open Generator & Shield
Render Scene opens a fitted camera/render scene; Return to Imported Unit restores the
import scene. Edit Shield Material N opens the existing material authoring workflow with
the generator as context and the same native shield placement. Bind materials to the
currently loaded custom unit blueprint; importing reference art does not replace that Lua.
Repeated import at the same source/placement/options reuses the model/rig and refreshes
only generated shields; moved instances remain independent. Missing bones/materials/DDS
are reported, never replaced with a guessed preset. With skeleton import disabled, the
native rest-bone center is used and animated attachment is explicitly unverified.
Generated spheres are preview-only and use source UVs/material values. The render scene
uses a labeled Blender-only HDR brightness conversion, native additive glow and disabled
shield shadows. Base-alpha/facing is a proxy; HDR line composition, depth/refraction/water,
damage/hits and active/recharge gameplay state require game validation. Full shields are
shown for authoring even though the native game initially disables meshes until activation.

SHIELD AUTHORING: select a stock Shield Forcefield or Shield Edge Detail material in the
effect browser, then Open Shield Authoring. This creates a separate native UV sphere with
body and glow materials. Open the Sanctuary sidebar. The preview uses original DDS pixels,
HDR colors, texture transforms, scrolling and a normal/facing proxy. Preview exposure is
view-only; it never changes game assets. Play the timeline to see scrolling.
For an imported generator, choose Edit Shield Material N from the generator, shield
surface, bone anchor or offset. Select Body or Rim / Glow to activate its editable material.
Native pattern, energy flow and rim colors are separate branches: change the hue and HDR
brightness, or Match Energy and Rim to Pattern Hue. Brightness is separate from opacity.
Live Preview updates the existing material without Apply; the explicit Apply / Rebuild
button remains available. Play the timeline to see native energy scrolling.
Native pattern and normal textures show thumbnails and source paths. Select the actual
slot, then Browse PNG and Replace This Texture. Change its native Tiling U/V and Offset
U/V, energy scroll U/V and normal strength where the source defines those fields. RGB
normals are the default; enable DXT5nm AG packing only when required by the native shader.
Original comparison, layer solo and exposure affect only the preview. Reset Selected
Native Property or Reset Private Materials to Source restores native source values; undo
and redo restore coherent controls/material data. Return and reopen retain the same draft
and staged textures, even after generator reimport or placement refresh.
Advanced controls label game-only fields with no Blender effect. All native fields export.
Bound native Value/RGB node outputs are supported; added nodes, links, drivers, painted
preview images and altered geometry/UVs/transforms are rejected. No hex pattern is generated
automatically. Original BC7/DXT/BGRA texture bytes remain exact unless replaced.
Validate Shield Draft, choose a new lowercase Unique Shield ID, Export New Unique Shield
Assets. Existing IDs and custom loader edits stop export. Bind Both Materials to Source
Unit, Return to Source Unit, Save Unit, Check Entire Project, then Export MM ZIP. Both native
references and a narrow opt-in loader append ship together. Reopen either exported shell
to edit another private copy. Changed drafts require another unique export before binding.
Return retains the draft; Discard This Draft removes only its generated scene/cache and
preserves exported assets. Save your .blend if you want to retain the draft across sessions.
Pending native controls are restored when reopening, including edits saved before live
debounce or with Live Preview disabled. A failed update preserves prior native data and
blocks export until corrected; changing the control again retries it.
A staged DDS moved or changed on disk blocks export: restore it, or select that texture
slot and replace it with your PNG. Other native controls stay intact. If a generated
preview PNG is missing, restore the exact cache file named by validation and retry;
it reloads empty preview data. Painted preview images remain unsupported. Keep your
source PNGs, .blend drafts and staged DDS/cache together until private export is saved.
The proxy does not reproduce HDRP intersection/refraction, hit callbacks or exact shader
arithmetic. Check color, opacity, glow, radius, motion and hits in-game before distributing.


MODELS, ANIMATIONS AND ICONS

Pin your final mesh and its armature. Confirm UVs, strongest vertex bone weights, root,
build bones, turret/muzzle bones and animation bindings. Import/export uses existing codecs.
Validate & Preview in Blender creates a SEPARATE round-trip scene; inspect it and play clips.
Export Unit to Project writes your model/textures/animations; it does not install live files.

Create an Icon Scene. New scenes fit the mesh inside the chosen camera (including perspective)
with a small margin. Fit Subject is available again after changing geometry or composition.
Manually repositioning the camera is respected at render time. Render & Save Icon reloads
the image and thumbnail every time, and saves PNG, DDS and sprite in your project.

The Icon tab shows the current project's saved Unit ID PNG after switching or reopening.
Reload Saved Icon refreshes an externally changed PNG. A cold image may briefly show
Preparing saved icon preview while its thumbnail loads; all icon controls stay available.
Return to the source unit if an old icon scene belongs to a different selected Unit ID.

Save your .blend with source meshes and actions. A game release ZIP is not an editable
Blender project. Use Create Project ZIP to share authoring files and Import Archive to restore.


RECOMMENDED EXPORT: REMMY MOD MANAGER

Use MM for faction mods. MM / Mod API 1.3 handles Lua,
faction registration, unit discovery, art packs, lobby activation and multiplayer consistency.

Set up publishing, set your release Version, save all drafts, Check Entire Project, then
Export MM ZIP. It includes only this project's Lua, declared plugin DLLs, art and faction icon;
no other faction's IDs, AI, hardcoded file paths, BepInEx install or loose-Lua catcher is added.
The version in mod.json is the version shown in MM. The generic release filename uses the
unique mod ID and version. The existing Dycom adapter keeps its requested legacy release name.

Install current Remmy Mod Manager separately using its own instructions. Copy the release
mod folder into engine/SanctuaryMods, or use the non-overwriting installer.bat for a first
install. For updates, replace ONLY your own mod folder with MM/the release instructions.
Enable gameplay mods in the lobby; every player must have the same release. If you updated
files mid-session, leave the lobby/reload MM as its version supports; a full restart is the
safest test when changing DLLs, loaders or standalone/direct files.


ADVANCED: STANDALONE / DIRECT INSTALL

Standalone and direct Mods installs cannot rely on the stock game finding loose Lua.
Studio audits BepInEx and Lua-cache loaders before installing. A trusted Remmy MM bootstrap
ZIP supplies only its BepInEx/doorstop infrastructure if absent; MM itself is NOT installed
by this path. No existing BepInEx, config or catcher is replaced. Incomplete installs and
unrecognized/conflicting catchers must be resolved explicitly; the tool stops rather than
guessing. Use MM when you already have MM or complex overlapping faction integrations.

NEW FACTIONS ARE MM-ONLY. The game's standalone faction slots are hard-coded; two faction
mods using the same slot can prevent one appearing. MM dynamically coordinates those slots.
Standalone/direct support here is for unit/art/behavior mods using existing game factions,
not new faction registration. Disabling faction publishing is not a workaround: unit tags
and build menus must actually target the existing faction you intend to extend.
Projects declaring compiled Plugins DLLs also require MM export: a Mod API DLL is not
automatically a BepInEx plugin. Studio stops instead of silently omitting those files.

If no catcher is installed, Studio supplies its own generic preloader DLL. It reads only
Studio-owned standalone cache packages; it does NOT register new factions.
It injects cached Lua at startup and rebuilds directory discovery. It does not poll Lua or
log every tick. Standalone installs are always enabled and require a FULL game restart.
All multiplayer participants must have the same standalone content and load order.

Export Standalone ZIP stages files without changing your game. Extract the ZIP and run
installer.bat for a preserving first install. Read its guide; conflicting installs stop.
Direct Install Project audits/validates first and updates only the project's owned payload.
Existing non-Studio catchers are never overwritten; unsupported cache formats/faction hooks
are reported. Do not combine standalone and MM copies of the same faction.


TEST AND TROUBLESHOOT

Before sharing: test an empty/small map, new match, human and AI commander spawn/selection,
movement, build menus, economy, factory production, upgrades, assistance and cancellation.
Then test effects, projectiles, shields, icons, replay/multiplayer and a different map.
Measure performance with realistic unit counts; previews/checks do not certify performance.

Frozen map / unselectable build-shader units: check release errors and the newest game /
BepInEx log for the FIRST exception, missing template/class/asset or unsupported engine API.
Check all participants' versions. Restart after DLL or standalone changes. Don't remove
unrelated mods or overwrite another loader to make an error disappear. Test your own mod
in a clean setup where possible. Save the exact release ZIP and error log for reproduction.

Draft changed on disk: reopen after saving a copy of your Text draft. Missing bone: fix the
Lua binding or exported rig, not the texture. Missing projectile: clone/save its template,
include its effect/model/material dependencies and assign its ID. No build menu: verify the
constructor's canBuild expression and the units' BUILDABLE_BY tags use your faction tag.

No toolkit can prove arbitrary authored Lua is bug-free without running the game. The
checker blocks many preventable packaging failures and gives file-specific diagnostics;
warnings and in-game smoke tests remain part of the release process.
