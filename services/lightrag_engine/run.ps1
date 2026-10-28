$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Engine = $PSScriptRoot
$Python = Join-Path $Engine '.venv\Scripts\python.exe'
$Server = Join-Path $Engine '.venv\Scripts\lightrag-server.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'LightRAG engine virtual environment is missing. Run the installation step first.' }
if (-not (Test-Path -LiteralPath $Server)) { throw 'lightrag-server entry point is missing from the engine environment.' }
Push-Location $Engine
try {
    & $Server --host 127.0.0.1 --port 10262 --working-dir (Join-Path $Root 'data\lightrag\storage') --input-dir (Join-Path $Root 'data\lightrag\input')
} finally {
    Pop-Location
}