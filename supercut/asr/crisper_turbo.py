"""CrisperWhisper 2.0 Turbo ASR backend with precise word-level timestamps.

The model runs either natively (when the ``crisperwhisper`` package is importable)
or inside an isolated Python environment discovered on disk. The subprocess path
streams progress heartbeats, honours cancellation, and scales its timeout with
the media duration so hour-long recordings do not get cut off.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from ..models import Segment, Transcript, Word
from ..paths import app_root
from ..procs import popen_hidden, run_hidden

CRISPER_MODEL_NAME = "crisper-turbo"
CRISPER_RUNTIME_ENV = "VKS_CRISPER_PYTHON"

_WORKER_SCRIPT = r"""
import json, os, sys
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
audio = sys.argv[1]
lang = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] not in ("", "auto") else None
print("__STATUS__ loading model", flush=True)
from crisperwhisper import CrisperWhisperModel
model = CrisperWhisperModel('turbo', backend='transformers')
print("__STATUS__ transcribing", flush=True)
kwargs = {"word_timestamps": True}
if lang:
    kwargs["language"] = lang
else:
    kwargs["language"] = "en"
res = model.transcribe(audio, **kwargs)
words = []
for w in (res.words or []):
    if w.word and w.word.strip() and w.start is not None and w.end is not None:
        words.append({'text': w.word.strip(), 'start': float(w.start), 'end': float(w.end),
                      'confidence': float(getattr(w, 'probability', 1.0) or 1.0)})
out = {'duration': float(getattr(res, 'duration', 0.0) or 0.0), 'language': str(getattr(res, 'language', '') or 'en'), 'words': words}
print('__JSON_START__', flush=True)
print(json.dumps(out), flush=True)
"""


def _python_has_crisper(python_path: Path) -> bool:
    try:
        result = run_hidden(
            [str(python_path), "-c", "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('crisperwhisper') else 1)"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def find_crisper_python(verify: bool = False) -> Optional[Path]:
    """Locate a Python interpreter that has the crisperwhisper package installed."""
    configured = os.environ.get(CRISPER_RUNTIME_ENV, "").strip().strip('"')
    if configured:
        p = Path(configured)
        if p.is_file():
            return p

    root = app_root()
    exe = "python.exe" if os.name == "nt" else "python"
    candidates = [
        root / "crisper-venv" / "Scripts" / exe,
        root / "crisper-venv" / "bin" / exe,
        root.parent / "2A-TST-Desktop" / ".omnivoice" / "Scripts" / exe,
        Path.home() / "dev" / "2A-TST-Desktop" / ".omnivoice" / "Scripts" / exe,
        Path.home() / "dev" / "VideoKeywordStitcher" / "crisper-venv" / "Scripts" / exe,
    ]

    for c in candidates:
        if c.is_file():
            if verify and not _python_has_crisper(c):
                continue
            return c
    return None


def crisper_available() -> bool:
    if importlib.util.find_spec("crisperwhisper") is not None:
        return True
    return find_crisper_python() is not None


class CrisperWhisperBackend:
    def __init__(self, model_name: str = "crisper-turbo", device: str = "cuda", language: str = "auto"):
        self.model_name = model_name
        self.device = device
        self.language = language or "auto"
        self._native_model = None
        self.crisper_python: Optional[Path] = None
        self._proc: Optional[subprocess.Popen] = None

        if importlib.util.find_spec("crisperwhisper") is not None:
            from crisperwhisper import CrisperWhisperModel
            print("Loading CrisperWhisper 2.0 Turbo (Transformers native)...")
            self._native_model = CrisperWhisperModel("turbo", backend="transformers")
        else:
            self.crisper_python = find_crisper_python()
            if not self.crisper_python:
                raise RuntimeError(
                    "CrisperWhisper requires the crisperwhisper package or a dedicated Python environment. "
                    "Run setup_crisper.bat, set VKS_CRISPER_PYTHON, or choose a Whisper model instead."
                )
            print(f"CrisperWhisper will run via subprocess environment: {self.crisper_python}")

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                if os.name == "nt":
                    run_hidden(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
                else:
                    proc.terminate()
            except Exception:
                pass

    def transcribe(
        self,
        audio_path: str,
        media_path: str,
        fallback_duration: float = 0.0,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Transcript:
        if progress_callback:
            progress_callback(0.05, "Loading CrisperWhisper Turbo...")

        if self._native_model is not None:
            kwargs = {"word_timestamps": True, "language": self.language if self.language not in ("", "auto") else "en"}
            result = self._native_model.transcribe(audio_path, **kwargs)
            duration = float(getattr(result, "duration", 0.0) or fallback_duration)
            words = []
            for w in result.words or []:
                clean_t = w.word.strip()
                if clean_t and w.start is not None and w.end is not None:
                    conf = float(getattr(w, "probability", 1.0) or 1.0)
                    words.append(Word(text=clean_t, start=float(w.start), end=float(w.end), confidence=conf))
            segments = self._words_to_segments(words, duration)
            if progress_callback:
                progress_callback(1.0, f"Transcribed {duration:.0f}s with CrisperWhisper")
            return Transcript(
                media_path=media_path,
                duration=duration,
                language=str(getattr(result, "language", "") or "en"),
                segments=segments,
                model_name=self.model_name,
                created_at=time.time(),
            )

        return self._transcribe_subprocess(audio_path, media_path, fallback_duration, progress_callback, cancel_check)

    # ------------------------------------------------------------------
    def _transcribe_subprocess(
        self,
        audio_path: str,
        media_path: str,
        fallback_duration: float,
        progress_callback: Optional[Callable[[float, str], None]],
        cancel_check: Optional[Callable[[], bool]],
    ) -> Transcript:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tf:
            tf.write(_WORKER_SCRIPT)
            script_path = tf.name

        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)

        # Generous timeout: model load + ~0.25x realtime on GPU, much slower on CPU.
        timeout = 900.0 + max(0.0, fallback_duration) * 1.5
        start = time.monotonic()
        stdout_lines: List[str] = []
        stderr_tail: List[str] = []
        status = {"phase": "starting"}

        try:
            cmd = [str(self.crisper_python), "-u", script_path, audio_path, self.language or "auto"]
            proc = popen_hidden(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            self._proc = proc

            def _read_stdout():
                try:
                    for line in proc.stdout:
                        if line.startswith("__STATUS__"):
                            status["phase"] = line.replace("__STATUS__", "").strip()
                        else:
                            stdout_lines.append(line)
                except Exception:
                    pass

            def _read_stderr():
                try:
                    for line in proc.stderr:
                        stderr_tail.append(line.rstrip())
                        if len(stderr_tail) > 60:
                            del stderr_tail[:20]
                except Exception:
                    pass

            t_out = threading.Thread(target=_read_stdout, daemon=True)
            t_err = threading.Thread(target=_read_stderr, daemon=True)
            t_out.start()
            t_err.start()

            cancelled = False
            timed_out = False
            last_beat = 0.0
            while proc.poll() is None:
                elapsed = time.monotonic() - start
                if cancel_check and cancel_check():
                    cancelled = True
                    self.cancel()
                    break
                if elapsed > timeout:
                    timed_out = True
                    self.cancel()
                    break
                if progress_callback and elapsed - last_beat >= 2.0:
                    last_beat = elapsed
                    if status["phase"].startswith("loading"):
                        frac = 0.08
                        msg = f"Loading CrisperWhisper model... ({elapsed:.0f}s)"
                    else:
                        # Estimate progress assuming ~0.3x realtime; never claim completion.
                        est_total = max(8.0, fallback_duration * 0.3 + 6.0)
                        frac = 0.12 + 0.83 * min(0.97, elapsed / est_total)
                        msg = f"CrisperWhisper transcribing... {elapsed:.0f}s elapsed"
                    progress_callback(frac, msg)
                time.sleep(0.25)

            t_out.join(timeout=5)
            t_err.join(timeout=5)

            if cancelled:
                raise RuntimeError("Transcription cancelled")
            if timed_out:
                raise RuntimeError(f"CrisperWhisper worker timed out after {timeout:.0f}s")
            if proc.returncode != 0:
                tail = "\n".join(stderr_tail[-12:]).strip()
                raise RuntimeError(f"CrisperWhisper worker failed (exit {proc.returncode}): {tail or 'no output'}")

            stdout = "".join(stdout_lines)
            json_marker = "__JSON_START__\n"
            if json_marker not in stdout:
                raise RuntimeError(f"Unexpected worker output: {stdout[-500:]}")

            payload = json.loads(stdout.split(json_marker, 1)[1].strip())
            duration = float(payload.get("duration", 0.0) or fallback_duration)
            words = [
                Word(text=w["text"], start=float(w["start"]), end=float(w["end"]),
                     confidence=float(w.get("confidence", 1.0) or 1.0))
                for w in payload.get("words", [])
                if w.get("text") and w.get("start") is not None and w.get("end") is not None
            ]
            segments = self._words_to_segments(words, duration)

            if progress_callback:
                progress_callback(1.0, f"Transcribed {duration:.0f}s with CrisperWhisper ({len(words)} words)")

            return Transcript(
                media_path=media_path,
                duration=duration,
                language=str(payload.get("language") or "en"),
                segments=segments,
                model_name=self.model_name,
                created_at=time.time(),
            )
        finally:
            self._proc = None
            if os.path.exists(script_path):
                try:
                    os.remove(script_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    def _words_to_segments(self, words: List[Word], total_duration: float) -> List[Segment]:
        if not words:
            return []
        segments: List[Segment] = []
        cur_words: List[Word] = []
        seg_start = words[0].start

        for i, w in enumerate(words):
            if not cur_words:
                seg_start = w.start
            cur_words.append(w)
            next_gap = (words[i + 1].start - w.end) if i + 1 < len(words) else 0.0
            # Group into sentences: punctuation, long pause, or > 7s of speech
            if w.text.endswith((".", "!", "?")) or (w.end - seg_start) >= 7.0 or next_gap >= 1.5:
                seg_text = " ".join(item.text for item in cur_words)
                segments.append(Segment(start=seg_start, end=w.end, text=seg_text, words=list(cur_words)))
                cur_words = []

        if cur_words:
            seg_text = " ".join(item.text for item in cur_words)
            segments.append(Segment(start=seg_start, end=cur_words[-1].end, text=seg_text, words=cur_words))

        return segments
