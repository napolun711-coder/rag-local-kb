#Requires -Version 5.1
<#
  Local RAG knowledge-base desktop launcher.

  Safe repair policy:
  - Reuses the project lifecycle and idempotent setup scripts.
  - Repairs only project-local Python dependencies.
  - Stops only verified project-owned listeners on expected local ports.
  - Never kills unrelated port owners.
  - Never deletes knowledge, uploads, memory, indexes, or configuration.
  - Never downloads a model automatically.
  - Performs at most one repair and one retry per launch.
#>
[CmdletBinding()]
param(
    [switch]$SelfTest,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Lifecycle = Join-Path $Root 'scripts\lifecycle.py'
$Setup = Join-Path $Root 'scripts\setup.ps1'
$EnginePython = Join-Path $Root 'services\lightrag_engine\.venv\Scripts\python.exe'
$EnvironmentFile = Join-Path $Root '.env'
$IntegrationEnvironment = [string]$env:RAGKB_ENV_FILE

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ('== ' + $Text + ' ==') -ForegroundColor Cyan
}

function Write-OK([string]$Text) {
    Write-Host ('[OK] ' + $Text) -ForegroundColor Green
}

function Write-Warn2([string]$Text) {
    Write-Host ('[WARN] ' + $Text) -ForegroundColor Yellow
}

function Write-Error2([string]$Text) {
    Write-Host ('[ERROR] ' + $Text) -ForegroundColor Red
}

function Pause-IfNeeded {
    if ($NoPause -or $SelfTest) { return }
    Write-Host ''
    Write-Host 'Press any key to close...' -ForegroundColor DarkGray
    try {
        $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    } catch {
        Read-Host 'Press Enter to close'
    }
}

function Read-EnvValue([string]$Name, [string]$Default) {
    if (Test-Path -LiteralPath $EnvironmentFile) {
        try {
            foreach ($raw in [IO.File]::ReadAllLines($EnvironmentFile)) {
                $line = $raw.Trim()
                if (-not $line -or $line.StartsWith('#') -or $line.IndexOf('=') -lt 1) {
                    continue
                }
                $pair = $line.Split('=', 2)
                if ($pair[0].Trim() -eq $Name) {
                    return $pair[1].Trim().Trim('"').Trim("'")
                }
            }
        } catch {
            return $Default
        }
    }
    return $Default
}

function Test-Dependencies {
    if (-not (Test-Path -LiteralPath $Python)) {
        return $false
    }
    & $Python -c "import fastapi, uvicorn, requests" 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $EnginePython)) {
        return $false
    }
    & $EnginePython -c "import fastapi, orjson, lightrag" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-ProjectSetup {
    if (-not (Test-Path -LiteralPath $Setup)) {
        Write-Error2 ('Setup script not found: ' + $Setup)
        return $false
    }
    Write-Step 'Repairing project-local dependencies'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Setup | Out-Host
    $exitCode = $LASTEXITCODE
    return ($exitCode -eq 0)
}

function Invoke-LifecycleStart {
    & $Python -B $Lifecycle start --reuse-running | Out-Host
    $exitCode = $LASTEXITCODE
    return $exitCode
}

function Get-StatusPayload {
    try {
        $raw = & $Python -B $Lifecycle status --json 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) {
            return $null
        }
        return ($raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-ExpectedPorts {
    $ports = @(
        [int](Read-EnvValue 'RAGKB_PORT' '18080'),
        [int](Read-EnvValue 'RAGKB_LIGHTRAG_SHELL_PORT' '10261'),
        [int](Read-EnvValue 'RAGKB_LIGHTRAG_ENGINE_PORT' '10262')
    )
    $portFiles = @(
        (Join-Path $Root 'logs\app.port'),
        (Join-Path $Root 'logs\lightrag-shell.port'),
        (Join-Path $Root 'logs\runtime\engine.port'),
        (Join-Path $Root 'logs\runtime\app.port'),
        (Join-Path $Root 'logs\runtime\shell.port')
    )
    foreach ($portFile in $portFiles) {
        if (-not (Test-Path -LiteralPath $portFile)) { continue }
        try {
            $value = [int]([IO.File]::ReadAllText($portFile).Trim())
            if ($value -gt 0) { $ports += $value }
        } catch {
            continue
        }
    }
    return @($ports | Sort-Object -Unique)
}

function Get-ProjectListeners {
    $listeners = @()
    foreach ($port in (Get-ExpectedPorts)) {
        $connections = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        foreach ($connection in $connections) {
            if (-not $connection.OwningProcess) { continue }
            $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$connection.OwningProcess) -ErrorAction SilentlyContinue
            if (-not $process) { continue }
            $executable = [string]$process.ExecutablePath
            $commandLine = [string]$process.CommandLine
            if (-not $executable.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)) {
                continue
            }
            if (-not $IntegrationEnvironment -and $commandLine -match '(?i)\\\.integration-run\\') {
                continue
            }
            if ($commandLine -notmatch '(?i)(main\.py|lightrag_shell|lightrag\.api\.lightrag_server)') {
                continue
            }
            $listeners += [pscustomobject]@{
                Port = $port
                Pid = [int]$process.ProcessId
                CommandLine = $commandLine
            }
        }
    }
    return @($listeners | Sort-Object Pid,Port -Unique)
}

function Stop-ProjectListeners([object[]]$Listeners) {
    if (-not $Listeners -or $Listeners.Count -eq 0) {
        return 0
    }
    foreach ($listener in $Listeners) {
        Write-Warn2 ('Stopping verified project listener: PID ' + $listener.Pid + ' on port ' + $listener.Port)
        Stop-Process -Id $listener.Pid -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 800
    return $Listeners.Count
}

function Write-Diagnostics {
    Write-Step 'Startup diagnostics'
    $status = Get-StatusPayload
    if ($status -and $status.services) {
        foreach ($service in $status.services) {
            $url = if ($service.url) { $service.url } else { '-' }
            Write-Host (('{0,-7} {1,-10} {2}' -f $service.service, $service.state, $url))
        }
    } else {
        Write-Warn2 'Lifecycle status could not be read.'
    }
    $logRoot = Join-Path $Root 'logs\runtime'
    Write-Host ('Runtime logs: ' + $logRoot) -ForegroundColor DarkGray

    $engineError = Join-Path $logRoot 'engine.stderr.log'
    if (Test-Path -LiteralPath $engineError) {
        try {
            $tail = (Get-Content -LiteralPath $engineError -Tail 160 -ErrorAction Stop) -join "`n"
            $match = [regex]::Match($tail, "(?i)model\s+'([^']+)'\s+not found")
            if ($match.Success) {
                Write-Warn2 ('Missing local model: ' + $match.Groups[1].Value)
                Write-Host ('Suggested command: ollama pull ' + $match.Groups[1].Value) -ForegroundColor Yellow
                Write-Host 'The launcher will not download models automatically.' -ForegroundColor DarkGray
            }
        } catch {
            # Diagnostics must never hide the original startup failure.
        }
    }
}

function Invoke-Launcher {
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor DarkGray
    Write-Host ' Local RAG Knowledge Base' -ForegroundColor White
    Write-Host (' Project: ' + $Root) -ForegroundColor DarkGray
    Write-Host '============================================================' -ForegroundColor DarkGray

    if ($SelfTest) {
        Write-Step 'Self-test'
        $deps = Test-Dependencies
        if ($deps) { Write-OK 'Project Python dependencies are importable.' }
        else { Write-Error2 'Project Python dependencies are incomplete.' }
        if (Test-Path -LiteralPath $Lifecycle) { Write-OK 'Lifecycle script found.' }
        else { Write-Error2 'Lifecycle script is missing.' }
        if (Test-Path -LiteralPath $Setup) { Write-OK 'Idempotent setup script found.' }
        else { Write-Error2 'Setup script is missing.' }
        $payload = Get-StatusPayload
        if ($payload) { Write-OK 'Lifecycle status command succeeded.' }
        else { Write-Warn2 'Lifecycle status command could not be read.' }
        if ($deps -and (Test-Path -LiteralPath $Lifecycle)) { return 0 }
        return 1
    }

    $repairPerformed = $false
    if (-not (Test-Dependencies)) {
        if (-not (Invoke-ProjectSetup)) {
            Write-Error2 'Automatic dependency repair failed.'
            Write-Diagnostics
            return 1
        }
        $repairPerformed = $true
        if (-not (Test-Dependencies)) {
            Write-Error2 'Dependencies are still incomplete after repair.'
            Write-Diagnostics
            return 1
        }
        Write-OK 'Project-local dependencies repaired.'
    }

    Write-Step 'Starting services'
    $exitCode = Invoke-LifecycleStart
    if ($exitCode -eq 0) {
        Write-OK 'Services are ready.'
        return 0
    }

    Write-Warn2 ('Initial startup failed with exit code ' + $exitCode + '.')
    $listeners = @(Get-ProjectListeners)
    if ($listeners.Count -gt 0) {
        Write-Step 'Repairing verified stale project processes'
        $stopped = Stop-ProjectListeners $listeners
        if ($stopped -gt 0) {
            $repairPerformed = $true
        }
    }

    if ($repairPerformed) {
        Write-Step 'Retrying once after safe repair'
        $exitCode = Invoke-LifecycleStart
        if ($exitCode -eq 0) {
            Write-OK 'Repair succeeded; services are ready.'
            return 0
        }
        Write-Warn2 ('Retry failed with exit code ' + $exitCode + '.')
    }

    Write-Error2 'Automatic repair did not complete startup.'
    Write-Diagnostics
    return 1
}

$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\RagKbDesktopLauncher', [ref]$createdNew)
if (-not $createdNew) {
    Write-Warn2 'Another desktop launcher is already running. It will not start a second service group.'
    $mutex.Dispose()
    exit 0
}

try {
    $result = Invoke-Launcher
    if ($result -ne 0) {
        Pause-IfNeeded
    }
    exit $result
} finally {
    try { $mutex.ReleaseMutex() } catch { }
    $mutex.Dispose()
}
