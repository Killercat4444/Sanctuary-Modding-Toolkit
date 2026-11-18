param([string]$EngineDirectory, [string]$ModsDirectory, [string]$PackageDirectory=$PSScriptRoot, [switch]$NonInteractive)
# Preserving first-install helper. No downloader, deletion, assembly rewriting or loader replacement.
$ErrorActionPreference='Stop'
function Sha([string]$Path) {
    $stream=[IO.File]::OpenRead($Path);$sha=[Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose();$sha.Dispose() }
}
function Resolved([string]$Path) { [IO.Path]::GetFullPath($Path).TrimEnd([char[]]'\/') }
function Under([string]$Path,[string]$Root) {
    (Resolved $Path).StartsWith((Resolved $Root)+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)
}
function NoLinks([string]$Path) {
    $cursor=Resolved $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item=Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Linked install path is not supported: $cursor" }
        }
        $parent=[IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }; $cursor=$parent
    }
}
try {
    Write-Host 'Sanctuary Studio - advanced standalone unit/art install. MM is recommended; new factions are MM-only.'
    if (-not $EngineDirectory) {
        if ($NonInteractive) { throw 'EngineDirectory is required.' }
        $EngineDirectory=Read-Host 'Sanctuary engine folder (containing Sanctuary.exe)'
    }
    $engine=Resolved ($EngineDirectory.Trim('"'))
    if (-not (Test-Path -LiteralPath (Join-Path $engine 'Sanctuary.exe') -PathType Leaf)) { throw 'Invalid Sanctuary engine folder.' }
    if (-not $ModsDirectory) {
        $defaultMods=Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'My Games\Enhearten Media\Sanctuary\Mods'
        if ($NonInteractive) { throw 'ModsDirectory is required.' }
        $choice=Read-Host "Mods folder [Enter for $defaultMods]"
        $ModsDirectory=if ($choice) { $choice.Trim('"') } else { $defaultMods }
    }
    $mods=Resolved $ModsDirectory; $package=Resolved $PackageDirectory
    if ($engine -eq $mods -or (Under $engine $mods) -or (Under $mods $engine)) { throw 'Engine and Mods must be separate folders.' }
    NoLinks $engine; NoLinks $mods; NoLinks $package
    foreach ($process in @(Get-Process -Name Sanctuary -ErrorAction SilentlyContinue)) {
        if (-not $process.Path -or (Under $process.Path $engine)) { throw 'Close Sanctuary fully before installing.' }
    }
    $descriptors=@(Get-ChildItem -LiteralPath (Join-Path $package 'engine\StudioStandalone') -Filter runtime.json -Recurse -File)
    if ($descriptors.Count -ne 1) { throw 'Expected exactly one Studio package descriptor.' }
    $descriptor=Get-Content -LiteralPath $descriptors[0].FullName -Raw | ConvertFrom-Json
    if ($descriptor.format -ne 'Sanctuary Studio standalone 1' -or $descriptor.id -notmatch '^[a-z0-9][a-z0-9._-]{0,63}$') { throw 'Invalid package descriptor.' }
    if (@($descriptor.factions).Count -gt 0) { throw 'New factions require MM dynamic registration; standalone faction slots are not supported.' }
    $assembly=Join-Path $engine 'Sanctuary_Data\Managed\Trebuchet.dll'
    if (-not (Test-Path -LiteralPath $assembly -PathType Leaf) -or (Sha $assembly) -ne $descriptor.gameAssemblySha256) { throw 'Different game build: re-export for this game version or use MM.' }
    $required=@('BepInEx/core/BepInEx.dll','BepInEx/core/BepInEx.Preloader.dll','BepInEx/core/Mono.Cecil.dll','BepInEx/core/0Harmony.dll','winhttp.dll','doorstop_config.ini')
    $present=(Test-Path -LiteralPath (Join-Path $engine 'BepInEx'))
    foreach ($relative in $required) { if (Test-Path -LiteralPath (Join-Path $engine $relative)) { $present=$true } }
    if ($present) {
        foreach ($relative in $required) {
            $path=Join-Path $engine $relative
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Existing BepInEx is incomplete. Repair with its original installer; not replacing: $relative" }
            if ($relative.EndsWith('.dll')) {
                $bytes=[IO.File]::ReadAllBytes($path)
                if ($bytes.Length -lt 2 -or $bytes[0] -ne 77 -or $bytes[1] -ne 90) { throw "Existing BepInEx DLL is invalid; not replacing: $relative" }
            }
        }
    }
    $runtimeRelative='BepInEx/patchers/SanctuaryStudio/SanctuaryStudioStandalone.dll'
    $runtimeSource=Join-Path $package ('engine/'+$runtimeRelative)
    foreach ($folder in @('patchers','plugins')) {
        $base=Join-Path $engine ('BepInEx/'+$folder)
        if (-not (Test-Path -LiteralPath $base -PathType Container)) { continue }
        foreach ($dll in @(Get-ChildItem -LiteralPath $base -Filter *.dll -Recurse -File)) {
            NoLinks $dll.FullName
            $metadata=[Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($dll.FullName))
            if ($dll.Name -ieq 'Sanctuary.ModApi.dll') { throw 'MM already exists: use the MM release, not this standalone package.' }
            if ($metadata.Contains('SanctuaryStudioStandalone')) {
                if ((Sha $dll.FullName) -ne (Sha $runtimeSource)) { throw "Different existing Studio Lua catcher; not replacing: $($dll.FullName)" }
            } elseif (($metadata.Contains('pathToFileContents') -or $metadata.Contains('CreateFileCache') -or $metadata.Contains('TryGetFileContent')) -and ($metadata.Contains('Lua') -or $metadata.Contains('Cache') -or $metadata.Contains('Overlay'))) {
                throw "Existing Lua/cache integration needs explicit compatibility verification; not replacing: $($dll.FullName)"
            }
        }
    }
    $payload=@{}
    foreach ($mapping in @(@('engine',$engine),@('Mods',$mods))) {
        $sourceRoot=Join-Path $package $mapping[0]
        if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) { continue }
        foreach ($source in @(Get-ChildItem -LiteralPath $sourceRoot -Recurse -File)) {
            NoLinks $source.FullName
            $relative=$source.FullName.Substring((Resolved $sourceRoot).Length+1)
            $target=Resolved (Join-Path $mapping[1] $relative)
            if (-not (Under $target $mapping[1])) { throw "Install target escapes root: $target" }
            if ($payload.ContainsKey($target)) { throw "Duplicate install target: $target" }
            $payload[$target]=$source.FullName
        }
    }
    # Bootstrap is shared infrastructure, not project-owned; never update or retire it.
    $shared=@{}
    if (-not $present) {
        $boot=Join-Path $package 'bootstrap'
        foreach ($relative in $required) { if (-not (Test-Path -LiteralPath (Join-Path $boot $relative) -PathType Leaf)) { throw "Missing bootstrap: $relative" } }
        foreach ($source in @(Get-ChildItem -LiteralPath $boot -Recurse -File)) {
            NoLinks $source.FullName
            $relative=$source.FullName.Substring((Resolved $boot).Length+1).Replace('\','/')
            if ($relative -notmatch '^BepInEx/core/[^/]+\.(dll|xml)$' -and $relative -notin @('winhttp.dll','doorstop_config.ini','.doorstop_version')) { throw "Unapproved bootstrap file: $relative" }
            $target=Resolved (Join-Path $engine $relative);$payload[$target]=$source.FullName;$shared[$target]=$true
        }
    }
    $ledger=Join-Path $engine ('StudioStandalone/'+$descriptor.id+'/installed-files.json')
    if (Test-Path -LiteralPath $ledger) { throw 'Project already installed. Use Studio direct install for hash-checked updates; this first-install helper will not overwrite its ownership record.' }
    # Validate the COMPLETE plan before any write.
    foreach ($target in $payload.Keys) {
        NoLinks $target
        if (Test-Path -LiteralPath $target) {
            if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or (Sha $target) -ne (Sha $payload[$target])) { throw "Conflicting existing file; nothing installed: $target" }
        }
    }
    if (-not $NonInteractive) {
        Write-Host "Ready to add $($payload.Count) files to $engine and $mods. Existing infrastructure is preserved."
        if ((Read-Host 'Type INSTALL to continue') -cne 'INSTALL') { throw 'Cancelled; nothing installed.' }
    }
    $owned=@{}
    foreach ($target in $payload.Keys) {
        if (-not (Test-Path -LiteralPath $target)) {
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
            # Exclusive destination protects files created between validation and publication.
            $input=[IO.File]::OpenRead($payload[$target])
            try {
                $output=[IO.File]::Open($target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                try { $input.CopyTo($output) } finally { $output.Dispose() }
            } finally { $input.Dispose() }
        }
        if (-not $shared.ContainsKey($target)) { $owned[$target]=Sha $target }
    }
    $ledgerJson=$owned | ConvertTo-Json
    [IO.File]::WriteAllText($ledger,$ledgerJson,(New-Object Text.UTF8Encoding($false)))
    Write-Host 'Installed. Restart Sanctuary, then test a new small match. All players need matching content.'
    exit 0
} catch { Write-Error -ErrorAction Continue $_; Write-Host 'Installation stopped. Existing loaders/files were not replaced. Prefer MM if compatibility is unclear.'; exit 1 }
