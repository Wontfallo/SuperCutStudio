@echo off
setlocal
cd /d "%~dp0"
title SuperCut Studio

if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" "%~dp0desktop.py" %*
) else (
    python "%~dp0desktop.py" %*
)

if errorlevel 1 (
    echo.
    echo [ERROR] SuperCut Studio terminated with error code %errorlevel%.
    pause
)
