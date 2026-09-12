"""Thumbnail generation, contact sheet / sprite sheet creation, and keyframe extraction."""

from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

from ..models import ClipInterval
from ..paths import ffmpeg_path
from .probe import probe_media


def extract_clip_thumbnail(
    video_path: str,
    timestamp: float,
    output_path: str,
    width: int = 320,
    height: int = 180,
) -> bool:
    """
    Extract a single crisp preview thumbnail at a given timestamp using fast FFmpeg keyframe seeking.

    Args:
        video_path: Absolute or relative path to the input video file.
        timestamp: Seek position in seconds from start of video.
        output_path: Destination path for the saved image (.jpg, .png).
        width: Thumbnail width in pixels (even integer).
        height: Thumbnail height in pixels (even integer).

    Returns:
        bool: True if thumbnail was successfully extracted and written, False otherwise.
    """
    if not video_path or not os.path.isfile(video_path):
        return False

    try:
        if os.path.getsize(video_path) == 0:
            return False
    except OSError:
        return False

    # Normalize dimensions to even positive integers
    target_w = max(16, int(width))
    target_h = max(16, int(height))
    target_w = (target_w // 2) * 2
    target_h = (target_h // 2) * 2

    # Safe non-negative timestamp
    ts = max(0.0, float(timestamp))

    # Ensure parent directory exists
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Fast input seek (-ss before -i) jumps directly to keyframe without decoding preceding stream
    scale_pad_vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black"
    )

    cmd = [
        ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{ts:.3f}",
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", scale_pad_vf,
        "-q:v", "2",
        str(output_path),
    ]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=25,
        )
        if res.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
            return True

        # Clean up failed/corrupted partial output
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        return False
    except Exception:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        return False


def extract_batch_thumbnails(
    intervals: Sequence[ClipInterval],
    output_dir: str,
    default_video_path: Optional[str] = None,
    width: int = 320,
    height: int = 180,
    time_offset_ratio: float = 0.5,
    prefix: str = "thumb_",
    selected_only: bool = True,
) -> List[str]:
    """
    Batch extract preview thumbnails for a sequence of ClipIntervals.

    Args:
        intervals: List of ClipIntervals to generate thumbnails for.
        output_dir: Folder to store generated thumbnail images.
        default_video_path: Fallback video path if clip.source_video is empty.
        width: Thumbnail width in pixels.
        height: Thumbnail height in pixels.
        time_offset_ratio: Relative point within clip (0.0=start, 0.5=midpoint, 1.0=end)
                           used when no keyword hit timestamp is present.
        prefix: Filename prefix for generated images.
        selected_only: If True, only extract for intervals where clip.selected is True.

    Returns:
        List[str]: List of paths to successfully generated thumbnails.
    """
    os.makedirs(output_dir, exist_ok=True)
    generated: List[str] = []

    clips_to_process = [c for c in intervals if (not selected_only or c.selected)]

    for idx, clip in enumerate(clips_to_process, start=1):
        vpath = clip.source_video or default_video_path
        if not vpath or not os.path.isfile(vpath):
            continue

        # Choose best timestamp: keyword hit start if inside clip boundary, else midpoint
        if clip.keyword_hits and clip.start <= clip.keyword_hits[0].start <= clip.end:
            ts = clip.keyword_hits[0].start
        else:
            ratio = max(0.0, min(1.0, float(time_offset_ratio)))
            ts = clip.start + (clip.duration * ratio)
        ts = max(0.0, ts)

        c_idx = getattr(clip, "index", 0) or idx
        kw_clean = re.sub(r'[\\/*?:"<>|]', '_', clip.primary_keyword.replace(" ", "_"))
        out_filename = f"{prefix}{c_idx:03d}_{kw_clean}_{clip.start:.1f}s.jpg"
        out_path = os.path.join(output_dir, out_filename)

        ok = extract_clip_thumbnail(
            video_path=vpath,
            timestamp=ts,
            output_path=out_path,
            width=width,
            height=height,
        )
        if ok:
            generated.append(out_path)

    return generated


# Convenient aliases for callers
batch_extract_thumbnails = extract_batch_thumbnails
batch_generate_thumbnails = extract_batch_thumbnails
extract_clip_thumbnails = extract_batch_thumbnails
generate_thumbnails_for_clips = extract_batch_thumbnails


def _parse_color(val: Union[str, Tuple[int, int, int]]) -> Tuple[int, int, int]:
    """Parse color strings (hex, common names) or tuples into an RGB 3-tuple."""
    if isinstance(val, (tuple, list)) and len(val) >= 3:
        return (int(val[0]), int(val[1]), int(val[2]))
    if isinstance(val, str):
        val = val.strip()
        if val.startswith("#"):
            h = val.lstrip("#")
            if len(h) == 6:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            elif len(h) == 3:
                return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
        color_map = {
            "black": (0, 0, 0),
            "white": (255, 255, 255),
            "dark": (20, 24, 33),
            "gray": (128, 128, 128),
            "cyan": (0, 200, 220),
            "green": (50, 205, 50),
        }
        return color_map.get(val.lower(), (20, 24, 33))
    return (20, 24, 33)


def generate_contact_sheet(
    intervals: Optional[Sequence[ClipInterval]] = None,
    output_path: str = "contact_sheet.jpg",
    video_path: Optional[str] = None,
    image_paths: Optional[Sequence[str]] = None,
    columns: int = 4,
    rows: Optional[int] = None,
    tile_width: int = 320,
    tile_height: int = 180,
    padding: int = 10,
    margin: int = 16,
    bg_color: Union[str, Tuple[int, int, int]] = (20, 24, 33),
    header_title: Optional[str] = None,
    show_labels: bool = True,
    selected_only: bool = True,
    max_frames: int = 16,
    time_offset_ratio: float = 0.5,
) -> Optional[str]:
    """
    Generate a composite thumbnail sprite sheet / contact sheet for a quick visual index.

    Supports generating from:
    1. A list of ClipIntervals (extracts preview thumbnails per clip).
    2. A video file path (uniformly samples frames across duration).
    3. A list of existing image file paths.

    Args:
        intervals: Sequence of ClipIntervals to index.
        output_path: Destination filepath for the composite image (.jpg, .png).
        video_path: Input video path (used as fallback or for uniform sampling).
        image_paths: Optional sequence of existing thumbnail images to tile.
        columns: Number of grid columns (default 4).
        rows: Optional fixed number of rows (default auto-calculated).
        tile_width: Width of each thumbnail tile in pixels.
        tile_height: Height of each thumbnail tile in pixels.
        padding: Space in pixels between adjacent tiles.
        margin: Outer border margin in pixels around the entire grid.
        bg_color: Background color (RGB tuple or hex/color name string).
        header_title: Optional title header banner text.
        show_labels: Whether to render bottom label banners per tile with metadata.
        selected_only: For intervals, only include clips where clip.selected is True.
        max_frames: Maximum frames to extract when sampling directly from video_path.
        time_offset_ratio: Timestamp offset within clip if no keyword hit timestamp exists.

    Returns:
        Optional[str]: Path to the generated contact sheet image, or None on failure.
    """
    tiles: List[Tuple[Image.Image, str, bool]] = []  # (PIL Image, label, is_high_energy)

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Case 1: Clip intervals
        if intervals is not None:
            clips_to_process = [c for c in intervals if (not selected_only or c.selected)]
            for idx, clip in enumerate(clips_to_process, start=1):
                vpath = clip.source_video or video_path
                if not vpath or not os.path.isfile(vpath):
                    continue

                if clip.keyword_hits and clip.start <= clip.keyword_hits[0].start <= clip.end:
                    ts = clip.keyword_hits[0].start
                else:
                    ratio = max(0.0, min(1.0, float(time_offset_ratio)))
                    ts = clip.start + (clip.duration * ratio)
                ts = max(0.0, ts)

                c_idx = getattr(clip, "index", 0) or idx
                thumb_file = os.path.join(tmp_dir, f"tile_{c_idx}_{idx}.jpg")
                extracted = extract_clip_thumbnail(vpath, ts, thumb_file, width=tile_width, height=tile_height)

                is_energy = (
                    getattr(clip, "is_high_energy", False)
                    or clip.primary_keyword.lower() in ("high-energy", "high energy", "energy")
                    or bool(clip.custom_title and "high energy" in clip.custom_title.lower())
                    or any(
                        h.keyword.lower() in ("high-energy", "high energy", "energy")
                        for h in getattr(clip, "keyword_hits", [])
                    )
                )

                if extracted and os.path.isfile(thumb_file):
                    try:
                        img = Image.open(thumb_file).convert("RGB")
                    except Exception:
                        img = Image.new("RGB", (tile_width, tile_height), (40, 40, 40))
                else:
                    img = Image.new("RGB", (tile_width, tile_height), (40, 40, 40))

                label = f"#{c_idx:02d} {clip.primary_keyword} ({clip.start:.1f}s)"
                tiles.append((img, label, is_energy))

        # Case 2: Existing image paths
        elif image_paths is not None:
            for idx, ipath in enumerate(image_paths, start=1):
                if not os.path.isfile(ipath):
                    continue
                try:
                    img = Image.open(ipath).convert("RGB")
                    if img.size != (tile_width, tile_height):
                        img = img.resize((tile_width, tile_height), Image.Resampling.LANCZOS)
                    lbl = Path(ipath).stem
                    is_energy = "energy" in lbl.lower()
                    tiles.append((img, lbl, is_energy))
                except Exception:
                    continue

        # Case 3: Video file uniform sampling
        elif video_path is not None and os.path.isfile(video_path):
            info = probe_media(video_path)
            duration = info.duration if info and info.duration > 0 else 10.0
            num_samples = max(1, min(max_frames, columns * (rows or 4)))
            timestamps = [(i + 0.5) * (duration / num_samples) for i in range(num_samples)]

            for idx, ts in enumerate(timestamps, start=1):
                thumb_file = os.path.join(tmp_dir, f"sample_{idx}.jpg")
                extracted = extract_clip_thumbnail(video_path, ts, thumb_file, width=tile_width, height=tile_height)
                if extracted and os.path.isfile(thumb_file):
                    try:
                        img = Image.open(thumb_file).convert("RGB")
                    except Exception:
                        img = Image.new("RGB", (tile_width, tile_height), (40, 40, 40))
                else:
                    img = Image.new("RGB", (tile_width, tile_height), (40, 40, 40))

                mins = int(ts // 60)
                secs = int(ts % 60)
                label = f"#{idx:02d} {mins:02d}:{secs:02d}"
                tiles.append((img, label, False))

        if not tiles:
            return None

        # Compute grid dimensions
        n_tiles = len(tiles)
        cols = max(1, min(columns, n_tiles))
        num_rows = rows if rows is not None else math.ceil(n_tiles / cols)

        tw = max(16, int(tile_width))
        th = max(16, int(tile_height))
        label_height = 24 if show_labels else 0
        header_height = 48 if header_title else 0

        sheet_w = margin * 2 + cols * tw + (cols - 1) * padding
        sheet_h = margin * 2 + header_height + num_rows * (th + label_height) + (num_rows - 1) * padding

        bg_rgb = _parse_color(bg_color)
        sheet = Image.new("RGB", (sheet_w, sheet_h), color=bg_rgb)
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default()

        # Render Header
        if header_title:
            draw.rectangle([margin, margin + 4, margin + 4, margin + 32], fill=(0, 200, 220))
            draw.text((margin + 12, margin + 8), str(header_title), font=font, fill=(245, 245, 250))
            subtext = f"{n_tiles} clips | {cols}x{num_rows} grid | {tw}x{th} tiles"
            draw.text((margin + 12, margin + 26), subtext, font=font, fill=(140, 150, 165))

        # Render Tiles
        for i, (tile_img, label_text, is_energy) in enumerate(tiles):
            c_row = i // cols
            c_col = i % cols
            x = margin + c_col * (tw + padding)
            y = margin + header_height + c_row * (th + label_height + padding)

            # Paste tile image
            if tile_img.size != (tw, th):
                tile_img = tile_img.resize((tw, th), Image.Resampling.LANCZOS)
            sheet.paste(tile_img, (x, y))

            # Border: Green for high energy, Cyan for primary keywords
            border_color = (50, 205, 50) if is_energy else (0, 200, 220)
            draw.rectangle([x, y, x + tw - 1, y + th - 1], outline=border_color, width=2)

            # High energy indicator badge
            if is_energy:
                badge_w = 54
                badge_h = 16
                draw.rectangle([x + tw - badge_w - 4, y + 4, x + tw - 4, y + 4 + badge_h], fill=(50, 205, 50))
                draw.text((x + tw - badge_w, y + 6), "ENERGY", font=font, fill=(0, 0, 0))

            # Label banner
            if show_labels:
                lbl_y = y + th
                draw.rectangle([x, lbl_y, x + tw - 1, lbl_y + label_height - 1], fill=(28, 33, 44))
                dot_color = (50, 205, 50) if is_energy else (0, 200, 220)
                draw.rectangle([x, lbl_y, x + 3, lbl_y + label_height - 1], fill=dot_color)
                draw.text((x + 8, lbl_y + 5), label_text[:36], font=font, fill=(230, 235, 245))

        # Ensure directory exists and save
        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        sheet.save(output_path, quality=92)
        return output_path


# Aliases for contact sheet / sprite sheet creation
generate_sprite_sheet = generate_contact_sheet
create_sprite_sheet = generate_contact_sheet
create_contact_sheet = generate_contact_sheet
generate_thumbnail_sprite_sheet = generate_contact_sheet
create_thumbnail_sprite_sheet = generate_contact_sheet
