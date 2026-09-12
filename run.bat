@echo off
setlocal
cd /d "%~dp0"
title SuperCut Studio

if not exist "%~dp0venv\Scripts\python.exe" (
    echo ERROR: The app environment is missing. Run setup.bat first.
    pause
    exit /b 1
)

"%~dp0venv\Scripts\python.exe" "%~dp0desktop.py" %*
if errorlevel 1 (
    echo.
    echo [ERROR] SuperCut Studio terminated with error code %errorlevel%.
    pause
)
