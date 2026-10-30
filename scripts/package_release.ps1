#Requires -Version 5.1
<#
  Build a code-only Windows release ZIP and SHA256 checksum inside .updates.
  The package excludes data, knowledge, logs, .env, config/update.json, venvs,
  .tools, and model storage. It is suitable for GitHub Releases.
#>
[CmdletBinding()]
param(
    [string]$Version = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

# Keep this list in sync with scripts/update.ps1.
$CodeEntries = @(
    'app', 'static', 'scripts', 'docs', 'tests', '.github',
    'services\lightrag_shell',
    'services\lightrag_engine\run.ps1',
    'services\lightrag_engine\README.md',
    'services\lightrag_engine\requirements.lock',
    'services\lightrag_engine\.env.example',
    'main.py', 'start.cmd', 'start.ps1', 'stop.ps1', 'status.ps1',
    'setup.cmd', 'deploy.cmd', 'update.cmd', 'RAG启动器.ps1',
    'README.md', 'LICENSE', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md',
    'CODE_OF_CONDUCT.md', '部署阶段.md',
    'pyproject.toml', 'uv.lock', '.python-version', '.env.example',
    'config\.gitkeep', 'config\memory-supervisor.example.json',
    'config\update.example.json'
)

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ('== ' + $Text + ' ==') -ForegroundColor Cyan
}
function Write-OK([string]$Text) { Write-Host ('[OK] ' + $Text) -ForegroundColor Green }

if (-not $Version) {
    $text = [IO.File]::ReadAllText((Join-Path $Root 'pyproject.toml'))
    $match = [regex]::Match($text, '(?m)^\s*version\s*=\s*"([^"]+)"')
    if (-not $match.Success) { throw 'Cannot read project version from pyproject.toml' }
    $Version = $match.Groups[1].Value
}

$releaseDir = Join-Path $Root '.updates\release'
$stage = Join-Path $releaseDir ('stage-' + $Version + '-' + $PID)
$zip = Join-Path $releaseDir ('local-rag-kb-' + $Version + '-win64.zip')
$sum = $zip + '.sha256'

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
if (Test-Path -LiteralPath $sum) { Remove-Item -LiteralPath $sum -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

Write-Step ('Packaging version ' + $Version)
foreach ($relative in $CodeEntries) {
    $source = Join-Path $Root $relative
    if (-not (Test-Path -LiteralPath $source)) { continue }
    $destination = Join-Path $stage $relative
    $parent = Split-Path -Parent $destination
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    if (Test-Path -LiteralPath $source -PathType Container) {
        Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
    } else {
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}
# Do not package runtime data, user config, caches, or virtual environments.
foreach ($excluded in @('data', 'knowledge', 'logs', '.venv', '.tools', '.updates', '.integration-run')) {
    $path = Join-Path $stage $excluded
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
$cacheNames = @('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.idea', '.vscode')
Get-ChildItem -LiteralPath $stage -Recurse -Force | Where-Object {
    $_.PSIsContainer -and ($cacheNames -contains $_.Name)
} | Sort-Object FullName -Descending | ForEach-Object {
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
}
Get-ChildItem -LiteralPath $stage -Recurse -Force -File | Where-Object {
    $_.Extension -in @('.pyc', '.pyo')
} | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
$configDir = Join-Path $stage 'config'
if (Test-Path -LiteralPath $configDir) {
    Get-ChildItem -LiteralPath $configDir -File | Where-Object {
        $_.Name -notlike '*.example.json' -and $_.Name -ne '.gitkeep'
    } | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
}

Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal -Force
$hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
$encoding = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($sum, ($hash + '  ' + (Split-Path -Leaf $zip) + "`n"), $encoding)
Remove-Item -LiteralPath $stage -Recurse -Force

Write-OK ('Release ZIP: ' + $zip)
Write-OK ('SHA256: ' + $hash)
Write-OK ('Checksum file: ' + $sum)
