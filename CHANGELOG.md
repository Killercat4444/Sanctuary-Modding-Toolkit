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
