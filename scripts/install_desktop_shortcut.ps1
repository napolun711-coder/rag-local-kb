#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$Desktop
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $Root 'RAG启动器.ps1'
$Icon = Join-Path $Root 'static\app.ico'
# Default is project-local. Writing to the user's Desktop is an explicit opt-in.
$TargetDir = if ($Desktop) { [Environment]::GetFolderPath('Desktop') } else { $Root }
$ShortcutPath = Join-Path $TargetDir 'RAG 本地知识库启动.lnk'
$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $Launcher)) { throw ('Missing launcher: ' + $Launcher) }
if (-not (Test-Path -LiteralPath $Icon)) { throw ('Missing icon: ' + $Icon) }
if (-not (Test-Path -LiteralPath $PowerShell)) { throw ('Missing PowerShell: ' + $PowerShell) }
if (-not (Test-Path -LiteralPath $TargetDir)) { throw ('Shortcut target folder not found: ' + $TargetDir) }
if ((Test-Path -LiteralPath $ShortcutPath) -and -not $Force) {
    throw ('Shortcut already exists: ' + $ShortcutPath + '. Use -Force to update it.')
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PowerShell
$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $Launcher + '"'
$shortcut.WorkingDirectory = $Root
$shortcut.IconLocation = $Icon + ',0'
$shortcut.Description = '启动本地 RAG 知识库；启动失败时执行安全自动修复'
$shortcut.WindowStyle = 1
$shortcut.Save()

Write-Host ('[OK] Launcher shortcut written: ' + $ShortcutPath) -ForegroundColor Green
Write-Host ('Target: ' + $Launcher) -ForegroundColor DarkGray
if (-not $Desktop) {
    Write-Host 'Shortcut kept inside the project. Use -Desktop only if you explicitly want it on the desktop.' -ForegroundColor DarkGray
}
