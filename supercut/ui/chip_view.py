"""Interactive Keyword Matrix and Chip Tag Manager for CustomTkinter."""

from __future__ import annotations

from typing import Callable, List, Optional
import customtkinter as ctk

from .theme import (
    COLOR_BG_CARD,
    COLOR_BG_CARD_HOVER,
    COLOR_BG_INPUT,
    COLOR_BORDER,
    COLOR_CYAN,
    COLOR_RED,
    COLOR_TEXT_DIM,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_SMALL,
)


class KeywordChipView(ctk.CTkFrame):
    """Interactive tag container supporting dynamic keyword chip addition and removal."""

    def __init__(
        self,
        master,
        placeholder: str = "Add keyword and press Enter...",
        is_exclude: bool = False,
        on_change: Optional[Callable[[List[str]], None]] = None,
        on_auto_discover: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        super().__init__(
            master,
            fg_color=COLOR_BG_CARD,
            border_width=1,
            border_color=COLOR_RED if is_exclude else COLOR_BORDER,
            corner_radius=8,
            **kwargs,
        )
        self.is_exclude = is_exclude
        self.on_change_callback = on_change
        self.on_auto_discover_callback = on_auto_discover
        self._keywords: List[str] = []

        self.grid_columnconfigure(0, weight=1)
        self._build_ui(placeholder)


    def _build_ui(self, placeholder: str):
        # Input bar
        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        input_frame.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(
            input_frame,
            placeholder_text=placeholder,
            height=32,
            font=FONT_BODY,
            fg_color=COLOR_BG_INPUT,
            border_color=COLOR_BORDER,
            border_width=1,
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.entry.bind("<Return>", lambda e: self._on_add_btn())

        btn_color = "#4c1d24" if self.is_exclude else "#1e293b"
        btn_hover = "#7f1d1d" if self.is_exclude else "#334155"
        text_accent = "#f87171" if self.is_exclude else COLOR_CYAN

        self.add_btn = ctk.CTkButton(
            input_frame,
            text="+ Add",
            width=65,
            height=32,
            font=FONT_BODY_BOLD,
            fg_color=btn_color,
            hover_color=btn_hover,
            text_color=text_accent,
            border_width=1,
            border_color=text_accent,
            corner_radius=6,
            command=self._on_add_btn,
        )
        self.add_btn.grid(row=0, column=1, padx=(0, 4))

        self.clear_btn = ctk.CTkButton(
            input_frame,
            text="Clear",
            width=50,
            height=32,
            font=FONT_SMALL,
            fg_color="transparent",
            hover_color=COLOR_BG_CARD_HOVER,
            text_color=COLOR_TEXT_MUTED,
            command=self.clear,
        )
        self.clear_btn.grid(row=0, column=2, padx=(0, 4))

        if not self.is_exclude:
            self.auto_discover_btn = ctk.CTkButton(
                input_frame,
                text="✨ Auto-Discover",
                width=115,
                height=32,
                font=FONT_BODY_BOLD,
                fg_color="#182738",
                hover_color="#20364d",
                text_color=COLOR_CYAN,
                border_width=1,
                border_color=COLOR_CYAN,
                corner_radius=6,
                command=self._on_auto_discover_btn,
            )
            self.auto_discover_btn.grid(row=0, column=3)
        else:
            self.auto_discover_btn = None

        # Chips scrollable container
        self.chips_container = ctk.CTkScrollableFrame(
            self,
            height=65,
            fg_color="transparent",
            orientation="horizontal",
        )
        self.chips_container.grid(row=1, column=0, sticky="ew", padx=6, pady=(2, 6))

        self.empty_label = ctk.CTkLabel(
            self.chips_container,
            text="No keywords added yet" if not self.is_exclude else "No exclusions set (optional)",
            font=FONT_SMALL,
            text_color=COLOR_TEXT_DIM,
        )
        self.empty_label.pack(side="left", padx=8, pady=4)

    def _on_auto_discover_btn(self):
        if self.on_auto_discover_callback:
            try:
                self.on_auto_discover_callback()
            except Exception as exc:
                print(f"Auto-discover callback error: {exc}")

    def set_on_auto_discover(self, callback: Callable[[], None]):
        self.on_auto_discover_callback = callback

    def _on_add_btn(self):

        text = self.entry.get().strip()
        if not text:
            return
        # Support comma-separated input: "apple, banana, cherry"
        items = [item.strip() for item in text.split(",") if item.strip()]
        for item in items:
            self.add_keyword(item, trigger_callback=False)
        self.entry.delete(0, "end")
        self._notify_change()

    def add_keyword(self, kw: str, trigger_callback: bool = True):
        clean = kw.strip()
        if not clean or clean in self._keywords:
            return
        self._keywords.append(clean)
        self._render_chips()
        if trigger_callback:
            self._notify_change()

    def remove_keyword(self, kw: str):
        if kw in self._keywords:
            self._keywords.remove(kw)
            self._render_chips()
            self._notify_change()

    def clear(self):
        self._keywords.clear()
        self._render_chips()
        self._notify_change()

    def get_keywords(self) -> List[str]:
        return list(self._keywords)

    def set_keywords(self, keywords: List[str] | str):
        if isinstance(keywords, str):
            self._keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        else:
            self._keywords = [str(k).strip() for k in keywords if str(k).strip()]
        self._render_chips()
        self._notify_change()

    def _render_chips(self):
        # Clear children
        for child in self.chips_container.winfo_children():
            child.destroy()

        if not self._keywords:
            self.empty_label = ctk.CTkLabel(
                self.chips_container,
                text="No keywords added yet" if not self.is_exclude else "No exclusions set (optional)",
                font=FONT_SMALL,
                text_color=COLOR_TEXT_DIM,
            )
            self.empty_label.pack(side="left", padx=8, pady=4)
            return

        chip_bg = "#3a131a" if self.is_exclude else "#0f2b3e"
        chip_border = "#f87171" if self.is_exclude else "#38bdf8"
        text_color = "#fca5a5" if self.is_exclude else "#e0f2fe"

        for kw in self._keywords:
            chip_frame = ctk.CTkFrame(
                self.chips_container,
                fg_color=chip_bg,
                border_color=chip_border,
                border_width=1,
                corner_radius=12,
                height=26,
            )
            chip_frame.pack(side="left", padx=3, pady=2)

            lbl = ctk.CTkLabel(
                chip_frame,
                text=kw,
                font=FONT_BODY_BOLD,
                text_color=text_color,
            )
            lbl.pack(side="left", padx=(8, 4), pady=2)

            close_btn = ctk.CTkButton(
                chip_frame,
                text="✕",
                width=16,
                height=16,
                font=("Arial", 10, "bold"),
                fg_color="transparent",
                hover_color=chip_border,
                text_color=text_color,
                corner_radius=8,
                command=lambda k=kw: self.remove_keyword(k),
            )
            close_btn.pack(side="right", padx=(0, 4), pady=2)

    def _notify_change(self):
        if self.on_change_callback:
            try:
                self.on_change_callback(self.get_keywords())
            except Exception:
                pass
