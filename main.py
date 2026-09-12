import argparse
import glob
import importlib.util
import json
import os
import queue
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

VIDEO_EXTS = ["*.mp4", "*.mkv", "*.avi", "*.mov", "*.webm", "*.m4v", "*.ts", "*.wmv", "*.flv"]

CRISPER_TURBO_MODEL = "crisper-turbo"
CRISPER_MODEL_NAMES = {CRISPER_TURBO_MODEL: "turbo"}
CRISPER_RUNTIME_ENV = "VKS_CRISPER_PYTHON"
CRISPER_REEXEC_ENV = "VKS_CRISPER_REEXEC"
FFMPEG_STALL_TIMEOUT_S = 180.0

AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
NVENC_ARGS = ["-c:v", "hevc_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
X264_ARGS = ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]

ENCODER_ATTEMPTS = [
    ("hevc_nvenc", False, NVENC_ARGS),
    ("libx264 (cpu fallback)", False, X264_ARGS),
]

# Set to the first encoder config that succeeds, so later encodes skip the
# trial-and-error pass.
_cached_encoder = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def emit_progress(pct: float, msg: str):
    """Machine-readable progress line consumed by the GUI."""
    print(f"@@PROGRESS|{min(100.0, max(0.0, pct)):.1f}|{msg}", flush=True)


def fmt_time(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def fmt_size(path: str) -> str:
    try:
        size = os.path.getsize(path)
    except OSError:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return "?"


def clean_word(word: str) -> str:
    return word.translate(str.maketrans('', '', string.punctuation)).lower().strip()


def merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]
    for current in intervals[1:]:
        prev = merged[-1]
        if current[0] <= prev[1]:
            merged[-1] = (prev[0], max(prev[1], current[1]))
        else:
            merged.append(current)
    return merged


def open_folder(path: str):
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as e:
        print(f"WARNING: Could not open folder: {e}")


# ---------------------------------------------------------------------------
# Transcription backends
# ---------------------------------------------------------------------------

def is_crisper_model(model_name: str) -> bool:
    return model_name.lower().strip() in CRISPER_MODEL_NAMES


def _python_has_crisper(python_path: Path) -> bool:
    """Check a candidate interpreter without loading model weights or using VRAM."""
    try:
        result = subprocess.run(
            [
                str(python_path),
                "-c",
                (
                    "import importlib.util,sys; "
                    "names=('crisperwhisper','transformers','torch'); "
                    "sys.exit(0 if all(importlib.util.find_spec(n) for n in names) else 1)"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def find_crisper_python() -> Optional[Path]:
    """Find a Python environment containing the Windows Transformers runtime.

    Prefer an explicit override, then this app's optional dedicated venv, then
    the known Video Dub environment that already owns the requested model.
    """
    candidates = []
    configured = os.environ.get(CRISPER_RUNTIME_ENV, "").strip()
    if configured:
        candidates.append(Path(configured))

    app_dir = Path(__file__).resolve().parent
    candidates.extend([
        app_dir / "crisper-venv" / "Scripts" / "python.exe",
        app_dir.parent / "2A-TST-Desktop" / ".omnivoice" / "Scripts" / "python.exe",
    ])

    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file() and _python_has_crisper(resolved):
            return resolved
    return None


def use_cached_crisper_offline(model_shorthand: str) -> bool:
    """Use a complete shared HF cache without making availability HEAD calls."""
    repo_dir_name = f"models--nyralabs--CrisperWhisper2.0_{model_shorthand}"
    cache_roots = []
    if os.environ.get("HUGGINGFACE_HUB_CACHE"):
        cache_roots.append(Path(os.environ["HUGGINGFACE_HUB_CACHE"]))
    if os.environ.get("HF_HOME"):
        cache_roots.append(Path(os.environ["HF_HOME"]) / "hub")
    cache_roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    if not any((root / repo_dir_name).is_dir() for root in cache_roots):
        return False
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return True


def relaunch_for_crisper_if_needed(model_name: str) -> Optional[int]:
    """Run this CLI under a Crisper-capable Python when the GUI venv lacks it.

    VideoKeywordStitcher's current Python 3.14 venv is ideal for faster-whisper,
    while the Video Dub model lives in a CUDA-enabled Python 3.12 environment.
    Re-launching the CLI keeps the GUI isolated and lets one model instance stay
    loaded for an entire multi-video job.
    """
    if not is_crisper_model(model_name):
        return None
    if importlib.util.find_spec("crisperwhisper") is not None:
        return None
    if os.environ.get(CRISPER_REEXEC_ENV) == "1":
        print("ERROR: The selected CrisperWhisper runtime cannot import crisperwhisper.")
        return 1

    python_path = find_crisper_python()
    if python_path is None:
        print(
            "ERROR: CrisperWhisper Turbo needs a Python environment containing "
            "crisperwhisper[transformers]. Run setup_crisper.bat, or set "
            f"{CRISPER_RUNTIME_ENV} to that environment's python.exe."
        )
        return 1

    print(f"Using CrisperWhisper runtime: {python_path}", flush=True)
    env = os.environ.copy()
    env[CRISPER_REEXEC_ENV] = "1"
    command = [str(python_path), "-u", str(Path(__file__).resolve()), *sys.argv[1:]]
    try:
        return subprocess.run(command, env=env).returncode
    except OSError as exc:
        print(f"ERROR: Could not start the CrisperWhisper runtime: {exc}")
        return 1


class FasterWhisperTranscriber:
    """Adapter from faster-whisper segments to this app's flat word timeline."""

    def __init__(self, model_name: str):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Run setup.bat first."
            ) from exc

        print(f"\nLoading Whisper model '{model_name}' on CUDA...")
        try:
            self.model = WhisperModel(model_name, device="cuda", compute_type="float16")
        except Exception as exc:
            print(f"WARNING: CUDA model load failed ({exc}). Falling back to CPU (slower).")
            self.model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe_words(
        self,
        audio_path: str,
        fallback_duration: float,
        prog: Callable[[float, str], None],
    ) -> Tuple[List[Tuple[float, float, str]], float]:
        segments, info = self.model.transcribe(
            audio_path, beam_size=5, word_timestamps=True
        )
        duration = float(getattr(info, "duration", 0.0) or fallback_duration)
        words: List[Tuple[float, float, str]] = []
        for segment in segments:
            if duration:
                frac = min(1.0, float(segment.end) / duration)
                prog(frac, f"transcribing {float(segment.end):.0f}s / {duration:.0f}s")
            for word in segment.words or []:
                cleaned = clean_word(word.word)
                if cleaned and word.start is not None and word.end is not None:
                    words.append((float(word.start), float(word.end), cleaned))
        return words, duration


class CrisperWhisperTranscriber:
    """CrisperWhisper 2.0 adapter using the same backend as Video Dub."""

    def __init__(self, model_name: str):
        shorthand = CRISPER_MODEL_NAMES[model_name]
        cached_offline = use_cached_crisper_offline(shorthand)
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        try:
            from crisperwhisper import CrisperWhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "crisperwhisper is not installed in the selected runtime. "
                "Run setup_crisper.bat first."
            ) from exc

        print(
            f"\nLoading CrisperWhisper 2.0 {shorthand} "
            "(Transformers, per-word timestamps)..."
        )
        if cached_offline:
            print("Using cached model files in offline mode (no Hugging Face request).")
        # This exactly matches Video Dub's Windows-safe path. The CT2 package
        # available to faster-whisper is not Nyra's Linux-only CT2 fork.
        self.model = CrisperWhisperModel(shorthand, backend="transformers")

    def transcribe_words(
        self,
        audio_path: str,
        fallback_duration: float,
        prog: Callable[[float, str], None],
    ) -> Tuple[List[Tuple[float, float, str]], float]:
        prog(0.05, "transcribing with CrisperWhisper word timestamps")
        result = self.model.transcribe(
            audio_path, language="en", word_timestamps=True
        )
        duration = float(getattr(result, "duration", 0.0) or fallback_duration)
        words: List[Tuple[float, float, str]] = []
        for word in result.words or []:
            cleaned = clean_word(word.word)
            if cleaned and word.start is not None and word.end is not None:
                words.append((float(word.start), float(word.end), cleaned))
        prog(1.0, f"transcribed {duration:.0f}s with CrisperWhisper")
        return words, duration


def load_transcriber(model_name: str):
    normalized = model_name.lower().strip()
    if normalized in CRISPER_MODEL_NAMES:
        return CrisperWhisperTranscriber(normalized)
    return FasterWhisperTranscriber(model_name)


# ---------------------------------------------------------------------------
# ffprobe / ffmpeg plumbing
# ---------------------------------------------------------------------------

@dataclass
class MediaInfo:
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    has_audio: bool = False


def probe_video(path: str) -> Optional[MediaInfo]:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None

    info = MediaInfo()
    try:
        info.duration = float(data.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        pass

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not info.width:
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            for key in ("avg_frame_rate", "r_frame_rate"):
                rate = stream.get(key) or ""
                if "/" in rate:
                    num, _, den = rate.partition("/")
                    try:
                        if float(den) > 0 and float(num) > 0:
                            info.fps = float(num) / float(den)
                            break
                    except ValueError:
                        pass
        elif stream.get("codec_type") == "audio":
            info.has_audio = True
    return info if info.width else None


def choose_target(infos: List[MediaInfo]) -> Tuple[int, int, int]:
    """Pick the normalization target: the most common resolution / fps across
    the batch (ties broken by the larger value), so the fewest videos need
    rescaling."""
    res_counts = Counter((i.width, i.height) for i in infos if i.width)
    (w, h), _ = max(res_counts.items(), key=lambda kv: (kv[1], kv[0][0] * kv[0][1]))
    fps_counts = Counter(int(round(i.fps)) for i in infos if i.fps > 0)
    if fps_counts:
        fps, _ = max(fps_counts.items(), key=lambda kv: (kv[1], kv[0]))
    else:
        fps = 30
    fps = min(max(fps, 10), 60)
    return w - (w % 2), h - (h % 2), fps


def _stop_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and proc.poll() is None:
            proc.kill()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_ffmpeg(cmd: List[str], total_duration: float = 0.0,
               progress_cb: Optional[Callable[[float], None]] = None,
               stall_timeout: float = FFMPEG_STALL_TIMEOUT_S) -> Tuple[int, str]:
    """Run FFmpeg while draining output and terminating genuinely stalled jobs.

    FFmpeg's progress pipe normally emits updates several times per second. A
    blocking ``for line in pipe`` cannot notice a frozen NVENC/decoder process,
    so read on a daemon thread and enforce an inactivity timeout in this thread.
    Returning a non-zero code lets the caller try its next encoder fallback.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    stderr_lines = []
    output_queue = queue.Queue()

    def read_output():
        try:
            for output_line in proc.stdout:
                output_queue.put(output_line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    last_output = time.monotonic()
    stalled = False

    while True:
        try:
            line = output_queue.get(timeout=1.0)
        except queue.Empty:
            if proc.poll() is not None and not reader.is_alive():
                break
            if stall_timeout > 0 and time.monotonic() - last_output >= stall_timeout:
                stalled = True
                message = (
                    f"FFmpeg produced no progress for {stall_timeout:.0f}s; "
                    "terminated the stalled attempt."
                )
                stderr_lines.append(message)
                _stop_process_tree(proc)
                break
            continue

        if line is None:
            break
        last_output = time.monotonic()
        line_str = line.strip()
        if line_str.startswith("out_time_ms=") and total_duration > 0 and progress_cb:
            try:
                t = int(line_str.split("=", 1)[1]) / 1_000_000
                progress_cb(min(1.0, t / total_duration))
            except ValueError:
                pass
        elif line_str:
            stderr_lines.append(line_str)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        stderr_lines.append("FFmpeg did not exit after closing its output; terminating it.")
        _stop_process_tree(proc)
    reader.join(timeout=2)
    if proc.stdout is not None:
        proc.stdout.close()
    code = proc.returncode if proc.returncode is not None else -1
    return (-1 if stalled else code), "\n".join(stderr_lines)


def encoder_attempts():
    """Encoder configs to try, with the last known-good one first."""
    if _cached_encoder is not None:
        return [_cached_encoder] + [a for a in ENCODER_ATTEMPTS if a != _cached_encoder]
    return list(ENCODER_ATTEMPTS)


def note_encoder_success(attempt):
    global _cached_encoder
    _cached_encoder = attempt


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

def parse_keywords(raw: str) -> Tuple[set, List[Tuple[str, ...]]]:
    singles = set()
    phrases = []
    for chunk in raw.split(","):
        words = [clean_word(w) for w in chunk.split()]
        words = [w for w in words if w]
        if not words:
            continue
        if len(words) == 1:
            singles.add(words[0])
        else:
            phrases.append(tuple(words))
    return singles, phrases


def find_hits(words: List[Tuple[float, float, str]], singles: set,
              phrases: List[Tuple[str, ...]], pad: float,
              duration: float) -> Tuple[List[Tuple[float, float]], int]:
    """words: list of (start, end, cleaned_text). Returns (intervals, hit_count)."""
    intervals = []
    hits = 0

    for start, end, text in words:
        matched = text in singles or (text.endswith("s") and text[:-1] in singles)
        if matched:
            hits += 1
            print(f"Found keyword '{text}' at {start:.2f}s - {end:.2f}s")
            intervals.append((max(0.0, start - pad),
                              min(duration, end + pad) if duration else end + pad))

    texts = [w[2] for w in words]
    for phrase in phrases:
        n = len(phrase)
        for i in range(len(texts) - n + 1):
            if tuple(texts[i:i + n]) == phrase:
                hits += 1
                start, end = words[i][0], words[i + n - 1][1]
                print(f"Found phrase '{' '.join(phrase)}' at {start:.2f}s - {end:.2f}s")
                intervals.append((max(0.0, start - pad),
                                  min(duration, end + pad) if duration else end + pad))

    return intervals, hits


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

@dataclass
class VideoStats:
    path: str
    info: Optional[MediaInfo] = None
    intervals: List[Tuple[float, float]] = field(default_factory=list)
    hits: int = 0
    clip_duration: float = 0.0
    transcribe_s: float = 0.0
    encode_s: float = 0.0
    clips_saved: int = 0
    output: str = ""            # per-video stitched file, if created
    failed: bool = False


def extract_audio(video_path: str, audio_path: str) -> bool:
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-err_detect", "ignore_err",
        "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        audio_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0


def analyze_video(video_path: str, info: MediaInfo, transcriber,
                  singles: set, phrases: List[Tuple[str, ...]], pad: float,
                  prog: Callable[[float, str], None]) -> VideoStats:
    """Transcribe one video and find keyword intervals."""
    name = os.path.basename(video_path)
    stats = VideoStats(path=video_path, info=info)
    print(f"\nAnalyzing {name}...")

    prog(0.0, f"{name}: extracting audio")
    temp_audio = os.path.join(tempfile.gettempdir(), f"vks_audio_{os.getpid()}.wav")
    try:
        if not extract_audio(video_path, temp_audio):
            print(f"WARNING: Failed to extract audio from {name}. Skipping.")
            stats.failed = True
            return stats

        prog(0.05, f"{name}: transcribing")
        t0 = time.perf_counter()
        words, _ = transcriber.transcribe_words(
            temp_audio,
            info.duration,
            lambda frac, message: prog(
                0.05 + 0.95 * frac, f"{name}: {message}"
            ),
        )
        stats.transcribe_s = time.perf_counter() - t0
    finally:
        if os.path.exists(temp_audio):
            os.remove(temp_audio)

    intervals, hits = find_hits(words, singles, phrases, pad, info.duration)
    stats.hits = hits
    if not intervals:
        print(f"No keywords found in {name}.")
        return stats

    stats.intervals = merge_intervals(intervals)
    stats.clip_duration = sum(e - s for s, e in stats.intervals)
    print(f"Found {hits} occurrences. Merged into {len(stats.intervals)} clips "
          f"({fmt_time(stats.clip_duration)} of video).")
    return stats


def save_individual_clips(stats: VideoStats, output_dir: str,
                          prog: Callable[[float, str], None]) -> None:
    name = os.path.splitext(os.path.basename(stats.path))[0]
    clips_dir = os.path.join(output_dir, f"{name}_clips")
    os.makedirs(clips_dir, exist_ok=True)

    n = len(stats.intervals)
    for i, (start, end) in enumerate(stats.intervals, 1):
        prog((i - 1) / n, f"{name}: saving clip {i}/{n}")
        clip_path = os.path.join(clips_dir, f"clip_{i:03d}_{start:.1f}s-{end:.1f}s.mp4")
        saved = False
        for attempt in encoder_attempts():
            label, hwaccel, vcodec = attempt
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
            if hwaccel:
                cmd += ["-hwaccel", "cuda"]
            cmd += ["-ss", f"{start:.3f}", "-i", stats.path, "-t", f"{end - start:.3f}"]
            cmd += vcodec + AUDIO_ARGS + ["-movflags", "+faststart", clip_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                note_encoder_success(attempt)
                saved = True
                break
        if saved:
            stats.clips_saved += 1
        else:
            print(f"WARNING: Failed to save clip {i} of {name}.")
    print(f"Saved {stats.clips_saved} individual clips to {clips_dir}")


def build_stitch_command(video_path: str, info: MediaInfo,
                         intervals: List[Tuple[float, float]],
                         target: Tuple[int, int, int], out_path: str,
                         use_hwaccel: bool, vcodec_args: List[str]) -> List[str]:
    w, h, fps = target
    n = len(intervals)
    audio_src = "0:a" if info.has_audio else "1:a"

    parts = []
    for i, (start, end) in enumerate(intervals):
        parts.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[{audio_src}]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]")
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=1[cv][ca]")
    # Normalize every output to the shared target profile so batch concat can
    # never mix resolutions, frame rates, or audio layouts.
    parts.append(
        f"[cv]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,format=yuv420p[outv]"
    )
    parts.append("[ca]aresample=48000[outa]")
    filter_complex = ";".join(parts)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats",
           "-progress", "pipe:1"]
    if use_hwaccel:
        cmd += ["-hwaccel", "cuda"]
    cmd += ["-i", video_path]
    if not info.has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-filter_complex", filter_complex, "-map", "[outv]", "-map", "[outa]"]
    cmd += vcodec_args + AUDIO_ARGS + ["-movflags", "+faststart", out_path]
    return cmd


def stitch_video(stats: VideoStats, target: Tuple[int, int, int], out_path: str,
                 prog: Callable[[float, str], None]) -> bool:
    name = os.path.basename(stats.path)
    t0 = time.perf_counter()
    for attempt in encoder_attempts():
        label, hwaccel, vcodec = attempt
        prog(0.0, f"{name}: stitching ({label})")
        cmd = build_stitch_command(stats.path, stats.info, stats.intervals, target,
                                   out_path, hwaccel, vcodec)
        code, err = run_ffmpeg(
            cmd, stats.clip_duration,
            lambda f: prog(f, f"{name}: stitching {f * 100:.0f}%"))
        if code == 0:
            note_encoder_success(attempt)
            stats.encode_s = time.perf_counter() - t0
            stats.output = out_path
            print(f"Saved stitched video to {out_path} ({fmt_size(out_path)})")
            return True
        print(f"WARNING: Encode with {label} failed, trying next fallback...")
        if err.strip():
            print(err.strip().splitlines()[-1])
    stats.encode_s = time.perf_counter() - t0
    stats.failed = True
    print(f"ERROR: All encoders failed for {name}.")
    return False


def stitch_batch(processed: List[str], output_dir: str,
                 prog: Callable[[float, str], None]) -> Optional[str]:
    print("\nStitching all per-video results into one batch video...")
    list_file = os.path.join(output_dir, "concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for pf in processed:
            f.write(f"file '{os.path.abspath(pf).replace(chr(92), '/')}'\n")

    final_output = os.path.join(output_dir, "FINAL_BATCH_STITCHED.mp4")
    total = sum((probe_video(p) or MediaInfo()).duration for p in processed)

    try:
        # All inputs share an identical normalized profile, so stream copy is safe.
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats",
               "-progress", "pipe:1", "-f", "concat", "-safe", "0", "-i", list_file,
               "-c", "copy", final_output]
        code, err = run_ffmpeg(cmd, total,
                               lambda f: prog(f, f"Batch stitching {f * 100:.0f}%"))
        if code != 0:
            print("WARNING: Stream-copy concat failed, re-encoding batch instead...")
            for label, hwaccel, vcodec in encoder_attempts():
                print(f"  Trying batch re-encode with {label}...")
                cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats",
                       "-progress", "pipe:1"]
                if hwaccel:
                    cmd += ["-hwaccel", "cuda"]
                cmd += ["-f", "concat", "-safe", "0", "-i", list_file]
                cmd += vcodec + AUDIO_ARGS + ["-movflags", "+faststart", final_output]
                code, err = run_ffmpeg(
                    cmd,
                    total,
                    lambda f, encoder=label: prog(
                        f, f"Batch re-encoding ({encoder}) {f * 100:.0f}%"
                    ),
                )
                if code == 0:
                    note_encoder_success((label, hwaccel, vcodec))
                    break
                print(f"WARNING: Batch re-encode with {label} failed.")
                if err.strip():
                    print(err.strip().splitlines()[-1])
        if code != 0:
            print("ERROR: Failed to stitch batch videos together.")
            if err.strip():
                print(err.strip().splitlines()[-1])
            return None
        print(f"Saved final batch video to {final_output} ({fmt_size(final_output)})")
        return final_output
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(all_stats: List[VideoStats], batch_file: Optional[str],
                  outputs_desc: str, model_load_s: float, batch_s: float,
                  total_s: float):
    with_hits = [s for s in all_stats if s.intervals]
    empty = [s for s in all_stats if not s.intervals and not s.failed]
    failed = [s for s in all_stats if s.failed]

    print("\n" + "=" * 50)
    print("JOB SUMMARY")
    print("=" * 50)
    for s in all_stats:
        name = os.path.basename(s.path)
        if s.failed:
            print(f"  {name}: FAILED")
        elif not s.intervals:
            print(f"  {name}: no keywords found (transcribe {fmt_time(s.transcribe_s)})")
        else:
            extra = f", encode {fmt_time(s.encode_s)}" if s.encode_s else ""
            print(f"  {name}: {s.hits} hits -> {len(s.intervals)} clips "
                  f"({fmt_time(s.clip_duration)}) | transcribe {fmt_time(s.transcribe_s)}{extra}")
    print("-" * 50)
    print(f"  Outputs kept:       {outputs_desc}")
    print(f"  Videos:             {len(with_hits)} with keywords, {len(empty)} without, {len(failed)} failed")
    print(f"  Total keyword hits: {sum(s.hits for s in all_stats)}")
    print(f"  Total clips:        {sum(len(s.intervals) for s in with_hits)} "
          f"({fmt_time(sum(s.clip_duration for s in with_hits))} of footage)")
    print(f"  Model load time:    {fmt_time(model_load_s)}")
    print(f"  Transcription time: {fmt_time(sum(s.transcribe_s for s in all_stats))}")
    encode_total = sum(s.encode_s for s in all_stats)
    if encode_total:
        print(f"  Encoding time:      {fmt_time(encode_total)}")
    if batch_file:
        print(f"  Batch stitch time:  {fmt_time(batch_s)}")
        print(f"  Final batch video:  {batch_file} ({fmt_size(batch_file)})")
    print(f"  Total job time:     {fmt_time(total_s)}")
    print("=" * 50)


# ---------------------------------------------------------------------------
# Stitch-only mode (No transcription)
# ---------------------------------------------------------------------------

def run_stitch_only(input_path: Path, output_dir: str, open_folder_when_done: bool):
    job_start = time.perf_counter()
    emit_progress(0, "Scanning input directory for videos...")

    if input_path.is_file():
        video_files = [str(input_path)]
    elif input_path.is_dir():
        video_files = []
        for ext in VIDEO_EXTS:
            video_files.extend(glob.glob(os.path.join(str(input_path), ext)))
        video_files = sorted(set(video_files))
    else:
        print(f"ERROR: Input path does not exist: {input_path}")
        sys.exit(1)

    # Exclude any previous FINAL_BATCH_STITCHED files to avoid recursion
    video_files = [vf for vf in video_files if not os.path.basename(vf).startswith("FINAL_BATCH_STITCHED")]

    if not video_files:
        print(f"ERROR: No video files found in {input_path}")
        sys.exit(1)

    print(f"Found {len(video_files)} video file(s) to normalize & stitch.")

    infos = {}
    for vf in video_files:
        info = probe_video(vf)
        if info:
            infos[vf] = info
            print(f"  {os.path.basename(vf)}: {info.width}x{info.height} @ {info.fps:.2f}fps, "
                  f"{fmt_time(info.duration)}{'' if info.has_audio else ' (NO AUDIO)'}")
        else:
            print(f"  WARNING: {os.path.basename(vf)} could not be read and will be skipped.")

    video_files = [vf for vf in video_files if vf in infos]
    if not video_files:
        print("ERROR: No readable video files found.")
        sys.exit(1)

    target = choose_target(list(infos.values()))
    w, h, fps = target
    print(f"Normalization Target: {w}x{h} @ {fps}fps, AAC 48kHz stereo, loudnorm audio volume")

    temp_norm_dir = tempfile.mkdtemp(prefix="vks_norm_")
    normalized_files = []
    n = len(video_files)

    try:
        for idx, vf in enumerate(video_files, 1):
            name = os.path.basename(vf)
            norm_path = os.path.join(temp_norm_dir, f"norm_{idx:04d}.mp4")
            pct_base = ((idx - 1) / n) * 85.0
            pct_step = (1.0 / n) * 85.0

            emit_progress(pct_base, f"[{idx}/{n}] Normalizing {name}")
            print(f"\n[{idx}/{n}] Normalizing {name}...")

            info = infos[vf]
            vf_filter = (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,format=yuv420p"
            )

            af_filters = []
            if info.has_audio:
                af_filters = [
                    "loudnorm=I=-16:LRA=11:TP=-1.5,aresample=48000",
                    "aresample=48000"
                ]
            else:
                af_filters = [None]

            success = False
            t0 = time.perf_counter()

            for af_filter in af_filters:
                if success:
                    break
                for attempt in encoder_attempts():
                    label, hwaccel, vcodec = attempt
                    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
                    if hwaccel:
                        cmd += ["-hwaccel", "cuda"]
                    cmd += ["-i", vf]
                    if not info.has_audio:
                        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

                    cmd += ["-vf", vf_filter]
                    if af_filter:
                        cmd += ["-af", af_filter]

                    cmd += vcodec + AUDIO_ARGS + ["-movflags", "+faststart", norm_path]

                    code, err = run_ffmpeg(
                        cmd, info.duration,
                        lambda f: emit_progress(pct_base + pct_step * f, f"[{idx}/{n}] Normalizing {name} ({f*100:.0f}%)")
                    )
                    if code == 0 and os.path.exists(norm_path) and os.path.getsize(norm_path) > 0:
                        note_encoder_success(attempt)
                        success = True
                        break
                    if err.strip():
                        print(f"WARNING: Normalization failed with {label} ({af_filter}): {err.strip().splitlines()[-1]}")

            if success:
                normalized_files.append(norm_path)
                print(f"  ✔ Normalized {name} ({fmt_size(norm_path)}) in {fmt_time(time.perf_counter() - t0)}")
            else:
                print(f"ERROR: Failed to normalize {name}.")

        if not normalized_files:
            print("ERROR: No files were successfully normalized.")
            sys.exit(1)

        emit_progress(85.0, "Stitching normalized files into final video...")
        os.makedirs(output_dir, exist_ok=True)

        final_file = stitch_batch(normalized_files, output_dir,
                                  lambda f: emit_progress(85.0 + f * 14.0, f"Batch stitching ({f*100:.0f}%)"))

        total_s = time.perf_counter() - job_start
        print("\n" + "=" * 50)
        print("FOLDER STITCH SUMMARY")
        print("=" * 50)
        print(f"  Input folder:       {input_path}")
        print(f"  Videos normalized:  {len(normalized_files)} / {len(video_files)}")
        print(f"  Target specs:       {w}x{h} @ {fps}fps, AAC 48kHz stereo, normalized volume")
        if final_file:
            print(f"  Final batch video:  {final_file} ({fmt_size(final_file)})")
        print(f"  Total time:         {fmt_time(total_s)}")
        print("=" * 50)

        emit_progress(100.0, "Done ✔")
        if open_folder_when_done and final_file:
            open_folder(os.path.abspath(output_dir))

    finally:
        shutil.rmtree(temp_norm_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="The Video Keyword Clip Stitcher")
    parser.add_argument("-i", "--input", required=True, help="Input video file or folder")
    parser.add_argument("-k", "--keywords", default="",
                        help="Comma-separated keywords/phrases to search for")
    parser.add_argument("-p", "--pad", type=float, default=1.0,
                        help="Padding around keywords in seconds (default: 1.0)")
    parser.add_argument(
        "-m", "--model", default=CRISPER_TURBO_MODEL,
        help=(
            "Transcription model. Use 'crisper-turbo' for CrisperWhisper 2.0 "
            "Turbo word timestamps, or a faster-whisper model such as "
            "large-v3 (default: crisper-turbo)"
        ),
    )
    parser.add_argument("-o", "--output-dir", default="output",
                        help="Output directory (default: 'output')")
    parser.add_argument("--outputs", default="stitched",
                        help="Comma-separated outputs to create & keep: any of "
                             "'clips' (individual clip files), 'stitched' (one "
                             "stitched video per source video), 'batch' (all "
                             "stitched videos joined into one). Default: stitched")
    parser.add_argument("--defer-stitch", action="store_true",
                        help="Analyze ALL videos (transcribe + find keywords) first, "
                             "then do all stitching at the end")
    parser.add_argument("--stitch-only", action="store_true",
                        help="Only stitch existing video files in input folder after normalizing size and volume (skips transcription)")
    parser.add_argument("--open-folder", action="store_true",
                        help="Open the output folder when the job completes")
    args = parser.parse_args()

    input_path = Path(args.input)

    if args.stitch_only:
        run_stitch_only(input_path, args.output_dir, args.open_folder)
        return

    reexec_returncode = relaunch_for_crisper_if_needed(args.model)
    if reexec_returncode is not None:
        raise SystemExit(reexec_returncode)

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ERROR: ffmpeg/ffprobe not found on PATH. Install FFmpeg first.")
        sys.exit(1)

    outputs = {o.strip().lower() for o in args.outputs.split(",") if o.strip()}
    valid = {"clips", "stitched", "batch"}
    if not outputs or not outputs <= valid:
        print(f"ERROR: --outputs must be a comma list from {sorted(valid)} (got '{args.outputs}').")
        sys.exit(1)
    want_clips = "clips" in outputs
    want_stitched = "stitched" in outputs
    want_batch = "batch" in outputs
    need_stitch = want_stitched or want_batch

    singles, phrases = parse_keywords(args.keywords)
    if not singles and not phrases:
        print("ERROR: No valid keywords provided.")
        sys.exit(1)
    shown = sorted(singles) + [" ".join(p) for p in phrases]
    print(f"Target keywords: {shown}")

    os.makedirs(args.output_dir, exist_ok=True)

    input_path = Path(args.input)
    if input_path.is_file():
        video_files = [str(input_path)]
    elif input_path.is_dir():
        video_files = []
        for ext in VIDEO_EXTS:
            video_files.extend(glob.glob(os.path.join(str(input_path), ext)))
        video_files = sorted(set(video_files))
        if not video_files:
            print(f"ERROR: No video files found in {input_path}")
            sys.exit(1)
    else:
        print(f"ERROR: Input path does not exist: {input_path}")
        sys.exit(1)

    job_start = time.perf_counter()

    emit_progress(0, "Analyzing input videos")
    print(f"\nAnalyzing {len(video_files)} video(s)...")
    infos = {}
    for vf in video_files:
        info = probe_video(vf)
        if info:
            infos[vf] = info
            print(f"  {os.path.basename(vf)}: {info.width}x{info.height} @ {info.fps:.2f}fps, "
                  f"{fmt_time(info.duration)}{'' if info.has_audio else ' (NO AUDIO)'}")
        else:
            print(f"  WARNING: {os.path.basename(vf)} could not be read and will be skipped.")
    video_files = [vf for vf in video_files if vf in infos]
    if not video_files:
        print("ERROR: No readable video files.")
        sys.exit(1)

    target = choose_target(list(infos.values()))
    if need_stitch:
        print(f"Normalization target: {target[0]}x{target[1]} @ {target[2]}fps, AAC 48kHz stereo")

    plan = []
    if want_clips:
        plan.append("individual clips")
    if want_stitched:
        plan.append("per-video stitched")
    if want_batch:
        plan.append("combined batch video")
    order = "analyze everything first, then stitch" if args.defer_stitch else "stitch each video as it finishes"
    print(f"Plan: keep [{', '.join(plan)}]; {order}.")

    display_model = (
        "CrisperWhisper 2.0 Turbo (0.8B)"
        if is_crisper_model(args.model)
        else f"Whisper {args.model}"
    )
    emit_progress(1, f"Loading {display_model}")
    t0 = time.perf_counter()
    try:
        transcriber = load_transcriber(args.model)
    except Exception as exc:
        print(f"ERROR: Could not load {display_model}: {exc}")
        raise SystemExit(1) from exc
    model_load_s = time.perf_counter() - t0

    # --- Progress bookkeeping: work units per stage ---------------------
    n = len(video_files)
    U_ANALYZE, U_CLIPS, U_STITCH, U_BATCH = 6.0, 1.0, 3.0, 2.0
    total_units = n * U_ANALYZE
    if want_clips:
        total_units += n * U_CLIPS
    if need_stitch:
        total_units += n * U_STITCH
    if want_batch and n > 1:
        total_units += U_BATCH
    state = {"done": 0.0}

    def stage(units):
        base = state["done"]

        def prog(frac, msg):
            pct = 2 + (base + units * min(1.0, max(0.0, frac))) / total_units * 96
            emit_progress(pct, msg)

        def finish():
            state["done"] = base + units
        return prog, finish

    def out_path_for(vf):
        name = os.path.splitext(os.path.basename(vf))[0]
        return os.path.join(args.output_dir, f"{name}_stitched.mp4")

    # --- Phase 1: analyze (and per-video work in immediate mode) --------
    all_stats: List[VideoStats] = []
    for idx, vf in enumerate(video_files):
        label = f"[{idx + 1}/{n}]"
        prog, finish = stage(U_ANALYZE)
        stats = analyze_video(vf, infos[vf], transcriber, singles, phrases, args.pad,
                              lambda f, m: prog(f, f"{label} {m}"))
        finish()
        all_stats.append(stats)

        if stats.intervals and want_clips:
            prog, finish = stage(U_CLIPS)
            save_individual_clips(stats, args.output_dir,
                                  lambda f, m: prog(f, f"{label} {m}"))
            finish()

        if stats.intervals and need_stitch and not args.defer_stitch:
            prog, finish = stage(U_STITCH)
            stitch_video(stats, target, out_path_for(vf),
                         lambda f, m: prog(f, f"{label} {m}"))
            finish()

    # --- Phase 2: deferred stitching ------------------------------------
    if need_stitch and args.defer_stitch:
        to_stitch = [s for s in all_stats if s.intervals]
        print(f"\nAll videos analyzed. Stitching {len(to_stitch)} video(s)...")
        for idx, stats in enumerate(to_stitch):
            label = f"[{idx + 1}/{len(to_stitch)}]"
            prog, finish = stage(U_STITCH)
            stitch_video(stats, target, out_path_for(stats.path),
                         lambda f, m: prog(f, f"{label} {m}"))
            finish()
        # account for skipped stitch units of videos without keywords
        state["done"] += U_STITCH * (n - len(to_stitch))

    stitched_files = [s.output for s in all_stats if s.output]

    # --- Phase 3: batch stitch ------------------------------------------
    batch_file = None
    batch_s = 0.0
    if want_batch:
        if len(stitched_files) > 1:
            prog, finish = stage(U_BATCH)
            t0 = time.perf_counter()
            batch_file = stitch_batch(stitched_files, args.output_dir, prog)
            batch_s = time.perf_counter() - t0
            finish()
        elif len(stitched_files) == 1:
            print("\nOnly one video produced output; nothing to batch-stitch.")
        else:
            print("\nNo stitched videos to combine into a batch.")

    # --- Phase 4: drop intermediates the user didn't ask for ------------
    if not want_stitched and stitched_files:
        if want_batch and not batch_file:
            print("WARNING: No batch video was made; keeping per-video stitched file(s) instead.")
        else:
            emit_progress(99, "Removing intermediate files")
            for f in stitched_files:
                try:
                    os.remove(f)
                except OSError as e:
                    print(f"WARNING: Could not delete {f}: {e}")
            print(f"Removed {len(stitched_files)} intermediate per-video stitched file(s).")
            stitched_files = []

    total_s = time.perf_counter() - job_start
    outputs_desc = ", ".join(plan)
    print_summary(all_stats, batch_file, outputs_desc, model_load_s, batch_s, total_s)
    emit_progress(100, "Done")

    produced = batch_file or stitched_files or any(s.clips_saved for s in all_stats)
    if args.open_folder and produced:
        open_folder(os.path.abspath(args.output_dir))

    if not produced:
        sys.exit(2)


if __name__ == "__main__":
    main()
