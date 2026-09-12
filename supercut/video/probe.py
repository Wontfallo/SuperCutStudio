"""ffprobe inspection and media metadata analysis."""

from __future__ import annotations

import json
import subprocess
from typing import List, Optional, Tuple

from ..models import MediaInfo
from ..paths import ffprobe_path
from ..procs import run_hidden


def probe_media(file_path: str) -> Optional[MediaInfo]:
    """Inspect media file using ffprobe and extract comprehensive stream details."""
    cmd = [
        ffprobe_path(),
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,sample_rate,channels,bit_rate",
        "-show_entries",
        "format=duration,bit_rate",
        "-of",
        "json",
        file_path,
    ]

    try:
        res = run_hidden(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        data = json.loads(res.stdout)
    except Exception:
        return None

    try:
        info = MediaInfo()
        format_data = data.get("format", {})
        try:
            info.duration = float(format_data.get("duration", 0.0) or 0.0)
        except (ValueError, TypeError):
            pass

        try:
            info.bitrate = int(format_data.get("bit_rate", 0) or 0)
        except (ValueError, TypeError):
            pass

        for stream in data.get("streams", []):
            ctype = stream.get("codec_type")
            if ctype == "video" and not info.width:
                try:
                    info.width = int(stream.get("width", 0) or 0)
                    info.height = int(stream.get("height", 0) or 0)
                except (ValueError, TypeError):
                    pass
                info.video_codec = str(stream.get("codec_name", "") or "")

                for rate_key in ("avg_frame_rate", "r_frame_rate"):
                    rate = str(stream.get(rate_key, "") or "")
                    if "/" in rate:
                        num, _, den = rate.partition("/")
                        try:
                            if float(den) > 0 and float(num) > 0:
                                info.fps = float(num) / float(den)
                                break
                        except (ValueError, TypeError):
                            pass
            elif ctype == "audio":
                info.has_audio = True
                info.audio_codec = str(stream.get("codec_name", "") or "")
                try:
                    info.sample_rate = int(stream.get("sample_rate", 48000) or 48000)
                    info.channels = int(stream.get("channels", 2) or 2)
                except (ValueError, TypeError):
                    pass

        return info if info.width else None
    except Exception:
        return None


def calculate_optimal_batch_target(infos: List[MediaInfo]) -> Tuple[int, int, int]:
    """Determine the optimal resolution and FPS to minimize re-scaling across a batch."""
    from collections import Counter

    valid = [i for i in infos if i.width > 0 and i.height > 0]
    if not valid:
        return 1920, 1080, 30

    res_counter = Counter((i.width, i.height) for i in valid)
    (w, h), _ = max(res_counter.items(), key=lambda kv: (kv[1], kv[0][0] * kv[0][1]))

    fps_counter = Counter(int(round(i.fps)) for i in valid if i.fps > 0)
    fps = max(fps_counter.items(), key=lambda kv: (kv[1], kv[0]))[0] if fps_counter else 30
    fps = max(10, min(60, fps))

    # Ensure even dimensions
    return w - (w % 2), h - (h % 2), fps
