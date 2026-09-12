"""Searchable Full Transcript Inspector with dynamic keyword highlighting."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import Dict, List, Optional
import customtkinter as ctk

from ..models import Transcript, VideoJobStats
from .theme import (
    COLOR_BG_CARD,
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
    FONT_SMALL,
)


def format_ts_clean(seconds: float) -> str:
    secs = int(seconds)
    m = secs // 60
    s = secs % 60
    return f"{m:02d}:{s:02d}"


class TranscriptInspectorWidget(ctk.CTkFrame):
    """Tab 3: Full Transcript Inspector with search filtering and keyword highlights."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.transcripts_map: Dict[str, Transcript] = {}
        self.active_keywords: List[str] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_ui()

    def _build_ui(self):
        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10, border_width=1, border_color=COLOR_BORDER)
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 8))
        toolbar.grid_columnconfigure(3, weight=1)

        # Video Selector
        ctk.CTkLabel(toolbar, text="Video:", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=0, padx=(12, 4), pady=8, sticky="w"
        )
        self.video_combo = ctk.CTkOptionMenu(
            toolbar,
            values=["No Transcripts Loaded"],
            width=200,
            height=28,
            font=FONT_SMALL,
            command=self._on_video_change,
        )
        self.video_combo.grid(row=0, column=1, padx=(0, 12), pady=8)

        # Search Bar
        ctk.CTkLabel(toolbar, text="Search:", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=2, padx=(0, 4), pady=8, sticky="w"
        )
        self.search_entry = ctk.CTkEntry(
            toolbar,
            placeholder_text="Search words, phrases...",
            width=220,
            height=28,
            font=FONT_BODY,
            fg_color=COLOR_BG_INPUT,
            border_color=COLOR_BORDER,
            border_width=1,
        )
        self.search_entry.grid(row=0, column=3, sticky="w", padx=(0, 10), pady=8)
        self.search_entry.bind("<KeyRelease>", lambda e: self._on_search())

        self.match_count_lbl = ctk.CTkLabel(
            toolbar,
            text="",
            font=FONT_SMALL,
            text_color=COLOR_CYAN,
        )
        self.match_count_lbl.grid(row=0, column=4, padx=8, pady=8)

        # Export & Copy Actions
        actions_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        actions_frame.grid(row=0, column=5, sticky="e", padx=(8, 12), pady=8)

        ctk.CTkButton(
            actions_frame,
            text="📋 Copy Text",
            width=85,
            height=28,
            font=FONT_SMALL,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self._copy_to_clipboard,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            actions_frame,
            text="💾 Export TXT",
            width=85,
            height=28,
            font=FONT_SMALL,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self._export_txt,
        ).pack(side="left", padx=2)

        # Text Box Container
        self.textbox = ctk.CTkTextbox(
            self,
            font=FONT_CODE,
            fg_color=COLOR_BG_INPUT,
            border_color=COLOR_BORDER,
            border_width=1,
            corner_radius=10,
            wrap="word",
        )
        self.textbox.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))

        # Configure Text Tags on native Tk Text widget
        try:
            self._tk_text = self.textbox._textbox
            self._tk_text.tag_config("ts", foreground="#64748b", font=("Consolas", 10))
            self._tk_text.tag_config("speaker", foreground="#818cf8", font=("Consolas", 10, "bold"))
            self._tk_text.tag_config("kw_hit", foreground="#00f2fe", background="#0b2e40", font=("Consolas", 11, "bold"))
            self._tk_text.tag_config("search_match", foreground="#ffffff", background="#b45309", font=("Consolas", 11, "bold"))
            self._tk_text.tag_config("body", foreground="#e2e8f0")
        except Exception:
            pass

        self._show_placeholder()

    def _show_placeholder(self):
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", "\n\n   No transcripts loaded.\n   Run 'Scan & Analyze Hits' to inspect searchable transcripts.")
        self.textbox.configure(state="disabled")

    def load_transcripts(self, stats: List[VideoJobStats], keywords: List[str]):
        """Load transcripts from completed analysis jobs."""
        self.transcripts_map.clear()
        self.active_keywords = [k.lower().strip() for k in keywords if k.strip()]

        for s in stats:
            if s.transcript and not s.failed:
                name = Path(s.path).name
                self.transcripts_map[name] = s.transcript

        options = list(self.transcripts_map.keys())
        if options:
            self.video_combo.configure(values=options)
            self.video_combo.set(options[0])
            self._render_active_transcript()
        else:
            self.video_combo.configure(values=["No Transcripts Found"])
            self.video_combo.set("No Transcripts Found")
            self._show_placeholder()

    def _on_video_change(self, choice: str):
        self._render_active_transcript()

    def _on_search(self):
        self._highlight_search()

    def _render_active_transcript(self):
        choice = self.video_combo.get()
        transcript = self.transcripts_map.get(choice)
        if not transcript:
            self._show_placeholder()
            return

        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")

        tk = self._tk_text
        for seg in transcript.segments:
            ts_str = f"[{format_ts_clean(seg.start)} --> {format_ts_clean(seg.end)}] "
            tk.insert("end", ts_str, "ts")

            if seg.speaker:
                tk.insert("end", f"<{seg.speaker}> ", "speaker")

            # Insert words with keyword detection
            seg_text = seg.text.strip()
            # If word timestamps are present, render word-by-word
            if seg.words:
                for w in seg.words:
                    word_clean = w.text.strip().lower()
                    is_kw = any(k in word_clean for k in self.active_keywords)
                    tag = "kw_hit" if is_kw else "body"
                    tk.insert("end", w.text + " ", tag)
            else:
                # Fallback to segment text
                tk.insert("end", seg_text, "body")

            tk.insert("end", "\n\n")

        self.textbox.configure(state="disabled")
        self._highlight_search()

    def _highlight_search(self):
        query = self.search_entry.get().strip().lower()
        tk = self._tk_text

        # Remove previous search highlights
        try:
            tk.tag_remove("search_match", "1.0", "end")
        except Exception:
            return

        if not query or len(query) < 2:
            self.match_count_lbl.configure(text="")
            return

        matches = 0
        start = "1.0"
        while True:
            pos = tk.search(query, start, stopindex="end", nocase=True)
            if not pos:
                break
            end_pos = f"{pos}+{len(query)}c"
            tk.tag_add("search_match", pos, end_pos)
            matches += 1
            start = end_pos

        self.match_count_lbl.configure(
            text=f"{matches} match{'es' if matches != 1 else ''}",
            text_color=COLOR_GREEN if matches > 0 else COLOR_TEXT_DIM,
        )

    def _copy_to_clipboard(self):
        try:
            content = self.textbox.get("1.0", "end")
            self.clipboard_clear()
            self.clipboard_append(content)
            self.match_count_lbl.configure(text="Copied to clipboard! ✔", text_color=COLOR_GREEN)
        except Exception:
            pass

    def _export_txt(self):
        choice = self.video_combo.get()
        if not choice or choice not in self.transcripts_map:
            return
        save_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=f"{Path(choice).stem}_transcript.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if save_path:
            content = self.textbox.get("1.0", "end")
            try:
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self.match_count_lbl.configure(text="Exported transcript! ✔", text_color=COLOR_GREEN)
            except Exception as e:
                self.match_count_lbl.configure(text=f"Export failed: {e}", text_color="#f87171")
