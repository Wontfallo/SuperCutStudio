"""Real audio waveform peak extraction for the reviewer timeline.

FFmpeg decodes the audio track to mono 8 kHz signed 16-bit PCM, which is then
reduced to ``buckets`` peak values in the 0..1 range. Results are cached on disk
keyed by file identity so re-opening a video is instant.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from ..paths import ffmpeg_path, waveform_cache_dir

SAMPLE_RATE = 8000


def _cache_key(media_path: str, buckets: int) -> str:
    try:
        st = os.stat(media_path)
        raw = f"{os.path.abspath(media_path)}|{st.st_size}|{int(st.st_mtime)}|{buckets}"
    except OSError:
        raw = f"{media_path}|{buckets}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def _decode_pcm(media_path: str, timeout: float) -> bytes:
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        media_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if res.returncode != 0:
        return b""
    return res.stdout


def _reduce_peaks(pcm: bytes, buckets: int) -> List[float]:
    if not pcm or buckets <= 0:
        return []
    try:
        import numpy as np

        samples = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype="<i2")
        if samples.size == 0:
            return []
        n = samples.size
        edges = np.linspace(0, n, buckets + 1, dtype=np.int64)
        peaks: List[float] = []
        abs_samples = np.abs(samples.astype(np.int32))
        for i in range(buckets):
            a, b = int(edges[i]), int(edges[i + 1])
            if b <= a:
                peaks.append(0.0)
                continue
            chunk = abs_samples[a:b]
            # RMS-weighted peak gives a more natural looking waveform than raw max.
            peak = float(chunk.max())
            rms = float((chunk.astype(np.float64) ** 2).mean() ** 0.5)
            val = (0.6 * peak + 0.4 * rms * 2.2) / 32768.0
            peaks.append(min(1.0, val))
    except Exception:
        # Pure-python fallback (slower but dependency free)
        import array

        arr = array.array("h")
        arr.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
        n = len(arr)
        if n == 0:
            return []
        peaks = []
        step = n / buckets
        for i in range(buckets):
            a = int(i * step)
            b = max(a + 1, int((i + 1) * step))
            chunk = arr[a:b]
            peak = max((abs(x) for x in chunk), default=0)
            peaks.append(min(1.0, peak / 32768.0))

    # Light normalization so quiet recordings still show shape.
    top = max(peaks) if peaks else 0.0
    if 0.0 < top < 0.6:
        gain = min(3.0, 0.85 / top)
        peaks = [min(1.0, p * gain) for p in peaks]
    return [round(p, 4) for p in peaks]


def compute_waveform(
    media_path: str,
    buckets: int = 600,
    duration_hint: float = 0.0,
    use_cache: bool = True,
) -> Dict[str, object]:
    """Return ``{"buckets": N, "peaks": [...], "duration": secs, "cached": bool}``."""
    buckets = max(50, min(4000, int(buckets)))
    key = _cache_key(media_path, buckets)
    cache_file: Optional[Path] = waveform_cache_dir() / f"{key}.json"

    if use_cache and cache_file and cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("peaks"):
                data["cached"] = True
                return data
        except Exception:
            pass

    timeout = 60.0 + max(0.0, duration_hint) * 0.5
    pcm = _decode_pcm(media_path, timeout=timeout)
    peaks = _reduce_peaks(pcm, buckets)
    duration = len(pcm) / 2.0 / SAMPLE_RATE if pcm else float(duration_hint or 0.0)

    data = {"buckets": buckets, "peaks": peaks, "duration": round(duration, 3), "cached": False}
    if use_cache and peaks and cache_file:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass
    return data


__all__ = ["compute_waveform"]
