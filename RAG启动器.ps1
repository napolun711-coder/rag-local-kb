
$ErrorActionPreference = "Stop"

$project = "<project-root>"
$script = Join-Path $project "start.ps1"

if (-not (Test-Path $script)) {
    [System.Windows.Forms.MessageBox]::Show(
        "找不到启动脚本：$script",
        "RAG 本地知识库"
    )
    exit 1
}

Start-Process `
    -FilePath "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$script`""
    ) `
    -WorkingDirectory $project
