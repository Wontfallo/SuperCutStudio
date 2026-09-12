"""Video format normalization and aspect ratio conforming (16:9, 9:16 Blur Canvas, 1:1)."""

from __future__ import annotations

from typing import Tuple

from ..models import AspectRatioMode


def build_aspect_ratio_filter(
    mode: AspectRatioMode,
    target_w: int,
    target_h: int,
    fps: int,
    tag: str = "",
) -> str:
    """
    Build FFmpeg filter string for scaling, fps, aspect ratio conversion, and canvas formatting.
    Ensures safe even dimensions (divisible by 2) for encoder compatibility and supports tag scoping.
    """
    lbl_bg_in = f"bg_in{tag}"
    lbl_fg_in = f"fg_in{tag}"
    lbl_bg = f"bg{tag}"
    lbl_fg = f"fg{tag}"

    if mode == AspectRatioMode.VERTICAL_9_16:
        # Standard TikTok/Reels resolution: 1080x1920 or user-specified vertical dimensions
        if target_w and target_h and target_w < target_h:
            vw = (target_w // 2) * 2
            vh = (target_h // 2) * 2
        else:
            vw = 1080
            vh = 1920

        return (
            f"split=2[{lbl_bg_in}][{lbl_fg_in}];"
            f"[{lbl_bg_in}]scale={vw}:{vh}:force_original_aspect_ratio=increase:force_divisible_by=2,"
            f"crop={vw}:{vh},boxblur=25:5,eq=brightness=-0.08:contrast=1.05,setsar=1[{lbl_bg}];"
            f"[{lbl_fg_in}]scale={vw}:{vh}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[{lbl_fg}];"
            f"[{lbl_bg}][{lbl_fg}]overlay=x='(W-w)/2':y='(H-h)/2',fps={fps},settb=AVTB,setsar=1,format=yuv420p"
        )
    elif mode == AspectRatioMode.SQUARE_1_1:
        # Square: 1080x1080 or min(w, h)
        raw_s = min(target_w, target_h) if target_w and target_h else 1080
        sw = max(128, (raw_s // 2) * 2)

        return (
            f"split=2[{lbl_bg_in}][{lbl_fg_in}];"
            f"[{lbl_bg_in}]scale={sw}:{sw}:force_original_aspect_ratio=increase:force_divisible_by=2,"
            f"crop={sw}:{sw},boxblur=25:5,eq=brightness=-0.08:contrast=1.05,setsar=1[{lbl_bg}];"
            f"[{lbl_fg_in}]scale={sw}:{sw}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[{lbl_fg}];"
            f"[{lbl_bg}][{lbl_fg}]overlay=x='(W-w)/2':y='(H-h)/2',fps={fps},settb=AVTB,setsar=1,format=yuv420p"
        )
    else:
        # Standard 16:9 or Source: scale with aspect preservation and black padding if necessary
        w = max(128, ((target_w or 1920) // 2) * 2)
        h = max(128, ((target_h or 1080) // 2) * 2)

        return (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},settb=AVTB,setsar=1,format=yuv420p"
        )


def build_audio_filter(normalize_loudness: bool = True) -> str:
    """Build standard 48kHz stereo resampler with audio sync and optional EBU R128 loudness normalization."""
    base_resample = "aresample=48000:async=1,aformat=channel_layouts=stereo:sample_fmts=fltp"
    if normalize_loudness:
        return f"loudnorm=I=-16:LRA=11:TP=-1.5,{base_resample}"
    return base_resample
