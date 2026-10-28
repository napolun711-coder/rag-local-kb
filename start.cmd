@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Local RAG Knowledge Base

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERROR] Python environment not found.
    echo         Run setup.cmd once to prepare this project.
    echo.
    pause
    exit /b 3
)

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
