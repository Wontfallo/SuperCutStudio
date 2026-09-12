"""System capability report for the studio (FFmpeg, GPU, ASR backends, caches)."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from typing import Any, Dict, Optional

from .. import __version__
from ..asr import TranscriptCache, crisper_available, cuda_available, describe_models, find_crisper_python
from ..paths import (
    APP_NAME,
    data_dir,
    default_output_dir,
    ffmpeg_available,
    ffmpeg_path,
    ffmpeg_version,
    ffprobe_path,
    is_frozen,
    logs_dir,
)
from ..video import probe_available_encoders
from ..procs import run_hidden


def gpu_name() -> Optional[str]:
    try:
        res = run_hidden(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=6,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return None


def build_info() -> Dict[str, str]:
    """Version / commit / build time written by build_installer.ps1 (or 'dev' from source)."""
    import json

    from ..paths import static_dir

    info = {"version": __version__, "commit": "", "built": ""}
    try:
        with open(static_dir() / "build_info.json", "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        info.update({k: str(v) for k, v in data.items() if k in info})
    except Exception:
        pass
    if not info["built"] and is_frozen():
        import datetime

        try:
            info["built"] = datetime.datetime.fromtimestamp(os.path.getmtime(sys.executable)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            pass
    return info


def build_stamp() -> str:
    info = build_info()
    if not is_frozen() and not info["commit"]:
        return "dev"
    parts = [p for p in (info["commit"], info["built"]) if p]
    return " · ".join(parts) if parts else "unknown"


_STARTED_AT = __import__("time").time()


def system_report(probe_encoders: bool = True) -> Dict[str, Any]:
    encoders = probe_available_encoders() if (probe_encoders and ffmpeg_available()) else {}
    crisper_py = find_crisper_python()
    cache = TranscriptCache()
    return {
        "app": {
            "name": APP_NAME,
            "version": __version__,
            "frozen": is_frozen(),
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}",
            "data_dir": str(data_dir()),
            "logs_dir": str(logs_dir()),
            "default_output_dir": str(default_output_dir()),
            "build": build_stamp(),
            "build_info": build_info(),
            "started_at": _STARTED_AT,
            "pid": os.getpid(),
        },
        "ffmpeg": {
            "available": ffmpeg_available(),
            "path": ffmpeg_path(),
            "ffprobe_path": ffprobe_path(),
            "version": ffmpeg_version(),
        },
        "encoders": encoders,
        "nvenc": bool(encoders.get("h264_nvenc") or encoders.get("hevc_nvenc")),
        "cuda": cuda_available(),
        "gpu": gpu_name(),
        "asr": {
            "crisper_available": crisper_available(),
            "crisper_python": str(crisper_py) if crisper_py else None,
            "models": describe_models(),
        },
        "transcript_cache": cache.stats(),
    }


__all__ = ["gpu_name", "system_report"]
