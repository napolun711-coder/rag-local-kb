@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Setup failed. See messages above.
    pause
)
exit /b %RC%
