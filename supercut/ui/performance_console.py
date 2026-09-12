"""Performance telemetry dashboard and live colored console for SuperCut Studio Pro."""

from __future__ import annotations

import time
from typing import Optional
import customtkinter as ctk

from .theme import (
    COLOR_BG_CARD,
    COLOR_BG_INPUT,
    COLOR_BORDER,
    COLOR_CYAN,
    COLOR_GREEN,
    COLOR_PURPLE,
    COLOR_RED,
    COLOR_TEXT_DIM,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_CODE,
    FONT_METRIC_LBL,
    FONT_METRIC_VAL,
    FONT_SMALL,
)


def format_duration_timer(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class MetricCard(ctk.CTkFrame):
    """Sleek KPI metric tile displaying live telemetry."""

    def __init__(self, master, label: str, initial_value: str = "--:--", color: str = COLOR_CYAN, **kwargs):
        super().__init__(
            master,
            fg_color=COLOR_BG_CARD,
            border_width=1,
            border_color=COLOR_BORDER,
            corner_radius=8,
            **kwargs,
        )
        self.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(
            self,
            text=label.upper(),
            font=FONT_METRIC_LBL,
            text_color=COLOR_TEXT_DIM,
        )
        self.lbl_title.grid(row=0, column=0, padx=10, pady=(6, 0), sticky="w")

        self.lbl_val = ctk.CTkLabel(
            self,
            text=initial_value,
            font=FONT_METRIC_VAL,
            text_color=color,
        )
        self.lbl_val.grid(row=1, column=0, padx=10, pady=(0, 6), sticky="w")

    def set_value(self, val: str, color: Optional[str] = None):
        self.lbl_val.configure(text=val)
        if color:
            self.lbl_val.configure(text_color=color)


class PerformanceConsoleWidget(ctk.CTkFrame):
    """Tab 4: Real-time telemetry, ETA calculator, throughput meter, and colored console."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self.start_time: Optional[float] = None
        self.timer_id = None
        self.auto_scroll = True
        self.total_input_duration = 0.0
        self.current_pct = 0.0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_ui()

    def _build_ui(self):
        # KPI Row
        kpi_frame = ctk.CTkFrame(self, fg_color="transparent")
        kpi_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 8))
        for c in range(4):
            kpi_frame.grid_columnconfigure(c, weight=1)

        self.card_elapsed = MetricCard(kpi_frame, "Elapsed Time", "00:00", COLOR_CYAN)
        self.card_elapsed.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.card_eta = MetricCard(kpi_frame, "Estimated ETA", "--:--", COLOR_PURPLE)
        self.card_eta.grid(row=0, column=1, padx=6, sticky="ew")

        self.card_throughput = MetricCard(kpi_frame, "Throughput", "--", COLOR_GREEN)
        self.card_throughput.grid(row=0, column=2, padx=6, sticky="ew")

        self.card_hits = MetricCard(kpi_frame, "Detected Hits", "0", COLOR_TEXT_WHITE)
        self.card_hits.grid(row=0, column=3, padx=(6, 0), sticky="ew")

        # Console Frame
        console_container = ctk.CTkFrame(
            self,
            fg_color=COLOR_BG_CARD,
            border_width=1,
            border_color=COLOR_BORDER,
            corner_radius=10,
        )
        console_container.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        console_container.grid_columnconfigure(0, weight=1)
        console_container.grid_rowconfigure(1, weight=1)

        # Console Header Toolbar
        tb = ctk.CTkFrame(console_container, fg_color="transparent")
        tb.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
        tb.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tb,
            text="Live Execution Console",
            font=FONT_BODY_BOLD,
            text_color=COLOR_TEXT_WHITE,
        ).grid(row=0, column=0, sticky="w")

        self.autoscroll_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            tb,
            text="Auto-scroll",
            font=FONT_SMALL,
            variable=self.autoscroll_var,
            width=20,
            checkbox_width=16,
            checkbox_height=16,
        ).grid(row=0, column=1, padx=(0, 10))

        ctk.CTkButton(
            tb,
            text="📋 Copy",
            width=65,
            height=26,
            font=FONT_SMALL,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self._copy_logs,
        ).grid(row=0, column=2, padx=(0, 6))

        ctk.CTkButton(
            tb,
            text="🗑 Clear",
            width=65,
            height=26,
            font=FONT_SMALL,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self.clear_logs,
        ).grid(row=0, column=3)

        # Log Text Box
        self.log_box = ctk.CTkTextbox(
            console_container,
            wrap="word",
            font=FONT_CODE,
            fg_color=COLOR_BG_INPUT,
            border_color=COLOR_BORDER,
            border_width=1,
            corner_radius=8,
            state="disabled",
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        try:
            self._tk_text = self.log_box._textbox
            self._tk_text.tag_config("err", foreground="#ff5555", font=("Consolas", 11, "bold"))
            self._tk_text.tag_config("warn", foreground="#ffb86c")
            self._tk_text.tag_config("ok", foreground="#50fa7b", font=("Consolas", 11, "bold"))
            self._tk_text.tag_config("hit", foreground="#8be9fd", font=("Consolas", 11, "bold"))
            self._tk_text.tag_config("hdr", foreground="#bd93f9", font=("Consolas", 11, "bold"))
            self._tk_text.tag_config("dim", foreground="#6272a4")
            self._tk_text.tag_config("info", foreground="#f8fafc")
        except Exception:
            pass

    def start_job(self, total_duration: float = 0.0):
        self.start_time = time.time()
        self.total_input_duration = total_duration
        self.current_pct = 0.0
        self.card_elapsed.set_value("00:00")
        self.card_eta.set_value("--:--")
        self.card_throughput.set_value("--")
        self._tick_timer()

    def stop_job(self):
        if self.timer_id:
            try:
                self.after_cancel(self.timer_id)
            except Exception:
                pass
            self.timer_id = None
        self.card_eta.set_value("Done ✔", COLOR_GREEN)

    def _tick_timer(self):
        if not self.start_time:
            return
        elapsed = time.time() - self.start_time
        self.card_elapsed.set_value(format_duration_timer(elapsed))

        # Calculate ETA based on progress percentage
        if self.current_pct > 2.0 and self.current_pct < 99.0:
            rate = self.current_pct / elapsed
            remaining_pct = 100.0 - self.current_pct
            eta_sec = remaining_pct / rate if rate > 0 else 0
            self.card_eta.set_value(f"~{format_duration_timer(eta_sec)}")
        elif self.current_pct >= 99.0:
            self.card_eta.set_value("Finalizing...")

        # Calculate Throughput
        if self.total_input_duration > 0 and elapsed > 1.0:
            processed_dur = (self.current_pct / 100.0) * self.total_input_duration
            mult = processed_dur / elapsed
            self.card_throughput.set_value(f"{mult:.1f}x real-time")
        elif elapsed > 2.0 and self.current_pct > 0:
            self.card_throughput.set_value(f"{self.current_pct / elapsed:.1f}%/s")

        self.timer_id = self.after(1000, self._tick_timer)

    def update_progress(self, pct: float, msg: str):
        self.current_pct = pct
        # Log machine messages
        if msg:
            self.log(msg)

    def update_hits_count(self, count: int):
        self.card_hits.set_value(str(count))

    def log(self, msg: str):
        """Append a colored log message to the console."""
        clean_msg = msg.strip()
        if not clean_msg:
            return

        tag = "info"
        lower = clean_msg.lower()
        if "error" in lower or "failed" in lower:
            tag = "err"
        elif "warning" in lower:
            tag = "warn"
        elif "found keyword" in lower or "found phrase" in lower or "🎯" in clean_msg:
            tag = "hit"
        elif "saved" in lower or "completed" in lower or "done ✔" in lower or "success" in lower:
            tag = "ok"
        elif clean_msg.startswith("=") or clean_msg.startswith("---") or "summary" in lower:
            tag = "hdr"
        elif clean_msg.startswith("[") and "]" in clean_msg:
            tag = "dim"

        self.log_box.configure(state="normal")
        try:
            cur_time = time.strftime("[%H:%M:%S] ")
            self.log_box.insert("end", cur_time, "dim")
            self.log_box.insert("end", clean_msg + "\n", tag)
        except Exception:
            self.log_box.insert("end", clean_msg + "\n")

        if self.autoscroll_var.get():
            self.log_box.see("end")

        self.log_box.configure(state="disabled")

    def clear_logs(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _copy_logs(self):
        try:
            content = self.log_box.get("1.0", "end")
            self.clipboard_clear()
            self.clipboard_append(content)
        except Exception:
            pass
