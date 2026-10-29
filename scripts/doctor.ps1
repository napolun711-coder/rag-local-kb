#Requires -Version 5.1
<#
  Local RAG Knowledge Base - environment doctor.
  Read-only checks. Exit codes: 0=all pass, 2=warnings, 3=failures.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$FailCount = 0
$WarnCount = 0

function Write-Head([string]$Text) {
    Write-Host ''
    Write-Host ('  ' + $Text) -ForegroundColor Cyan
    Write-Host ('  ' + ('-' * 58)) -ForegroundColor DarkGray
}
function Check([string]$Level, [string]$Name, [string]$Detail = '', [string]$Hint = '') {
    if ($Level -eq 'PASS') { Write-Host '  [PASS] ' -NoNewline -ForegroundColor Green }
    elseif ($Level -eq 'WARN') { Write-Host '  [WARN] ' -NoNewline -ForegroundColor Yellow; $script:WarnCount++ }
    else { Write-Host '  [FAIL] ' -NoNewline -ForegroundColor Red; $script:FailCount++ }
    Write-Host $Name
    if ($Detail) { Write-Host ('         ' + $Detail) -ForegroundColor DarkGray }
    if ($Hint -and $Level -ne 'PASS') { Write-Host ('         Hint: ' + $Hint) -ForegroundColor Yellow }
}
function Get-EnvValue([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    try {
        foreach ($raw in [IO.File]::ReadAllLines($Path)) {
            $line = ([string]$raw).Trim()
            if (-not $line -or $line.StartsWith('#') -or $line.IndexOf('=') -lt 1) { continue }
            $pair = $line.Split('=', 2)
            if ($pair[0].Trim() -eq $Name) {
                return $pair[1].Trim().Trim('"').Trim("'")
            }
        }
    } catch { }
    return ''
}

function Test-Tcp([string]$HostName, [int]$Port, [int]$TimeoutMs = 1500) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch { return $false } finally { $client.Close() }
}

Write-Host ''
Write-Host '  Local RAG Knowledge Base - Environment Doctor' -ForegroundColor White
Write-Host ('  Time: ' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) -ForegroundColor DarkGray
Write-Host ('  Project: ' + $root) -ForegroundColor DarkGray

# 1. App venv & imports
Write-Head '1. App environment'
$appPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $appPython) {
    $probe = & cmd /c "`"$appPython`" -c `"import app.main`" 2>&1"
    if ($LASTEXITCODE -eq 0) { Check PASS 'App .venv and app.main import' } else { Check FAIL 'App import failed' ($probe -join ' ') 'run setup.cmd' }
    $versionText = & $appPython -c "import sys; print('%d.%d' % (sys.version_info[0], sys.version_info[1]))" 2>$null
    try {
        $pyVersion = [version](([string]$versionText).Trim())
        if ($pyVersion -lt [version]'3.12') {
            Check FAIL 'App Python version is too old' ([string]$versionText) 'run deploy.cmd to recreate .venv'
        } else {
            Check PASS ('App Python version ' + [string]$versionText)
        }
    } catch {
        Check WARN 'Could not determine app Python version' ([string]$versionText) 'run deploy.cmd'
    }
} else {
    Check FAIL 'App .venv missing' $appPython 'run deploy.cmd'
}

# 2. Engine venv & LightRAG
Write-Head '2. LightRAG engine'
$engineDir = Join-Path $root 'services\lightrag_engine'
$enginePython = Join-Path $engineDir '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $enginePython) {
    $probe = & cmd /c "`"$enginePython`" -c `"import lightrag`" 2>&1"
    if ($LASTEXITCODE -eq 0) { Check PASS 'Engine .venv and lightrag import' } else { Check FAIL 'Engine lightrag import failed' ($probe -join ' ') 'run setup.cmd' }
} else {
    Check FAIL 'Engine .venv missing' $enginePython 'run setup.cmd'
}

# 3. .env files
Write-Head '3. Environment files'
if (Test-Path -LiteralPath (Join-Path $root '.env')) { Check PASS 'Root .env exists' } else { Check FAIL 'Root .env missing' '' 'run deploy.cmd' }
$engineEnvPath = Join-Path $engineDir '.env'
if (Test-Path -LiteralPath $engineEnvPath) { Check PASS 'Engine .env exists' } else { Check FAIL 'Engine .env missing' '' 'run deploy.cmd' }
$llmHost = Get-EnvValue $engineEnvPath 'LLM_BINDING_HOST'
$llmModel = Get-EnvValue $engineEnvPath 'LLM_MODEL'
$embeddingHost = Get-EnvValue $engineEnvPath 'EMBEDDING_BINDING_HOST'
$embeddingModel = Get-EnvValue $engineEnvPath 'EMBEDDING_MODEL'
if (-not $llmHost -or $llmHost -match 'your-openai-compatible-host' -or -not $llmModel) {
    Check WARN 'Engine generation endpoint/model is incomplete' '' 'run deploy.cmd -ConfigureLocal, or edit services\lightrag_engine\.env'
}

# 4. Local models
Write-Head '4. Local models (Ollama)'
$models = @()
try {
    $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
    $models = @($tags.models | ForEach-Object { [string]$_.name } | Where-Object { $_ })
} catch { $models = @() }
if ($models.Count) {
    Check PASS ('Ollama reachable, models: ' + (($models | Select-Object -First 6) -join ', '))
    if ($embeddingHost -match '11434') {
        if ($embeddingModel -and ($models -contains $embeddingModel -or ($models | Where-Object { $_ -eq $embeddingModel -or $_ -like ($embeddingModel + '*') }))) {
            Check PASS ('Embedding model present: ' + $embeddingModel)
        } else {
            Check WARN ('Embedding model missing: ' + $embeddingModel) '' ('ollama pull ' + ($embeddingModel -replace ':latest$',''))
        }
    }
    if ($llmHost -match '11434') {
        if ($llmModel -and ($models -contains $llmModel)) {
            Check PASS ('Local chat model present: ' + $llmModel)
        } else {
            Check WARN ('Local chat model missing: ' + $llmModel) '' 'edit services\lightrag_engine\.env or ollama pull a model'
        }
    }
} else {
    Check WARN 'Ollama is not reachable at 127.0.0.1:11434' '' 'install/start Ollama, or configure an external model endpoint'
}

# 5. Data directories
Write-Head '5. Data directories'
foreach ($name in @('data','knowledge','logs')) {
    $path = Join-Path $root $name
    if (Test-Path -LiteralPath $path) { Check PASS ($name + ' directory exists') }
    else { Check WARN ($name + ' directory missing') $path 'deploy.cmd/start will create it' }
}

# 6. Services running?
Write-Head '6. Service status'
$stateFile = Join-Path $root 'logs\runtime\services.json'
if (Test-Path -LiteralPath $stateFile) {
    try {
        $state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $services = if ($state.services) { $state.services } else { $state }
        $running = @()
        foreach ($svc in @('engine','shell','app')) {
            $s = $services.$svc
            if ($s -and $s.pid) {
                $proc = Get-Process -Id $s.pid -ErrorAction SilentlyContinue
                if ($proc) { $running += ($svc + ':' + $s.port) }
            }
        }
        if ($running.Count -gt 0) { Check PASS ('Running: ' + ($running -join ', ')) } else { Check WARN 'No services running' '' 'run start.ps1' }
    } catch { Check WARN 'services.json unreadable' $_.Exception.Message '' }
} else {
    Check WARN 'Not started yet (no services.json)' '' 'run start.ps1'
}

# 7. Ports (from state if available, else defaults)
Write-Head '7. Ports'
$portsToCheck = @()
try {
    if ($state) {
        $services = if ($state.services) { $state.services } else { $state }
        foreach ($svc in @('engine','shell','app')) {
            if ($services.$svc -and $services.$svc.port) { $portsToCheck += @{ name=$svc; port=[int]$services.$svc.port } }
        }
    }
} catch { }
if ($portsToCheck.Count -eq 0) { $portsToCheck = @(@{name='app default';port=18080}, @{name='engine default';port=10262}) }
foreach ($p in $portsToCheck) {
    $listening = Test-Tcp '127.0.0.1' $p.port 1000
    if ($listening) { Check PASS ('Port ' + $p.port + ' (' + $p.name + ') listening') }
    if (-not $listening) { Check WARN ('Port ' + $p.port + ' not listening') '' 'if services expected, check start.ps1 output' }
}

# 8. Disk space
Write-Head '8. Disk space'
$driveInfo = [System.IO.DriveInfo]::new($root)
$freeGB = [math]::Round($driveInfo.AvailableFreeSpace / 1GB, 1)
if ($freeGB -lt 2) { Check WARN ('Low disk: ' + $freeGB + ' GB free') '' 'clean disk' } else { Check PASS ('Free space: ' + $freeGB + ' GB') }

# Summary
Write-Host ''
Write-Host ('  ' + ('=' * 58)) -ForegroundColor DarkGray
if ($FailCount -gt 0) {
    Write-Host ('  Result: ' + $FailCount + ' failure(s), ' + $WarnCount + ' warning(s)') -ForegroundColor Red
    exit 3
}
if ($WarnCount -gt 0) {
    Write-Host ('  Result: ' + $WarnCount + ' warning(s), no failures') -ForegroundColor Yellow
    exit 2
}
Write-Host '  Result: all checks passed' -ForegroundColor Green
exit 0




