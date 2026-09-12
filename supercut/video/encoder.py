"""Hardware encoder orchestration, NVENC acceleration, and watchdog process management."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from ..models import EncoderChoice, QualityPreset
from ..paths import ffmpeg_path
from ..procs import hidden_kwargs, run_hidden

AUDIO_AAC_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]

NVENC_HEVC_ARGS = [
    "-c:v", "hevc_nvenc",
    "-preset", "p6",
    "-tune", "hq",
    "-rc", "vbr",
    "-cq", "22",
    "-b:v", "0",
    "-spatial-aq", "1",
    "-temporal-aq", "1",
    "-pix_fmt", "yuv420p",
]
NVENC_H264_ARGS = [
    "-c:v", "h264_nvenc",
    "-preset", "p6",
    "-tune", "hq",
    "-rc", "vbr",
    "-cq", "20",
    "-b:v", "0",
    "-spatial-aq", "1",
    "-temporal-aq", "1",
    "-pix_fmt", "yuv420p",
]
NVENC_AV1_ARGS = [
    "-c:v", "av1_nvenc",
    "-preset", "p6",
    "-tune", "hq",
    "-rc", "vbr",
    "-cq", "24",
    "-b:v", "0",
    "-spatial-aq", "1",
    "-pix_fmt", "yuv420p",
]
CPU_X264_ARGS = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
CPU_X265_ARGS = ["-c:v", "libx265", "-preset", "fast", "-crf", "24", "-pix_fmt", "yuv420p"]

# Auto mode prefers H.264: it plays everywhere (browsers, phones, NLEs, social uploads).
# HEVC / AV1 remain selectable explicitly for smaller files.
ENCODER_FALLBACK_ORDER = [
    ("h264_nvenc (NVIDIA GPU)", True, NVENC_H264_ARGS),
    ("h264_nvenc (NVIDIA GPU No-HWDec)", False, NVENC_H264_ARGS),
    ("hevc_nvenc (NVIDIA GPU)", True, NVENC_HEVC_ARGS),
    ("libx264 (CPU)", False, CPU_X264_ARGS),
    ("libx265 (CPU)", False, CPU_X265_ARGS),
]

# Quality preset -> quantizer offsets applied to the base args above.
_QUALITY_DELTA: Dict[QualityPreset, int] = {
    QualityPreset.DRAFT: +6,
    QualityPreset.BALANCED: 0,
    QualityPreset.HIGH: -4,
}
_QUALITY_PRESET_NAME: Dict[QualityPreset, Dict[str, str]] = {
    QualityPreset.DRAFT: {"nvenc": "p3", "cpu": "veryfast"},
    QualityPreset.BALANCED: {"nvenc": "p6", "cpu": "fast"},
    QualityPreset.HIGH: {"nvenc": "p7", "cpu": "medium"},
}

_cached_successful_encoder: Optional[Tuple[str, bool, List[str]]] = None
_nvenc_probed: dict[str, bool] = {}
_encoder_probe_cache: Optional[Dict[str, bool]] = None
_probe_lock = threading.Lock()


def is_nvenc_available(codec: str = "h264_nvenc", force_probe: bool = False) -> bool:
    """Probe if an NVENC hardware encoder is functional on the current system."""
    global _nvenc_probed
    if not force_probe and codec in _nvenc_probed:
        return _nvenc_probed[codec]

    try:
        cmd = [
            ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=s=256x256:d=0.04",
            "-c:v",
            codec,
            "-f",
            "null",
            "-",
        ]
        res = run_hidden(cmd, capture_output=True, text=True, timeout=8)
        supported = (res.returncode == 0)
    except Exception:
        supported = False

    _nvenc_probed[codec] = supported
    return supported


def probe_available_encoders(force: bool = False) -> Dict[str, bool]:
    """Functional probe of every encoder the app can use (cached)."""
    global _encoder_probe_cache
    with _probe_lock:
        if _encoder_probe_cache is not None and not force:
            return dict(_encoder_probe_cache)
        result: Dict[str, bool] = {}
        for codec in ("hevc_nvenc", "h264_nvenc", "av1_nvenc", "libx264", "libx265"):
            result[codec] = is_nvenc_available(codec, force_probe=force)
        _encoder_probe_cache = result
        return dict(result)


def reset_encoder_cache() -> None:
    """Reset cached successful encoder and probe cache (useful for testing and re-probing)."""
    global _cached_successful_encoder, _nvenc_probed, _encoder_probe_cache
    _cached_successful_encoder = None
    _nvenc_probed.clear()
    _encoder_probe_cache = None


def apply_quality(args: List[str], quality: QualityPreset = QualityPreset.BALANCED) -> List[str]:
    """Return a copy of encoder args adjusted for the requested quality preset."""
    if quality == QualityPreset.BALANCED:
        return list(args)
    out = list(args)
    delta = _QUALITY_DELTA.get(quality, 0)
    is_nvenc = "nvenc" in " ".join(out)
    for i, tok in enumerate(out[:-1]):
        if tok in ("-cq", "-crf"):
            try:
                out[i + 1] = str(max(0, min(51, int(out[i + 1]) + delta)))
            except ValueError:
                pass
        elif tok == "-preset":
            names = _QUALITY_PRESET_NAME.get(quality)
            if names:
                out[i + 1] = names["nvenc"] if is_nvenc else names["cpu"]
    return out


def get_encoder_attempts(
    user_choice: EncoderChoice = EncoderChoice.AUTO,
    quality: QualityPreset = QualityPreset.BALANCED,
) -> List[Tuple[str, bool, List[str]]]:
    """Get list of encoder configurations to try, prioritizing user preference and cached successes."""
    global _cached_successful_encoder

    if user_choice == EncoderChoice.NVENC_HEVC:
        attempts = [
            ("hevc_nvenc (NVIDIA GPU)", True, NVENC_HEVC_ARGS),
            ("hevc_nvenc (NVIDIA GPU No-HWDec)", False, NVENC_HEVC_ARGS),
            ("libx265 (CPU Fallback)", False, CPU_X265_ARGS),
            ("libx264 (CPU Fallback)", False, CPU_X264_ARGS),
        ]
    elif user_choice == EncoderChoice.NVENC_H264:
        attempts = [
            ("h264_nvenc (NVIDIA GPU)", True, NVENC_H264_ARGS),
            ("h264_nvenc (NVIDIA GPU No-HWDec)", False, NVENC_H264_ARGS),
            ("libx264 (CPU Fallback)", False, CPU_X264_ARGS),
        ]
    elif user_choice == EncoderChoice.NVENC_AV1:
        attempts = [
            ("av1_nvenc (NVIDIA GPU)", True, NVENC_AV1_ARGS),
            ("av1_nvenc (NVIDIA GPU No-HWDec)", False, NVENC_AV1_ARGS),
            ("libx264 (CPU Fallback)", False, CPU_X264_ARGS),
        ]
    elif user_choice == EncoderChoice.CPU_X264:
        attempts = [("libx264 (CPU)", False, CPU_X264_ARGS)]
    elif user_choice == EncoderChoice.CPU_X265:
        attempts = [("libx265 (CPU)", False, CPU_X265_ARGS)]
    elif _cached_successful_encoder is not None:
        # Auto mode: prioritize last successful encoder
        attempts = [_cached_successful_encoder] + [a for a in ENCODER_FALLBACK_ORDER if a != _cached_successful_encoder]
    elif not is_nvenc_available("h264_nvenc"):
        # If NVENC is not available on this hardware, put CPU encoders first to avoid wasted failures
        cpu_first = [("libx264 (CPU)", False, CPU_X264_ARGS), ("libx265 (CPU)", False, CPU_X265_ARGS)]
        attempts = cpu_first + [a for a in ENCODER_FALLBACK_ORDER if not a[0].startswith("libx")]
    else:
        attempts = list(ENCODER_FALLBACK_ORDER)

    if quality == QualityPreset.BALANCED:
        return attempts
    return [(label, hw, apply_quality(args, quality)) for label, hw, args in attempts]


def register_encoder_success(encoder_tuple: Tuple[str, bool, List[str]]) -> None:
    global _cached_successful_encoder
    # Store the base (balanced) variant so quality presets can be re-applied later.
    label, hw, args = encoder_tuple
    for base_label, base_hw, base_args in ENCODER_FALLBACK_ORDER:
        if base_label == label:
            _cached_successful_encoder = (base_label, base_hw, base_args)
            return
    _cached_successful_encoder = (label, hw, list(args))


def last_successful_encoder() -> Optional[str]:
    return _cached_successful_encoder[0] if _cached_successful_encoder else None


def terminate_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        run_hidden(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, text=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _popen_kwargs() -> dict:
    # Hide the console window of child processes when running the GUI build.
    return hidden_kwargs()


def run_ffmpeg_watched(
    cmd: List[str],
    total_duration: float = 0.0,
    progress_callback: Optional[Callable[[float], None]] = None,
    stall_timeout_seconds: float = 180.0,
    cancel_check: Optional[Callable[[], bool]] = None,
    cwd: Optional[str] = None,
) -> Tuple[int, str]:
    """Execute FFmpeg with non-blocking stdout reading, stall watchdog, and abrupt cancellation protection."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=cwd or None,
        **_popen_kwargs(),
    )

    out_queue: queue.Queue = queue.Queue()

    def reader_thread():
        try:
            for line in proc.stdout:
                out_queue.put(line)
        finally:
            out_queue.put(None)

    t = threading.Thread(target=reader_thread, daemon=True)
    t.start()

    stderr_lines: List[str] = []
    last_output_time = time.monotonic()
    stalled = False
    cancelled = False

    while True:
        if cancel_check and cancel_check():
            cancelled = True
            stderr_lines.append("Terminated: Cancelled by user")
            terminate_process_tree(proc)
            break

        try:
            line = out_queue.get(timeout=0.25)
        except queue.Empty:
            if cancel_check and cancel_check():
                cancelled = True
                stderr_lines.append("Terminated: Cancelled by user")
                terminate_process_tree(proc)
                break
            if proc.poll() is not None and not t.is_alive():
                break
            if stall_timeout_seconds > 0 and (time.monotonic() - last_output_time) >= stall_timeout_seconds:
                stalled = True
                stderr_lines.append(f"Terminated: FFmpeg stalled with no progress for {stall_timeout_seconds}s")
                terminate_process_tree(proc)
                break
            continue

        if line is None:
            break

        last_output_time = time.monotonic()
        clean = line.strip()
        if clean.startswith("out_time_ms=") and total_duration > 0 and progress_callback:
            try:
                micros = int(clean.split("=", 1)[1])
                secs = micros / 1_000_000.0
                progress_callback(min(1.0, max(0.0, secs / total_duration)))
            except ValueError:
                pass
        elif clean.startswith("out_time_us=") and total_duration > 0 and progress_callback:
            try:
                secs = int(clean.split("=", 1)[1]) / 1_000_000.0
                progress_callback(min(1.0, max(0.0, secs / total_duration)))
            except ValueError:
                pass
        elif clean and not clean.startswith(("frame=", "fps=", "bitrate=", "total_size=", "out_time=", "dup_frames=",
                                              "drop_frames=", "speed=", "progress=", "stream_0_0")):
            stderr_lines.append(clean)
            if len(stderr_lines) > 400:
                del stderr_lines[:100]

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        terminate_process_tree(proc)

    t.join(timeout=2)
    if proc.stdout:
        try:
            proc.stdout.close()
        except Exception:
            pass
    code = -1 if (stalled or cancelled) else (proc.returncode if proc.returncode is not None else -1)
    return code, "\n".join(stderr_lines)


def base_ffmpeg_cmd(progress: bool = True) -> List[str]:
    cmd = [ffmpeg_path(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error"]
    if progress:
        cmd += ["-nostats", "-progress", "pipe:1"]
    return cmd


__all__ = [
    "AUDIO_AAC_ARGS",
    "CPU_X264_ARGS",
    "CPU_X265_ARGS",
    "ENCODER_FALLBACK_ORDER",
    "NVENC_AV1_ARGS",
    "NVENC_H264_ARGS",
    "NVENC_HEVC_ARGS",
    "apply_quality",
    "base_ffmpeg_cmd",
    "get_encoder_attempts",
    "is_nvenc_available",
    "last_successful_encoder",
    "probe_available_encoders",
    "register_encoder_success",
    "reset_encoder_cache",
    "run_ffmpeg_watched",
    "terminate_process_tree",
]
