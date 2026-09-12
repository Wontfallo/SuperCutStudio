"""Per-video and multi-video batch stitching pipeline with hardware acceleration.

Two strategies exist for turning keyword intervals into a highlight reel:

* **Fast-seek (default)** – every clip becomes its own ``-ss/-t`` input so FFmpeg
  only decodes the frames that end up in the output. On an hour-long source with
  a handful of hits this is an order of magnitude faster than the legacy path.
* **Single-input trim (legacy)** – one input, ``trim``/``atrim`` per clip. Kept for
  compatibility and used automatically when fast-seek is disabled.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from ..models import AspectRatioMode, ClipInterval, JobConfig, MediaInfo, QualityPreset, TransitionMode, Word
from .encoder import (
    AUDIO_AAC_ARGS,
    base_ffmpeg_cmd,
    get_encoder_attempts,
    register_encoder_success,
    run_ffmpeg_watched,
)
from .normalizer import build_aspect_ratio_filter, build_audio_filter
from .probe import calculate_optimal_batch_target, probe_media
from .transitions import build_transition_concat_filter

AUDIO_CONFORM = "aresample=48000:async=1,aformat=channel_layouts=stereo:sample_fmts=fltp"
MAX_HWDEC_INPUTS = 12  # beyond this, decode on CPU (only clip ranges are decoded anyway)


def _remove_quiet(path: str) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _quality(config: JobConfig) -> QualityPreset:
    q = getattr(config, "quality", QualityPreset.BALANCED)
    try:
        return QualityPreset(q)
    except Exception:
        return QualityPreset.BALANCED


# ---------------------------------------------------------------------------
# Individual clip extraction
# ---------------------------------------------------------------------------

def extract_single_clip(
    video_path: str,
    clip: ClipInterval,
    target_w: int,
    target_h: int,
    fps: int,
    out_path: str,
    config: JobConfig,
    has_audio: bool = True,
    progress_callback: Optional[Callable[[float], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> bool:
    """Extract an individual clip with fast copy or hardware re-encode."""
    duration = clip.duration
    if duration <= 0.05:
        return False

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    # 1. Fast copy mode (keyframe cuts)
    if config.fast_copy:
        cmd = base_ffmpeg_cmd(progress=False) + [
            "-ss", f"{clip.start:.3f}", "-t", f"{duration:.3f}", "-i", video_path,
            "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", out_path,
        ]
        code, _ = run_ffmpeg_watched(cmd, duration, progress_callback, cancel_check=cancel_check)
        if code == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return True
        _remove_quiet(out_path)

    # 2. Re-encode mode with normalization
    vf_filter = build_aspect_ratio_filter(config.aspect_ratio, target_w, target_h, fps)
    loudnorm_attempts = [config.loudness_normalize if has_audio else False]
    if config.loudness_normalize and has_audio:
        loudnorm_attempts.append(False)

    for use_loudnorm in loudnorm_attempts:
        af_filter = build_audio_filter(use_loudnorm)

        for label, hwaccel, vcodec in get_encoder_attempts(config.encoder, _quality(config)):
            if cancel_check and cancel_check():
                return False
            cmd = base_ffmpeg_cmd()
            if hwaccel:
                cmd += ["-hwaccel", "cuda"]
            cmd += ["-ss", f"{clip.start:.3f}", "-i", video_path, "-t", f"{duration:.3f}"]

            if not has_audio:
                cmd += ["-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000:d={duration:.3f}"]
                cmd += ["-filter_complex", f"[0:v]{vf_filter}[v_out];[1:a]{af_filter}[a_out]"]
                cmd += ["-map", "[v_out]", "-map", "[a_out]", "-shortest"]
            else:
                cmd += ["-vf", vf_filter, "-af", af_filter]

            cmd += vcodec + AUDIO_AAC_ARGS + ["-movflags", "+faststart", out_path]

            code, err = run_ffmpeg_watched(cmd, duration, progress_callback, cancel_check=cancel_check)
            if code == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                register_encoder_success((label, hwaccel, vcodec))
                return True
            _remove_quiet(out_path)
            if cancel_check and cancel_check():
                return False

    return False


# ---------------------------------------------------------------------------
# Filter graph builders
# ---------------------------------------------------------------------------

def build_multi_clip_filter_complex(
    intervals: List[ClipInterval],
    has_audio: bool,
    target_w: int,
    target_h: int,
    fps: int,
    aspect_mode: AspectRatioMode,
    normalize_loudness: bool,
    transition: TransitionMode,
    transition_dur: float,
) -> Tuple[str, str, str]:
    """Legacy single-input graph: trim/atrim per clip, conform, transition, concat."""
    if not has_audio:
        normalize_loudness = False
    parts = []
    n = len(intervals)
    if n == 0:
        return "", "", ""

    audio_src = "0:a" if has_audio else "1:a"
    clip_durations = [c.duration for c in intervals]

    for i, c in enumerate(intervals):
        parts.append(f"[0:v]trim=start={c.start:.3f}:end={c.end:.3f},setpts=PTS-STARTPTS[v{i}_raw]")
        parts.append(f"[{audio_src}]atrim=start={c.start:.3f}:end={c.end:.3f},asetpts=PTS-STARTPTS[a{i}_raw]")

        norm_v = build_aspect_ratio_filter(aspect_mode, target_w, target_h, fps, tag=f"_{i}")
        parts.append(f"[v{i}_raw]{norm_v}[v{i}]")
        parts.append(f"[a{i}_raw]{AUDIO_CONFORM}[a{i}]")

    concat_fc, out_v, out_a = build_transition_concat_filter(n, clip_durations, transition, transition_dur)
    if concat_fc:
        parts.append(concat_fc)

    af_final = build_audio_filter(normalize_loudness)
    parts.append(f"{out_a}{af_final}[final_a]")

    return ";".join(parts), out_v, "[final_a]"


def build_seek_inputs_filter_complex(
    intervals: List[ClipInterval],
    has_audio: bool,
    target_w: int,
    target_h: int,
    fps: int,
    aspect_mode: AspectRatioMode,
    normalize_loudness: bool,
    transition: TransitionMode,
    transition_dur: float,
) -> Tuple[str, str, str]:
    """Fast-seek graph: input *i* is already trimmed to clip *i* via ``-ss/-t``.

    When the source has no audio track, input ``n`` is a lavfi ``anullsrc`` generator
    that is split and trimmed to each clip's duration.
    """
    if not has_audio:
        normalize_loudness = False
    n = len(intervals)
    if n == 0:
        return "", "", ""

    parts: List[str] = []
    clip_durations = [c.duration for c in intervals]

    if not has_audio:
        split_labels = "".join(f"[sil{i}]" for i in range(n))
        parts.append(f"[{n}:a]asplit={n}{split_labels}")

    for i, c in enumerate(intervals):
        norm_v = build_aspect_ratio_filter(aspect_mode, target_w, target_h, fps, tag=f"_{i}")
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS,{norm_v}[v{i}]")
        if has_audio:
            parts.append(f"[{i}:a]atrim=duration={c.duration:.3f},asetpts=PTS-STARTPTS,{AUDIO_CONFORM}[a{i}]")
        else:
            parts.append(f"[sil{i}]atrim=duration={c.duration:.3f},asetpts=PTS-STARTPTS,{AUDIO_CONFORM}[a{i}]")

    concat_fc, out_v, out_a = build_transition_concat_filter(n, clip_durations, transition, transition_dur)
    if concat_fc:
        parts.append(concat_fc)

    af_final = build_audio_filter(normalize_loudness)
    parts.append(f"{out_a}{af_final}[final_a]")
    return ";".join(parts), out_v, "[final_a]"


def build_seek_input_args(video_path: str, intervals: Sequence[ClipInterval], hwaccel: bool, has_audio: bool) -> List[str]:
    """``-ss/-t/-i`` argument block for every clip (plus a silent generator when needed)."""
    args: List[str] = []
    use_hw = hwaccel and len(intervals) <= MAX_HWDEC_INPUTS
    for c in intervals:
        if use_hw:
            args += ["-hwaccel", "cuda"]
        args += ["-ss", f"{c.start:.3f}", "-t", f"{c.duration:.3f}", "-i", video_path]
    if not has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    return args


# ---------------------------------------------------------------------------
# Subtitle burn-in helper
# ---------------------------------------------------------------------------

def _write_burn_in_subtitles(
    selected_clips: List[ClipInterval],
    config: JobConfig,
    work_dir: str,
    transcript_words: Optional[Sequence[Word]],
    target_w: int,
    target_h: int,
) -> Optional[str]:
    """Write the subtitle file used for burn-in into ``work_dir`` and return its *basename*."""
    from .subtitles import generate_ass_for_clips, generate_stitched_karaoke_ass

    name = f"_supercut_burn_{uuid.uuid4().hex[:10]}.ass"
    path = os.path.join(work_dir, name)
    style = getattr(config, "subtitle_style", "karaoke_amber") or "karaoke_amber"
    mode = getattr(config, "subtitle_mode", "karaoke") or "karaoke"
    if mode == "karaoke" and transcript_words:
        generate_stitched_karaoke_ass(selected_clips, transcript_words, path, style_name=style,
                                      play_w=target_w or 1920, play_h=target_h or 1080)
    else:
        generate_ass_for_clips(selected_clips, path, style_name=style)
    return name


# ---------------------------------------------------------------------------
# Per-video highlight reel
# ---------------------------------------------------------------------------

def stitch_single_video_highlights(
    video_path: str,
    media_info: MediaInfo,
    intervals: List[ClipInterval],
    target_w: int,
    target_h: int,
    fps: int,
    out_path: str,
    config: JobConfig,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    transcript_words: Optional[Sequence[Word]] = None,
) -> bool:
    """Stitch all selected keyword intervals of a single source video into one highlight reel."""
    selected_clips = [c for c in intervals if c.selected and c.duration > 0.05]
    if not selected_clips:
        return False

    name = Path(video_path).name
    total_clip_sec = sum(c.duration for c in selected_clips)
    video_path = os.path.abspath(video_path)
    out_path = os.path.abspath(out_path)
    out_dir = os.path.dirname(out_path)
    os.makedirs(out_dir, exist_ok=True)

    use_fast_seek = bool(getattr(config, "fast_seek", True))

    loudnorm_attempts = [config.loudness_normalize if media_info.has_audio else False]
    if config.loudness_normalize and media_info.has_audio:
        loudnorm_attempts.append(False)

    sub_basename: Optional[str] = None
    try:
        if config.burn_subtitles:
            try:
                sub_basename = _write_burn_in_subtitles(selected_clips, config, out_dir, transcript_words, target_w, target_h)
            except Exception as exc:
                print(f"WARNING: Failed to setup subtitle burn-in: {exc}")
                sub_basename = None

        strategies = [use_fast_seek, False] if use_fast_seek else [False]
        for fast_seek in strategies:
            for use_loudnorm in loudnorm_attempts:
                builder = build_seek_inputs_filter_complex if fast_seek else build_multi_clip_filter_complex
                fc, out_v, out_a = builder(
                    intervals=selected_clips,
                    has_audio=media_info.has_audio,
                    target_w=target_w,
                    target_h=target_h,
                    fps=fps,
                    aspect_mode=config.aspect_ratio,
                    normalize_loudness=use_loudnorm,
                    transition=config.transition,
                    transition_dur=config.transition_duration,
                )
                if sub_basename:
                    fc += f";{out_v}subtitles='{sub_basename}'[sub_v]"
                    out_v = "[sub_v]"

                for label, hwaccel, vcodec in get_encoder_attempts(config.encoder, _quality(config)):
                    if cancel_check and cancel_check():
                        return False
                    if progress_callback:
                        mode = "fast-seek" if fast_seek else "full-decode"
                        progress_callback(0.0, f"{name}: Encoding highlights with {label} ({mode})")

                    cmd = base_ffmpeg_cmd()
                    if fast_seek:
                        cmd += build_seek_input_args(video_path, selected_clips, hwaccel, media_info.has_audio)
                    else:
                        if hwaccel:
                            cmd += ["-hwaccel", "cuda"]
                        cmd += ["-i", video_path]
                        if not media_info.has_audio:
                            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

                    cmd += ["-filter_complex", fc, "-map", out_v, "-map", out_a]
                    cmd += vcodec + AUDIO_AAC_ARGS + ["-movflags", "+faststart"]
                    if not fast_seek:
                        cmd += ["-shortest"]
                    cmd += [out_path]

                    def handle_prog(frac: float, _label=label):
                        if progress_callback:
                            progress_callback(frac, f"{name}: {_label} {frac * 100:.0f}%")

                    code, err = run_ffmpeg_watched(cmd, total_clip_sec, handle_prog, cancel_check=cancel_check, cwd=out_dir)
                    if code == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        register_encoder_success((label, hwaccel, vcodec))
                        return True

                    _remove_quiet(out_path)
                    if cancel_check and cancel_check():
                        return False
                    if err.strip():
                        print(f"WARNING: Encoder {label} failed: {err.strip().splitlines()[-1]}")
    finally:
        if sub_basename:
            _remove_quiet(os.path.join(out_dir, sub_basename))

    return False


# ---------------------------------------------------------------------------
# Master batch
# ---------------------------------------------------------------------------

def stitch_master_batch(
    stitched_files: List[str],
    output_dir: str,
    config: JobConfig,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    output_name: str = "FINAL_BATCH_SUPERCUT.mp4",
) -> Optional[str]:
    """Join all per-video highlights into a master supercut montage."""
    valid_files = [f for f in stitched_files if os.path.isfile(f) and os.path.getsize(f) > 0]
    if not valid_files:
        return None

    if len(valid_files) == 1:
        return valid_files[0]

    os.makedirs(output_dir, exist_ok=True)
    list_file = os.path.join(output_dir, f"batch_concat_{uuid.uuid4().hex[:8]}.txt")
    final_output = os.path.join(output_dir, output_name or "FINAL_BATCH_SUPERCUT.mp4")

    with open(list_file, "w", encoding="utf-8") as handle:
        for f in valid_files:
            clean_p = os.path.abspath(f).replace("\\", "/").replace("'", r"'\''")
            handle.write(f"file '{clean_p}'\n")

    total_duration = sum((probe_media(p) or MediaInfo()).duration for p in valid_files)

    try:
        if progress_callback:
            progress_callback(0.1, "Combining batch videos (stream-copy)...")

        copy_cmd = base_ffmpeg_cmd() + [
            "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", "-movflags", "+faststart", final_output,
        ]

        def handle_prog(frac: float):
            if progress_callback:
                progress_callback(frac, f"Batch joining {frac * 100:.0f}%")

        code, _ = run_ffmpeg_watched(copy_cmd, total_duration, handle_prog, cancel_check=cancel_check)
        if code == 0 and os.path.exists(final_output) and os.path.getsize(final_output) > 0:
            return final_output
        _remove_quiet(final_output)
        if cancel_check and cancel_check():
            return None

        print("WARNING: Stream-copy concat failed; falling back to re-encode batch...")
        for label, hwaccel, vcodec in get_encoder_attempts(config.encoder, _quality(config)):
            cmd = base_ffmpeg_cmd()
            if hwaccel:
                cmd += ["-hwaccel", "cuda"]
            cmd += ["-f", "concat", "-safe", "0", "-i", list_file]
            cmd += vcodec + AUDIO_AAC_ARGS + ["-movflags", "+faststart", final_output]

            code, err = run_ffmpeg_watched(cmd, total_duration, handle_prog, cancel_check=cancel_check)
            if code == 0 and os.path.exists(final_output) and os.path.getsize(final_output) > 0:
                register_encoder_success((label, hwaccel, vcodec))
                return final_output
            _remove_quiet(final_output)
            if cancel_check and cancel_check():
                return None

        return None
    finally:
        _remove_quiet(list_file)


# ---------------------------------------------------------------------------
# Direct folder stitch (no ASR)
# ---------------------------------------------------------------------------

def stitch_folder_direct(
    folder_path: str,
    output_dir: str,
    config: Optional[JobConfig] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    output_name: str = "FINAL_BATCH_SUPERCUT.mp4",
) -> Optional[str]:
    """Directly normalize and stitch all video files in a folder into a single video without ASR."""
    import glob

    cfg = config or JobConfig()
    p = Path(folder_path)
    if not p.is_dir():
        raise FileNotFoundError(f"Input directory not found: {folder_path}")

    video_exts = ["*.mp4", "*.mkv", "*.avi", "*.mov", "*.webm", "*.m4v", "*.ts", "*.wmv", "*.flv"]
    files = []
    for ext in video_exts:
        files.extend(glob.glob(os.path.join(str(p), ext)))
        files.extend(glob.glob(os.path.join(str(p), ext.upper())))
    valid_files = sorted(set(files), key=lambda s: s.lower())
    valid_files = [f for f in valid_files if not Path(f).name.startswith("FINAL_BATCH_")]

    if not valid_files:
        raise FileNotFoundError(f"No video files found in '{folder_path}'")

    media_infos = {}
    for f in valid_files:
        info = probe_media(f)
        if info:
            media_infos[f] = info

    readable = [f for f in valid_files if f in media_infos]
    if not readable:
        raise RuntimeError("No readable video files found in folder.")

    if cfg.target_width and cfg.target_height and cfg.target_fps:
        target_w, target_h, fps = cfg.target_width, cfg.target_height, cfg.target_fps
    else:
        target_w, target_h, fps = calculate_optimal_batch_target([media_infos[f] for f in readable])
    if cfg.aspect_ratio == AspectRatioMode.VERTICAL_9_16:
        target_w, target_h = 1080, 1920
    elif cfg.aspect_ratio == AspectRatioMode.SQUARE_1_1:
        target_w, target_h = 1080, 1080

    temp_norm_dir = tempfile.mkdtemp(prefix="supercut_norm_")
    normalized_files = []
    n = len(readable)

    try:
        for idx, vf in enumerate(readable, 1):
            if cancel_check and cancel_check():
                return None
            name = Path(vf).name
            norm_path = os.path.join(temp_norm_dir, f"norm_{idx:04d}.mp4")
            pct_base = ((idx - 1) / n) * 85.0
            pct_step = (1.0 / n) * 85.0

            if progress_callback:
                progress_callback(pct_base, f"[{idx}/{n}] Normalizing {name}")

            info = media_infos[vf]
            vf_filter = build_aspect_ratio_filter(cfg.aspect_ratio, target_w, target_h, fps)

            success = False
            for label, hwaccel, vcodec in get_encoder_attempts(cfg.encoder, _quality(cfg)):
                cmd = base_ffmpeg_cmd()
                if hwaccel:
                    cmd += ["-hwaccel", "cuda"]
                cmd += ["-i", vf]
                if not info.has_audio:
                    cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
                    cmd += ["-filter_complex", f"[0:v]{vf_filter}[v_out];[1:a]{AUDIO_CONFORM}[a_out]"]
                    cmd += ["-map", "[v_out]", "-map", "[a_out]", "-shortest"]
                else:
                    cmd += ["-vf", vf_filter, "-af", AUDIO_CONFORM]
                cmd += vcodec + AUDIO_AAC_ARGS + ["-movflags", "+faststart", norm_path]

                def handle_prog(frac: float, _idx=idx, _name=name, _base=pct_base, _step=pct_step):
                    if progress_callback:
                        progress_callback(_base + _step * frac, f"[{_idx}/{n}] Normalizing {_name} ({frac * 100:.0f}%)")

                code, err = run_ffmpeg_watched(cmd, info.duration, handle_prog, cancel_check=cancel_check)
                if code == 0 and os.path.exists(norm_path) and os.path.getsize(norm_path) > 0:
                    register_encoder_success((label, hwaccel, vcodec))
                    success = True
                    break
                _remove_quiet(norm_path)
                if cancel_check and cancel_check():
                    return None

            if success:
                normalized_files.append(norm_path)

        if not normalized_files:
            raise RuntimeError("No files were successfully normalized.")

        if progress_callback:
            progress_callback(85.0, "Stitching normalized files into master video...")

        os.makedirs(output_dir, exist_ok=True)
        if len(normalized_files) == 1:
            final_file = os.path.join(output_dir, output_name or "FINAL_BATCH_SUPERCUT.mp4")
            shutil.copyfile(normalized_files[0], final_file)
        else:
            final_file = stitch_master_batch(
                normalized_files,
                output_dir,
                cfg,
                progress_callback=lambda f, m: progress_callback(85.0 + f * 14.0, m) if progress_callback else None,
                cancel_check=cancel_check,
                output_name=output_name,
            )

        if progress_callback:
            progress_callback(100.0, "Done [OK]")

        return final_file
    finally:
        shutil.rmtree(temp_norm_dir, ignore_errors=True)


__all__ = [
    "build_multi_clip_filter_complex",
    "build_seek_input_args",
    "build_seek_inputs_filter_complex",
    "extract_single_clip",
    "stitch_folder_direct",
    "stitch_master_batch",
    "stitch_single_video_highlights",
]
