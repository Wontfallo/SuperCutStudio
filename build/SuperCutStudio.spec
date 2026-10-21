# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SuperCut Studio (desktop shell + bundled web studio).

Build from the project root:
    venv\Scripts\python -m PyInstaller build\SuperCutStudio.spec --noconfirm
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))

APP_NAME = "SuperCutStudio"
ICON = str(ROOT / "static" / "app.ico")

from supercut import __version__ as APP_VERSION  # noqa: E402
from PyInstaller.utils.win32.versioninfo import (  # noqa: E402
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VSVersionInfo, VarFileInfo, VarStruct,
)


def _version_tuple(v):
    parts = [int(p) for p in v.split(".")[:3] if p.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_version_tuple(APP_VERSION), prodvers=_version_tuple(APP_VERSION)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Wontfallo"),
            StringStruct("FileDescription", "SuperCut Studio"),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("InternalName", APP_NAME),
            StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
            StringStruct("ProductName", "SuperCut Studio"),
            StringStruct("ProductVersion", APP_VERSION),
            StringStruct("LegalCopyright", "Wontfallo"),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

datas = [
    (str(ROOT / "static"), "static"),
]

# Bundled FFmpeg binaries (optional): build/ffmpeg/ffmpeg.exe + ffprobe.exe
ffmpeg_dir = ROOT / "build" / "ffmpeg"
if (ffmpeg_dir / "ffmpeg.exe").exists():
    datas.append((str(ffmpeg_dir / "ffmpeg.exe"), "bin"))
if (ffmpeg_dir / "ffprobe.exe").exists():
    datas.append((str(ffmpeg_dir / "ffprobe.exe"), "bin"))

binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "multipart",
    "python_multipart",
    "anyio._backends._asyncio",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr_loader",
    "pythonnet",
]
hiddenimports += collect_submodules("supercut")

# ASR stack: faster-whisper + ctranslate2 + tokenizers + onnxruntime (VAD)
for pkg in ("faster_whisper", "ctranslate2", "tokenizers", "onnxruntime", "huggingface_hub", "av", "webview", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # pragma: no cover - optional packages
        print(f"[spec] skipping {pkg}: {exc}")

excludes = [
    "torch", "torchvision", "torchaudio", "customtkinter", "tkinter", "_tkinter",
    "matplotlib", "scipy", "pandas", "IPython", "jupyter", "notebook", "playwright",
    "PIL.ImageQt", "PyQt5", "PyQt6", "PySide2", "PySide6", "test", "tests", "pytest",
]

block_cipher = None

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version=VERSION_INFO,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
