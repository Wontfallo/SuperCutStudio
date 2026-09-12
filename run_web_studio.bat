@echo off
setlocal
cd /d "%~dp0"
title SuperCut Studio (browser mode)

if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" "%~dp0web_app.py" %*
) else (
    python "%~dp0web_app.py" %*
)

if errorlevel 1 (
    echo.
    echo [ERROR] SuperCut Studio terminated with error code %errorlevel%.
    pause
)
