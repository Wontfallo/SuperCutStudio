import json
import os
import subprocess
import sys
import threading
import time

import customtkinter as ctk
from tkinter import filedialog

APP_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(APP_DIR, "main.py")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")

VIDEO_FILETYPES = [
    ("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.ts *.wmv *.flv"),
    ("All files", "*.*"),
]

MODEL_OPTIONS = {
    "CrisperWhisper Turbo (0.8B)": "crisper-turbo",
    "Whisper large-v3": "large-v3",
    "Whisper large-v2": "large-v2",
    "Whisper medium": "medium",
    "Whisper small": "small",
    "Whisper base": "base",
    "Whisper tiny": "tiny",
}
MODEL_LABELS = {value: label for label, value in MODEL_OPTIONS.items()}
MODEL_CHOICES = list(MODEL_OPTIONS)
DEFAULT_MODEL_LABEL = "CrisperWhisper Turbo (0.8B)"


class VideoStitcherApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Video Keyword Clip Stitcher")
        self.geometry("900x760")
        self.minsize(760, 600)

        self.process = None
        self.cancelled = False
        self.start_time = None
        self.timer_id = None

        self._build_ui()
        self._load_settings()
        self._update_model_detail()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(8, weight=1)

        # Row 0: Input
        ctk.CTkLabel(self, text="Input:", anchor="w").grid(
            row=0, column=0, padx=(20, 10), pady=(20, 0), sticky="w")

        self.input_entry = ctk.CTkEntry(self)
        self.input_entry.grid(row=0, column=1, padx=(0, 10), pady=(20, 0), sticky="ew")

        self.input_mode = ctk.CTkSegmentedButton(self, values=["File", "Folder"])
        self.input_mode.set("Folder")
        self.input_mode.grid(row=0, column=2, padx=(0, 10), pady=(20, 0))

        self.browse_btn = ctk.CTkButton(self, text="Browse...", width=90,
                                        command=self.browse_input)
        self.browse_btn.grid(row=0, column=3, padx=(0, 20), pady=(20, 0))

        # Row 1: Keywords
        ctk.CTkLabel(self, text="Keywords:", anchor="w").grid(
            row=1, column=0, padx=(20, 10), pady=(10, 0), sticky="w")

        self.kw_entry = ctk.CTkEntry(
            self, placeholder_text="e.g. apple, banana, hot dog (phrases supported)")
        self.kw_entry.grid(row=1, column=1, columnspan=3, padx=(0, 20),
                           pady=(10, 0), sticky="ew")

        # Row 2: Output directory
        ctk.CTkLabel(self, text="Output Dir:", anchor="w").grid(
            row=2, column=0, padx=(20, 10), pady=(10, 0), sticky="w")

        self.out_entry = ctk.CTkEntry(self)
        self.out_entry.insert(0, "output")
        self.out_entry.grid(row=2, column=1, columnspan=2, padx=(0, 10),
                            pady=(10, 0), sticky="ew")

        self.out_browse_btn = ctk.CTkButton(self, text="Browse...", width=90,
                                            command=self.browse_output)
        self.out_browse_btn.grid(row=2, column=3, padx=(0, 20), pady=(10, 0))

        # Row 3: Options frame
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.grid(row=3, column=0, columnspan=4, padx=20, pady=(12, 0), sticky="ew")
        opts.grid_columnconfigure(0, weight=1)

        # Row A: pad / model / open-folder
        row_a = ctk.CTkFrame(opts, fg_color="transparent")
        row_a.grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(row_a, text="Padding (s):").grid(row=0, column=0, sticky="w")
        self.pad_entry = ctk.CTkEntry(row_a, width=70)
        self.pad_entry.insert(0, "1.0")
        self.pad_entry.grid(row=0, column=1, padx=(8, 24), sticky="w")

        ctk.CTkLabel(row_a, text="Transcription Model:").grid(row=0, column=2, sticky="w")
        self.model_combo = ctk.CTkComboBox(
            row_a, values=MODEL_CHOICES, width=235, command=self._update_model_detail)
        self.model_combo.set(DEFAULT_MODEL_LABEL)
        self.model_combo.grid(row=0, column=3, padx=(8, 24), sticky="w")

        self.open_folder_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(row_a, text="Open folder when done",
                        variable=self.open_folder_var).grid(row=0, column=4, sticky="w")

        self.model_detail_label = ctk.CTkLabel(
            opts,
            text="",
            text_color="gray55",
            font=("Segoe UI", 11),
            anchor="w",
        )
        self.model_detail_label.grid(row=1, column=0, pady=(4, 0), sticky="w")

        # Outputs group: one place that decides everything that is produced & kept
        out_frame = ctk.CTkFrame(opts, border_width=1, border_color="gray35")
        out_frame.grid(row=2, column=0, pady=(12, 0), sticky="ew")
        out_frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(out_frame, text="Outputs to create & keep  (pick any):",
                     font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=4, padx=12, pady=(8, 2), sticky="w")

        self.out_clips_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(out_frame, text="Individual clips",
                        variable=self.out_clips_var).grid(
            row=1, column=0, padx=(12, 20), pady=4, sticky="w")

        self.out_stitched_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(out_frame, text="Per-video stitched",
                        variable=self.out_stitched_var).grid(
            row=1, column=1, padx=(0, 20), pady=4, sticky="w")

        self.out_batch_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(out_frame, text="Combined batch video",
                        variable=self.out_batch_var).grid(
            row=1, column=2, padx=(0, 12), pady=4, sticky="w")

        ctk.CTkLabel(
            out_frame, text_color="gray55", font=("Segoe UI", 11), justify="left",
            text="Individual clips = one file per keyword hit  •  Per-video stitched = one video per "
                 "source file  •  Combined batch = all per-video results joined into one video").grid(
            row=2, column=0, columnspan=4, padx=12, pady=(0, 8), sticky="w")

        # Row C: processing order
        self.defer_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            opts, text="Analyze all videos first, then stitch at the end (instead of stitching each video as it finishes)",
            variable=self.defer_var).grid(row=3, column=0, pady=(10, 0), sticky="w")

        # Row 4: buttons
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=4, column=0, columnspan=4, padx=20, pady=(16, 4), sticky="ew")
        btns.grid_columnconfigure(0, weight=1)
        btns.grid_columnconfigure(1, weight=1)

        self.run_btn = ctk.CTkButton(btns, text="▶ Start Keyword Stitching",
                                     command=self.start_process, height=40,
                                     font=("Segoe UI", 15, "bold"))
        self.run_btn.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        self.stitch_folder_btn = ctk.CTkButton(btns, text="🎬 Stitch Folder Only",
                                                command=self.start_stitch_only_process, height=40,
                                                font=("Segoe UI", 15, "bold"),
                                                fg_color="#2e7d32", hover_color="#1b5e20")
        self.stitch_folder_btn.grid(row=0, column=1, padx=(0, 10), sticky="ew")

        self.stop_btn = ctk.CTkButton(btns, text="■ Stop", width=90, height=40,
                                      command=self.stop_process, state="disabled",
                                      fg_color="#b3261e", hover_color="#8c1d18")
        self.stop_btn.grid(row=0, column=2)

        # Row 5: progress
        prog = ctk.CTkFrame(self, fg_color="transparent")
        prog.grid(row=5, column=0, columnspan=4, padx=20, pady=(4, 0), sticky="ew")
        prog.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(prog)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=0, column=0, sticky="ew")

        self.pct_label = ctk.CTkLabel(prog, text="0%", width=50)
        self.pct_label.grid(row=0, column=1, padx=(10, 0))

        self.elapsed_label = ctk.CTkLabel(prog, text="00:00", width=60)
        self.elapsed_label.grid(row=0, column=2, padx=(6, 0))

        # Row 6: status
        self.status_label = ctk.CTkLabel(self, text="Ready", anchor="w",
                                         text_color="gray")
        self.status_label.grid(row=6, column=0, columnspan=4, padx=20,
                               pady=(4, 0), sticky="ew")

        # Row 7: log header
        ctk.CTkLabel(self, text="Console Output:", anchor="w").grid(
            row=7, column=0, columnspan=4, padx=20, pady=(6, 0), sticky="w")

        # Row 8: log box
        self.log_box = ctk.CTkTextbox(self, state="disabled", wrap="word",
                                      font=("Consolas", 12))
        self.log_box.grid(row=8, column=0, columnspan=4, padx=20, pady=(4, 20),
                          sticky="nsew")

        # Colored log tags on the underlying tk.Text widget
        try:
            tk_text = self.log_box._textbox
            tk_text.tag_config("err", foreground="#ff7b72")
            tk_text.tag_config("warn", foreground="#e3b341")
            tk_text.tag_config("ok", foreground="#7ee787")
            tk_text.tag_config("hit", foreground="#79c0ff")
            tk_text.tag_config("hdr", foreground="#d2a8ff")
        except Exception:
            pass

    def _update_model_detail(self, choice=None):
        label = choice or self.model_combo.get()
        model = MODEL_OPTIONS.get(label, label)
        if model == "crisper-turbo":
            detail = (
                "Fastest CrisperWhisper standard model · precise per-word timestamps · "
                "first use may download model files"
            )
        else:
            detail = "Original faster-whisper backend · CUDA with automatic CPU fallback"
        self.model_detail_label.configure(text=detail)

    # ------------------------------------------------------------------
    # Browsing
    # ------------------------------------------------------------------

    def browse_input(self):
        if self.input_mode.get() == "File":
            path = filedialog.askopenfilename(filetypes=VIDEO_FILETYPES)
        else:
            path = filedialog.askdirectory()
        if path:
            self.input_entry.delete(0, "end")
            self.input_entry.insert(0, path)

    def browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, path)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, msg):
        tag = None
        if "ERROR" in msg or "FAILED" in msg or "Failed" in msg:
            tag = "err"
        elif msg.startswith("WARNING"):
            tag = "warn"
        elif msg.startswith("Found keyword") or msg.startswith("Found phrase"):
            tag = "hit"
        elif "Saved" in msg or "Completed" in msg or "✔" in msg:
            tag = "ok"
        elif msg.startswith("=") or msg == "JOB SUMMARY" or msg.startswith("-" * 10):
            tag = "hdr"

        self.log_box.configure(state="normal")
        try:
            if tag:
                self.log_box.insert("end", msg + "\n", tag)
            else:
                self.log_box.insert("end", msg + "\n")
        except Exception:
            self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Job control
    # ------------------------------------------------------------------

    def start_process(self):
        input_path = self.input_entry.get().strip()
        keywords = self.kw_entry.get().strip()
        output_dir = self.out_entry.get().strip() or "output"
        pad = self.pad_entry.get().strip()
        model = MODEL_OPTIONS.get(self.model_combo.get(), self.model_combo.get())

        if not input_path or not os.path.exists(input_path):
            self.log("ERROR: Input path is empty or does not exist.")
            self.status_label.configure(text="Invalid input path")
            return
        if not keywords:
            self.log("ERROR: Keywords are required.")
            self.status_label.configure(text="Keywords required")
            return
        try:
            float(pad)
        except ValueError:
            self.log(f"ERROR: Padding must be a number (got '{pad}').")
            self.status_label.configure(text="Invalid padding value")
            return
        if not (self.out_clips_var.get() or self.out_stitched_var.get()
                or self.out_batch_var.get()):
            self.log("ERROR: Pick at least one output to create (clips, per-video stitched, or batch).")
            self.status_label.configure(text="No outputs selected")
            return

        output_dir = os.path.abspath(output_dir)
        self._save_settings()

        self.cancelled = False
        self.log("--- Starting Job ---")
        self.status_label.configure(text="Starting...")
        self.run_btn.configure(state="disabled", text="Running...")
        self.stitch_folder_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_bar.set(0)
        self.pct_label.configure(text="0%")

        self.start_time = time.time()
        self._tick_elapsed()

        thread = threading.Thread(
            target=self.run_cli,
            args=(input_path, keywords, output_dir, pad, model),
            daemon=True)
        thread.start()

    def start_stitch_only_process(self):
        input_path = self.input_entry.get().strip()
        output_dir = self.out_entry.get().strip() or "output"

        if not input_path or not os.path.exists(input_path):
            self.log("ERROR: Input path is empty or does not exist.")
            self.status_label.configure(text="Invalid input path")
            return

        output_dir = os.path.abspath(output_dir)
        self._save_settings()

        self.cancelled = False
        self.log("--- Starting Folder Stitch (No Transcription) ---")
        self.status_label.configure(text="Normalizing & Stitching...")
        self.run_btn.configure(state="disabled")
        self.stitch_folder_btn.configure(state="disabled", text="Stitching...")
        self.stop_btn.configure(state="normal")
        self.progress_bar.set(0)
        self.pct_label.configure(text="0%")

        self.start_time = time.time()
        self._tick_elapsed()

        thread = threading.Thread(
            target=self.run_cli,
            args=(
                input_path,
                "",
                output_dir,
                "1.0",
                MODEL_OPTIONS.get(self.model_combo.get(), self.model_combo.get()),
            ),
            kwargs={"stitch_only": True},
            daemon=True)
        thread.start()

    def stop_process(self):
        if self.process is None or self.process.poll() is not None:
            return
        self.cancelled = True
        self.status_label.configure(text="Stopping...")
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    capture_output=True)
            else:
                self.process.terminate()
        except Exception as e:
            self.log(f"WARNING: Could not stop process: {e}")

    def run_cli(self, input_path, keywords, output_dir, pad, model, stitch_only=False):
        if stitch_only:
            cmd = [
                sys.executable, "-u", MAIN_SCRIPT,
                "-i", input_path,
                "-o", output_dir,
                "--stitch-only",
            ]
            if self.open_folder_var.get():
                cmd.append("--open-folder")
        else:
            outputs = []
            if self.out_clips_var.get():
                outputs.append("clips")
            if self.out_stitched_var.get():
                outputs.append("stitched")
            if self.out_batch_var.get():
                outputs.append("batch")

            cmd = [
                sys.executable, "-u", MAIN_SCRIPT,
                "-i", input_path,
                "-k", keywords,
                "-o", output_dir,
                "-p", pad,
                "-m", model,
                "--outputs", ",".join(outputs),
            ]
            if self.defer_var.get():
                cmd.append("--defer-stitch")
            if self.open_folder_var.get():
                cmd.append("--open-folder")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        returncode = -1
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                env=env)

            for line in self.process.stdout:
                line = line.rstrip("\r\n")
                if line.startswith("@@PROGRESS|"):
                    parts = line.split("|", 2)
                    if len(parts) == 3:
                        try:
                            pct = float(parts[1])
                        except ValueError:
                            continue
                        self.after(0, self._update_progress, pct, parts[2])
                elif line.strip():
                    self.after(0, self.log, line)

            self.process.wait()
            returncode = self.process.returncode

        except Exception as e:
            self.after(0, self.log, f"ERROR: {e}")

        finally:
            self.after(0, self._job_finished, returncode, output_dir)

    def _job_finished(self, returncode, output_dir):
        if self.timer_id is not None:
            try:
                self.after_cancel(self.timer_id)
            except Exception:
                pass
            self.timer_id = None

        if self.cancelled:
            self.log("--- Job cancelled by user ---")
            self.status_label.configure(text="Cancelled")
        elif returncode == 0:
            self.log("--- Job Completed Successfully! ✔ ---")
            self.progress_bar.set(1.0)
            self.pct_label.configure(text="100%")
            self.status_label.configure(text="Done ✔")
            if self.open_folder_var.get():
                self._open_folder(output_dir)
        else:
            self.log(f"--- Job Failed with return code {returncode} ---")
            self.status_label.configure(text="Failed")

        self.process = None
        self.run_btn.configure(state="normal", text="▶ Start Keyword Stitching")
        self.stitch_folder_btn.configure(state="normal", text="🎬 Stitch Folder Only")
        self.stop_btn.configure(state="disabled")

    def _open_folder(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as e:
            self.log(f"WARNING: Could not open folder: {e}")

    # ------------------------------------------------------------------
    # Progress / elapsed
    # ------------------------------------------------------------------

    def _update_progress(self, pct, message):
        self.progress_bar.set(pct / 100.0)
        self.pct_label.configure(text=f"{pct:.0f}%")
        self.status_label.configure(text=message)

    def _tick_elapsed(self):
        if self.start_time is None:
            return
        elapsed = int(time.time() - self.start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        if h:
            self.elapsed_label.configure(text=f"{h}:{m:02d}:{s:02d}")
        else:
            self.elapsed_label.configure(text=f"{m:02d}:{s:02d}")
        self.timer_id = self.after(1000, self._tick_elapsed)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            return

        def set_entry(entry, value):
            entry.delete(0, "end")
            entry.insert(0, value)

        try:
            if s.get("input_path"):
                set_entry(self.input_entry, s["input_path"])
            if s.get("input_mode") in ("File", "Folder"):
                self.input_mode.set(s["input_mode"])
            if s.get("keywords"):
                set_entry(self.kw_entry, s["keywords"])
            if s.get("output_dir"):
                set_entry(self.out_entry, s["output_dir"])
            if s.get("pad"):
                set_entry(self.pad_entry, str(s["pad"]))
            saved_model = s.get("model")
            if saved_model in MODEL_LABELS:
                self.model_combo.set(MODEL_LABELS[saved_model])
            elif saved_model in MODEL_CHOICES:
                self.model_combo.set(saved_model)
            if isinstance(s.get("out_clips"), bool):
                self.out_clips_var.set(s["out_clips"])
            if isinstance(s.get("out_stitched"), bool):
                self.out_stitched_var.set(s["out_stitched"])
            if isinstance(s.get("out_batch"), bool):
                self.out_batch_var.set(s["out_batch"])
            if isinstance(s.get("defer_stitch"), bool):
                self.defer_var.set(s["defer_stitch"])
            if isinstance(s.get("open_folder"), bool):
                self.open_folder_var.set(s["open_folder"])
        except Exception:
            pass

    def _save_settings(self):
        settings = {
            "input_path": self.input_entry.get().strip(),
            "input_mode": self.input_mode.get(),
            "keywords": self.kw_entry.get().strip(),
            "output_dir": self.out_entry.get().strip(),
            "pad": self.pad_entry.get().strip(),
            "model": MODEL_OPTIONS.get(self.model_combo.get(), self.model_combo.get()),
            "out_clips": self.out_clips_var.get(),
            "out_stitched": self.out_stitched_var.get(),
            "out_batch": self.out_batch_var.get(),
            "defer_stitch": self.defer_var.get(),
            "open_folder": self.open_folder_var.get(),
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
        except OSError:
            pass

    def _on_close(self):
        self._save_settings()
        self.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    app = VideoStitcherApp()
    app.mainloop()
