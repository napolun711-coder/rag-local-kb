#Requires -Version 5.1
<#
  Local RAG Knowledge Base - user-confirmed natural upgrade.

  Supported:
  - Git clone: fetch origin, show incoming commits, fast-forward only.
  - GitHub Releases: latest release asset + SHA256.
  - Local release archive: -ArchivePath <zip> -Checksum <sha256>.

  Safety:
  - Writes only inside this project.
  - Stops only lifecycle-owned services.
  - Backs up the SQLite memory DB, configuration, and (for release/archive mode) code.
  - Never replaces data/, knowledge/, logs/, user .env/config, model files, or
    venvs during code replacement.
  - Rollback restores code and runtime backup on failure.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$Yes,
    [switch]$Release,
    [switch]$NoRestart,
    [switch]$SkipSetup,
    [string]$ArchivePath = '',
    [string]$Checksum = '',
    [string]$ProjectRoot = ''
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ScriptRoot = $PSScriptRoot
$Root = if ($ProjectRoot) { (Resolve-Path -LiteralPath $ProjectRoot).Path } else { Split-Path -Parent $ScriptRoot }
$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$UpdateRoot = Join-Path $Root '.updates'
$Setup = Join-Path $Root 'scripts\setup.ps1'
$Start = Join-Path $Root 'start.ps1'
$Lifecycle = Join-Path $Root 'scripts\lifecycle.py'
$AppPython = Join-Path $Root '.venv\Scripts\python.exe'

# Keep this list in sync with scripts/package_release.ps1.
$CodeEntries = @(
    'app',
    'static',
    'scripts',
    'docs',
    'tests',
    '.github',
    'services\lightrag_shell',
    'services\lightrag_engine\run.ps1',
    'services\lightrag_engine\README.md',
    'services\lightrag_engine\requirements.lock',
    'services\lightrag_engine\.env.example',
    'main.py',
    'start.cmd',
    'start.ps1',
    'stop.ps1',
    'status.ps1',
    'setup.cmd',
    'deploy.cmd',
    'update.cmd',
    'RAG启动器.ps1',
    'README.md',
    'README.zh-CN.md',
    'LICENSE',
    'CHANGELOG.md',
    'CONTRIBUTING.md',
    'SECURITY.md',
    'CODE_OF_CONDUCT.md',
    '部署阶段.md',
    'pyproject.toml',
    'uv.lock',
    '.python-version',
    '.env.example',
    'config\.gitkeep',
    'config\memory-supervisor.example.json',
    'config\update.example.json'
)

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ('== ' + $Text + ' ==') -ForegroundColor Cyan
}
function Write-OK([string]$Text) { Write-Host ('[OK] ' + $Text) -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host ('[WARN] ' + $Text) -ForegroundColor Yellow }
function Write-Error2([string]$Text) { Write-Host ('[ERROR] ' + $Text) -ForegroundColor Red }

function Read-ProjectVersion([string]$Path) {
    $pyproject = Join-Path $Path 'pyproject.toml'
    if (Test-Path -LiteralPath $pyproject) {
        try {
            $text = [IO.File]::ReadAllText($pyproject)
            $match = [regex]::Match($text, '(?m)^\s*version\s*=\s*"([^"]+)"')
            if ($match.Success) { return $match.Groups[1].Value }
        } catch { }
    }
    $init = Join-Path $Path 'app\__init__.py'
    if (Test-Path -LiteralPath $init) {
        try {
            $match = [regex]::Match([IO.File]::ReadAllText($init), '__version__\s*=\s*"([^"]+)"')
            if ($match.Success) { return $match.Groups[1].Value }
        } catch { }
    }
    return '0.0.0'
}

function Get-UpdateConfig {
    $result = @{ repo = $env:RAGKB_UPDATE_REPO; branch = 'main'; asset_pattern = 'win64.zip' }
    $path = Join-Path $Root 'config\update.json'
    if (Test-Path -LiteralPath $path) {
        try {
            $payload = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($payload.repo) { $result.repo = [string]$payload.repo }
            if ($payload.branch) { $result.branch = [string]$payload.branch }
            if ($payload.asset_pattern) { $result.asset_pattern = [string]$payload.asset_pattern }
        } catch { throw ('Invalid config\update.json: ' + $_.Exception.Message) }
    }
    if ($env:RAGKB_UPDATE_BRANCH) { $result.branch = $env:RAGKB_UPDATE_BRANCH }
    if ($env:RAGKB_UPDATE_ASSET_PATTERN) { $result.asset_pattern = $env:RAGKB_UPDATE_ASSET_PATTERN }
    return $result
}

function Invoke-Git([string[]]$Arguments, [switch]$Quiet) {
    # Native git writes progress to stderr. Under ErrorActionPreference=Stop,
    # PowerShell 5.1 can turn that into a terminating error, so capture it
    # explicitly and use $LASTEXITCODE for the real result.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = @(& git -C $Root @Arguments 2>&1 | ForEach-Object { [string]$_ })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return @{ Code = $code; Output = $output }
}

function Copy-Tree([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($item in @(Get-ChildItem -LiteralPath $Source -Force)) {
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }
}

function Copy-Entry([string]$Relative, [string]$DestinationRoot) {
    $source = Join-Path $Root $Relative
    if (-not (Test-Path -LiteralPath $source)) { return }
    $destination = Join-Path $DestinationRoot $Relative
    $parent = Split-Path -Parent $destination
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    if (Test-Path -LiteralPath $source -PathType Container) {
        if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
        Copy-Tree $source $destination
    } else {
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

function Backup-Runtime([string]$BackupDir) {
    $target = Join-Path $BackupDir 'runtime'
    foreach ($relative in @(
        'data\memory', 'data\index-compatibility.json', '.env',
        'config\app.env', 'config\engine.env', 'services\lightrag_engine\.env'
    )) {
        Copy-Entry $relative $target
    }
}

function Restore-Runtime([string]$BackupDir) {
    $source = Join-Path $BackupDir 'runtime'
    if (-not (Test-Path -LiteralPath $source)) { return }
    foreach ($relative in @(
        'data\memory', 'data\index-compatibility.json', '.env',
        'config\app.env', 'config\engine.env', 'services\lightrag_engine\.env'
    )) {
        $from = Join-Path $source $relative
        if (-not (Test-Path -LiteralPath $from)) { continue }
        $to = Join-Path $Root $relative
        $parent = Split-Path -Parent $to
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        if (Test-Path -LiteralPath $from -PathType Container) {
            if (Test-Path -LiteralPath $to) { Remove-Item -LiteralPath $to -Recurse -Force }
            Copy-Tree $from $to
        } else {
            Copy-Item -LiteralPath $from -Destination $to -Force
        }
    }
}

function Backup-Code([string]$BackupDir) {
    $target = Join-Path $BackupDir 'code'
    $present = New-Object System.Collections.Generic.List[string]
    foreach ($relative in $CodeEntries) {
        if (Test-Path -LiteralPath (Join-Path $Root $relative)) {
            Copy-Entry $relative $target
            $present.Add($relative)
        }
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $BackupDir 'code-entries.json'), ($present | ConvertTo-Json), $encoding)
}

function Restore-Code([string]$BackupDir) {
    $target = Join-Path $BackupDir 'code'
    if (-not (Test-Path -LiteralPath $target)) { return }
    foreach ($relative in $CodeEntries) {
        $current = Join-Path $Root $relative
        if (Test-Path -LiteralPath $current) { Remove-Item -LiteralPath $current -Recurse -Force }
        $saved = Join-Path $target $relative
        if (Test-Path -LiteralPath $saved) {
            $parent = Split-Path -Parent $current
            if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            if (Test-Path -LiteralPath $saved -PathType Container) { Copy-Tree $saved $current }
            else { Copy-Item -LiteralPath $saved -Destination $current -Force }
        }
    }
}

function Apply-Code([string]$SourceRoot) {
    foreach ($relative in $CodeEntries) {
        $current = Join-Path $Root $relative
        if (Test-Path -LiteralPath $current) { Remove-Item -LiteralPath $current -Recurse -Force }
    }
    foreach ($relative in $CodeEntries) {
        $from = Join-Path $SourceRoot $relative
        if (-not (Test-Path -LiteralPath $from)) { continue }
        $to = Join-Path $Root $relative
        $parent = Split-Path -Parent $to
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        if (Test-Path -LiteralPath $from -PathType Container) { Copy-Tree $from $to }
        else { Copy-Item -LiteralPath $from -Destination $to -Force }
    }
}

function Stop-Services {
    if (-not (Test-Path -LiteralPath $AppPython) -or -not (Test-Path -LiteralPath $Lifecycle)) {
        Write-Warn2 'Lifecycle environment not found; assuming no project services to stop.'
        return
    }
    & $AppPython -B $Lifecycle stop | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Lifecycle stop failed' }
}

function Run-Setup {
    if (-not (Test-Path -LiteralPath $Setup)) { throw 'Setup script is missing after update' }
    & $PowerShell -NoProfile -ExecutionPolicy Bypass -File $Setup -ConfigureLocal | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed after update' }
}

function Start-And-Check {
    if (-not (Test-Path -LiteralPath $Start)) { throw 'Start script is missing after update' }
    & $PowerShell -NoProfile -ExecutionPolicy Bypass -File $Start --no-browser | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Service startup failed after update' }
    $portFile = Join-Path $Root 'logs\app.port'
    $port = 18080
    if (Test-Path -LiteralPath $portFile) {
        try { $port = [int]([IO.File]::ReadAllText($portFile).Trim()) } catch { $port = 18080 }
    }
    $health = $null
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $health = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $port + '/health') -TimeoutSec 5
            if ($health -and [string]$health.status -eq 'ok') { return }
        } catch { }
        Start-Sleep -Seconds 1
    }
    throw 'Health check did not pass after update'
}

function Get-Checksum([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Resolve-Checksum([string]$Archive, [string]$Provided) {
    if ($Provided) { return $Provided.Trim().ToLowerInvariant() }
    foreach ($candidate in @($Archive + '.sha256', [IO.Path]::ChangeExtension($Archive, '.sha256'))) {
        if (Test-Path -LiteralPath $candidate) {
            $text = ([IO.File]::ReadAllText($candidate).Trim() -split '\s+')[0]
            if ($text) { return $text.ToLowerInvariant() }
        }
    }
    return ''
}

function Get-ReleaseArchive([string]$Repo, [string]$Pattern, [string]$Destination) {
    $headers = @{ 'User-Agent' = 'local-rag-kb-updater' }
    $release = Invoke-RestMethod -Uri ('https://api.github.com/repos/' + $Repo + '/releases/latest') -Headers $headers -TimeoutSec 30
    $tag = [string]$release.tag_name
    $assets = @($release.assets)
    $asset = @($assets | Where-Object { $_.name -match [regex]::Escape($Pattern) } | Select-Object -First 1)
    if (-not $asset.Count) {
        $asset = @($assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1)
    }
    if (-not $asset.Count) { throw 'No matching release ZIP asset found' }
    $checksumAsset = @($assets | Where-Object { $_.name -eq ($asset[0].name + '.sha256') } | Select-Object -First 1)
    if (-not $checksumAsset.Count) {
        $checksumAsset = @($assets | Where-Object { $_.name -match 'sha256' } | Select-Object -First 1)
    }
    if (-not $checksumAsset.Count) { throw 'No SHA256 asset found for release ZIP' }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $zip = Join-Path $Destination $asset[0].name
    $sumFile = Join-Path $Destination $checksumAsset[0].name
    Invoke-WebRequest -Uri $asset[0].browser_download_url -OutFile $zip -UseBasicParsing -TimeoutSec 300
    Invoke-WebRequest -Uri $checksumAsset[0].browser_download_url -OutFile $sumFile -UseBasicParsing -TimeoutSec 60
    $expected = (([IO.File]::ReadAllText($sumFile).Trim() -split '\s+')[0]).ToLowerInvariant()
    $actual = Get-Checksum $zip
    if ($expected -ne $actual) { throw 'Release ZIP SHA256 mismatch' }
    return @{ Tag = $tag; Zip = $zip; Version = ($tag -replace '^[vV]', '') }
}

function Assert-SafeArchive([string]$Zip) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        foreach ($entry in $archive.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            if ([IO.Path]::IsPathRooted($name) -or $name -match '(^|/)\.\.(/|$)') {
                throw 'Release archive contains an unsafe path'
            }
        }
    } finally {
        $archive.Dispose()
    }
}

function Expand-Release([string]$Zip, [string]$Destination) {
    Assert-SafeArchive $Zip
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Expand-Archive -LiteralPath $Zip -DestinationPath $Destination -Force
    if (Test-Path -LiteralPath (Join-Path $Destination 'pyproject.toml')) { return $Destination }
    $candidate = @(Get-ChildItem -LiteralPath $Destination -Directory | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName 'pyproject.toml')
    } | Select-Object -First 1)
    if (-not $candidate.Count) { throw 'Release archive does not contain pyproject.toml' }
    return $candidate[0].FullName
}

$mutexCreated = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\RagKbUpdater', [ref]$mutexCreated)
if (-not $mutexCreated) {
    Write-Warn2 'Another updater is already running.'
    $mutex.Dispose()
    exit 2
}

$backupDir = ''
$previousHead = ''
$gitMode = $false
$codeApplied = $false
try {
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor DarkGray
    Write-Host ' Local RAG Knowledge Base - Update' -ForegroundColor White
    Write-Host (' Project: ' + $Root) -ForegroundColor DarkGray
    Write-Host ' Code-only upgrade; user data is backed up and preserved.' -ForegroundColor DarkGray
    Write-Host '============================================================' -ForegroundColor DarkGray

    $config = Get-UpdateConfig
    $currentVersion = Read-ProjectVersion $Root
    $hasGit = Test-Path -LiteralPath (Join-Path $Root '.git')
    $mode = if ($ArchivePath) { 'archive' } elseif ($Release -or -not $hasGit) { 'release' } else { 'git' }
    $gitMode = $mode -eq 'git'
    Write-Host ('Current version: ' + $currentVersion + '  Mode: ' + $mode) -ForegroundColor DarkGray

    $targetVersion = ''
    $remoteRef = ''
    $incoming = @()
    $archiveSource = ''
    $releaseInfo = $null

    if ($mode -eq 'git') {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git is not installed' }
        $status = Invoke-Git @('status', '--porcelain', '--untracked-files=no')
        if ($status.Code -ne 0) { throw 'Unable to read git status' }
        if ($status.Output.Count -gt 0) {
            Write-Warn2 'Tracked local code changes exist. Update will refuse to overwrite them.'
            if (-not $CheckOnly) { throw 'Commit or stash tracked changes before updating' }
        }
        $remotes = Invoke-Git @('remote')
        if ($remotes.Code -ne 0 -or -not (@($remotes.Output) -contains 'origin')) {
            Write-Error2 'No git origin is configured for this checkout.'
            Write-Host '  Add the GitHub remote, or use -Release with config\update.json.' -ForegroundColor Yellow
            exit 2
        }
        $branch = $config.branch
        $fetch = Invoke-Git @('fetch', '--prune', 'origin')
        if ($fetch.Code -ne 0) { throw 'git fetch failed; check network and the origin remote' }
        $remoteRef = 'origin/' + $branch
        $remote = Invoke-Git @('rev-parse', '--verify', $remoteRef)
        if ($remote.Code -ne 0) { throw ('Remote branch not found: ' + $remoteRef) }
        $local = Invoke-Git @('rev-parse', 'HEAD')
        $previousHead = [string]$local.Output[0]
        if ([string]$remote.Output[0] -eq $previousHead) {
            Write-OK 'Already up to date.'
            exit 0
        }
        $incoming = @((Invoke-Git @('log', '--oneline', '--no-decorate', ('HEAD..' + $remoteRef))).Output)
        $targetVersion = 'git ' + ([string]$remote.Output[0]).Substring(0, 8)
        Write-Host ('Incoming commits: ' + $incoming.Count) -ForegroundColor Cyan
        $incoming | Select-Object -First 12 | ForEach-Object { Write-Host ('  ' + $_) -ForegroundColor DarkGray }
    } else {
        if ($ArchivePath) {
            $archive = (Resolve-Path -LiteralPath $ArchivePath).Path
            $expected = Resolve-Checksum $archive $Checksum
            if (-not $expected) {
                Write-Warn2 'No SHA256 provided or found next to the local archive.'
                if (-not $CheckOnly) { throw 'Provide -Checksum <sha256> for local archive updates' }
            } elseif ((Get-Checksum $archive) -ne $expected) {
                throw 'Local archive SHA256 mismatch'
            }
            $releaseInfo = @{ Tag = 'local'; Zip = $archive; Version = '' }
            $targetVersion = 'local archive'
            Write-Host ('Local archive: ' + $archive) -ForegroundColor DarkGray
        } else {
            if (-not $config.repo -or $config.repo -match '^OWNER/') {
                Write-Error2 'No update repository configured.'
                Write-Host '  Git clone: update uses origin automatically.' -ForegroundColor Yellow
                Write-Host '  Release ZIP: copy config\update.example.json to config\update.json and set repo.' -ForegroundColor Yellow
                Write-Host '  Offline: update.cmd -ArchivePath <zip> -Checksum <sha256>' -ForegroundColor Yellow
                exit 2
            }
            $headers = @{ 'User-Agent' = 'local-rag-kb-updater' }
            $latest = Invoke-RestMethod -Uri ('https://api.github.com/repos/' + $config.repo + '/releases/latest') -Headers $headers -TimeoutSec 30
            $targetVersion = [string]$latest.tag_name
            $versionText = $targetVersion -replace '^[vV]', ''
            Write-Host ('Latest release: ' + $targetVersion + '  Current: ' + $currentVersion) -ForegroundColor Cyan
            $assets = @($latest.assets | ForEach-Object { $_.name })
            Write-Host ('Assets: ' + (($assets | Select-Object -First 8) -join ', ')) -ForegroundColor DarkGray
            try {
                $currentParsed = [version]$currentVersion
                $latestParsed = [version]$versionText
                if ($latestParsed -le $currentParsed) {
                    Write-OK 'Already up to date.'
                    exit 0
                }
            } catch { }
        }
        if ($CheckOnly) {
            Write-Host 'Check-only: no download, backup, or code change.' -ForegroundColor Cyan
            exit 0
        }
    }

    if ($CheckOnly) {
        Write-Host 'Check-only: no download, backup, or code change.' -ForegroundColor Cyan
        exit 0
    }

    if (-not $Yes) {
        Write-Host ''
        Write-Host 'The updater will:' -ForegroundColor Cyan
        Write-Host '  1. stop project-owned services'
        Write-Host '  2. back up the SQLite memory DB and configuration inside .updates\backups'
        Write-Host '  3. replace code only (data/, knowledge/, logs/, .env, config/, models and venvs preserved)'
        Write-Host '  4. sync dependencies, start services, health-check, and roll back on failure'
        $answer = Read-Host 'Type UPGRADE to continue'
        if ($answer -ne 'UPGRADE') { Write-Host 'Cancelled.'; exit 0 }
    }

    if ($mode -eq 'release' -and -not $ArchivePath) {
        $downloadDir = Join-Path $UpdateRoot ('download-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
        $releaseInfo = Get-ReleaseArchive $config.repo $config.asset_pattern $downloadDir
        $targetVersion = $releaseInfo.Tag
        Write-OK ('Downloaded and verified: ' + $releaseInfo.Zip)
    }
    if ($mode -ne 'git') {
        $extractDir = Join-Path $UpdateRoot ('extract-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        $archiveSource = Expand-Release $releaseInfo.Zip $extractDir
        $newVersion = Read-ProjectVersion $archiveSource
        if ($newVersion -eq '0.0.0') { throw 'Release archive has no readable version' }
        Write-Host ('Release version: ' + $newVersion) -ForegroundColor Cyan
    }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupDir = Join-Path $UpdateRoot ('backups\' + $stamp)
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

    Write-Step 'Stopping project-owned services'
    Stop-Services
    Write-OK 'Services stopped or were not running'

    Write-Step 'Backing up runtime data and configuration'
    Backup-Runtime $backupDir
    Write-OK ('Runtime backup: ' + $backupDir)
    if ($mode -ne 'git') {
        Backup-Code $backupDir
        Write-OK 'Code backup prepared'
    }

    Write-Step 'Applying update'
    $codeApplied = $true
    if ($mode -eq 'git') {
        $pull = Invoke-Git @('pull', '--ff-only', 'origin', $config.branch)
        $pull.Output | ForEach-Object { Write-Host $_ }
        if ($pull.Code -ne 0) { throw 'git pull --ff-only failed' }
        Write-OK 'Code updated from git'
    } else {
        Apply-Code $archiveSource
        Write-OK 'Code replaced from release archive'
    }

    if (-not $SkipSetup) {
        Write-Step 'Syncing dependencies'
        Run-Setup
        Write-OK 'Dependencies synced'
    }

    if (-not $NoRestart) {
        Write-Step 'Starting and health-checking services'
        Start-And-Check
        Write-OK 'Services healthy after update'
    }

    $newVersion = Read-ProjectVersion $Root
    Write-Host ''
    Write-OK ('Update complete. Version: ' + $newVersion)
    Write-Host ('Backup retained at: ' + $backupDir) -ForegroundColor DarkGray
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-Error2 $message
    if ($backupDir -and -not $codeApplied -and -not $NoRestart) {
        try { Start-And-Check; Write-OK 'Services restarted after the failed update.' }
        catch { Write-Warn2 'Could not restart services after the failed update.' }
    }
    if ($backupDir -and $codeApplied) {
        Write-Step 'Rolling back code and runtime backup'
        try {
            if ($gitMode -and $previousHead) {
                Invoke-Git @('reset', '--hard', $previousHead) | Out-Null
            } else {
                Restore-Code $backupDir
            }
            Restore-Runtime $backupDir
            Write-OK 'Previous code and runtime data restored.'
            if (-not $SkipSetup) {
                try { Run-Setup; Write-OK 'Dependencies restored.' }
                catch { Write-Warn2 ('Code/data restored, but dependency sync failed: ' + $_.Exception.Message) }
            }
            if (-not $NoRestart) {
                try { Start-And-Check; Write-OK 'Previous services are healthy.' }
                catch { Write-Warn2 ('Code/data restored, but service restart failed: ' + $_.Exception.Message) }
            }
        } catch {
            Write-Error2 ('Rollback of code/runtime failed: ' + $_.Exception.Message)
        }
    }
    exit 1
} finally {
    try { $mutex.ReleaseMutex() } catch { }
    $mutex.Dispose()
}
