"""Audio extraction, waveform sampling, and acoustic analysis."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from ..paths import ffmpeg_path


def extract_audio_for_asr(video_path: str, output_wav_path: Optional[str] = None) -> Optional[str]:
    """Extract 16kHz mono 16-bit PCM WAV for Whisper / CrisperWhisper speech recognition."""
    if not output_wav_path:
        fd, output_wav_path = tempfile.mkstemp(prefix="supercut_asr_", suffix=".wav")
        os.close(fd)

    cmd = [
        ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-err_detect",
        "ignore_err",
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        output_wav_path,
    ]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if res.returncode == 0 and os.path.exists(output_wav_path) and os.path.getsize(output_wav_path) > 0:
            return output_wav_path
    except (subprocess.SubprocessError, OSError):
        pass

    if os.path.exists(output_wav_path):
        try:
            os.remove(output_wav_path)
        except OSError:
            pass
    return None
