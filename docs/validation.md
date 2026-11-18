# 2.2.10 validation and release checklist

This is an automated-tested creator-tools preview. It is not yet certified by an end-to-end interactive game test on another creator's machine.

## Automated coverage

- Own factory GUI reproduced the installed 2.2.6 ImagePreview draw-context exception. Six actual full staged panel draws pass across cold saved-image loading, real save, external image changes, reload, unit switching and distinct projects.
- Actual existing-PNG save and repeated CPU render/save retain fresh thumbnails and matching DDS pixels after the final native write. Repeated reselect, same-timestamp Reload, deliberate chooser reuse, stale source rejection without writes, actual .blend reopen and two registration cycles pass.
- Forty saved portraits enforce the 32-entry cache bound; unused module-created images are removed while user-retained images survive. Corrupt PNG errors and missing/restored images recover without draw exceptions.
- The prior shield fixes are preserved byte-for-byte. Affected unit/workshop/model/animation/icon/portability and shield save/load/export/undo/generator regressions are repeated. Export functions outside the icon save function remain structurally identical; icon encoding and native exporter modules remain byte-identical.

- Reproduced stale-preview DDS export on isolated2.2.8. Changed/missing staging now blocks actual export without partial project writes; explicit selected-slot PNG replacement retains other native values and emits exact staged DDS bytes.
- Pre229 staged receipt migration checks decoded native pixels against the saved content-addressed preview before adoption; a mismatch is rejected. Actual save/reopen preserves byte receipts; nonempty painted images are still rejected.
- All EDA/Chosen/Guard pairs retain exact native values and DDS through actual private export/bind; preview exposure/solo/comparison do not alter game data. Duplicate IDs preserve every project byte.
- Actual PNG replacement and reset undo/redo, saved drafts after undo, moved original PNG, missing/restored staged DDS and missing/restored generated preview PNG are covered. Missing preview data remains blocked until the exact file is restored.
- Two same-source generators keep independent graphs/images/meshes/edits; five repeat import/editor openings retain edits with stable generated data counts; unique private bind/reimport affects only the selected generator.
- Own factory GUI checks include PNG staging and reset undo/redo through actual operators, plus real timer/worker/panel behavior. No input is injected into user windows.

- Independently reproduced2.2.7 saved-current-draft pending-edit loss, unclosed startup log/abandoned empty directory, late-update document partial commit and missing-document callback failure.
- Real imported-generator .blend save/load with pending manual/live color, scroll and UV changes; preserved staged PNG, exact private material/DDS export/bind and supported direct node edits.
- Fault-injected late update restores exact native documents, node values, local transforms and control bookkeeping; malformed/removed documents are preserved or diagnosed, the queue stops cleanly and corrected updates recover.
- Actual own worker processes tested through owner-marker/first-request failures; failed startup closes logs/own directory, later request failure retains its worker and all final workers/timers/job directories clean up.
- The separate hidden factory GUI test records actual timer scheduling, worker handoff, animation progress, Stop and real panel draw in its receipt. It injects no user input and does not certify user-driven acceptance or game parity.

- Native editor checks generator/body/anchor/offset selection, source-owned context, body/rim activation, actual texture previews and legacy-anchor ownership fallback.
- Debounced controls change existing nodes without rebuilding materials/images. Separate pattern/energy/rim hue and brightness, comparison/solo, PNG pattern/normal replacement and native UV/scroll controls produce visible changes in genuine CPU renders.
- Unique-ID export/bind rereads exact colors/vectors and staged native DDS. Draft reuse, different/multiple generators/shields and nonuniform-placement refresh preserve private edits and exact native world vertices.
- Reset restores exact original decimal values. Actual factory Blender undo and redo pass, stale file pickers preserve existing data, manual Apply works with Live Preview disabled, panels draw without native writes, and unregister removes owned handlers/timers.
- A real saved 2.2.6 .blend loads under 2.2.7 with upgraded native texture/color controls and its unapplied color edit retained. Missing source comparison snapshots are never fabricated; unregister removes the owned file-load handler.
- No supplied-reference pixels or video motion were inspected: Library transfer is blocked by Windows os.setxattr support. These renders verify editor controls, not game/video appearance parity. The shader arithmetic and browser/renderer code are unchanged.

- Real Windows sharing locks on completed result reads, frame reads, request replacement and worker frame replacement; bounded retry/failure and cancellation.
- First-frame publication, warm Play, 300 rapid selections, generation superseding during frame read, dependency/back navigation, scene changes/removal, malformed payloads and image-upload errors.
- Three close/reopen cycles and conservative abandoned-cache recovery with live/legacy/foreign/unexpected-content preservation.
- The timer pump honors registration, delays, callback return and cancellation. It does not claim an actual interactive-window event loop. Earlier missing-animation benchmark causes remain unproven; independently reproduced I/O and delayed-Stop defects are fixed.

- Actual unit-browser and loose companion imports, source-custom IDs/body/glow/DDS, multiple native definitions and diagnostic preservation.
- Radius25 = diameter50 from the actual source sphere; bone-local offset, parent translation/rotation/nonuniform scale and posed attachment matrix compare directly with the native loader formula.
- Contextual editable material scene retains world-space shield vertices under sheared parent transforms, clones private referenced DDS from a different source root, and binds export to the original project's scene.
- Repeat source/placement/options import preserves model/rig identity and generated-data counts; ordinary non-shield imports add no shield camera/render scene. Missing bones/refs/invalid radii are nonfatal and never replaced by a preset.
- Native XYZ and converted XYZ coordinates and camera-up direction checked; imported skeleton disabled uses the actual serialized rest-bone center and labels animation uncertainty.
- Actual generator/sphere CPU renders at native size inspect source patterns and the surrounding shape. Native additive edge blend and ShadowCastingMode.Off are reflected; brightness/base-alpha remain explicit Blender proxies, with no game-parity claim.

- All three native faction shield body/glow pairs import on the actual Sphere_hipol geometry and UV layer; unchanged native document values remain exact.
- BC7/BGRA DDS adapter pixels compare exactly with an independent existing QA decoder. Original texture bytes and native codecs are preserved; malformed formats, arrays, volumes, cubemaps and truncated BC7 inputs stop decoding.
- Native float/HDR color/vector/tiling controls, bound node values and owned PNG/DXT5nm staging are validated. Added graph nodes, painted preview images, geometry/UV edits and constraints stop export.
- Private material/texture byte round trips, both-shell manifest reimport, duplicate-ID preservation, source-project changes, stale export binding and existing custom loader protection are checked.
- Repeated import/export/discard and injected failed-import cleanup preserve other drafts, return to the source scene and remove owned generated data/cache.
- Actual CPU renders check native UV patterns, source scrolling, separate body/glow layers and HDR color response. The glow-only render uses an explicitly recorded preview exposure. These renders do not certify Unity/HDRP game parity.
- Actual MM ZIP validates private material/DDS references, source-unit material/glowMaterial fields, native loader append and archive CRCs. Existing MM-only custom-faction policy and runtime/exporter files are unchanged.

- Instrumented 2,197-entry cold/warm index, category/search redraw and repeated effect/projectile selection benchmarks; UI submission and background completion measured separately.
- Selection autoplay at 256 x 256 / 30 frames, main-thread rendering rejection, latest-only rapid selection and same-length metadata-cache invalidation.
- Repeated browser close/reopen cancels jobs/timers and removes generated job directories; clean projectile drafts are reused, edited drafts remain retained.
- Worker frame cache is limited to 32 MiB; stock preview bundle and renderer/signature files remain byte-identical to 2.2.2. Interactive popup/category acceptance is still required.

- Exact same-path typed beam resolution, rejection of unrelated paths/types, missing-reference and disabled-renderer diagnostics.
- Three formerly blank distance-emitting effects show bounded motion; zero demo speed suppresses distance-only births, and source files remain unchanged.
- Preview speed changes fingerprint/cache identity and is separate from saved unit/projectile settings.
- Incremental bundle receipt identifies re-rendered entries and byte-identical reused images; every final GIF/poster is decoded again.

- Blender 4.5.3 registration/unregistration and new panel/property contracts.
- All 295 stock unit documents retain unedited source exactly; beam Lua parses/serializes/compiles without changing its meaning.
- Twelve complete unit archetypes, paired host/client classes, component generation, generic non-Dycom faction/commander metadata and versioned MM release.
- Friendly projectile names and consumer aliases, including ped311's T3 EDA Tank/Battleship/Submarine consumers.
- Custom effect dependencies, private projectile isolation, typed edits, inline animation, shield normal-map replacement and unchanged stock files.
- Complete 2,197-entry stock GIF library: validate every 256 x 256 GIF's 30 frames before packaging; no project-specific content or personal absolute paths in the library. See the external build QA receipt for final results and supported/partial/swatch counts.
- Blank/loaded Settings draw tested with a read-only scene proxy; this prevents draw-time ID writes. The screenshot's hidden exception text has not been independently recovered.
- Sampled curves, gradient modes, flipbook defaults, alpha blending, source preservation and representative effect/projectile rendering. Automated pixel checks are not proof of near-game fidelity.
- New orthographic/perspective icon fitting, lens shift, repeat render/thumbnail refresh, PNG/DDS/sprite ownership, and model/skeleton/authored-animation round trips.
- Content-cache invalidation even for same-size files with preserved timestamps; archive CRC checks and source-only project exchange.
- Missing behavior classes, bones, identity and asset references block release; static checks never execute author Lua.
- Isolated standalone cache injection, append placement, directory discovery, repeated-hook safety and patching a COPY of the current game assembly.
- Isolated direct install preserves existing infrastructure/unrelated content and rejects a changed catcher.
- Standalone release installer tested for clean first install, repeated-install refusal, incomplete BepInEx and conflicting destination files.
- Native import/export codec files are byte-identical to the installed 2.1.9 baseline.

## Required before broad public release

1. Install the addon on a clean Blender 4.5 profile; configure a second machine's own paths.
2. Create a project with a new name/tag/prefix/mod ID; author and save a commander, engineer, factory and combat unit.
3. Export MM ZIP and enable it in current Remmy MM. Confirm faction list/icon, commander spawn/selection/movement, build menus, AI and multiplayer.
4. Exercise effect/projectile changes and the supplied shield normal map in-game; approximated GIFs cannot verify Unity shader behavior.
5. Test production, upgrades, assist/cancel and save/reload; test another map and realistic unit counts.
6. Separately test a unit-only standalone mod in a clean game/BepInEx setup. Verify startup cache activation and every unit/template path. New factions must use MM.
7. Save the exact addon/release ZIP and first error log for any failure. Test authored Lua again after game/MM updates.

Static validation cannot certify arbitrary engine API calls, computed nil values, dynamic imports, shader graphs or simulation state transitions. The addon must report these limits rather than promise to prevent every frozen-map error.
