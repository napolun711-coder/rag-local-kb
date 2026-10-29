#Requires -Version 5.1
<#
  Local RAG Knowledge Base - one-click install / repair.
  Idempotent: safe to run again. Only writes project-local venvs and .env files.
  Does not touch business data.
#>
[CmdletBinding()]
param(
    [switch]$SkipEngine,
    [switch]$SkipApp,
    [switch]$CheckOnly,
    [switch]$ConfigureLocal
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $root '.tools'
# Keep every uv download, Python install, cache, and tool artifact inside the
# project. This setup script must not write to system or user profile paths.
$env:UV_CACHE_DIR = Join-Path $Tools 'uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools 'python'
$env:UV_TOOL_DIR = Join-Path $Tools 'uv-tools'
$env:UV_TOOL_BIN_DIR = Join-Path $Tools 'bin'

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
    $tools = Join-Path $Tools 'uv'
    $downloads = Join-Path $Tools 'downloads'
    New-Item -ItemType Directory -Path $tools -Force | Out-Null
    New-Item -ItemType Directory -Path $downloads -Force | Out-Null
    $zip = Join-Path $downloads $name
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
    # Deliberately project-local: never install uv system-wide via winget.
    return (Install-UvStandalone)
}

function Read-EnvLines([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    try { return @([IO.File]::ReadAllLines($Path)) } catch { return @() }
}

function Get-EnvValue([string]$Path, [string]$Name) {
    foreach ($raw in (Read-EnvLines $Path)) {
        $line = ([string]$raw).Trim()
        if (-not $line -or $line.StartsWith('#') -or $line.IndexOf('=') -lt 1) { continue }
        $pair = $line.Split('=', 2)
        if ($pair[0].Trim() -eq $Name) {
            return $pair[1].Trim().Trim('"').Trim("'")
        }
    }
    return ''
}

function Set-EnvValues([string]$Path, [hashtable]$Values) {
    $lines = @(Read-EnvLines $Path)
    $remaining = @{}
    foreach ($key in $Values.Keys) { $remaining[$key] = $true }
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $lines) {
        $line = [string]$raw
        $trimmed = $line.Trim()
        $matched = $null
        if ($trimmed -and -not $trimmed.StartsWith('#') -and $trimmed.IndexOf('=') -ge 1) {
            $matched = $trimmed.Split('=', 2)[0].Trim()
        }
        if ($matched -and $remaining.ContainsKey($matched)) {
            $result.Add($matched + '=' + [string]$Values[$matched])
            $remaining.Remove($matched)
        } else {
            $result.Add($line)
        }
    }
    foreach ($key in $remaining.Keys) {
        $result.Add($key + '=' + [string]$Values[$key])
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllLines($Path, $result.ToArray(), $encoding)
}

function Get-OllamaModels {
    try {
        $response = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
        return @($response.models | ForEach-Object { [string]$_.name } | Where-Object { $_ })
    } catch {
        return @()
    }
}

function Test-EngineNeedsLocalConfig([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    $hostValue = Get-EnvValue $Path 'LLM_BINDING_HOST'
    $model = Get-EnvValue $Path 'LLM_MODEL'
    return (-not $hostValue) -or ($hostValue -match 'your-openai-compatible-host') -or (-not $model)
}

function Configure-LocalOllama([string]$Path) {
    $models = @(Get-OllamaModels)
    if (-not $models.Count) {
        Write-Warn2 'Ollama is not reachable on http://127.0.0.1:11434.'
        Write-Host '  Install Ollama, then run: ollama pull bge-m3; ollama pull qwen2.5:3b' -ForegroundColor Yellow
        Write-Host '  Re-run deploy.cmd -ConfigureLocal after the models exist.' -ForegroundColor DarkGray
        return $false
    }
    $embedding = @($models | Where-Object { $_ -match '^bge-m3' } | Select-Object -First 1)
    if (-not $embedding.Count) {
        Write-Warn2 'Embedding model bge-m3 is not installed.'
        Write-Host '  Run: ollama pull bge-m3' -ForegroundColor Yellow
        return $false
    }
    $preferred = @(
        'qwen2.5:3b', 'qwen2.5:7b', 'qwen2.5', 'llama3.1:8b', 'llama3.1',
        'qwen2.5-coder:7b', 'mistral:7b', 'gemma3:4b'
    )
    $chat = ''
    foreach ($candidate in $preferred) {
        if ($models -contains $candidate) { $chat = $candidate; break }
    }
    if (-not $chat) {
        $chat = @($models | Where-Object { $_ -notmatch '(?i)bge|rerank|embed' } | Select-Object -First 1)
    }
    if (-not $chat) {
        Write-Warn2 'No local chat model was found.'
        Write-Host '  Run: ollama pull qwen2.5:3b' -ForegroundColor Yellow
        return $false
    }
    Set-EnvValues $Path @{
        'LLM_BINDING' = 'openai'
        'LLM_BINDING_HOST' = 'http://127.0.0.1:11434/v1'
        'LLM_BINDING_API_KEY' = 'ollama'
        'LLM_MODEL' = $chat
        'EMBEDDING_BINDING' = 'ollama'
        'EMBEDDING_BINDING_HOST' = 'http://127.0.0.1:11434'
        'EMBEDDING_MODEL' = [string]$embedding[0]
        'EMBEDDING_DIM' = '1024'
    }
    Write-OK ('Configured local Ollama: ' + $chat + ' + ' + $embedding[0])
    return $true
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkGray
Write-Host ' Local RAG Knowledge Base - Setup' -ForegroundColor White
Write-Host (' Project: ' + $root) -ForegroundColor DarkGray
Write-Host '============================================================' -ForegroundColor DarkGray

if ($CheckOnly) {
    Write-Step 'Environment check (no writes)'
    $uvFound = Find-Uv
    if ($uvFound) { Write-OK ('uv: ' + $uvFound) }
    else { Write-Warn2 'uv is not available; normal setup would download it into .tools\uv.' }

    $appPython = Join-Path $root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $appPython) {
        & cmd /c "`"$appPython`" -c `"import app.main`" >nul 2>&1"
        if ($LASTEXITCODE -eq 0) { Write-OK 'App .venv and app.main import' }
        else { Write-Warn2 'App .venv exists but app.main import failed.' }
    } else { Write-Warn2 'App .venv is missing; normal setup would create it.' }

    $enginePython = Join-Path $root 'services\lightrag_engine\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $enginePython) {
        & cmd /c "`"$enginePython`" -c `"import lightrag`" >nul 2>&1"
        if ($LASTEXITCODE -eq 0) { Write-OK 'Engine .venv and lightrag import' }
        else { Write-Warn2 'Engine .venv exists but lightrag import failed.' }
    } else { Write-Warn2 'Engine .venv is missing; normal setup would create it.' }

    $appEnv = Join-Path $root '.env'
    $engineEnv = Join-Path $root 'services\lightrag_engine\.env'
    if (Test-Path -LiteralPath $appEnv) { Write-OK 'Root .env exists' }
    else { Write-Warn2 'Root .env is missing; normal setup would create it from .env.example.' }
    if (Test-Path -LiteralPath $engineEnv) {
        if (Test-EngineNeedsLocalConfig $engineEnv) {
            Write-Warn2 'Engine .env still needs a model endpoint/model (placeholder or empty).'
        } else { Write-OK 'Engine .env has a configured model endpoint and model.' }
    } else { Write-Warn2 'Engine .env is missing; normal setup would create it.' }

    $models = @(Get-OllamaModels)
    if ($models.Count) {
        Write-OK ('Ollama models: ' + (($models | Select-Object -First 8) -join ', '))
        $embedding = @($models | Where-Object { $_ -match '^bge-m3' } | Select-Object -First 1)
        if (-not $embedding.Count) { Write-Warn2 'Embedding model bge-m3 is missing.' }
        if ($ConfigureLocal) {
            $chat = @($models | Where-Object { $_ -notmatch '(?i)bge|rerank|embed' } | Select-Object -First 1)
            if ($chat.Count) {
                Write-Host ('  Would configure local engine: ' + $chat[0] + ' + ' + $(if ($embedding.Count) { $embedding[0] } else { 'bge-m3 (missing)' })) -ForegroundColor Cyan
            }
        }
    } else {
        Write-Warn2 'Ollama is not reachable; local model configuration cannot be auto-filled.'
    }
    Write-Host 'No files were written by this check.' -ForegroundColor DarkGray
    exit 0
}

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
$engineCreated = $false
if (-not (Test-Path -LiteralPath $engineEnv)) {
    if (Test-Path -LiteralPath $engineExample) {
        Copy-Item -LiteralPath $engineExample -Destination $engineEnv
        $engineCreated = $true
        Write-OK 'Created engine .env from example'
    } else { Write-Warn2 'No engine .env.example found; create it manually' }
} else { Write-OK 'Engine .env already exists, left untouched' }

if ($ConfigureLocal -and (Test-Path -LiteralPath $engineEnv) -and
    ($engineCreated -or (Test-EngineNeedsLocalConfig $engineEnv))) {
    Write-Step 'Configuring local Ollama endpoint'
    if (-not (Configure-LocalOllama $engineEnv)) {
        Write-Warn2 'Local model configuration was not written. Edit services\lightrag_engine\.env manually.'
    }
} elseif (Test-EngineNeedsLocalConfig $engineEnv) {
    Write-Warn2 'Engine model endpoint/model is still a placeholder.'
    Write-Host '  Run deploy.cmd -ConfigureLocal to fill it from local Ollama.' -ForegroundColor Yellow
}

Write-Step '4/4 Done'
Write-Host ''
Write-Host 'Next: run start.ps1 to launch services.' -ForegroundColor Cyan
Write-Host 'Run scripts\doctor.ps1 to verify environment.' -ForegroundColor DarkGray
Write-Host 'Run setup.cmd again anytime for idempotent repair.' -ForegroundColor DarkGray
exit 0

