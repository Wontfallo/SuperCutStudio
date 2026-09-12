"""SuperCut Studio desktop shell.

Starts the FastAPI studio server on a free local port and hosts the UI inside a
native window (pywebview / Edge WebView2). Falls back to the default browser
when a native web view is unavailable. This is the PyInstaller entry point.
"""

from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# Multiprocessing / frozen safety must come before heavy imports.
if getattr(sys, "frozen", False):
    import multiprocessing

    multiprocessing.freeze_support()

os.environ.setdefault("VKS_DESKTOP", "1")
os.environ.setdefault("VKS_SHUTDOWN_TOKEN", secrets.token_hex(16))


def _ensure_std_streams() -> None:
    """A windowed (no-console) PyInstaller app has sys.stdout/stderr = None.

    uvicorn's default log config and plain print() calls both break on that, so
    route them to a file inside the per-user log directory instead.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from supercut.paths import logs_dir

        target = logs_dir() / "console.log"
        stream = open(target, "a", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        import io

        stream = io.StringIO()
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    if getattr(sys, "__stdout__", None) is None:
        sys.__stdout__ = stream  # type: ignore[assignment]
    if getattr(sys, "__stderr__", None) is None:
        sys.__stderr__ = stream  # type: ignore[assignment]


_ensure_std_streams()

from supercut import __version__  # noqa: E402
from supercut.paths import APP_NAME, resource_root  # noqa: E402
from supercut.server.logging_setup import get_logger, setup_logging  # noqa: E402

log = get_logger("supercut.desktop")


def _free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
        except OSError:
            continue
    return preferred


def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _start_server(port: int) -> threading.Thread:
    import uvicorn

    import web_app

    # log_config=None: skip uvicorn's dictConfig (it probes sys.stdout.isatty()); logging is set up by setup_logging().
    config = uvicorn.Config(web_app.app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                            log_config=None)
    server = uvicorn.Server(config)

    def _run():
        try:
            server.run()
        except Exception:
            log.exception("Server crashed")

    t = threading.Thread(target=_run, daemon=True, name="supercut-server")
    t.start()
    return t


def _icon_path() -> str | None:
    for cand in (resource_root() / "static" / "app.ico", resource_root() / "app.ico"):
        if cand.exists():
            return str(cand)
    return None


def _run_webview(url: str) -> bool:
    try:
        import webview  # pywebview
    except Exception as exc:
        log.warning("pywebview unavailable (%s); falling back to browser", exc)
        return False

    from supercut.server.dialogs import register_native_picker

    window_holder: dict = {}

    def picker(kind: str, title: str, initial: str):
        win = window_holder.get("win")
        if win is None:
            return None
        try:
            if kind == "folder":
                res = win.create_file_dialog(webview.FOLDER_DIALOG, directory=initial or "")
            elif kind == "save":
                res = win.create_file_dialog(webview.SAVE_DIALOG, directory=initial or "", save_filename="supercut.mp4")
            else:
                res = win.create_file_dialog(
                    webview.OPEN_DIALOG,
                    directory=initial or "",
                    allow_multiple=False,
                    file_types=("Video Files (*.mp4;*.mkv;*.avi;*.mov;*.webm;*.m4v;*.ts;*.wmv;*.flv)", "All files (*.*)"),
                )
        except Exception as exc:
            log.warning("Native dialog failed: %s", exc)
            return None
        if not res:
            return ""
        if isinstance(res, (list, tuple)):
            return str(res[0]) if res else ""
        return str(res)

    try:
        window = webview.create_window(
            f"{APP_NAME} v{__version__}",
            url,
            width=1440,
            height=920,
            min_size=(1080, 700),
            background_color="#0b0f19",
            text_select=False,
        )
        window_holder["win"] = window
        register_native_picker(picker)

        def _on_closed():
            try:
                req = urllib.request.Request(
                    url + "/api/shutdown", method="POST",
                    headers={"x-shutdown-token": os.environ.get("VKS_SHUTDOWN_TOKEN", "")},
                )
                urllib.request.urlopen(req, timeout=2)
            except Exception:
                pass

        window.events.closed += _on_closed
        icon = _icon_path()
        start_kwargs = {"debug": bool(os.environ.get("VKS_DEBUG"))}
        if icon:
            start_kwargs["icon"] = icon
        try:
            webview.start(**start_kwargs)
        except TypeError:
            start_kwargs.pop("icon", None)
            webview.start(**start_kwargs)
        return True
    except Exception as exc:
        log.warning("Native window failed (%s); falling back to browser", exc)
        register_native_picker(None)
        return False


def main() -> int:
    log_path = setup_logging(filename="desktop.log")
    port = _free_port(int(os.environ.get("VKS_PORT", "8765")))
    url = f"http://127.0.0.1:{port}"
    log.info("%s v%s starting on %s (log: %s)", APP_NAME, __version__, url, log_path)

    _start_server(port)
    if not _wait_for_server(url):
        log.error("Server did not start in time")
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, f"The studio server failed to start.\nSee log: {log_path}",
                                             APP_NAME, 0x10)
        except Exception:
            pass
        return 1

    headless = os.environ.get("VKS_HEADLESS") == "1" or "--headless" in sys.argv
    force_browser = os.environ.get("VKS_BROWSER") == "1" or "--browser" in sys.argv
    if headless:
        print(f"{APP_NAME} is running headless at {url}. Press Ctrl+C to stop.", flush=True)
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        return 0
    if force_browser or not _run_webview(url):
        webbrowser.open(url)
        print(f"{APP_NAME} is running at {url}. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
