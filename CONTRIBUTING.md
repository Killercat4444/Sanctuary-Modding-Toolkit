# Build and contribute

`extension/` is the Blender extension source root. It contains the manifest,
Python modules, configuration data, and source for the optional runtime helper.
The legacy v1.4.9 ZIP stays at the repository root.

Use Blender 4.5 on Windows. From the repository root:

```powershell
blender --command extension validate .\extension
blender --command extension build --source-dir .\extension
```

If `blender` is not on PATH, invoke the Blender executable by its full path.
Install the resulting ZIP from Blender’s Add-ons preferences. Configure a
local Sanctuary engine directory before importing or exporting game assets.

The source build does not include the pre-rendered stock preview library
or the compiled standalone runtime helper. Preview workflows that support
local generation can use installed assets. Workflows that deploy the standalone
helper require its compiled DLL from a matching build. Its C# source and the
installer helper are in [extension/runtime/](extension/runtime/).

When testing export changes, keep the test game and project separate from
your everyday install. Verify new packages before sharing, and record the
Blender, game, and Mod Manager versions you tested.
