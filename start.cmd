@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Local RAG Knowledge Base

if not exist ".venv\Scripts\python.exe" goto :prepare
if not exist "services\lightrag_engine\.venv\Scripts\python.exe" goto :prepare
goto :ready

:prepare
echo.
echo [INFO] First run or missing local environment detected.
echo        Preparing project-local dependencies; this may take several minutes.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy.ps1" -NoStart
if errorlevel 1 (
    echo.
    echo [ERROR] Automatic preparation failed. Review the messages above.
    echo.
    pause
    exit /b 3
)

:ready
if not exist "scripts\lifecycle.py" (
    echo.
    echo [ERROR] Lifecycle launcher not found: scripts\lifecycle.py
    echo.
    pause
    exit /b 3
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" --reuse-running %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo [ERROR] Application startup failed. Exit code: %RC%
    echo         Check logs in logs\runtime\ for details.
    echo.
    pause
)

exit /b %RC%
