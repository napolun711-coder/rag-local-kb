@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Local RAG Knowledge Base - Deploy

if not exist "scripts\deploy.ps1" (
    echo.
    echo [ERROR] Deployment script not found: scripts\deploy.ps1
    echo.
    pause
    exit /b 3
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy.ps1" %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo [ERROR] Deployment failed. Exit code: %RC%
    echo         All files remain inside this project. Check the messages above.
    echo.
    pause
)

exit /b %RC%
