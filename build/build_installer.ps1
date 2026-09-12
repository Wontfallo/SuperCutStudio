<#
.SYNOPSIS
  Build SuperCut Studio: PyInstaller one-folder bundle + Inno Setup installer.

.DESCRIPTION
  1. Runs the test suites (skip with -SkipTests).
  2. Copies FFmpeg (ffmpeg.exe / ffprobe.exe) into build\ffmpeg so it ships with the app
     (from -FfmpegDir, or auto-detected on PATH). Skip with -NoFfmpeg.
  3. Runs PyInstaller with build\SuperCutStudio.spec  -> dist\SuperCutStudio\
  4. Compiles build\installer.iss with Inno Setup     -> dist\SuperCutStudio-Setup-<version>.exe

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File build\build_installer.ps1
#>
[CmdletBinding()]
param(
  [switch]$SkipTests,
  [switch]$NoFfmpeg,
  [switch]$NoInstaller,
  [switch]$CloseRunning,
  [string]$FfmpegDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "venv not found at $Python. Run setup.bat first." }

$Version = & $Python -c "import supercut; print(supercut.__version__)"
Write-Host "== SuperCut Studio v$Version build ==" -ForegroundColor Cyan

# 1. Tests -------------------------------------------------------------------
if (-not $SkipTests) {
  Write-Host "[1/4] Running test suite..." -ForegroundColor Yellow
  & $Python -m pytest -q
  if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
  if (Get-Command node -ErrorAction SilentlyContinue) {
    & node tests\test_browser_frontend.js
    if ($LASTEXITCODE -ne 0) { throw "browser tests failed" }
  }
} else {
  Write-Host "[1/4] Tests skipped" -ForegroundColor DarkGray
}

# 2. FFmpeg ------------------------------------------------------------------
$FfmpegOut = Join-Path $Root "build\ffmpeg"
if ($NoFfmpeg) {
  Write-Host "[2/4] FFmpeg bundling skipped (app will use FFmpeg from PATH)" -ForegroundColor DarkGray
  if (Test-Path $FfmpegOut) { Remove-Item -Recurse -Force $FfmpegOut }
} else {
  Write-Host "[2/4] Bundling FFmpeg..." -ForegroundColor Yellow
  New-Item -ItemType Directory -Force $FfmpegOut | Out-Null
  $ffmpegExe = $null; $ffprobeExe = $null
  # Candidate real binaries (package-manager shims are tiny launchers and must be skipped).
  $candidates = @()
  if ($FfmpegDir) { $candidates += (Join-Path $FfmpegDir "ffmpeg.exe") }
  $cmd = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
  if ($cmd) { $candidates += $cmd.Source }
  $candidates += @(
    "C:\ProgramData\chocolatey\lib\ffmpeg-full\tools\ffmpeg\bin\ffmpeg.exe",
    "C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin\ffmpeg.exe",
    (Join-Path $env:USERPROFILE "scoop\apps\ffmpeg\current\bin\ffmpeg.exe")
  )
  $wingetPkgs = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
  if (Test-Path $wingetPkgs) {
    $candidates += Get-ChildItem $wingetPkgs -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
  }
  foreach ($c in $candidates) {
    if (-not $c -or -not (Test-Path $c)) { continue }
    $item = Get-Item $c
    if ($item.Length -lt 20MB) { continue }   # shim / launcher, not the real thing
    $probe = Join-Path $item.DirectoryName "ffprobe.exe"
    if (-not (Test-Path $probe)) { continue }
    try { $v = & $c -version 2>$null | Select-Object -First 1 } catch { $v = $null }
    if (-not $v -or $v -notmatch "ffmpeg version") { continue }
    $ffmpegExe = $c; $ffprobeExe = $probe
    Write-Host "      $v"
    break
  }
  if ($ffmpegExe -and (Test-Path $ffmpegExe) -and $ffprobeExe -and (Test-Path $ffprobeExe)) {
    Copy-Item $ffmpegExe (Join-Path $FfmpegOut "ffmpeg.exe") -Force
    Copy-Item $ffprobeExe (Join-Path $FfmpegOut "ffprobe.exe") -Force
    Write-Host "      ffmpeg: $ffmpegExe"
  } else {
    Write-Warning "FFmpeg not found; the installed app will require FFmpeg on PATH."
  }
}

# 3. PyInstaller -------------------------------------------------------------
Write-Host "[3/4] Running PyInstaller..." -ForegroundColor Yellow
$distExe = (Join-Path $Root "dist\SuperCutStudio\SuperCutStudio.exe")
$running = Get-Process -Name SuperCutStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path -ieq $distExe) }
if ($running) {
  if ($CloseRunning) {
    Write-Host "      closing running dist\SuperCutStudio.exe (pid $($running.Id -join ', '))" -ForegroundColor Yellow
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
  } else {
    throw "dist\SuperCutStudio\SuperCutStudio.exe is running (pid $($running.Id -join ', ')). Close the app, or re-run with -CloseRunning."
  }
}
if (Test-Path "dist\SuperCutStudio") { Remove-Item -Recurse -Force "dist\SuperCutStudio" }
& $Python -m PyInstaller "build\SuperCutStudio.spec" --noconfirm --clean --distpath dist --workpath build\pyi-work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$exe = "dist\SuperCutStudio\SuperCutStudio.exe"
if (-not (Test-Path $exe)) { throw "Bundle missing: $exe" }
$size = (Get-ChildItem "dist\SuperCutStudio" -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("      bundle: {0:N0} MB" -f $size)

# 4. Inno Setup --------------------------------------------------------------
if ($NoInstaller) {
  Write-Host "[4/4] Installer skipped" -ForegroundColor DarkGray
  exit 0
}
Write-Host "[4/4] Compiling installer..." -ForegroundColor Yellow
$iscc = @(
  (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup (ISCC.exe) not found. Install it with: winget install JRSoftware.InnoSetup" }
& $iscc "/DAppVersion=$Version" "build\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
$setup = Get-ChildItem "dist\SuperCutStudio-Setup-$Version.exe"
Write-Host ("== Done: {0} ({1:N1} MB) ==" -f $setup.FullName, ($setup.Length / 1MB)) -ForegroundColor Green
