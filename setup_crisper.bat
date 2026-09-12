@echo off
setlocal

set "APP_DIR=%~dp0"
set "CRISPER_VENV=%APP_DIR%crisper-venv"

echo Setting up the optional CrisperWhisper Turbo runtime...
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo ERROR: The Python launcher ^(py.exe^) was not found.
    echo Install 64-bit Python 3.12, then run this file again.
    pause
    exit /b 1
)

if not exist "%CRISPER_VENV%\Scripts\python.exe" (
    py -3.12 -m venv "%CRISPER_VENV%"
    if errorlevel 1 (
        echo ERROR: Python 3.12 is required for the known-good CUDA runtime.
        pause
        exit /b 1
    )
)

call "%CRISPER_VENV%\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto :failed

echo Installing the same CUDA PyTorch stack used by Video Dub...
python -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 goto :failed

echo Installing CrisperWhisper 2.0 with the Windows Transformers backend...
python -m pip install "crisperwhisper[transformers]==2.0.1" "transformers==5.3.0"
if errorlevel 1 goto :failed

python -c "import crisperwhisper, torch, transformers; print('CrisperWhisper runtime ready. CUDA:', torch.cuda.is_available())"
if errorlevel 1 goto :failed

echo.
echo Setup complete. VideoKeywordStitcher will find this runtime automatically.
pause
exit /b 0

:failed
echo.
echo ERROR: CrisperWhisper setup failed. Review the messages above.
pause
exit /b 1
