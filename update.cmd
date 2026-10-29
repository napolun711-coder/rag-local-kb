@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Local RAG Knowledge Base - Update

if not exist "scripts\update.ps1" (
    echo.
    echo [ERROR] Update script not found: scripts\update.ps1
    echo.
    pause
    exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1" %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo [ERROR] Update did not complete. Exit code: %RC%
    echo         Review the messages above. User data is not deleted by the updater.
    echo.
    pause
)

exit /b %RC%
