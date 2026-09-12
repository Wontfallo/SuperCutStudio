"""SuperCut Studio Pro - Next-Generation Multimodal Desktop Video Keyword Stitcher.

UI/UX Lead Implementation with dark glassmorphic styling, multi-tab workflow,
interactive clip curation, transcript inspection, and real-time performance telemetry.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog
from typing import List, Optional

# Configure Tcl/Tk environment for Windows BEFORE importing any GUI module
for _tcl in [r"C:\Python314\tcl\tcl8.6", os.path.join(sys.base_prefix, "tcl", "tcl8.6")]:
    if os.path.isdir(_tcl):
        os.environ["TCL_LIBRARY"] = _tcl
        break
for _tk in [r"C:\Python314\tcl\tk8.6", os.path.join(sys.base_prefix, "tcl", "tk8.6")]:
    if os.path.isdir(_tk):
        os.environ["TK_LIBRARY"] = _tk
        break

import customtkinter as ctk

from supercut.models import (
    AspectRatioMode,
    ClipInterval,
    EncoderChoice,
    JobConfig,
    TransitionMode,
    VideoJobStats,
)
from supercut.pipeline import JobResult, SuperCutPipeline
from supercut.search import detect_high_energy_moments, discover_top_keywords
from supercut.ui import (

    COLOR_BG_CARD,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BG_ROOT,
    COLOR_BORDER,
    COLOR_CYAN,
    COLOR_GREEN,
    COLOR_PURPLE,
    COLOR_RED,
    COLOR_TEXT_CYAN,
    COLOR_TEXT_DIM,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_CODE,
    FONT_CODE_BOLD,
    FONT_SECTION,
    FONT_SMALL,
    FONT_SUBTITLE,
    FONT_TITLE,
    ClipReviewerWidget,
    KeywordChipView,
    MediaScannerWidget,
    PerformanceConsoleWidget,
    TranscriptInspectorWidget,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")

MODEL_OPTIONS = {
    "CrisperWhisper Turbo (0.8B) [Recommended]": "crisper-turbo",
    "Whisper large-v3": "large-v3",
    "Whisper large-v2": "large-v2",
    "Whisper medium": "medium",
    "Whisper small": "small",
    "Whisper base": "base",
    "Whisper tiny": "tiny",
}
MODEL_LABELS = {v: k for k, v in MODEL_OPTIONS.items()}

ASPECT_OPTIONS = {
    "Source (Preserve Original)": AspectRatioMode.SOURCE,
    "16:9 Widescreen (1920x1080)": AspectRatioMode.LANDSCAPE_16_9,
    "9:16 Vertical Blur (Shorts/TikTok)": AspectRatioMode.VERTICAL_9_16,
    "1:1 Square (Instagram 1080x1080)": AspectRatioMode.SQUARE_1_1,
}
ASPECT_LABELS = {v: k for k, v in ASPECT_OPTIONS.items()}


class SuperCutStudioApp(ctk.CTk):
    """Main Desktop Studio Window for SuperCut Studio Pro."""

    def __init__(self):
        super().__init__()

        # Appearance & Window Configuration
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("SuperCut Studio Pro — Multimodal AI Video Keyword Stitcher")
        self.geometry("1180x860")
        self.minsize(980, 720)
        self.configure(fg_color=COLOR_BG_ROOT)

        # Application State
        self.pipeline: Optional[SuperCutPipeline] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.last_analyzed_stats: List[VideoJobStats] = []
        self._is_running = False
        self._cancelled = False

        # Build Interface
        self._build_header()
        self._build_tabs()
        self._build_dock()

        # Load Persisted Settings
        self._load_settings()

        # Window Close Hook
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Elevate window to top and force focus (prevents hiding behind IDE on Windows 11)
        self.lift()
        self.attributes("-topmost", True)
        self.after(600, lambda: self.attributes("-topmost", False))
        self.focus_force()

    # ----------------------------------------------------------------------
    # 1. Glassmorphic Header & Branding
    # ----------------------------------------------------------------------
    def _build_header(self):
        header = ctk.CTkFrame(
            self,
            fg_color=COLOR_BG_PANEL,
            corner_radius=0,
            border_width=1,
            border_color=COLOR_BORDER,
            height=60,
        )
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        # Title & Subtitle
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=18, pady=8)

        ctk.CTkLabel(
            title_box,
            text="SUPERCUT STUDIO PRO",
            font=FONT_TITLE,
            text_color=COLOR_CYAN,
        ).pack(side="top", anchor="w")

        ctk.CTkLabel(
            title_box,
            text="Multimodal Acoustic Keyword Extraction & Conformance Engine",
            font=FONT_CODE,
            text_color=COLOR_TEXT_MUTED,
        ).pack(side="top", anchor="w")

        # Badges & Status in Top Right
        badge_box = ctk.CTkFrame(header, fg_color="transparent")
        badge_box.pack(side="right", padx=18, pady=10)

        gpu_badge = ctk.CTkFrame(badge_box, fg_color="#182738", border_color=COLOR_CYAN, border_width=1, corner_radius=6)
        gpu_badge.pack(side="right", padx=(8, 0))
        ctk.CTkLabel(gpu_badge, text="⚡ CUDA / NVENC Accelerated", font=FONT_CODE_BOLD, text_color=COLOR_CYAN).pack(
            padx=10, pady=4
        )

        version_badge = ctk.CTkFrame(badge_box, fg_color="#1e2230", corner_radius=6)
        version_badge.pack(side="right")
        ctk.CTkLabel(version_badge, text="v2.5 Next-Gen", font=FONT_CODE, text_color=COLOR_TEXT_MUTED).pack(
            padx=8, pady=4
        )

    # ----------------------------------------------------------------------
    # 2. Master Tabbed Views
    # ----------------------------------------------------------------------
    def _build_tabs(self):
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=COLOR_BG_ROOT,
            segmented_button_fg_color=COLOR_BG_PANEL,
            segmented_button_selected_color=COLOR_CYAN,
            segmented_button_selected_hover_color="#00a3cc",
            segmented_button_unselected_hover_color=COLOR_BG_CARD,
            text_color=COLOR_TEXT_WHITE,
            corner_radius=10,
        )
        self.tabview.pack(fill="both", expand=True, padx=14, pady=(4, 0))

        # Add 4 Core Tabs
        self.tab_studio = self.tabview.add("🎛️ Studio & Search")
        self.tab_curation = self.tabview.add("🎞️ Interactive Clip Reviewer")
        self.tab_transcript = self.tabview.add("📝 Transcript Inspector")
        self.tab_performance = self.tabview.add("📊 Performance & Logs")

        # Setup Tab 1: Studio & Search
        self._setup_studio_tab()

        # Setup Tab 2: Interactive Clip Reviewer
        self.clip_reviewer = ClipReviewerWidget(
            self.tab_curation,
            on_stitch_selected=self._start_stitch_selected,
        )
        self.clip_reviewer.pack(fill="both", expand=True)

        # Setup Tab 3: Full Transcript Inspector
        self.transcript_inspector = TranscriptInspectorWidget(self.tab_transcript)
        self.transcript_inspector.pack(fill="both", expand=True)

        # Setup Tab 4: Performance & Logs
        self.perf_console = PerformanceConsoleWidget(self.tab_performance)
        self.perf_console.pack(fill="both", expand=True)

    # ----------------------------------------------------------------------
    # 3. Tab 1: Studio & Search Implementation
    # ----------------------------------------------------------------------
    def _setup_studio_tab(self):
        # Two-Column Scrollable Grid for Layout
        scroll = ctk.CTkScrollableFrame(self.tab_studio, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=4, pady=4)
        scroll.grid_columnconfigure(0, weight=1)
        scroll.grid_columnconfigure(1, weight=1)

        # Left Column: Media Scanner & Keyword Matrix
        left_col = ctk.CTkFrame(scroll, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)
        left_col.grid_columnconfigure(0, weight=1)

        # 1. Media Scanner
        self.scanner_widget = MediaScannerWidget(left_col)
        self.scanner_widget.pack(fill="x", pady=(0, 10))

        # 2. Keyword Matrix with Chips
        kw_card = ctk.CTkFrame(left_col, fg_color=COLOR_BG_PANEL, corner_radius=10, border_width=1, border_color=COLOR_BORDER)
        kw_card.pack(fill="x", pady=(0, 10))
        kw_card.grid_columnconfigure(0, weight=1)

        kw_header = ctk.CTkFrame(kw_card, fg_color="transparent")
        kw_header.pack(fill="x", padx=12, pady=(10, 4))
        kw_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            kw_header,
            text="Target Keywords (Press Enter or use commas)",
            font=FONT_SECTION,
            text_color=COLOR_TEXT_WHITE,
        ).grid(row=0, column=0, sticky="w")

        self.auto_discover_btn = ctk.CTkButton(
            kw_header,
            text="✨ Auto-Discover Keywords",
            height=26,
            font=FONT_SMALL,
            fg_color="#182738",
            hover_color="#20364d",
            text_color=COLOR_CYAN,
            border_width=1,
            border_color=COLOR_CYAN,
            corner_radius=6,
            command=self.auto_discover_keywords,
        )
        self.auto_discover_btn.grid(row=0, column=1, sticky="e")

        self.kw_chips = KeywordChipView(
            kw_card,
            placeholder="e.g. apple, banana, golden retriever, 'exact phrase'...",
            is_exclude=False,
            on_change=lambda kw: self._save_settings(),
            on_auto_discover=self.auto_discover_keywords,
        )
        self.kw_chips.pack(fill="x", padx=12, pady=(0, 10))


        # 3. Exclude Keywords
        exclude_card = ctk.CTkFrame(left_col, fg_color=COLOR_BG_PANEL, corner_radius=10, border_width=1, border_color=COLOR_BORDER)
        exclude_card.pack(fill="x", pady=(0, 10))
        exclude_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            exclude_card,
            text="Negative Exclude Keywords (Optional)",
            font=FONT_SECTION,
            text_color="#fca5a5",
        ).pack(anchor="w", padx=12, pady=(10, 4))

        self.exclude_chips = KeywordChipView(
            exclude_card,
            placeholder="e.g. bad word, ignore text...",
            is_exclude=True,
            on_change=lambda kw: self._save_settings(),
        )
        self.exclude_chips.pack(fill="x", padx=12, pady=(0, 10))

        # Right Column: Matching Settings, Conformance & Sliders
        right_col = ctk.CTkFrame(scroll, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)
        right_col.grid_columnconfigure(0, weight=1)

        # 1. Search Engine Tuning Card
        search_card = ctk.CTkFrame(right_col, fg_color=COLOR_BG_PANEL, corner_radius=10, border_width=1, border_color=COLOR_BORDER)
        search_card.pack(fill="x", pady=(0, 10))
        search_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            search_card,
            text="Acoustic Matcher & Boundary Tuning",
            font=FONT_SECTION,
            text_color=COLOR_TEXT_WHITE,
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 8), sticky="w")

        # Regex / Case Sensitive / Silence Snapping Switches
        self.regex_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            search_card,
            text="Regex Pattern Mode",
            variable=self.regex_var,
            font=FONT_SMALL,
            progress_color=COLOR_CYAN,
        ).grid(row=1, column=0, padx=12, pady=4, sticky="w")

        self.case_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            search_card,
            text="Case-Sensitive",
            variable=self.case_var,
            font=FONT_SMALL,
            progress_color=COLOR_CYAN,
        ).grid(row=1, column=1, padx=12, pady=4, sticky="w")

        self.silence_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            search_card,
            text="Snap to Acoustic Silence Pauses",
            variable=self.silence_var,
            font=FONT_SMALL,
            progress_color=COLOR_GREEN,
        ).grid(row=2, column=0, padx=12, pady=4, sticky="w")

        self.sentence_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            search_card,
            text="Full Sentence Expansion",
            variable=self.sentence_var,
            font=FONT_SMALL,
            progress_color=COLOR_PURPLE,
        ).grid(row=2, column=1, padx=12, pady=4, sticky="w")

        # Fuzzy Threshold Slider
        fuzzy_frame = ctk.CTkFrame(search_card, fg_color="transparent")
        fuzzy_frame.grid(row=3, column=0, columnspan=2, padx=12, pady=(6, 8), sticky="ew")
        fuzzy_frame.grid_columnconfigure(1, weight=1)

        self.fuzzy_label = ctk.CTkLabel(
            fuzzy_frame,
            text="Fuzzy Threshold: Exact (0.0)",
            font=FONT_SMALL,
            text_color=COLOR_TEXT_MUTED,
            width=160,
            anchor="w",
        )
        self.fuzzy_label.grid(row=0, column=0, sticky="w")

        self.fuzzy_slider = ctk.CTkSlider(
            fuzzy_frame,
            from_=0.0,
            to=0.95,
            number_of_steps=19,
            progress_color=COLOR_CYAN,
            command=self._on_fuzzy_change,
        )
        self.fuzzy_slider.set(0.0)
        self.fuzzy_slider.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # Padding Sliders (Pre-Pad and Post-Pad)
        pad_frame = ctk.CTkFrame(search_card, fg_color="transparent")
        pad_frame.grid(row=4, column=0, columnspan=2, padx=12, pady=(2, 10), sticky="ew")
        pad_frame.grid_columnconfigure(1, weight=1)
        pad_frame.grid_columnconfigure(3, weight=1)

        self.prepad_lbl = ctk.CTkLabel(pad_frame, text="Pre-Pad: 1.0s", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED, width=80, anchor="w")
        self.prepad_lbl.grid(row=0, column=0, sticky="w")

        self.prepad_slider = ctk.CTkSlider(
            pad_frame,
            from_=0.0,
            to=10.0,
            number_of_steps=100,
            progress_color=COLOR_CYAN,
            command=self._on_prepad_change,
        )
        self.prepad_slider.set(1.0)
        self.prepad_slider.grid(row=0, column=1, sticky="ew", padx=(4, 12))

        self.postpad_lbl = ctk.CTkLabel(pad_frame, text="Post-Pad: 1.0s", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED, width=85, anchor="w")
        self.postpad_lbl.grid(row=0, column=2, sticky="w")

        self.postpad_slider = ctk.CTkSlider(
            pad_frame,
            from_=0.0,
            to=10.0,
            number_of_steps=100,
            progress_color=COLOR_CYAN,
            command=self._on_postpad_change,
        )
        self.postpad_slider.set(1.0)
        self.postpad_slider.grid(row=0, column=3, sticky="ew", padx=(4, 0))

        # 2. Video Conformance & Subtitle Styling Card
        conform_card = ctk.CTkFrame(right_col, fg_color=COLOR_BG_PANEL, corner_radius=10, border_width=1, border_color=COLOR_BORDER)
        conform_card.pack(fill="x", pady=(0, 10))
        conform_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            conform_card,
            text="Video Conformance & Subtitles",
            font=FONT_SECTION,
            text_color=COLOR_TEXT_WHITE,
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 8), sticky="w")

        # Aspect Ratio Selector
        ctk.CTkLabel(conform_card, text="Aspect Ratio:", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED).grid(
            row=1, column=0, padx=12, pady=4, sticky="w"
        )
        self.aspect_combo = ctk.CTkOptionMenu(
            conform_card,
            values=list(ASPECT_OPTIONS.keys()),
            font=FONT_SMALL,
            height=28,
        )
        self.aspect_combo.set("Source (Preserve Original)")
        self.aspect_combo.grid(row=1, column=1, padx=(0, 12), pady=4, sticky="ew")

        # Model Selector
        ctk.CTkLabel(conform_card, text="ASR Engine:", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED).grid(
            row=2, column=0, padx=12, pady=4, sticky="w"
        )
        self.model_combo = ctk.CTkOptionMenu(
            conform_card,
            values=list(MODEL_OPTIONS.keys()),
            font=FONT_SMALL,
            height=28,
        )
        self.model_combo.set("CrisperWhisper Turbo (0.8B) [Recommended]")
        self.model_combo.grid(row=2, column=1, padx=(0, 12), pady=4, sticky="ew")

        # Subtitle Burn-In Toggle & Style
        self.burn_sub_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            conform_card,
            text="Burn Subtitles into Video",
            variable=self.burn_sub_var,
            font=FONT_SMALL,
            progress_color=COLOR_GREEN,
        ).grid(row=3, column=0, padx=12, pady=(6, 10), sticky="w")

        sub_style_frame = ctk.CTkFrame(conform_card, fg_color="transparent")
        sub_style_frame.grid(row=3, column=1, padx=(0, 12), pady=(6, 10), sticky="ew")
        sub_style_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sub_style_frame, text="Style:", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=0, padx=(0, 4), sticky="w"
        )
        self.sub_style_combo = ctk.CTkOptionMenu(
            sub_style_frame,
            values=["karaoke_amber (Glowing Karaoke)", "clean_white (Minimalist)"],
            font=FONT_SMALL,
            height=26,
        )
        self.sub_style_combo.set("karaoke_amber (Glowing Karaoke)")
        self.sub_style_combo.grid(row=0, column=1, sticky="ew")

        # 3. Output Configuration & NLE Timeline Exports Card
        out_card = ctk.CTkFrame(right_col, fg_color=COLOR_BG_PANEL, corner_radius=10, border_width=1, border_color=COLOR_BORDER)
        out_card.pack(fill="x", pady=(0, 10))
        out_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            out_card,
            text="Outputs & Professional NLE Exports",
            font=FONT_SECTION,
            text_color=COLOR_TEXT_WHITE,
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 8), sticky="w")

        # Output Dir Row
        ctk.CTkLabel(out_card, text="Output Dir:", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED).grid(
            row=1, column=0, padx=12, pady=4, sticky="w"
        )
        out_dir_frame = ctk.CTkFrame(out_card, fg_color="transparent")
        out_dir_frame.grid(row=1, column=1, padx=(0, 12), pady=4, sticky="ew")
        out_dir_frame.grid_columnconfigure(0, weight=1)

        self.out_dir_entry = ctk.CTkEntry(
            out_dir_frame,
            font=FONT_CODE,
            height=28,
            fg_color=COLOR_BG_INPUT,
            border_color=COLOR_BORDER,
            border_width=1,
        )
        self.out_dir_entry.insert(0, "output")
        self.out_dir_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            out_dir_frame,
            text="Browse",
            width=65,
            height=28,
            font=FONT_SMALL,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self._on_browse_output,
        ).grid(row=0, column=1)

        # Output Selection Checkboxes
        opts_box = ctk.CTkFrame(out_card, fg_color="transparent")
        opts_box.grid(row=2, column=0, columnspan=2, padx=12, pady=6, sticky="ew")
        opts_box.grid_columnconfigure(0, weight=1)
        opts_box.grid_columnconfigure(1, weight=1)
        opts_box.grid_columnconfigure(2, weight=1)

        self.out_clips_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts_box, text="Individual Clips", variable=self.out_clips_var, font=FONT_SMALL).grid(
            row=0, column=0, sticky="w", pady=2
        )

        self.out_stitched_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(opts_box, text="Per-Video Stitched", variable=self.out_stitched_var, font=FONT_SMALL).grid(
            row=0, column=1, sticky="w", pady=2
        )

        self.out_batch_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(opts_box, text="Combined Master Batch", variable=self.out_batch_var, font=FONT_SMALL).grid(
            row=0, column=2, sticky="w", pady=2
        )

        # NLE & Subtitle Exports Row
        nle_box = ctk.CTkFrame(out_card, fg_color="transparent")
        nle_box.grid(row=3, column=0, columnspan=2, padx=12, pady=(2, 6), sticky="ew")
        nle_box.grid_columnconfigure(0, weight=1)
        nle_box.grid_columnconfigure(1, weight=1)
        nle_box.grid_columnconfigure(2, weight=1)

        self.export_srt_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(nle_box, text="Export .SRT Subtitles", variable=self.export_srt_var, font=FONT_SMALL).grid(
            row=0, column=0, sticky="w", pady=2
        )

        self.export_fcpxml_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(nle_box, text="Export FCPXML (Premiere)", variable=self.export_fcpxml_var, font=FONT_SMALL).grid(
            row=0, column=1, sticky="w", pady=2
        )

        self.export_edl_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(nle_box, text="Export EDL (DaVinci)", variable=self.export_edl_var, font=FONT_SMALL).grid(
            row=0, column=2, sticky="w", pady=2
        )

        # Open Folder when Done
        self.open_folder_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            out_card,
            text="Open destination directory when processing completes",
            variable=self.open_folder_var,
            font=FONT_SMALL,
        ).grid(row=4, column=0, columnspan=2, padx=12, pady=(4, 10), sticky="w")

    # ----------------------------------------------------------------------
    # 4. Master Bottom Action Dock (Visible across all tabs!)
    # ----------------------------------------------------------------------
    def _build_dock(self):
        dock = ctk.CTkFrame(
            self,
            fg_color=COLOR_BG_PANEL,
            corner_radius=0,
            border_width=1,
            border_color=COLOR_BORDER,
            height=78,
        )
        dock.pack(fill="x", side="bottom")
        dock.pack_propagate(False)
        dock.grid_columnconfigure(0, weight=1)

        # Progress & Status Bar Row
        prog_row = ctk.CTkFrame(dock, fg_color="transparent")
        prog_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(6, 2))
        prog_row.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(
            prog_row,
            height=8,
            progress_color=COLOR_CYAN,
            corner_radius=4,
        )
        self.progress_bar.set(0.0)
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.pct_label = ctk.CTkLabel(prog_row, text="0%", font=FONT_CODE_BOLD, text_color=COLOR_TEXT_MUTED, width=45)
        self.pct_label.grid(row=0, column=1)

        # Status Message & Master Actions Row
        action_row = ctk.CTkFrame(dock, fg_color="transparent")
        action_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        action_row.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            action_row,
            text="Ready  •  Configure inputs and click 'Scan & Analyze Hits' to curate, or 'Start Full SuperCut'",
            font=FONT_SMALL,
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w", pady=2)

        # Action CTA Buttons
        btn_box = ctk.CTkFrame(action_row, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        self.scan_analyze_btn = ctk.CTkButton(
            btn_box,
            text="🔍 Scan & Analyze Hits",
            font=FONT_BODY_BOLD,
            height=34,
            fg_color="#182738",
            hover_color="#20364d",
            border_color=COLOR_CYAN,
            border_width=1,
            text_color=COLOR_CYAN,
            corner_radius=8,
            command=self.start_analyze_job,
        )
        self.scan_analyze_btn.pack(side="left", padx=(0, 8))

        self.run_full_btn = ctk.CTkButton(
            btn_box,
            text="⚡ Start Full SuperCut",
            font=FONT_BODY_BOLD,
            height=34,
            fg_color=COLOR_GREEN,
            hover_color="#059669",
            text_color="#ffffff",
            corner_radius=8,
            command=self.start_full_job,
        )
        self.run_full_btn.pack(side="left", padx=(0, 8))

        self.stitch_folder_btn = ctk.CTkButton(
            btn_box,
            text="🎬 Stitch Folder Only",
            font=FONT_BODY_BOLD,
            height=34,
            fg_color="#1e293b",
            hover_color="#334155",
            text_color=COLOR_TEXT_WHITE,
            corner_radius=8,
            command=self.start_stitch_only_job,
        )
        self.stitch_folder_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            btn_box,
            text="⏹ Stop",
            font=FONT_BODY_BOLD,
            height=34,
            width=80,
            fg_color="#451216",
            hover_color="#63171d",
            text_color="#fca5a5",
            corner_radius=8,
            state="disabled",
            command=self.stop_job,
        )
        self.stop_btn.pack(side="left")

    # ----------------------------------------------------------------------
    # Sliders & UI Listeners
    # ----------------------------------------------------------------------
    def _on_fuzzy_change(self, val: float):
        if val <= 0.01:
            self.fuzzy_label.configure(text="Fuzzy Threshold: Exact (0.0)")
        else:
            self.fuzzy_label.configure(text=f"Fuzzy Threshold: {val:.2f}")

    def _on_prepad_change(self, val: float):
        self.prepad_lbl.configure(text=f"Pre-Pad: {val:.1f}s")

    def _on_postpad_change(self, val: float):
        self.postpad_lbl.configure(text=f"Post-Pad: {val:.1f}s")

    def _on_browse_output(self):
        chosen = filedialog.askdirectory(title="Select Output Directory")
        if chosen:
            self.out_dir_entry.delete(0, "end")
            self.out_dir_entry.insert(0, chosen)
            self._save_settings()

    # ----------------------------------------------------------------------
    # 5. Settings Persistence (settings.json)
    # ----------------------------------------------------------------------
    def _load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return

        try:
            if "input_path" in data and data["input_path"]:
                self.scanner_widget.set_path(data["input_path"], data.get("input_mode"))
                self.scanner_widget.trigger_scan()

            if "keywords" in data:
                self.kw_chips.set_keywords(data["keywords"])

            if "exclude_keywords" in data:
                self.exclude_chips.set_keywords(data["exclude_keywords"])

            if "output_dir" in data:
                self.out_dir_entry.delete(0, "end")
                self.out_dir_entry.insert(0, data["output_dir"])

            if "pre_pad" in data:
                val = float(data["pre_pad"])
                self.prepad_slider.set(val)
                self.prepad_lbl.configure(text=f"Pre-Pad: {val:.1f}s")
            elif "pad" in data:
                val = float(data["pad"])
                self.prepad_slider.set(val)
                self.postpad_slider.set(val)
                self.prepad_lbl.configure(text=f"Pre-Pad: {val:.1f}s")
                self.postpad_lbl.configure(text=f"Post-Pad: {val:.1f}s")

            if "post_pad" in data:
                val = float(data["post_pad"])
                self.postpad_slider.set(val)
                self.postpad_lbl.configure(text=f"Post-Pad: {val:.1f}s")

            if "model" in data:
                m = data["model"]
                if m in MODEL_LABELS:
                    self.model_combo.set(MODEL_LABELS[m])

            if "aspect_ratio" in data:
                a = data["aspect_ratio"]
                if a in ASPECT_LABELS:
                    self.aspect_combo.set(ASPECT_LABELS[a])

            if "burn_subtitles" in data:
                self.burn_sub_var.set(bool(data["burn_subtitles"]))

            if "subtitle_style" in data:
                s = data["subtitle_style"]
                if "clean" in s.lower():
                    self.sub_style_combo.set("clean_white (Minimalist)")
                else:
                    self.sub_style_combo.set("karaoke_amber (Glowing Karaoke)")

            if "export_fcpxml" in data:
                self.export_fcpxml_var.set(bool(data["export_fcpxml"]))

            if "export_edl" in data:
                self.export_edl_var.set(bool(data["export_edl"]))

            if "export_srt" in data:
                self.export_srt_var.set(bool(data["export_srt"]))

            if "out_clips" in data:
                self.out_clips_var.set(bool(data["out_clips"]))

            if "out_stitched" in data:
                self.out_stitched_var.set(bool(data["out_stitched"]))

            if "out_batch" in data:
                self.out_batch_var.set(bool(data["out_batch"]))

            if "use_regex" in data:
                self.regex_var.set(bool(data["use_regex"]))

            if "case_sensitive" in data:
                self.case_var.set(bool(data["case_sensitive"]))

            if "fuzzy_threshold" in data:
                f_val = float(data["fuzzy_threshold"])
                self.fuzzy_slider.set(f_val)
                self._on_fuzzy_change(f_val)

            if "snap_to_silence" in data:
                self.silence_var.set(bool(data["snap_to_silence"]))

            if "sentence_mode" in data:
                self.sentence_var.set(bool(data["sentence_mode"]))

            if "open_folder" in data:
                self.open_folder_var.set(bool(data["open_folder"]))

        except Exception as e:
            print(f"Notice: partial settings load: {e}")

    def _save_settings(self):
        settings = {
            "input_path": self.scanner_widget.get_path(),
            "input_mode": self.scanner_widget.get_mode(),
            "keywords": self.kw_chips.get_keywords(),
            "exclude_keywords": self.exclude_chips.get_keywords(),
            "output_dir": self.out_dir_entry.get().strip() or "output",
            "pre_pad": self.prepad_slider.get(),
            "post_pad": self.postpad_slider.get(),
            "model": MODEL_OPTIONS.get(self.model_combo.get(), "crisper-turbo"),
            "aspect_ratio": ASPECT_OPTIONS.get(self.aspect_combo.get(), AspectRatioMode.SOURCE).value,
            "burn_subtitles": self.burn_sub_var.get(),
            "subtitle_style": "clean_white" if "clean" in self.sub_style_combo.get() else "karaoke_amber",
            "export_fcpxml": self.export_fcpxml_var.get(),
            "export_edl": self.export_edl_var.get(),
            "export_srt": self.export_srt_var.get(),
            "out_clips": self.out_clips_var.get(),
            "out_stitched": self.out_stitched_var.get(),
            "out_batch": self.out_batch_var.get(),
            "use_regex": self.regex_var.get(),
            "fuzzy_threshold": self.fuzzy_slider.get(),
            "case_sensitive": self.case_var.get(),
            "snap_to_silence": self.silence_var.get(),
            "sentence_mode": self.sentence_var.get(),
            "open_folder": self.open_folder_var.get(),
        }

        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
                json.dump(settings, handle, indent=2)
        except OSError:
            pass

    def _on_close(self):
        self._save_settings()
        self.stop_job()
        self.destroy()

    # ----------------------------------------------------------------------
    # 6. Job Execution & Pipeline Management (Thread-Safe)
    # ----------------------------------------------------------------------
    def _build_config(self) -> JobConfig:
        return JobConfig(
            keywords=self.kw_chips.get_keywords(),
            exclude_keywords=self.exclude_chips.get_keywords(),
            case_sensitive=self.case_var.get(),
            fuzzy_threshold=self.fuzzy_slider.get(),
            use_regex=self.regex_var.get(),
            pre_pad=self.prepad_slider.get(),
            post_pad=self.postpad_slider.get(),
            snap_to_silence=self.silence_var.get(),
            sentence_mode=self.sentence_var.get(),
            model=MODEL_OPTIONS.get(self.model_combo.get(), "crisper-turbo"),
            aspect_ratio=ASPECT_OPTIONS.get(self.aspect_combo.get(), AspectRatioMode.SOURCE),
            burn_subtitles=self.burn_sub_var.get(),
            subtitle_style="clean_white" if "clean" in self.sub_style_combo.get() else "karaoke_amber",
            export_srt=self.export_srt_var.get(),
            export_fcpxml=self.export_fcpxml_var.get(),
            export_edl=self.export_edl_var.get(),
            save_individual_clips=self.out_clips_var.get(),
            save_per_video_stitched=self.out_stitched_var.get(),
            save_batch_stitched=self.out_batch_var.get(),
            output_dir=self.out_dir_entry.get().strip() or "output",
            open_folder_when_done=self.open_folder_var.get(),
        )

    def _set_ui_running(self, running: bool, status_text: str = ""):
        self._is_running = running
        if running:
            self.scan_analyze_btn.configure(state="disabled")
            self.run_full_btn.configure(state="disabled")
            self.stitch_folder_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.progress_bar.set(0.0)
            self.pct_label.configure(text="0%")
            if status_text:
                self.status_label.configure(text=status_text, text_color=COLOR_CYAN)
        else:
            self.scan_analyze_btn.configure(state="normal")
            self.run_full_btn.configure(state="normal")
            self.stitch_folder_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            if status_text:
                self.status_label.configure(text=status_text, text_color=COLOR_TEXT_WHITE)

    def _safe_progress(self, pct: float, msg: str):
        def _update():
            self.progress_bar.set(pct / 100.0)
            self.pct_label.configure(text=f"{pct:.0f}%")
            self.status_label.configure(text=msg)
            self.perf_console.update_progress(pct, msg)

        self.after(0, _update)

    # ----------------------------------------------------------------------
    # Automatic Keyword & Topic Discovery
    # ----------------------------------------------------------------------
    def auto_discover_keywords(self):
        """Automatically discover top keywords and punchy phrases from transcripts."""
        # 1. If we already have transcripts from analyzed stats
        cached_transcripts = [s.transcript for s in self.last_analyzed_stats if s.transcript]
        if cached_transcripts:
            all_discovered: Dict[str, float] = {}
            for tr in cached_transcripts:
                for kw, score in discover_top_keywords(tr, top_n=15):
                    all_discovered[kw] = max(all_discovered.get(kw, 0.0), score)

            if all_discovered:
                top_kws = [k for k, _ in sorted(all_discovered.items(), key=lambda x: -x[1])[:15]]
                for kw in top_kws:
                    self.kw_chips.add_keyword(kw)
                self._save_settings()
                self.status_label.configure(
                    text=f"Auto-Discovered {len(top_kws)} Keywords: {', '.join(top_kws[:5])}...",
                    text_color=COLOR_GREEN,
                )
                self.perf_console.log(f"Auto-discovered {len(top_kws)} keywords from cached transcripts: {', '.join(top_kws)}")
                return

        # 2. Check if media path is selected in the scanner
        input_path = self.scanner_widget.get_path()
        if not input_path or not os.path.exists(input_path):
            self.status_label.configure(
                text="Please select a video file or folder before auto-discovering keywords.",
                text_color=COLOR_RED,
            )
            return

        self._save_settings()
        self._cancelled = False
        config = self._build_config()

        self._set_ui_running(True, "Transcribing media to auto-discover keywords...")
        self.perf_console.start_job(total_duration=sum(i.duration for i in self.scanner_widget.media_cache.values()))
        self.perf_console.log(f"=== Auto-Discovering Keywords from: {input_path} ===")

        self.pipeline = SuperCutPipeline(config, progress_callback=self._safe_progress)

        def worker():
            try:
                from supercut.asr import create_transcriber, transcribe_cached
                from supercut.audio import extract_audio_for_asr
                from supercut.video import probe_media

                video_paths = self.pipeline.scan_video_inputs(input_path)
                if not video_paths:
                    raise FileNotFoundError(f"No video files found in '{input_path}'")

                transcriber = create_transcriber(
                    model_name=config.model,
                    device=config.device,
                    compute_type=config.compute_type,
                )

                all_discovered: Dict[str, float] = {}
                discovered_stats: List[VideoJobStats] = []
                n_vids = len(video_paths)

                for idx, vp in enumerate(video_paths, start=1):
                    if self._cancelled:
                        break
                    base_pct = ((idx - 1) / n_vids) * 90.0
                    self._safe_progress(base_pct, f"[{idx}/{n_vids}] Extracting audio from {Path(vp).name}")

                    info = probe_media(vp)
                    wav_path = extract_audio_for_asr(vp)
                    if not wav_path:
                        continue
                    try:
                        self._safe_progress(base_pct + 10.0, f"[{idx}/{n_vids}] Transcribing {Path(vp).name}")
                        tr = transcribe_cached(
                            transcriber=transcriber,
                            audio_path=wav_path,
                            media_path=vp,
                            fallback_duration=info.duration if info else 0.0,
                            cache=self.pipeline.transcript_cache,
                        )
                        stat = VideoJobStats(path=vp, info=info, transcript=tr)
                        discovered_stats.append(stat)

                        for kw, score in discover_top_keywords(tr, top_n=15):
                            all_discovered[kw] = max(all_discovered.get(kw, 0.0), score)
                    finally:
                        if os.path.exists(wav_path):
                            try:
                                os.remove(wav_path)
                            except OSError:
                                pass

                if all_discovered:
                    top_kws = [k for k, _ in sorted(all_discovered.items(), key=lambda x: -x[1])[:15]]
                    def _update_ui():
                        self.last_analyzed_stats = discovered_stats
                        for kw in top_kws:
                            self.kw_chips.add_keyword(kw)
                        self._save_settings()
                        self._set_ui_running(False, f"Auto-Discovered {len(top_kws)} Keywords ✔")
                        self.perf_console.stop_job()
                        self.perf_console.log(f"Auto-discovered top keywords: {', '.join(top_kws)}")
                    self.after(0, _update_ui)
                else:
                    self.after(0, lambda: self._set_ui_running(False, "No keywords discovered in speech."))
            except Exception as e:
                self.after(0, self._on_job_error, f"Keyword Discovery Error: {e}")

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    # ----------------------------------------------------------------------
    # Mode A: Scan & Analyze Hits (Phase 1 Only -> Prepares Reviewer Tab)
    # ----------------------------------------------------------------------
    def start_analyze_job(self):

        input_path = self.scanner_widget.get_path()
        if not input_path or not os.path.exists(input_path):
            self.status_label.configure(text="Error: Valid input path is required.", text_color=COLOR_RED)
            return

        keywords = self.kw_chips.get_keywords()
        if not keywords:
            self.status_label.configure(text="Error: Enter at least one keyword in the Matrix.", text_color=COLOR_RED)
            return

        self._save_settings()
        self._cancelled = False
        config = self._build_config()

        self._set_ui_running(True, "Starting Audio & Keyword Analysis...")
        self.perf_console.start_job(total_duration=sum(i.duration for i in self.scanner_widget.media_cache.values()))
        self.perf_console.log(f"=== Starting Analysis Job on: {input_path} ===")
        self.perf_console.log(f"Keywords: {', '.join(keywords)}")

        self.pipeline = SuperCutPipeline(config, progress_callback=self._safe_progress)

        def worker():
            try:
                stats = self.pipeline.analyze(input_path)
                self.last_analyzed_stats = stats
                self.after(0, self._on_analyze_finished, stats)
            except Exception as e:
                self.after(0, self._on_job_error, str(e))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _on_analyze_finished(self, stats: List[VideoJobStats]):
        self._set_ui_running(False, "Analysis Complete ✔ Check Tab 2 to Curate Clips")
        self.perf_console.stop_job()

        all_clips: List[ClipInterval] = []
        for s in stats:
            all_clips.extend(s.intervals)

        total_hits = sum(s.total_hits for s in stats)
        self.perf_console.update_hits_count(total_hits)
        self.perf_console.log(f"=== Analysis Completed: Found {total_hits} keyword hits across {len(stats)} media files ===")

        # Populate Tab 2: Clip Reviewer
        self.clip_reviewer.load_intervals(all_clips, stats)

        # Populate Tab 3: Full Transcript Inspector
        self.transcript_inspector.load_transcripts(stats, self.kw_chips.get_keywords())

        # Switch to Tab 2 automatically for interactive curation
        self.tabview.set("🎞️ Interactive Clip Reviewer")

    # ----------------------------------------------------------------------
    # Mode B: Stitch Selected Clips (Triggered from Reviewer Tab)
    # ----------------------------------------------------------------------
    def _start_stitch_selected(self, selected_clips: List[ClipInterval]):
        if not selected_clips:
            self.status_label.configure(text="No clips selected to stitch.", text_color=COLOR_RED)
            return

        self._save_settings()
        self._cancelled = False
        config = self._build_config()

        self._set_ui_running(True, f"Stitching {len(selected_clips)} Curated Clips...")
        tot_dur = sum(c.duration for c in selected_clips)
        self.perf_console.start_job(total_duration=tot_dur)
        self.perf_console.log(f"=== Rendering & Stitching {len(selected_clips)} Selected Clips ({tot_dur:.1f}s total) ===")

        self.pipeline = SuperCutPipeline(config, progress_callback=self._safe_progress)

        def worker():
            try:
                result = self.pipeline.render_clips_and_stitch(self.last_analyzed_stats)
                self.after(0, self._on_render_finished, result)
            except Exception as e:
                self.after(0, self._on_job_error, str(e))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    # ----------------------------------------------------------------------
    # Mode C: Full SuperCut (End-to-End Analyze + Stitch)
    # ----------------------------------------------------------------------
    def start_full_job(self):
        input_path = self.scanner_widget.get_path()
        if not input_path or not os.path.exists(input_path):
            self.status_label.configure(text="Error: Valid input path is required.", text_color=COLOR_RED)
            return

        keywords = self.kw_chips.get_keywords()
        if not keywords:
            self.status_label.configure(text="Error: Enter at least one keyword in the Matrix.", text_color=COLOR_RED)
            return

        self._save_settings()
        self._cancelled = False
        config = self._build_config()

        self._set_ui_running(True, "Running End-to-End SuperCut Pipeline...")
        self.perf_console.start_job(total_duration=sum(i.duration for i in self.scanner_widget.media_cache.values()))
        self.perf_console.log(f"=== Starting Full SuperCut Job on: {input_path} ===")

        self.pipeline = SuperCutPipeline(config, progress_callback=self._safe_progress)

        def worker():
            try:
                # Analyze first
                stats = self.pipeline.analyze(input_path)
                self.last_analyzed_stats = stats

                # Render & Stitch
                result = self.pipeline.render_clips_and_stitch(stats)
                self.after(0, self._on_full_job_finished, stats, result)
            except Exception as e:
                self.after(0, self._on_job_error, str(e))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _on_full_job_finished(self, stats: List[VideoJobStats], result: JobResult):
        self._on_analyze_finished(stats)
        self._on_render_finished(result)

    def _on_render_finished(self, result: JobResult):
        self._set_ui_running(False, "Stitching Finished Successfully! ✔")
        self.perf_console.stop_job()
        self.perf_console.log("=== Job Completed Successfully! ===")
        if result.batch_output_file:
            self.perf_console.log(f"Master Batch Supercut: {result.batch_output_file}")
        for sf in result.stitched_files:
            self.perf_console.log(f"Stitched Highlights: {sf}")
        self.progress_bar.set(1.0)
        self.pct_label.configure(text="100%")

    # ----------------------------------------------------------------------
    # Mode D: Stitch Folder Only (No Transcription)
    # ----------------------------------------------------------------------
    def start_stitch_only_job(self):
        input_path = self.scanner_widget.get_path()
        if not input_path or not os.path.exists(input_path):
            self.status_label.configure(text="Error: Valid folder path is required.", text_color=COLOR_RED)
            return

        self._save_settings()
        self._cancelled = False
        config = self._build_config()

        self._set_ui_running(True, "Concatenating Folder Videos...")
        self.perf_console.start_job()
        self.perf_console.log(f"=== Stitching Folder Clips Directly: {input_path} ===")

        self.pipeline = SuperCutPipeline(config, progress_callback=self._safe_progress)

        def worker():
            try:
                files = self.pipeline.scan_video_inputs(input_path)
                if not files:
                    raise FileNotFoundError("No video files found in folder.")
                from supercut.video import stitch_master_batch

                out = stitch_master_batch(
                    stitched_files=files,
                    output_dir=config.output_dir,
                    config=config,
                    progress_callback=self._safe_progress,
                )
                self.after(0, self._on_stitch_folder_finished, out)
            except Exception as e:
                self.after(0, self._on_job_error, str(e))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _on_stitch_folder_finished(self, out_file: Optional[str]):
        self._set_ui_running(False, "Folder Stitch Completed ✔")
        self.perf_console.stop_job()
        if out_file:
            self.perf_console.log(f"Output: {out_file}")
            if self.open_folder_var.get():
                try:
                    if os.name == "nt":
                        os.startfile(os.path.abspath(self.out_dir_entry.get().strip()))
                except Exception:
                    pass
        self.progress_bar.set(1.0)
        self.pct_label.configure(text="100%")

    # ----------------------------------------------------------------------
    # Clean Stop & Error Handling
    # ----------------------------------------------------------------------
    def stop_job(self):
        if not self._is_running:
            return
        self._cancelled = True
        self.status_label.configure(text="Cancelling processing...", text_color=COLOR_RED)
        self.perf_console.log("WARNING: Job cancellation requested by user...")

        if self.pipeline:
            self.pipeline.cancel()

        self._set_ui_running(False, "Cancelled by user")
        self.perf_console.stop_job()

    def _on_job_error(self, err_msg: str):
        self._set_ui_running(False, f"Job Failed: {err_msg}")
        self.perf_console.stop_job()
        self.perf_console.log(f"ERROR: {err_msg}")


def main():
    """Launch SuperCut Studio Pro Desktop Application."""
    print("[INFO] Starting SuperCut Studio Pro Desktop Window...")
    try:
        app = SuperCutStudioApp()
        print("[INFO] Desktop window initialized successfully. Entering main event loop...")
        app.mainloop()
        print("[INFO] Desktop window closed cleanly.")
    except Exception as e:
        import traceback
        print(f"\n[FATAL ERROR] SuperCut Studio Pro crashed: {e}\n")
        traceback.print_exc()


if __name__ == "__main__":
    main()
