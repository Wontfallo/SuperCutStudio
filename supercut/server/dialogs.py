"""Native file/folder pickers usable from a background HTTP request.

Priority:
1. pywebview window (desktop shell) - true native dialog owned by the app window.
2. PowerShell + WinForms (Windows) - works from any thread and inside PyInstaller.
3. tkinter fallback (source runs on other platforms).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Callable, List, Optional
from ..procs import popen_hidden, run_hidden

VIDEO_FILTER = "Video Files|*.mp4;*.mkv;*.avi;*.mov;*.webm;*.m4v;*.ts;*.wmv;*.flv|All Files|*.*"

# The desktop shell registers a callable here: pick(kind, title, initial) -> Optional[str]
_native_picker: Optional[Callable[[str, str, str], Optional[str]]] = None
_dialog_lock = threading.Lock()


def register_native_picker(fn: Optional[Callable[[str, str, str], Optional[str]]]) -> None:
    global _native_picker
    _native_picker = fn


def _powershell_pick(kind: str, title: str, initial: str) -> Optional[str]:
    if os.name != "nt":
        return None
    initial = initial if initial and os.path.isdir(initial) else ""
    safe_title = title.replace("'", "''")
    safe_initial = initial.replace("'", "''")
    if kind == "folder":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            f"$d.Description = '{safe_title}'; $d.ShowNewFolderButton = $true; "
            + (f"$d.SelectedPath = '{safe_initial}'; " if safe_initial else "")
            + "$r = $d.ShowDialog((New-Object System.Windows.Forms.Form -Property @{TopMost=$true})); "
            "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }"
        )
    elif kind == "save":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.SaveFileDialog; "
            f"$d.Title = '{safe_title}'; $d.Filter = 'MP4 Video|*.mp4|All Files|*.*'; "
            + (f"$d.InitialDirectory = '{safe_initial}'; " if safe_initial else "")
            + "$r = $d.ShowDialog((New-Object System.Windows.Forms.Form -Property @{TopMost=$true})); "
            "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.FileName }"
        )
    else:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.OpenFileDialog; "
            f"$d.Title = '{safe_title}'; $d.Filter = '{VIDEO_FILTER}'; $d.Multiselect = $false; "
            + (f"$d.InitialDirectory = '{safe_initial}'; " if safe_initial else "")
            + "$r = $d.ShowDialog((New-Object System.Windows.Forms.Form -Property @{TopMost=$true})); "
            "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.FileName }"
        )
    try:
        res = run_hidden(
            ["powershell", "-NoProfile", "-STA", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        out = (res.stdout or "").strip()
        return out or None
    except Exception:
        return None


def _tk_pick(kind: str, title: str, initial: str) -> Optional[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    result: List[Optional[str]] = [None]

    def _run():
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if kind == "folder":
                result[0] = filedialog.askdirectory(title=title, initialdir=initial or None) or None
            elif kind == "save":
                result[0] = filedialog.asksaveasfilename(title=title, initialdir=initial or None,
                                                         defaultextension=".mp4") or None
            else:
                result[0] = filedialog.askopenfilename(
                    title=title, initialdir=initial or None,
                    filetypes=[("Video Files", "*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.ts *.wmv *.flv"), ("All Files", "*.*")],
                ) or None
            root.destroy()
        except Exception:
            result[0] = None

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=600)
    return result[0]


def pick_path(kind: str = "file", title: str = "", initial: str = "") -> Optional[str]:
    """Show a native picker and return the chosen path (or None when cancelled)."""
    kind = kind if kind in ("file", "folder", "save") else "file"
    if not title:
        title = {"file": "Select a video file", "folder": "Select a folder of videos", "save": "Save as"}[kind]
    with _dialog_lock:
        if _native_picker is not None:
            try:
                res = _native_picker(kind, title, initial)
                if res is not None:
                    return res or None
            except Exception:
                pass
        res = _powershell_pick(kind, title, initial)
        if res is None and os.name != "nt":
            res = _tk_pick(kind, title, initial)
        return res


def reveal_in_file_manager(path: str) -> bool:
    """Open Explorer / Finder at the given path, selecting the file when possible."""
    if not path:
        return False
    try:
        if os.name == "nt":
            if os.path.isfile(path):
                popen_hidden(["explorer", "/select,", os.path.normpath(path)])
            elif os.path.isdir(path):
                os.startfile(os.path.normpath(path))  # type: ignore[attr-defined]
            else:
                return False
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R" if os.path.isfile(path) else "", path])
            return True
        subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        return True
    except Exception:
        return False


__all__ = ["pick_path", "register_native_picker", "reveal_in_file_manager"]
