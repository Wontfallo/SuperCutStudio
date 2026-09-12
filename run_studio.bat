@echo off
setlocal
cd /d "%~dp0"
title SuperCut Studio Pro — Multimodal AI Video Studio

echo ========================================================
echo  SuperCut Studio Pro — Multimodal AI Video Studio
echo ========================================================
echo [1/3] Configuring Tcl/Tk graphic environment...
set "TCL_LIBRARY=C:\Python314\tcl\tcl8.6"
set "TK_LIBRARY=C:\Python314\tcl\tk8.6"

echo [2/3] Checking Python runtime...
if exist "%~dp0venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

echo [3/3] Launching Desktop GUI Window with %PY_EXE%...
echo.
"%PY_EXE%" "%~dp0studio_app.py" %*

if errorlevel 1 (
    echo.
    echo [ERROR] SuperCut Studio Pro terminated with error code %errorlevel%.
    pause
)
