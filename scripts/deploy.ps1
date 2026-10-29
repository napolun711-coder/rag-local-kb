#Requires -Version 5.1
<#
  Local RAG Knowledge Base - one-click project-local deployment.

  Boundaries:
  - Writes only inside this project root.
  - Does not create a desktop shortcut, install system packages, or download models.
  - Preserves existing .env, data, knowledge, logs, and engine venv where possible.
  - Creates a project-local launcher shortcut only.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$NoStart,
    [switch]$NoShortcut,
    [switch]$ConfigureLocal,
    [switch]$SkipEngine
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot | Split-Path -Parent
$Setup = Join-Path $Root 'scripts\setup.ps1'
$Doctor = Join-Path $Root 'scripts\doctor.ps1'
$Start = Join-Path $Root 'start.ps1'
$Launcher = Join-Path $Root 'RAG启动器.ps1'
$Icon = Join-Path $Root 'static\app.ico'
$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ('== ' + $Text + ' ==') -ForegroundColor Cyan
}
function Write-OK([string]$Text) { Write-Host ('[OK] ' + $Text) -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host ('[WARN] ' + $Text) -ForegroundColor Yellow }
function Write-Error2([string]$Text) { Write-Host ('[ERROR] ' + $Text) -ForegroundColor Red }

function Invoke-Setup([bool]$Check) {
    $argsList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Setup, '-ConfigureLocal')
    if ($Check) { $argsList += '-CheckOnly' }
    if ($SkipEngine) { $argsList += '-SkipEngine' }
    & $PowerShell @argsList | Out-Host
    return $LASTEXITCODE
}

function Invoke-Doctor {
    & $PowerShell -NoProfile -ExecutionPolicy Bypass -File $Doctor | Out-Host
    return $LASTEXITCODE
}

function Get-EnvValue([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    try {
        foreach ($raw in [IO.File]::ReadAllLines($Path)) {
            $line = ([string]$raw).Trim()
            if (-not $line -or $line.StartsWith('#') -or $line.IndexOf('=') -lt 1) { continue }
            $pair = $line.Split('=', 2)
            if ($pair[0].Trim() -eq $Name) { return $pair[1].Trim().Trim('"').Trim("'") }
        }
    } catch { }
    return ''
}

function Test-EngineReady {
    $engineEnv = Join-Path $Root 'services\lightrag_engine\.env'
    $hostValue = Get-EnvValue $engineEnv 'LLM_BINDING_HOST'
    $model = Get-EnvValue $engineEnv 'LLM_MODEL'
    if (-not $hostValue -or -not $model -or $hostValue -match 'your-openai-compatible-host') {
        Write-Error2 'Engine generation endpoint/model is not configured.'
        Write-Host '  For local Ollama: install/start Ollama, run: ollama pull bge-m3; ollama pull qwen2.5:3b' -ForegroundColor Yellow
        Write-Host '  Then re-run deploy.cmd.' -ForegroundColor Yellow
        Write-Host '  For an external endpoint: edit services\lightrag_engine\.env first.' -ForegroundColor Yellow
        return $false
    }
    return $true
}

function New-ProjectShortcut {
    if ($NoShortcut) { return }
    $shortcutPath = Join-Path $Root 'RAG 本地知识库启动.lnk'
    if (Test-Path -LiteralPath $shortcutPath) {
        Write-OK 'Project-local launcher shortcut already exists'
        return
    }
    if (-not (Test-Path -LiteralPath $Launcher)) {
        Write-Warn2 'Launcher script is missing; shortcut not created.'
        return
    }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $PowerShell
        $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $Launcher + '"'
        $shortcut.WorkingDirectory = $Root
        if (Test-Path -LiteralPath $Icon) { $shortcut.IconLocation = $Icon + ',0' }
        $shortcut.Description = '启动本地 RAG 知识库；启动失败时执行安全自动修复'
        $shortcut.WindowStyle = 1
        $shortcut.Save()
        Write-OK ('Project-local launcher shortcut: ' + $shortcutPath)
    } catch {
        Write-Warn2 ('Could not create the project-local shortcut: ' + $_.Exception.Message)
    }
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkGray
Write-Host ' Local RAG Knowledge Base - Deploy' -ForegroundColor White
Write-Host (' Project: ' + $Root) -ForegroundColor DarkGray
Write-Host ' All writes stay inside this project.' -ForegroundColor DarkGray
Write-Host '============================================================' -ForegroundColor DarkGray

if ($CheckOnly) {
    Write-Step 'Check-only mode'
    $setupCode = Invoke-Setup $true
    if ($setupCode -ne 0) { exit $setupCode }
    $doctorCode = Invoke-Doctor
    if ($doctorCode -eq 3) { exit 3 }
    Write-Host ''
    Write-Host 'Check-only finished. No services were started.' -ForegroundColor Cyan
    exit 0
}

Write-Step 'Installing / repairing project environment'
$setupCode = Invoke-Setup
if ($setupCode -ne 0) {
    Write-Error2 ('Setup failed with exit code ' + $setupCode + '.')
    exit $setupCode
}
if (-not (Test-EngineReady)) { exit 3 }

Write-Step 'Running environment doctor'
$doctorCode = Invoke-Doctor
if ($doctorCode -eq 3) {
    Write-Error2 'Doctor reported failures. Deployment is incomplete; fix the items above and re-run deploy.cmd.'
    exit 3
}

New-ProjectShortcut

if ($NoStart) {
    Write-Host ''
    Write-OK 'Deployment prepared. Start later with start.cmd.'
    exit 0
}

Write-Step 'Starting services'
& $PowerShell -NoProfile -ExecutionPolicy Bypass -File $Start --reuse-running | Out-Host
$startCode = $LASTEXITCODE
if ($startCode -ne 0) {
    Write-Error2 ('Startup failed with exit code ' + $startCode + '. See logs\runtime\.')
    exit $startCode
}
Write-OK 'Deployment complete; services are ready.'
exit 0
