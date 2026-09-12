"""Child-process helpers that never flash a console window.

Every external tool the studio launches (ffmpeg, ffprobe, nvidia-smi, taskkill,
PowerShell dialogs, the CrisperWhisper worker) must go through these wrappers.
In a windowed build each plain ``subprocess.run`` would otherwise pop up a
black console for a fraction of a second.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict


def hidden_kwargs() -> Dict[str, Any]:
    """Extra ``subprocess`` keyword arguments that suppress console windows on Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def run_hidden(cmd, **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` with stdin detached and no console window."""
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    for k, v in hidden_kwargs().items():
        kwargs.setdefault(k, v)
    return subprocess.run(cmd, **kwargs)


def popen_hidden(cmd, **kwargs) -> subprocess.Popen:
    """``subprocess.Popen`` with no console window."""
    for k, v in hidden_kwargs().items():
        kwargs.setdefault(k, v)
    return subprocess.Popen(cmd, **kwargs)


__all__ = ["hidden_kwargs", "popen_hidden", "run_hidden"]
