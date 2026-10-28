$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Lifecycle = Join-Path $Root 'scripts\lifecycle.py'

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Application virtual environment is missing. Run: uv sync'
}

& $Python -B $Lifecycle start @args
exit $LASTEXITCODE
