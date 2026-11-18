# 2.2.10 Saved Icon Preview Fix

- Reproduced the blank Icon panel in an own factory GUI: Blender forbids writing ImagePreview.image_size during panel drawing. Drawing now reads prepared thumbnails; a bounded timer loads cold or externally changed PNGs outside drawing.
- Render and existing-PNG saves prepare the thumbnail after the final native PNG/DDS/sprite write. Reload Saved Icon refreshes images even when filesystem timestamps are unchanged.
- The current project's own saved Unit ID PNG is discovered after unit switching and reopening. Project/unit identities remain separate; stale icon-scene selections and implicit other-unit assigned paths stop before writes. Deliberate file chooser reuse remains available.
- Tests cover real panel drawing, repeated render/save, native DDS pixels, unit/project switching, missing/corrupt/restored PNGs, bounded cache cleanup, actual save/reopen and registration cleanup.
- Includes all staged 2.2.7–2.2.9 shield and worker fixes. Native codecs, icon encoding, shaders, renderer and MM export dispatch remain unchanged. Installed roots remain 2.2.6 pending authorized installation; no user Blender restart or hot reload.

# 2.2.9 Shield Texture Integrity Preview

- Independently reproduced a 2.2.8 defect where changed staged DDS bytes exported while the preview still showed older pixels. Replacement texture byte receipts now block that export before project writes.
- Explicit PNG replacement repairs the selected missing/changed staged DDS while retaining other native values and strict graph/geometry checks. Saved receipts participate in actual Blender undo/reset and file save/load.
- Pre229 saved replacements acquire a receipt only when the decoded native pixels match their content-addressed saved preview; mismatched replacements are retained and diagnosed.
- Missing preview PNGs report their exact cache path. Restoring the PNG reloads an empty file-backed image, then strict graph validation verifies it. Nonempty painted images remain rejected and are never overwritten.
- Separate release checks cover all three faction shields, exact values/DDS export/bind/reimport, private-ID collisions, same-source generator isolation, repeated import/editor data counts and true own GUI PNG/reset undo/redo. Native shaders, codecs, renderer and MM export policy are unchanged.
- Candidate installation is paused; installed roots remain 2.2.6. Supplied-reference video and in-game appearance acceptance remain pending.

# 2.2.8 Saved Edit and Worker Lifecycle Preview

- Saved current-format shield drafts restore pending native controls after file load, including edits saved before live debounce or with Live Preview disabled. Generator context, staged PNG/DDS, UV transforms, scrolling and private export/bind remain coherent.
- Supported direct native Value/RGB node edits survive reopening; stale controls do not overwrite them.
- Late live-update failures restore exact documents, node outputs, local transforms and control bookkeeping. Malformed/missing native documents produce actionable errors without escaping timer/restore callbacks; corrected edits recover and stale export remains blocked.
- Failed browser process startup closes its log and removes only its own new empty directory. Owner-marker and first-request failures stop/reap the own child; failed later requests retain a reusable existing worker.
- Baseline failures were independently reproduced on isolated2.2.7. Native renderer/shader arithmetic, game codecs, export policy and game/project files remain unchanged. Supplied video/reference acceptance remains blocked by unavailable Windows materialization.

# 2.2.7 Native Shield Editor Preview

- Generator, surface, bone and offset selections resolve the same source shield. Explicit selection activates an editable body/rim mesh and material.
- Separate native pattern, energy and rim hues plus HDR brightness update the existing material through debounced controls. Native opacity remains separate; matching hues retains each branch brightness.
- Current native texture thumbnails, paths and file browsing expose pattern/normal replacement, source UV tiling/offset, energy scrolling and normal strength. No synthetic game material fields are added.
- Original-source comparison, layer solo and exposure are preview-only. Exact selected/all-source resets and Blender undo/redo retain coherent native documents.
- Returning/reopening reuses the correct per-unit/per-shield private draft, including replacement textures. Reimport/moved nonuniform parent context refresh preserves private material edits and exact source surface placement.
- Saved older drafts upgrade their controls on file load, retaining existing graph edits, native textures and unapplied control edits. Older drafts have no invented original-source comparison; open a new source draft for that snapshot.
- End-to-end CPU renders verify visible color/pattern/normal/motion changes and unique-ID export/bind rereads native values and staged DDS payloads. Renderer, native codecs, MM export policy and effect-browser reliability remain unchanged.
- Supplied Library video/screenshots could not be materialized on Windows because the current helper requires os.setxattr. No reference pixels/motion or exact game parity are claimed; the video appearance pass remains pending a readable local reference.

# 2.2.6 Browser Reliability Preview

- Completed previews retry transient Windows cache locks for a bounded interval instead of permanently losing their UI publication. Request replacement is asynchronous; the worker retries its own atomic cache writes.
- Stop cancels delayed selection autoplay. Results are checked against the current request, selection and scene before publication; a queued newer request keeps polling alive.
- First animation frame displays before the ready status. Display, malformed-payload and persistent I/O failures include retry instructions instead of silently stopping.
- Marked abandoned browser jobs can be recovered only from their own cache after both owner and worker are inactive. Live, legacy/unmarked, foreign and unexpected-content folders are preserved.
- Real Windows lock tests, warm-cache playback, rapid selection, dependency/back navigation, scene change/removal, repeated open/close and timer cancellation checks pass. Interactive acceptance remains pending; historical benchmark causes are not asserted.

# 2.2.5 Automatic Generator Shield Preview

- Unit browser and loose-model imports resolve the unit's own shield definitions and exact source assets; no manual material search.
- Native radius-one sphere multiplied by blueprint radii, named-bone pose/rest attachment, local offset, one parent/world scale and native glow dilation.
- Multiple definitions, source-local custom material/DDS references, missing-reference diagnostics, non-shield import preservation and owned refresh without duplicate imported units.
- Fitted generator/shield render scene, Y/Z conversion-aware camera, source-frame/FPS scrolling, preview-only HDR conversion, native no-shadow setting and additive glow.
- Existing editable/private-ID material workflow retains generator context and exact placement, including nonuniform/sheared parent transforms; source models/codecs remain untouched.
- Engine activation, runtime Lua radius changes, HDRP depth/refraction/water/hit behavior and exact HDR line/alpha arithmetic remain unverified.

# 2.2.4 Shield Authoring Preview

- Separate native sphere/UV scene with body and glow layers, original DDS pixels, native HDR/tiling/vector/float controls and scrolling normal/facing proxy.
- Isolated BC7/BGRA preview adapter uses Blender's bundled OpenImageIO; no codec, exporter or original texture payload changes.
- Stage your own PNG/DXT5nm normal, export collision-safe private materials/textures, bind both shield references and publish through the existing MM workflow.
- Reject unsupported graph/image/geometry/UV edits, changed project bindings, stale exported drafts and edited existing loader hooks.
- Discard and failed-import cleanup remove only generated draft data/cache. Exported project assets remain independent.
- Blender is a shader proxy; HDRP depth/refraction/water/hits and exact game shader arithmetic require in-game validation.
# 2.2.3 Browser Performance Preview

- Catalogue indexing runs outside the UI thread; warm opens reuse the current catalogue.
- Cached search/category filtering avoids decoding all row metadata on every redraw.
- Selection is debounced and automatically plays a full 256 x 256 / 30-frame animation; the newest selection cancels stale results.
- Animation rendering is performed in bounded factory Blender workers, with a 32 MiB frame-cache limit, one timer, unchanged-frame suppression and targeted redraws.
- Browser close cancels its workers/timers and removes its generated job files after the children exit. Clean projectile browsing reuses its draft instead of accumulating Text blocks; edited drafts remain preserved.
- Preview images, source shaders, native codecs and MM/export/runtime policy remain unchanged. Cold preparation is asynchronous; interactive acceptance remains required.

# 2.2.2 Creator Tools Preview

- Preview-only correction for verified same-path particle/beam extension mismatches; Lua and exports are unchanged.
- Actionable blank/partial reasons in the browser and offline gallery, including missing assets and disabled renderer helpers.
- Labeled moving-emitter demonstrations use source rateOverDistance and lifetimes; demo speed is not a game/export setting.
- Source-unaffected GIFs/posters are reused byte-identically, with exact fingerprint-based bundled cache reuse.
- Zero trail widths, shader noise/distortion and hit responses remain explicitly unsupported; no invented widths or shader parity claim.
- Native codecs, MM/export policy and runtime/installer files remain unchanged.

# 2.2.1 Creator Tools Preview

- Settings panel no longer assigns scene properties while drawing a blank unit.
- 256 x 256 / 30-frame stock previews, larger simulated browser posters and duration-aware playback.
- Native flipbook defaults, projected mesh UVs, sampled emission, curves/gradients and HDR bloom for supported source layers.
- Explicit partial/unsupported labels; shader-only noise and distortion are not invented as solid polygons. Shields remain surface swatches, not hit-response simulations.
- Renderer/cache signature invalidation and version-separated bundled gallery extraction.
- Native codecs, MM-only new-faction policy, exporters and preserving installers remain unchanged.
- Isolated preview build only: interactive/game/second-machine acceptance remains required.

# 2.2.0 Creator Tools Preview

- Complete template-based Lua generation for 12 roles; paired native host/client subclasses and component authoring.
- Searchable projectile names, faction/tier tags and real unit-consumer aliases integrated with Lua field selection.
- Offline stock GIF library, background project-wide GIF generation, inline playback and straight-line projectile previews.
- Typed/raw VFX and projectile editing, safe dependency cloning, HDR color/intensity and material texture replacement.
- Custom shield normal maps with RGB/DXT5nm and linear-data handling; opt-in shield loader support.
- Icon image/thumbnail refresh every render and camera-fit control, including perspective/lens shift.
- Generic project/faction manifests, single-commander MM registration, release versions and installer.
- Whole-project non-executing Lua/reference checker; missing classes/bones/models/projectiles/dependencies block releases.
- Content-hash art caching, faster packaging, and project exchange excludes generated releases/history/cache.
- Preserving standalone unit/art runtime and installer: trusted bootstrap only if absent; never replaces existing loaders.
- New factions are MM-only: help explains hard-coded standalone slot conflicts and MM's dynamic assignment.
- Existing import/export codecs remain byte-identical; all changes are within the existing addon.
- Preview limitations and mandatory in-game smoke tests documented; automated checks cannot prove arbitrary Lua correct.

# 2.1.0 Preview

- Workshop / Unit Browser / Project navigation, with Model, Behaviour, Icon and Export pages.
- Visible animation import and clip authoring, playback, and idle/walk/build Lua assignment.
- Grouped behaviour fields and template selection; selected editor follows the visible category.
- Simplified icon flow, existing-image picker and render/save from inside the icon scene.
- Camera defaults and reference images are collapsible; full camera presets retained.
- Removed deployment backup option and backup creation from Studio and legacy deployment.
- Effect browser with categories, source-unit filtering, texture thumbnails and Blender previews.
- Explicit unit/projectile visual slots; isolated projectile copies preserve other weapon behaviour.
- Custom effect copies include dependencies and colour/size/speed/lifetime adjustments.
- Projectile-aware project validation; existing import/export codecs retained.
- Blender 4.5 registration, panel contracts, actual effect preview, cloning and Lua binding checks.

# 2.0.8 Preview

- Visual size editor: read-only source project-unit picker or Lua file path;
  copy all four size groups, or a single group, into the current draft.
- Includes collision/selection offsets; mirrors absent optional source offsets.
- Preserves unrelated Lua and unit identity. Confirmation, Undo, numeric-source
  validation, unapplied-preview guard; no disk writes until normal Save Lua.
- Refreshes visual guides when a target mesh is pinned; also works without a mesh.
- Clear expand/collapse triangle on Visual size editor heading.
- Export codecs, camera defaults, shared project handling and deployment unchanged.

# 2.0.7 Preview

- Remember Full Camera Setup replaces the angle-only capture: world position,
  rotation/scale, projection type, lens, orthographic scale, sensor, lens shift,
  clipping, depth of field, stereo/panorama settings and camera display controls.
- Camera-view viewport zoom/pan and pixel aspect are remembered too. Icon output
  resolution stays controlled by the existing Icon resolution selector.
- Full presets disable automatic framing; new scenes retain the exact camera.
  Optional angle-only automatic framing remains available.
- Evaluated parent/constraint transforms are captured as a standalone camera;
  focus-object/bone position becomes an independent icon-scene focus target.
- Existing angle-only defaults load unchanged until a full setup is remembered.
- Shared project settings and all model/animation/texture export codecs unchanged.

# 2.0.6 Preview

- Global camera XYZ controls and Remember This Camera Angle, persisted outside
  .blend files. New icon scenes keep the preferred angle with automatic framing.
- Project selection shared across .blend files and saved automatically when a
  valid project is selected. Existing per-file selection can be remembered once.
- Old .blend project paths cannot overwrite the global selection on load.
- Cross-project Lua draft export guard; original drafts are preserved.
- Scoped atomic JSON settings with backups; no writes to mod/game files or all
  Blender preferences. Existing icon scenes, forest rotation and codecs unchanged.

# 2.0.5 Preview

- Viewport-only collision, footprint, selection, click-box and building-skirt
  guides with numeric dimensions, one-unit ruler, REST export-mesh fitting and
  explicit Apply-to-Lua. Coordinate/offset and 1.3x selection rules verified
  against the installed game loader; existing codecs unchanged.
- Stale unit/project/draft guards, no evaluation of Lua expressions, and no
  overlay objects that could accidentally render or export.
- New icon camera defaults rotated 180 degrees around world Z; forest.exr also
  rotated 180 degrees. Source scene and existing icon scenes preserved.
- Lua donor picker collapses byte-identical same-unit copies, distinguishes
  edited versions by source, and resolves mixed-case project identities.

# 2.0.4 Preview

- Render & Save Unit Icon and Save Unit Icon from PNG write project-owned PNG,
  transparent BC3 DDS, and correctly sized .sansprite files without mesh export.
- Repair Missing Icon Sprites migrates older DDS-only projects without changing
  their textures. Deploy/package validation rejects missing or mismatched pairs.
- Unit rename, import and faction asset conversion keep sprite references aligned.
- Icon scenes cannot render under another unit's ID. Existing square render sizes
  are preserved; non-square source PNGs are rejected rather than distorted.
- Three vanilla sidebar references (land, air, building) load read-only from the
  installed game's UI archive. Reference images never enter the scene or project.
- Legacy auto-deploy is no longer executed when saving Lua or exporting a unit
  into its authoring project. Only an explicit Deploy action writes live Mods.
- Existing mesh, skeleton, animation, material and DDS codecs are unchanged.

# 2.0.3 Preview

- Project-owned Lua copies on donor/new-unit creation, complete export and pack
  import; existing edited files are preserved. Explicit repair for old projects.
- Unit Browser no longer reveals live/game Lua for editing, and its refreshed
  project cards read displayName from the newly created local template.
- Searchable named-value choices throughout Studio Lua fields and tag add/edit,
  including custom entries. Effects remain unchanged and deferred.

# v1.5.6 Beta

- Imported skeletons now retain their original bone IDs while appending newly
  created deform bones to the export manifest.
- New rotate/deform bones no longer have their vertex weights discarded and
  reassigned to Root during `.sanmodel` export.
- Duplicated imported bones have stale Sanctuary name, path, and bind-index
  metadata normalized when they become new export bones.

# v1.5.5 Beta

- Texture conversion now always writes `<unit>_lod0_Albedo.dds`,
  `<unit>_lod0_mask.dds`, and `<unit>_lod0_normal.dds`.
- Every successful conversion creates or refreshes `<unit>_lod0.sanmaterial`
  beside the DDS files.
- Replacement units clone their stock loose/packed material when available,
  preserving unit-specific shader data while redirecting the three core
  texture slots to canonical project files.
- Existing project materials retain custom and unknown properties.

# v1.5.4 Beta

- Animation export now converts each source rig's object scale and rotation
  into the canonical skeleton's local basis. Duplicate animation rigs no longer
  produce oversized, mirrored, or reversed bone motion when their object
  transforms differ from the final model rig.
- Added an **AO Strength** control for AO-only mask conversion. This preserves
  the AO pattern while allowing overly dark bakes to be blended toward white.
- Added compatibility with older `smt_<name>.json` project manifests.

# v1.5.3 Beta

- Added **Show Live Mods** to the Unit Browser. It defaults off so a unit that
  exists in both the project library and deployed Mods folder is shown once.
- Added a **Create Backup** deployment toggle. It defaults on for safety, but
  may be disabled to update the live mod without duplicating its archives.
- Replaced **Add All** with **Add All Selected**. It adds directly selected
  Action datablocks, or Actions owned by selected armatures, and ignores
  unrelated animations left behind by imported reference models.

# v1.5.2 Beta

- Removed the redundant Browser & Game Location sidebar panel. The main
  browser button prompts for the Sanctuary install on first use.
- Combined skeleton export with Model Import / Export.
- Made clip names and per-clip FPS directly editable in the animation list.
- Kept the project's export Unit ID stable while importing reference units and
  routed all exports into the active mod project's unit folder.
- Unlocked faction prefix, unit family, tier, and three-digit identity fields;
  project Lua/assets are renamed together when the generated ID changes.
- Added a full Lua source view with line numbers, changed-line highlighting,
  two-click per-line reset, and two-way synchronization with visual controls.
- Live Lua files can now be staged into an active project and edited directly.
- Removed Dycom-specific defaults from new generic projects and Lua drafts.

# v1.5.1 Beta

- Reorganized the sidebar into collapsible Model, Skeleton, Animation,
  Texture, Browser, and Mod Manager sections.
- Added a persistent Lua authoring dialog with a **Use Unit ID from Project**
  option, tier + three-digit ID generation, removable tag chips, and menus of
  known tags.
- Added safe editing of project Lua while preserving unknown and custom Lua
  fields, plus per-file drafts that survive accidentally closing the dialog.
- Added labeled, exclusive **All / one choice** browser filters and project/live
  mod sources.
- Added `.sanpack` project import with optional sibling Lua and UI content.
- Changed texture-size controls to exact 1024-pixel steps.

# v1.4.9

- Preserve evaluated per-corner normals through the exporter's edge splitting
  and triangulation, including smooth shading, sharp edges and custom normals.
- Recalculate tangents only after restoring those normals.
- Read packed mask data as Non-Color in Blender previews, preventing unwanted
  sRGB decoding of metallic and AO channels. DDS output bytes are unchanged.
- No changes to skeletons, bone IDs, animation import/export, geometry positions,
  UVs, texture conversion, Lua support, browser, or extraction behavior.

# v1.4.8

- Added a question-mark help button beside every PNG input. Hover for a tooltip or click for a readable explanation.
- Explained base color/diffuse versus AO versus tangent-space normal inputs. Mask help follows the selected AO / Grayscale or Sanctuary RGBA mode.
- Added descriptions to the filename fields and clarified that Default Smoothness controls material gloss, not smooth shading of mesh faces.
- All model, skeleton, animation, DDS, unit browser, and extraction behavior remains unchanged from v1.4.7.

# v1.4.7

- Fixed imported rest orientations: set nonzero EditBone length before assigning its matrix, including the bind-pose-only importer. Added skeleton rest-matrix validation.
- Fixed imported triangle winding under the Y/Z reflection so face orientation agrees with normals.
- Restored the earlier deformation-preserving, quaternion-stable editable animation preview. Kept v1.4.6 animation-export functions unchanged and verified identical engineer output after save/reopen.
- Changed Windows file-location handling to open the real directory directly, including spaced Lua/template paths.
- Added confirmed, incremental Units.sanpack extraction for missing LOD0 browsing files, with space/deployment warnings, progress, cancellation, existing-file preservation, path validation, and automatic folder opening on completion.
- DDS conversion now follows actual unit material filenames. For the engineer this fixes `_albedo_team.dds` versus the material's `_Albedo.dds` mismatch without changing the verified encoder bytes.

# v1.4.6

- Removed 60-FPS/SLERP animation-import rebaking. Native key times, source FPS, and finite unweighted Hermite curves are coordinate-mapped into editable Actions.
- Preserved original native curve bytes for untouched imports, including original clip timing.
- Retained imported Actions on save even when they have not been selected/assigned, and verified native byte-exact export after saving/reopening.
- Fixed the baked-source export math: serialize each source's actual pose and rebuild canonical parent-local transforms, without subtracting its rest pose and applying a different rig's rest pose. This fixes the mismatched foot paths in the supplied engineer project.
- Added Baked Pose (default) and explicit Authored + Constraints source evaluation. Constraint mute states and original scene/rig state are restored after export.
- Added a folder-icon dropdown beside unit names for LOD0 files and Lua/.santp templates, with an archive fallback for packed-only units.
- Kept the verified v1.4.5 texture encoder unchanged.

# v1.4.5

- Replaced the external-Pillow DDS path with a bundled, deterministic DXT5/BC3 encoder. It reproduces the known-good reference textures byte-for-byte and works in a stock Blender install.
- Added Pillow-compatible fixed-point Lanczos scaling, including the 1024-to-2048 AO-mask path used by `ucl1501`.
- Fixed the final animation-export implementation so it actually restores each clip's frozen Source Pose before assigning and sampling the Action. The previous restoration call existed only in an older function that was later overridden.
- Kept strict DXT5 header and payload verification after every top-only conversion.

# v1.4.4

- DDS export now verifies the actual DDS header and top-level payload. Pillow builds that silently write uncompressed RGBA are rejected; the toolkit tries other available Python/Pillow runtimes until one emits real DXT5/BC3.
- Strong Albedo/AO slot swap detection: when AO-only mode sees a grayscale PNG in Albedo and a color PNG in Mask/AO, the slots are automatically corrected and the UI paths are swapped to match.
- Animation items now freeze a pose baseline when added. Unkeyed IK/control channels no longer inherit whichever armature/pose happened to be active when the blend was later saved/exported.
- Animation sampling reads the evaluated dependency-graph armature, not potentially stale original pose-bone matrices.
- Complete Unit Export uses the pinned canonical mesh + rig instead of the current selection.
- Skeleton export uses the pinned canonical rig once one is established, preventing animation-source armatures from accidentally overwriting the unit skeleton.
- Existing model/skeleton/animation outputs continue to overwrite atomically.
# v1.5.0 Beta — Mod projects and authoring

- Replaced additive browser checkboxes with exclusive **All / one choice** filters for source, faction, tech level, and unit type. Selecting Guard now shows Guard only.
- Unit indexing includes editable project folders and live deployed mods. Base, untagged/replacement, and tagged faction records use distinct keys, so the same unit ID can appear from multiple sources.
- Added editable mod projects with a manifest and `Units`, `Lua`, and `UI` trees. Projects can be exported as validated `.SMT`/ZIP packages or genuine RAR archives when WinRAR is installed.
- Added path-traversal validation for imported packages, verified archive writes, atomic sanpack replacement, and timestamped deployment backups. Base-game archives are never modified.
- Added live deploy/update for `Units.sanpack`, `Lua`, and `UI.sanpack`, plus optional automatic deployment after unit/Lua export.
- Added a donor-based unit-template creator. It generates IDs such as `UDL1100`, preserves a stock unit's detailed structure, and edits identity, faction/tier/build tags, health, economy, and weapon target layers. Authoring choices persist in the project manifest.
- New units receive a cloned, renamed unit-local `.sanmaterial`; arbitrary helper Lua stored under the project `Lua` tree is retained and deployed.
- Added custom unit-icon PNG conversion/deployment to `UI/Sprites/Icons/Units/<id>.dds`.
- Added a separate team-mask input which is combined into albedo alpha, plus editable faction-color preview nodes for imported materials.
- All model, skeleton, animation, coordinate, UV, and verified BC3 behavior from v1.4.9 is retained.
# 1.5.5 Beta

- Texture conversion now always writes `<unit>_lod0_Albedo.dds`, `<unit>_lod0_mask.dds`, and `<unit>_lod0_normal.dds`.
- Every successful conversion creates or refreshes `<unit>_lod0.sanmaterial` beside the DDS files.
- Replacement units clone their stock loose/packed material when available, preserving unit-specific shader properties while redirecting the three core texture slots to the canonical project files.
- Existing project materials retain custom/unknown properties; only the material name and `_MainTex`, `_NormalMap`, and `_MaskMap` references are updated.
