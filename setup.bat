@echo off
setlocal
cd /d "%~dp0"
echo Setting up SuperCut Studio...
echo.

where py >nul 2>nul
if errorlevel 1 (
    python -m venv "%~dp0venv"
) else (
    py -3 -m venv "%~dp0venv"
)
if errorlevel 1 (
    echo ERROR: Could not create a virtual environment. Install Python 3.10+ first.
    pause
    exit /b 1
)

call "%~dp0venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Setup complete!
echo   run_desktop.bat     - native desktop window
echo   run_web_studio.bat  - browser mode (http://127.0.0.1:8080)
echo   run_studio.bat      - legacy CustomTkinter studio
echo.
pause
