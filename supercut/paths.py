"""Application paths, user-data directories, and FFmpeg binary resolution.

Everything that needs to know *where things live* goes through this module so
that the app behaves identically when run from source, from a PyInstaller
bundle installed under Program Files (read-only), or from a portable folder.
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

APP_NAME = "SuperCut Studio"
APP_ID = "SuperCutStudio"

FFMPEG_ENV = "VKS_FFMPEG"
FFPROBE_ENV = "VKS_FFPROBE"
DATA_DIR_ENV = "VKS_DATA_DIR"


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Directory containing the application code / executable."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Directory containing bundled resources (static assets, icons, ffmpeg bin)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return app_root()


def static_dir() -> Path:
    return resource_root() / "static"


@lru_cache(maxsize=1)
def data_dir() -> Path:
    """Per-user writable data directory (settings, cache, logs, uploads)."""
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        base = Path(override)
    elif not is_frozen():
        # Running from source: keep data next to the code for easy inspection.
        base = app_root() / ".supercut_data"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_ID
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_file() -> Path:
    return data_dir() / "settings.json"


def uploads_dir() -> Path:
    p = data_dir() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def transcript_cache_dir() -> Path:
    p = cache_dir() / "transcripts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def waveform_cache_dir() -> Path:
    p = cache_dir() / "waveforms"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_output_dir() -> Path:
    """Default render destination: the user's Videos folder when available."""
    if not is_frozen():
        return app_root() / "output"
    if os.name == "nt":
        videos = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Videos"
        if videos.is_dir():
            return videos / "SuperCut Output"
    return Path.home() / "SuperCut Output"


def legacy_transcript_cache_dir() -> Path:
    """Location used by earlier releases (still read for backwards compatibility)."""
    return Path.home() / ".vks_cache" / "transcripts"


# ---------------------------------------------------------------------------
# FFmpeg discovery
# ---------------------------------------------------------------------------

def _candidate_dirs() -> list[Path]:
    dirs = [
        resource_root() / "bin",
        app_root() / "bin",
        app_root() / "ffmpeg" / "bin",
        app_root() / "ffmpeg",
        data_dir() / "bin",
    ]
    return dirs


def _find_binary(name: str, env_var: str) -> str:
    exe = f"{name}.exe" if os.name == "nt" else name
    configured = os.environ.get(env_var, "").strip().strip('"')
    if configured:
        p = Path(configured)
        if p.is_file():
            return str(p)
        if p.is_dir() and (p / exe).is_file():
            return str(p / exe)

    for d in _candidate_dirs():
        cand = d / exe
        if cand.is_file():
            return str(cand)

    found = shutil.which(name)
    if found:
        return found
    return name  # Let subprocess raise a clear FileNotFoundError.


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    return _find_binary("ffmpeg", FFMPEG_ENV)


@lru_cache(maxsize=1)
def ffprobe_path() -> str:
    return _find_binary("ffprobe", FFPROBE_ENV)


def reset_binary_cache() -> None:
    ffmpeg_path.cache_clear()
    ffprobe_path.cache_clear()


def ffmpeg_available() -> bool:
    p = ffmpeg_path()
    return os.path.isfile(p) or shutil.which(p) is not None


def ffmpeg_version() -> Optional[str]:
    """Return the first line of `ffmpeg -version`, or None when unavailable."""
    import subprocess

    try:
        res = subprocess.run(
            [ffmpeg_path(), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout.splitlines()[0].strip()
    except Exception:
        pass
    return None


__all__ = [
    "APP_ID",
    "APP_NAME",
    "app_root",
    "cache_dir",
    "data_dir",
    "default_output_dir",
    "ffmpeg_available",
    "ffmpeg_path",
    "ffmpeg_version",
    "ffprobe_path",
    "is_frozen",
    "legacy_transcript_cache_dir",
    "logs_dir",
    "reset_binary_cache",
    "resource_root",
    "settings_file",
    "static_dir",
    "transcript_cache_dir",
    "uploads_dir",
    "waveform_cache_dir",
]
