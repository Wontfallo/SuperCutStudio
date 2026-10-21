"""Native Win32 startup splash (no Tk, no Qt): a borderless window that shows a bitmap and a status line.

The window lives on its own thread with its own message loop, so it stays responsive while the
server and the WebView2 window start up. ``show_splash`` returns immediately; use ``set_text`` to
update the status line and ``close`` once the main window is visible. On anything other than
Windows, or if anything goes wrong, every method is a silent no-op.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import wintypes

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WM_PAINT = 0x000F
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_ERASEBKGND = 0x0014
WM_APP_TEXT = 0x8000 + 1
SRCCOPY = 0x00CC0020
IMAGE_BITMAP = 0
LR_LOADFROMFILE = 0x0010
LR_CREATEDIBSECTION = 0x2000
TRANSPARENT = 1
DT_LEFT = 0x0
DT_SINGLELINE = 0x20
DT_VCENTER = 0x4
DT_END_ELLIPSIS = 0x8000
SM_CXSCREEN, SM_CYSCREEN = 0, 1
HALFTONE = 4

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
        ("bmBits", wintypes.LPVOID),
    ]


class _Splash:
    def __init__(self, image_path: str, text: str, text_pos: tuple[int, int], text_color: int, bg: int):
        self.image_path = image_path
        self.text = text
        self.text_pos = text_pos
        self.text_color = text_color
        self.bg = bg
        self.hwnd = None
        self.hbitmap = None
        self.hfont = None
        self.bm_w = self.bm_h = 0
        self.scale = 1.0
        self._ready = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="supercut-splash", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)

    # ---- public -------------------------------------------------------------------------------
    def set_text(self, text: str) -> None:
        with self._lock:
            self.text = text
        if self.hwnd and not self._closed:
            try:
                ctypes.windll.user32.PostMessageW(self.hwnd, WM_APP_TEXT, 0, 0)
            except Exception:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.hwnd:
            try:
                ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        self._thread.join(2.0)

    # ---- window thread --------------------------------------------------------------------------
    def _run(self) -> None:
        user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
        try:
            try:
                dpi = user32.GetDpiForSystem()
                self.scale = max(1.0, dpi / 96.0)
            except Exception:
                self.scale = 1.0
            self.hbitmap = user32.LoadImageW(None, self.image_path, IMAGE_BITMAP, 0, 0,
                                             LR_LOADFROMFILE | LR_CREATEDIBSECTION)
            if not self.hbitmap:
                return
            bm = BITMAP()
            gdi32.GetObjectW(self.hbitmap, ctypes.sizeof(BITMAP), ctypes.byref(bm))
            self.bm_w, self.bm_h = bm.bmWidth, bm.bmHeight
            w, h = int(self.bm_w * self.scale), int(self.bm_h * self.scale)

            self._proc = WNDPROC(self._wndproc)  # keep a reference or the callback is collected
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.hbrBackground = gdi32.CreateSolidBrush(self.bg)
            wc.lpszClassName = "SuperCutSplash"
            wc.hCursor = user32.LoadCursorW(None, ctypes.c_wchar_p(32512))
            if not user32.RegisterClassW(ctypes.byref(wc)):
                return
            sx, sy = user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)
            x, y = max(0, (sx - w) // 2), max(0, (sy - h) // 2)
            self.hfont = gdi32.CreateFontW(-int(15 * self.scale), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
            self.hwnd = user32.CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_TOPMOST, "SuperCutSplash", "SuperCut Studio",
                                               WS_POPUP | WS_VISIBLE, x, y, w, h, None, None, hinst, None)
            if not self.hwnd:
                return
            user32.SetWindowPos(self.hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_NOTOPMOST: shown on top once, then normal
            user32.UpdateWindow(self.hwnd)
            self._ready.set()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
        finally:
            self._ready.set()
            try:
                if self.hbitmap:
                    gdi32.DeleteObject(self.hbitmap)
                if self.hfont:
                    gdi32.DeleteObject(self.hfont)
            except Exception:
                pass
            self.hwnd = None

    def _wndproc(self, hwnd, msg, wparam, lparam):
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            try:
                mem = gdi32.CreateCompatibleDC(hdc)
                old = gdi32.SelectObject(mem, self.hbitmap)
                w, h = int(self.bm_w * self.scale), int(self.bm_h * self.scale)
                gdi32.SetStretchBltMode(hdc, HALFTONE)
                gdi32.StretchBlt(hdc, 0, 0, w, h, mem, 0, 0, self.bm_w, self.bm_h, SRCCOPY)
                gdi32.SelectObject(mem, old)
                gdi32.DeleteDC(mem)
                with self._lock:
                    text = self.text
                if text:
                    oldf = gdi32.SelectObject(hdc, self.hfont)
                    gdi32.SetBkMode(hdc, TRANSPARENT)
                    gdi32.SetTextColor(hdc, self.text_color)
                    tx, ty = int(self.text_pos[0] * self.scale), int(self.text_pos[1] * self.scale)
                    rc = wintypes.RECT(tx, ty, w - int(16 * self.scale), ty + int(24 * self.scale))
                    user32.DrawTextW(hdc, text, -1, ctypes.byref(rc), DT_LEFT | DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS)
                    gdi32.SelectObject(hdc, oldf)
            finally:
                user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_APP_TEXT:
            user32.InvalidateRect(hwnd, None, False)
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class _NoSplash:
    def set_text(self, text: str) -> None:  # pragma: no cover - trivial
        pass

    def close(self) -> None:  # pragma: no cover - trivial
        pass


def show_splash(image_path: str | os.PathLike, text: str = "Starting...", text_pos: tuple[int, int] = (212, 278),
                text_color: int = 0x8A8A8A, bg: int = 0x121212):
    """Show the splash bitmap (a .bmp file) centred on the primary screen. Never raises."""
    if sys.platform != "win32" or os.environ.get("VKS_NO_SPLASH") == "1":
        return _NoSplash()
    try:
        path = os.fspath(image_path)
        if not os.path.exists(path):
            return _NoSplash()
        return _Splash(path, text, text_pos, text_color, bg)
    except Exception:
        return _NoSplash()
