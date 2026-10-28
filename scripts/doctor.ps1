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
} else {
    Check FAIL 'App .venv missing' $appPython 'run setup.cmd'
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
if (Test-Path -LiteralPath (Join-Path $root '.env')) { Check PASS 'Root .env exists' } else { Check FAIL 'Root .env missing' '' 'run setup.cmd' }
if (Test-Path -LiteralPath (Join-Path $engineDir '.env')) { Check PASS 'Engine .env exists' } else { Check FAIL 'Engine .env missing' '' 'run setup.cmd' }

# 4. Services running?
Write-Head '4. Service status'
$stateFile = Join-Path $root 'logs\runtime\services.json'
if (Test-Path -LiteralPath $stateFile) {
    try {
        $state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $running = @()
        foreach ($svc in @('engine','shell','app')) {
            $s = $state.$svc
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

# 5. Ports (from state if available, else defaults)
$portsToCheck = @()
try {
    if ($state) {
        foreach ($svc in @('engine','shell','app')) {
            if ($state.$svc -and $state.$svc.port) { $portsToCheck += @{ name=$svc; port=[int]$state.$svc.port } }
        }
    }
} catch { }
if ($portsToCheck.Count -eq 0) { $portsToCheck = @(@{name='app default';port=18080}, @{name='engine default';port=10262}) }
foreach ($p in $portsToCheck) {
    $listening = Test-Tcp '127.0.0.1' $p.port 1000
    if ($listening) { Check PASS ('Port ' + $p.port + ' (' + $p.name + ') listening') }
    if (-not $listening) { Check WARN ('Port ' + $p.port + ' not listening') '' 'if services expected, check start.ps1 output' }
}

# 6. Disk space
Write-Head '5. Disk space'
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




