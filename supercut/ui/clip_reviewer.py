"""Interactive Clip Reviewer table and curation cockpit for SuperCut Studio Pro."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional
import customtkinter as ctk

from ..models import ClipInterval, VideoJobStats
from .theme import (
    COLOR_BG_CARD,
    COLOR_BG_CARD_HOVER,
    COLOR_BG_INPUT,
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
)


def format_ts(seconds: float) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    secs = int(seconds)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}.{millis:03d}"
    return f"{m:02d}:{s:02d}.{millis:03d}"


class ClipCard(ctk.CTkFrame):
    """Visual card representing a single detected keyword clip."""

    def __init__(
        self,
        master,
        clip: ClipInterval,
        index: int,
        on_toggle: Callable[[ClipInterval, bool], None],
        **kwargs,
    ):
        super().__init__(
            master,
            fg_color=COLOR_BG_CARD,
            border_width=1,
            border_color=COLOR_BORDER,
            corner_radius=8,
            **kwargs,
        )
        self.clip = clip
        self.index = index
        self.on_toggle = on_toggle

        self.grid_columnconfigure(3, weight=1)
        self._build_ui()

    def _build_ui(self):
        # Column 0: Checkbox
        self.var = ctk.BooleanVar(value=self.clip.selected)
        self.cb = ctk.CTkCheckBox(
            self,
            text="",
            variable=self.var,
            width=24,
            checkbox_width=20,
            checkbox_height=20,
            corner_radius=5,
            fg_color=COLOR_CYAN,
            hover_color="#00a3cc",
            command=self._on_check,
        )
        self.cb.grid(row=0, column=0, padx=(10, 4), pady=8, sticky="w")

        # Column 1: Index tag
        ctk.CTkLabel(
            self,
            text=f"#{self.index:03d}",
            font=FONT_CODE_BOLD,
            text_color=COLOR_TEXT_MUTED,
            width=42,
            anchor="w",
        ).grid(row=0, column=1, padx=4, pady=8, sticky="w")

        # Column 2: Keyword Badge
        kw = self.clip.primary_keyword
        kw_frame = ctk.CTkFrame(
            self,
            fg_color="#182738",
            border_color="#0284c7",
            border_width=1,
            corner_radius=6,
        )
        kw_frame.grid(row=0, column=2, padx=4, pady=8, sticky="w")

        ctk.CTkLabel(
            kw_frame,
            text=f"🎯 {kw}",
            font=FONT_BODY_BOLD,
            text_color=COLOR_TEXT_CYAN,
        ).pack(padx=8, pady=2)

        # Column 3: Context dialogue / matched text
        context_text = ""
        if self.clip.keyword_hits:
            hit = self.clip.keyword_hits[0]
            context_text = f'Matched: "{hit.matched_text}"'
        else:
            context_text = "Detected interval"

        # If source video is specified, add it
        src_name = Path(self.clip.source_video).name if self.clip.source_video else ""

        middle_frame = ctk.CTkFrame(self, fg_color="transparent")
        middle_frame.grid(row=0, column=3, padx=8, pady=6, sticky="ew")
        middle_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            middle_frame,
            text=context_text,
            font=FONT_BODY,
            text_color=COLOR_TEXT_WHITE,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        if src_name:
            ctk.CTkLabel(
                middle_frame,
                text=f"From: {src_name}",
                font=FONT_SMALL,
                text_color=COLOR_TEXT_DIM,
                anchor="w",
            ).grid(row=1, column=0, sticky="w")

        # Column 4: Timestamp Pill & Duration
        right_frame = ctk.CTkFrame(self, fg_color="transparent")
        right_frame.grid(row=0, column=4, padx=(8, 12), pady=6, sticky="e")

        ts_str = f"{format_ts(self.clip.start)} ➜ {format_ts(self.clip.end)}"
        ctk.CTkLabel(
            right_frame,
            text=ts_str,
            font=FONT_CODE_BOLD,
            text_color=COLOR_TEXT_MUTED,
            anchor="e",
        ).grid(row=0, column=0, sticky="e")

        dur_str = f"Duration: {self.clip.duration:.2f}s"
        ctk.CTkLabel(
            right_frame,
            text=dur_str,
            font=FONT_SMALL,
            text_color=COLOR_CYAN,
            anchor="e",
        ).grid(row=1, column=0, sticky="e")

        self._update_style()

    def _on_check(self):
        self.clip.selected = self.var.get()
        self._update_style()
        self.on_toggle(self.clip, self.clip.selected)

    def _update_style(self):
        if self.clip.selected:
            self.configure(fg_color=COLOR_BG_CARD, border_color=COLOR_BORDER)
        else:
            self.configure(fg_color="#10131a", border_color="#1c202a")

    def set_selected(self, val: bool):
        self.var.set(val)
        self.clip.selected = val
        self._update_style()


class ClipReviewerWidget(ctk.CTkFrame):
    """Tab 2: Master Interactive Clip Reviewer with curation filters and batch stitching."""

    def __init__(
        self,
        master,
        on_stitch_selected: Optional[Callable[[List[ClipInterval]], None]] = None,
        **kwargs,
    ):
        super().__init__(
            master,
            fg_color="transparent",
            **kwargs,
        )
        self.on_stitch_selected_callback = on_stitch_selected
        self.all_clips: List[ClipInterval] = []
        self.cards: List[ClipCard] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_ui()

    def _build_ui(self):
        # Toolbar Container
        toolbar = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10, border_width=1, border_color=COLOR_BORDER)
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 8))
        toolbar.grid_columnconfigure(4, weight=1)

        # Keyword Filter
        ctk.CTkLabel(toolbar, text="Keyword:", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=0, padx=(12, 4), pady=8, sticky="w"
        )
        self.kw_filter = ctk.CTkOptionMenu(
            toolbar,
            values=["All Keywords"],
            width=130,
            height=28,
            font=FONT_SMALL,
            command=self._apply_filter,
        )
        self.kw_filter.grid(row=0, column=1, padx=(0, 10), pady=8)

        # Video Filter
        ctk.CTkLabel(toolbar, text="Source:", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=2, padx=(4, 4), pady=8, sticky="w"
        )
        self.video_filter = ctk.CTkOptionMenu(
            toolbar,
            values=["All Videos"],
            width=150,
            height=28,
            font=FONT_SMALL,
            command=self._apply_filter,
        )
        self.video_filter.grid(row=0, column=3, padx=(0, 10), pady=8)

        # Batch Selection Buttons
        sel_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        sel_frame.grid(row=0, column=4, sticky="w", padx=6)

        ctk.CTkButton(
            sel_frame,
            text="Select All",
            width=70,
            height=28,
            font=FONT_SMALL,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self.select_all,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            sel_frame,
            text="Deselect All",
            width=75,
            height=28,
            font=FONT_SMALL,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self.deselect_all,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            sel_frame,
            text="Invert",
            width=55,
            height=28,
            font=FONT_SMALL,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self.invert_selection,
        ).pack(side="left", padx=2)

        # Stitch Selected CTA button
        self.stitch_btn = ctk.CTkButton(
            toolbar,
            text="🎬 Stitch Selected (0)",
            height=32,
            font=FONT_BODY_BOLD,
            fg_color=COLOR_GREEN,
            hover_color="#059669",
            text_color="#ffffff",
            corner_radius=8,
            command=self._on_stitch_click,
        )
        self.stitch_btn.grid(row=0, column=5, padx=12, pady=8, sticky="e")

        # Status & Curation Summary Banner
        self.summary_banner = ctk.CTkLabel(
            self,
            text="No clips detected yet. Run 'Scan & Analyze Hits' on Tab 1 to curate clips.",
            font=FONT_SMALL,
            text_color=COLOR_TEXT_DIM,
            anchor="w",
        )
        self.summary_banner.grid(row=2, column=0, padx=16, pady=(4, 6), sticky="w")

        # Scrollable Cards View
        self.scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=COLOR_BG_INPUT,
            corner_radius=10,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 4))
        self.scroll.grid_columnconfigure(0, weight=1)

        self.empty_msg = ctk.CTkLabel(
            self.scroll,
            text="Detected keyword clips will appear here.\nYou will be able to review, include, or exclude clips before rendering.",
            font=FONT_BODY,
            text_color=COLOR_TEXT_DIM,
            justify="center",
        )
        self.empty_msg.pack(pady=60)

    def load_intervals(self, intervals: List[ClipInterval], stats: Optional[List[VideoJobStats]] = None):
        """Populate the reviewer with detected clips."""
        self.all_clips = intervals
        self.cards.clear()

        # Update Filter Menus
        keywords = sorted(set(c.primary_keyword for c in intervals if c.primary_keyword))
        self.kw_filter.configure(values=["All Keywords"] + keywords)
        self.kw_filter.set("All Keywords")

        videos = sorted(set(Path(c.source_video).name for c in intervals if c.source_video))
        self.video_filter.configure(values=["All Videos"] + videos)
        self.video_filter.set("All Videos")

        self._render_cards()
        self._update_stats_summary()

    def _render_cards(self):
        for child in self.scroll.winfo_children():
            child.destroy()

        kw_sel = self.kw_filter.get()
        vid_sel = self.video_filter.get()

        filtered = []
        for c in self.all_clips:
            if kw_sel != "All Keywords" and c.primary_keyword != kw_sel:
                continue
            if vid_sel != "All Videos" and Path(c.source_video).name != vid_sel:
                continue
            filtered.append(c)

        if not filtered:
            msg = "No clips match the active filter." if self.all_clips else "No clips detected yet."
            empty = ctk.CTkLabel(
                self.scroll,
                text=msg,
                font=FONT_BODY,
                text_color=COLOR_TEXT_DIM,
            )
            empty.pack(pady=50)
            return

        for idx, clip in enumerate(filtered, start=1):
            card = ClipCard(
                self.scroll,
                clip=clip,
                index=idx,
                on_toggle=self._on_card_toggled,
            )
            card.pack(fill="x", padx=6, pady=4)
            self.cards.append(card)

    def _on_card_toggled(self, clip: ClipInterval, selected: bool):
        self._update_stats_summary()

    def _apply_filter(self, choice=None):
        self._render_cards()
        self._update_stats_summary()

    def select_all(self):
        for c in self.cards:
            c.set_selected(True)
        self._update_stats_summary()

    def deselect_all(self):
        for c in self.cards:
            c.set_selected(False)
        self._update_stats_summary()

    def invert_selection(self):
        for c in self.cards:
            c.set_selected(not c.clip.selected)
        self._update_stats_summary()

    def _update_stats_summary(self):
        total = len(self.all_clips)
        selected = [c for c in self.all_clips if c.selected]
        sel_count = len(selected)
        sel_dur = sum(c.duration for c in selected)

        m = int(sel_dur) // 60
        s = int(sel_dur) % 60

        if total > 0:
            summary_txt = f"Curation: {sel_count} of {total} clips selected  •  Total Footage: {m:02d}m {s:02d}s"
            self.summary_banner.configure(text=summary_txt, text_color=COLOR_TEXT_WHITE)
            self.stitch_btn.configure(
                text=f"🎬 Stitch Selected ({sel_count})",
                state="normal" if sel_count > 0 else "disabled",
            )
        else:
            self.summary_banner.configure(
                text="No clips detected yet. Run 'Scan & Analyze Hits' on Tab 1 to curate clips.",
                text_color=COLOR_TEXT_DIM,
            )
            self.stitch_btn.configure(text="🎬 Stitch Selected (0)", state="disabled")

    def _on_stitch_click(self):
        selected = [c for c in self.all_clips if c.selected]
        if selected and self.on_stitch_selected_callback:
            self.on_stitch_selected_callback(selected)
