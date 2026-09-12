"""Video selector and directory scanner with real-time media probe inspector."""

from __future__ import annotations

import glob
import os
import threading
from pathlib import Path
from tkinter import filedialog
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from ..models import MediaInfo
from ..video import probe_media
from .theme import (
    COLOR_BG_CARD,
    COLOR_BG_CARD_HOVER,
    COLOR_BG_INPUT,
    COLOR_BORDER,
    COLOR_CYAN,
    COLOR_GREEN,
    COLOR_TEXT_DIM,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_CODE,
    FONT_SECTION,
    FONT_SMALL,
)

VIDEO_EXTS = ["*.mp4", "*.mkv", "*.avi", "*.mov", "*.webm", "*.m4v", "*.ts", "*.wmv", "*.flv"]


def format_seconds_hms(secs: float) -> str:
    s = int(round(secs))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


def format_bytes_human(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024 or unit == "TB":
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return "?"


class MediaScannerWidget(ctk.CTkFrame):
    """Media selector and folder scanner with asynchronous ffprobe inspection."""

    def __init__(
        self,
        master,
        on_path_changed: Optional[Callable[[str, str], None]] = None,
        **kwargs,
    ):
        super().__init__(
            master,
            fg_color=COLOR_BG_CARD,
            border_width=1,
            border_color=COLOR_BORDER,
            corner_radius=10,
            **kwargs,
        )
        self.on_path_changed_callback = on_path_changed
        self.scanned_files: List[str] = []
        self.media_cache: Dict[str, MediaInfo] = {}
        self._scanning = False

        self.grid_columnconfigure(1, weight=1)
        self._build_ui()

    def _build_ui(self):
        # Header / Controls Row
        ctk.CTkLabel(
            self,
            text="Media Source",
            font=FONT_SECTION,
            text_color=COLOR_TEXT_WHITE,
        ).grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")

        self.mode_selector = ctk.CTkSegmentedButton(
            self,
            values=["Folder", "File"],
            font=FONT_SMALL,
            height=26,
            selected_color=COLOR_CYAN,
            selected_hover_color="#00a3cc",
            command=self._on_mode_change,
        )
        self.mode_selector.set("Folder")
        self.mode_selector.grid(row=0, column=2, padx=12, pady=(10, 4), sticky="e")

        # Path Entry Row
        path_frame = ctk.CTkFrame(self, fg_color="transparent")
        path_frame.grid(row=1, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="ew")
        path_frame.grid_columnconfigure(0, weight=1)

        self.path_entry = ctk.CTkEntry(
            path_frame,
            placeholder_text="Select input video file or folder containing videos...",
            height=34,
            font=FONT_BODY,
            fg_color=COLOR_BG_INPUT,
            border_color=COLOR_BORDER,
            border_width=1,
        )
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.path_entry.bind("<FocusOut>", lambda e: self.trigger_scan())
        self.path_entry.bind("<Return>", lambda e: self.trigger_scan())

        self.browse_btn = ctk.CTkButton(
            path_frame,
            text="Browse...",
            width=85,
            height=34,
            font=FONT_BODY_BOLD,
            fg_color="#1e293b",
            hover_color="#334155",
            border_color=COLOR_BORDER,
            border_width=1,
            command=self._on_browse,
        )
        self.browse_btn.grid(row=0, column=1, padx=(0, 6))

        self.rescan_btn = ctk.CTkButton(
            path_frame,
            text="🔄 Scan",
            width=70,
            height=34,
            font=FONT_BODY_BOLD,
            fg_color="#0f2b3e",
            hover_color="#1e405a",
            text_color=COLOR_CYAN,
            border_color=COLOR_CYAN,
            border_width=1,
            command=self.trigger_scan,
        )
        self.rescan_btn.grid(row=0, column=2)

        # Summary Bar
        self.summary_frame = ctk.CTkFrame(self, fg_color=COLOR_BG_INPUT, corner_radius=6, height=30)
        self.summary_frame.grid(row=2, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="ew")
        self.summary_frame.grid_columnconfigure(1, weight=1)

        self.status_icon = ctk.CTkLabel(
            self.summary_frame,
            text="📁",
            font=("Segoe UI", 12),
        )
        self.status_icon.grid(row=0, column=0, padx=(10, 4), pady=4)

        self.summary_label = ctk.CTkLabel(
            self.summary_frame,
            text="No directory or file selected",
            font=FONT_SMALL,
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.summary_label.grid(row=0, column=1, sticky="w", pady=4)

        # Scrollable Detailed File List
        self.file_scroll = ctk.CTkScrollableFrame(
            self,
            height=105,
            fg_color=COLOR_BG_INPUT,
            corner_radius=6,
        )
        self.file_scroll.grid(row=3, column=0, columnspan=3, padx=12, pady=(0, 10), sticky="ew")
        self.file_scroll.grid_columnconfigure(0, weight=1)

        self._empty_file_msg = ctk.CTkLabel(
            self.file_scroll,
            text="Files and probe metadata will appear here after scanning",
            font=FONT_SMALL,
            text_color=COLOR_TEXT_DIM,
        )
        self._empty_file_msg.pack(pady=16)

    def _on_mode_change(self, mode: str):
        if self.on_path_changed_callback:
            self.on_path_changed_callback(self.get_path(), mode)

    def _on_browse(self):
        mode = self.mode_selector.get()
        if mode == "File":
            chosen = filedialog.askopenfilename(
                title="Select Video File",
                filetypes=[
                    ("Video Files", "*.mp4;*.mkv;*.avi;*.mov;*.webm;*.m4v;*.ts;*.wmv;*.flv"),
                    ("All Files", "*.*"),
                ],
            )
        else:
            chosen = filedialog.askdirectory(title="Select Folder with Videos")

        if chosen:
            self.set_path(chosen)
            self.trigger_scan()

    def get_path(self) -> str:
        return self.path_entry.get().strip()

    def get_mode(self) -> str:
        return self.mode_selector.get()

    def set_path(self, path: str, mode: Optional[str] = None):
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, path)
        if mode in ("File", "Folder"):
            self.mode_selector.set(mode)
        elif path and os.path.isfile(path):
            self.mode_selector.set("File")
        elif path and os.path.isdir(path):
            self.mode_selector.set("Folder")
        if self.on_path_changed_callback:
            self.on_path_changed_callback(path, self.mode_selector.get())

    def trigger_scan(self):
        path = self.get_path()
        if not path or not os.path.exists(path):
            self.summary_label.configure(
                text="Path does not exist or is empty",
                text_color=COLOR_TEXT_DIM,
            )
            return

        if self._scanning:
            return

        self._scanning = True
        self.summary_label.configure(
            text="Scanning and inspecting media...",
            text_color=COLOR_CYAN,
        )
        self.rescan_btn.configure(state="disabled")

        thread = threading.Thread(target=self._scan_worker, args=(path,), daemon=True)
        thread.start()

    def _scan_worker(self, path: str):
        files_found = []
        p = Path(path)

        if p.is_file():
            files_found = [str(p)]
        elif p.is_dir():
            for ext in VIDEO_EXTS:
                files_found.extend(glob.glob(os.path.join(str(p), ext)))
            files_found = sorted(set(files_found))
            files_found = [f for f in files_found if not Path(f).name.startswith("FINAL_BATCH_")]

        # Probe each file
        probed: Dict[str, MediaInfo] = {}
        total_duration = 0.0
        total_size = 0

        for f in files_found:
            try:
                total_size += os.path.getsize(f)
            except OSError:
                pass

            info = probe_media(f)
            if info:
                probed[f] = info
                total_duration += info.duration

        try:
            self.after(0, self._render_scan_results, files_found, probed, total_duration, total_size)
        except (RuntimeError, Exception):
            pass

    def _render_scan_results(
        self,
        files: List[str],
        probed: Dict[str, MediaInfo],
        total_dur: float,
        total_size: int,
    ):
        self.scanned_files = files
        self.media_cache = probed
        self._scanning = False
        self.rescan_btn.configure(state="normal")

        # Update Summary
        n = len(files)
        if n == 0:
            self.summary_label.configure(
                text="No supported video files found in selected path",
                text_color="#f87171",
            )
            self.status_icon.configure(text="⚠️")
        else:
            summary_str = f"Found {n} video{'s' if n != 1 else ''}  •  Total: {format_seconds_hms(total_dur)}  •  {format_bytes_human(total_size)}"
            self.summary_label.configure(
                text=summary_str,
                text_color=COLOR_GREEN,
            )
            self.status_icon.configure(text="✔")

        # Clear previous cards
        for child in self.file_scroll.winfo_children():
            child.destroy()

        if not files:
            self._empty_file_msg = ctk.CTkLabel(
                self.file_scroll,
                text="No video files matched (*.mp4, *.mkv, *.mov, etc.)",
                font=FONT_SMALL,
                text_color=COLOR_TEXT_DIM,
            )
            self._empty_file_msg.pack(pady=12)
            return

        for idx, f in enumerate(files, start=1):
            info = probed.get(f)
            name = Path(f).name
            size_str = format_bytes_human(os.path.getsize(f)) if os.path.exists(f) else "?"

            card = ctk.CTkFrame(
                self.file_scroll,
                fg_color=COLOR_BG_CARD_HOVER,
                corner_radius=6,
                border_width=1,
                border_color=COLOR_BORDER,
            )
            card.pack(fill="x", padx=2, pady=2)
            card.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                card,
                text=f"{idx:02d}",
                font=FONT_CODE,
                text_color=COLOR_TEXT_DIM,
                width=24,
            ).grid(row=0, column=0, padx=(8, 4), pady=4)

            ctk.CTkLabel(
                card,
                text=name,
                font=FONT_BODY_BOLD,
                text_color=COLOR_TEXT_WHITE,
                anchor="w",
            ).grid(row=0, column=1, sticky="w", padx=4, pady=4)

            details = []
            if info:
                if info.width and info.height:
                    details.append(f"{info.width}x{info.height}")
                if info.fps:
                    details.append(f"{info.fps:.2f} fps")
                if info.duration:
                    details.append(format_seconds_hms(info.duration))
                if info.video_codec:
                    details.append(info.video_codec.upper())
            details.append(size_str)

            detail_str = "  |  ".join(details)

            ctk.CTkLabel(
                card,
                text=detail_str,
                font=FONT_CODE,
                text_color=COLOR_TEXT_MUTED,
                anchor="e",
            ).grid(row=0, column=2, padx=(4, 10), pady=4, sticky="e")
