#Requires -Version 5.1
<#
  Local RAG Knowledge Base - one-click install / repair.
  Idempotent: safe to run again. Only writes project-local venvs and .env files.
  Does not touch business data.
#>
[CmdletBinding()]
param(
    [switch]$SkipEngine,
    [switch]$SkipApp
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent $PSScriptRoot

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ('== ' + $Text + ' ==') -ForegroundColor Cyan
}
function Write-OK([string]$Text) { Write-Host ('[OK] ' + $Text) -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host ('[WARN] ' + $Text) -ForegroundColor Yellow }
function Refresh-Path {
    $parts = @(
        (Join-Path $env:USERPROFILE '.local\bin'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama')
    )
    foreach ($part in $parts) {
        if ((Test-Path -LiteralPath $part) -and ($env:Path -notlike ('*' + $part + '*'))) {
            $env:Path = $part + ';' + $env:Path
        }
    }
}
function Find-Uv {
    Refresh-Path
    $command = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($command -and (Test-Path -LiteralPath $command.Source)) { return $command.Source }
    $candidates = @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\uv\uv.exe'),
        (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $wingetPackages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $wingetPackages) {
        $found = Get-ChildItem -LiteralPath $wingetPackages -Directory -Filter 'astral-sh.uv*' -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Filter 'uv.exe' -ErrorAction SilentlyContinue } |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return ''
}
function Install-UvStandalone {
    $archRaw = (([string]$env:PROCESSOR_ARCHITECTURE) + ' ' + ([string]$env:PROCESSOR_ARCHITEW6432)).ToUpperInvariant()
    $target = if ($archRaw -match 'ARM64') { 'aarch64-pc-windows-msvc' } else { 'x86_64-pc-windows-msvc' }
    $name = 'uv-' + $target + '.zip'
    $base = 'https://github.com/astral-sh/uv/releases/download/0.11.29/'
    $tools = Join-Path $root '.tools\uv'
    New-Item -ItemType Directory -Path $tools -Force | Out-Null
    $zip = Join-Path $env:TEMP ('ragkb-' + $name)
    $sumFile = $zip + '.sha256'
    try {
        Write-Host ('Downloading uv: ' + ($base + $name))
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) {
            & $curl.Source -L --fail --retry 3 --retry-delay 2 --connect-timeout 20 --max-time 300 --progress-bar -o $zip ($base + $name)
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $zip)) { throw ('Download failed: ' + $base + $name) }
        } else {
            Invoke-WebRequest -Uri ($base + $name) -OutFile $zip -UseBasicParsing -TimeoutSec 300
        }
        try {
            if ($curl) {
                & $curl.Source -sL --fail --max-time 60 -o $sumFile ($base + $name + '.sha256')
            } else {
                Invoke-WebRequest -Uri ($base + $name + '.sha256') -OutFile $sumFile -UseBasicParsing -TimeoutSec 60
            }
            if (Test-Path -LiteralPath $sumFile) {
                $raw = [IO.File]::ReadAllText($sumFile).Trim()
                $expected = (($raw -split '\s+')[0]).ToLowerInvariant()
                $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($expected -ne $actual) { throw 'uv package SHA256 mismatch' }
                Write-OK 'uv package SHA256 verified'
            }
        } catch {
            if ($_.Exception.Message -like '*SHA256 mismatch*') { throw }
            Write-Warn2 'Could not download checksum; using official HTTPS release'
        }
        Expand-Archive -LiteralPath $zip -DestinationPath $tools -Force
        $uvExe = Get-ChildItem -LiteralPath $tools -Recurse -File -Filter 'uv.exe' -ErrorAction Stop | Select-Object -First 1
        if (-not $uvExe) { throw 'uv.exe not found in archive' }
        Unblock-File -LiteralPath $uvExe.FullName -ErrorAction SilentlyContinue
        $env:Path = $uvExe.DirectoryName + ';' + $env:Path
        return $uvExe.FullName
    } finally {
        Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $sumFile -Force -ErrorAction SilentlyContinue
    }
}
function Resolve-Uv {
    $found = Find-Uv
    if ($found) { return $found }
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host 'uv not found, installing via winget...'
        try {
            & winget install --id astral-sh.uv --exact --silent --accept-package-agreements --accept-source-agreements | Out-Host
            Refresh-Path
            $found = Find-Uv
            if ($found) { return $found }
        } catch {
            Write-Warn2 ('winget install failed: ' + $_.Exception.Message)
        }
    }
    return (Install-UvStandalone)
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkGray
Write-Host ' Local RAG Knowledge Base - Setup' -ForegroundColor White
Write-Host (' Project: ' + $root) -ForegroundColor DarkGray
Write-Host '============================================================' -ForegroundColor DarkGray

if (-not $SkipApp) {
    Write-Step '1/4 App dependencies (uv sync)'
    $uvExe = Resolve-Uv
    if (-not $uvExe) { throw 'Cannot prepare uv. Check network/proxy.' }
    Write-OK ('uv: ' + $uvExe)
    Push-Location $root
    try {
        & $uvExe sync --frozen --no-dev
        if ($LASTEXITCODE -ne 0) { throw 'uv sync failed' }
    } finally { Pop-Location }
    $appPython = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $appPython)) { throw '.venv not created' }
    Push-Location $root
    try {
        & cmd /c "`"$appPython`" -c `"import app.main`" >nul 2>&1"
        if ($LASTEXITCODE -ne 0) { throw 'app.main import failed - source incomplete?' }
    } finally { Pop-Location }
    Write-OK 'App dependencies and source import verified'
}

if (-not $SkipEngine) {
    Write-Step '2/4 LightRAG engine venv'
    $engineDir = Join-Path $root 'services\lightrag_engine'
    $engineVenv = Join-Path $engineDir '.venv'
    $enginePython = Join-Path $engineVenv 'Scripts\python.exe'
    $lockFile = Join-Path $engineDir 'requirements.lock'
    if (-not (Test-Path -LiteralPath $lockFile)) { throw ('Missing ' + $lockFile) }
    $uvExe = Resolve-Uv
    if (-not (Test-Path -LiteralPath $enginePython)) {
        Push-Location $engineDir
        try {
            & $uvExe venv --python 3.12 --clear $engineVenv
            if ($LASTEXITCODE -ne 0) { throw 'engine venv creation failed' }
        } finally { Pop-Location }
    }
    Write-OK ('engine venv: ' + $engineVenv)
    Write-Host 'Installing engine dependencies from requirements.lock (this may take several minutes)...'
    Push-Location $engineDir
    try {
        & $uvExe pip install --python $enginePython -r $lockFile
        if ($LASTEXITCODE -ne 0) { throw 'engine pip install failed' }
    } finally { Pop-Location }
    Write-OK 'LightRAG engine dependencies installed'
}

Write-Step '3/4 Environment files (.env)'
$appEnv = Join-Path $root '.env'
$appExample = Join-Path $root '.env.example'
if (-not (Test-Path -LiteralPath $appEnv)) {
    if (Test-Path -LiteralPath $appExample) {
        Copy-Item -LiteralPath $appExample -Destination $appEnv
        Write-OK 'Created root .env from example'
    } else { Write-Warn2 'No .env.example found; create .env manually' }
} else { Write-OK 'Root .env already exists, left untouched' }

$engineEnv = Join-Path $root 'services\lightrag_engine\.env'
$engineExample = Join-Path $root 'services\lightrag_engine\.env.example'
if (-not (Test-Path -LiteralPath $engineEnv)) {
    if (Test-Path -LiteralPath $engineExample) {
        Copy-Item -LiteralPath $engineExample -Destination $engineEnv
        Write-OK 'Created engine .env from example'
    } else { Write-Warn2 'No engine .env.example found; create it manually' }
} else { Write-OK 'Engine .env already exists, left untouched' }
Write-Warn2 'Remember to fill API keys in .env files (never commit them).'

Write-Step '4/4 Done'
Write-Host ''
Write-Host 'Next: run start.ps1 to launch services.' -ForegroundColor Cyan
Write-Host 'Run scripts\doctor.ps1 to verify environment.' -ForegroundColor DarkGray
Write-Host 'Run setup.cmd again anytime for idempotent repair.' -ForegroundColor DarkGray
exit 0

