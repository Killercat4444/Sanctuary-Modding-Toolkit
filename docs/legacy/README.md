# Sanctuary Modding Toolkit v1.4.9

This release preserves evaluated smooth/custom corner normals while preparing
export meshes. It also reads packed mask data as Non-Color in Blender previews.
Disable the older toolkit version before enabling this one. Re-export the mesh
to update existing sanmodel normals; old exports do not repair themselves.
Reimport a model to rebuild its preview material. No animation rebake, skeleton
edit, texture conversion, or mesh cleanup is required for these two fixes.

Texture-input help update built on the working v1.4.7 release. Model, skeleton, animation, texture conversion, unit locations, and archive extraction are unchanged.

## Texture input help

Each PNG field now has a **?** button: hover for a tooltip or click to read the explanation. The filename fields also have hover descriptions.

- **Albedo PNG:** base-color/diffuse texture containing the unit's colors, not AO.
- **Mask / AO PNG:** grayscale ambient occlusion in AO / Grayscale mode, or an already packed R=metallic, G=AO, B=emission, A=smoothness PNG in Sanctuary RGBA mode. Not diffuse.
- **Normal PNG:** tangent-space RGB normal texture, usually purple/blue. Not a height/bump map.

All textures must match the model's UVs. The mask help button follows the selected Mask Input mode. Default Smoothness changes material gloss in AO mode, not the smooth shading of mesh faces.

No reimport or re-export is required just to use this help update. The following v1.4.7 workflow notes remain applicable.

## Install and retest

Disable older Sanctuary Toolkit versions, install this ZIP, and restart Blender. Keep a project backup. **Reimport the model, skeleton, and animations together** for previously broken imported test units: loading only a new Action onto an old incorrectly oriented rig does not repair its rest bones. Do not remove or rebake your original authoring/source rigs.

Keep **Source Evaluation: Baked Pose** for your existing baked animation export entries. Their per-source pose export, fixed baseline, and canonical hierarchy behavior are unchanged. The first animation frame does not need to equal the skeleton rest pose, and clips do not need identical keyframes.

## Blender import and preview

New edit bones receive a nonzero length before their source matrix is assigned. Previously, setting the matrix on a zero-length bone lost the intended orientation; a bone-position-only animation test did not catch the resulting mesh deformation. Both skeleton-backed and bind-pose-only imports now use the proper rest orientations. Triangle winding is also corrected during the Y/Z reflection so faces agree with the imported normals.

As requested, default imported animation preview is restored to the earlier deformation-preserving, quaternion-stable editable Action path. This creates preview keys on the existing 60-Hz-or-source-rate timeline using linked quaternion interpolation, rather than v1.4.6's raw scalar quaternion curve preview. This import-side behavior is separate from exporting your already-baked source Actions; those are not rewritten or rebaked. Imported clips are retained on save even if never selected.

## Unit location menu

The folder icon beside a unit name has **Unit Files (LOD0)** and **Lua / Unit Template** entries. Lua/template access opens its actual containing directory, avoiding the unreliable Windows Explorer /select argument with spaced paths. The shipped templates use .santp files under engine/LJ/lua/common/units/unitsTemplates/<unit>.

If the loose LOD0 files exist, the unit location opens immediately. Otherwise a confirmation dialog offers to extract Units.sanpack to the Units folder beside the archive. It displays required space and explains that these are browsing/reference copies, not a deployed mod: the game still uses its packed/mod assets, so editing the browsing copies alone is not the mod deployment step.

Extraction is incremental with progress and Esc cancellation. The source archive is not modified; existing files are never overwritten. Completed files are retained on cancellation and skipped on a subsequent extraction. Partial temporary files are removed. Unsafe archive paths, links/junctions, duplicates, and encrypted entries are rejected. The requested LOD0 folder opens after success. The prompt does not recur once that unit's required loose files exist. If the filesystem does not support the exclusive hard-link publication used for safe extraction, extraction reports an error instead of overwriting files.

## Textures

The byte-exact BC3/DXT5 encoder is unchanged. The latest three engineer DDS outputs matched the known-good reference bytes, but the albedo filename did not match its game material: the addon wrote ucl1501_lod0_albedo_team.dds while _MainTex references ucl1501_lod0_Albedo.dds.

Conversion now reads the unit's .sanmaterial from the export directory first, otherwise from the configured game's Units.sanpack, and writes the exact _MainTex, _MaskMap, and _NormalMap filenames. New units without a material default to _Albedo.dds, _mask.dds, and _normal.dds. A shared/external texture reference is rejected rather than silently writing an unused local filename. Keep the game folder configured, or place a custom material beside your exported files when overriding the stock material.

For the supplied engineer sources: color PNG in **Albedo PNG**, grayscale AO bake in **Mask / AO PNG**, normal PNG in **Normal PNG**, AO/Grayscale mask mode, smoothness 118/255, No Team Color, 2048, Top Mip Only. The screenshot's slot paths already follow this layout. Copy the new material-matching filenames into the actual mod/pack deployment, not just the browsing folder. This release does not delete old _albedo_team files or change game materials automatically.

## Validation

- Blender 4.5.3: rest matrices agree with source matrices within approximately 0.0000012.
- Commander and engineer deformed meshes match the earlier working v1.4.5 preview within approximately 0.000002 at every preview frame tested; Rest and Walk were also rendered for inspection.
- The v1.4.6 animation-export functions are unchanged. The engineer produces the same 602,568 bytes after save/reopen with each original source rig and the canonical rig selected.
- Texture bytes still match all three known-good references, now under material-matching names.
- Location paths, confirmation-before-extraction, incremental extraction, skip-existing, cancellation cleanup, and unsafe-path rejection were tested in workspace fixtures. The installed archive was only read for validation; it was not re-extracted or changed during testing. Actual Explorer window navigation and in-game rendering still require the user's final test.
