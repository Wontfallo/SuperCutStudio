"""SuperCut Studio Pro CLI - Hardware-accelerated command-line interface."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from supercut.models import AspectRatioMode, EncoderChoice, JobConfig, TransitionMode
from supercut.pipeline import SuperCutPipeline


def print_banner():
    banner = r"""
======================================================================
  🎬 SUPERCUT STUDIO PRO (VKS Ultra) v2.0
  Hardware-Accelerated AI Video Keyword Supercut & Clip Engine
======================================================================
"""
    print(banner)


def parse_args():
    parser = argparse.ArgumentParser(
        description="SuperCut Studio Pro: AI-driven video keyword montage and supercut generator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Inputs & Keywords
    parser.add_argument("-i", "--input", required=True, help="Path to video file or folder containing videos")
    parser.add_argument("-k", "--keywords", default="", help="Comma-separated keywords or phrases to search")
    parser.add_argument(
        "--auto-keywords",
        nargs="?",
        const=15,
        type=int,
        default=None,
        metavar="TOP_N",
        help="Automatically discover top keywords from transcripts (optional count, default: 15)",
    )
    parser.add_argument("-x", "--exclude", default="", help="Comma-separated keywords to exclude from matching")
    parser.add_argument("-o", "--output-dir", default="output", help="Output directory for clips and stitched videos")


    # Timing & Boundaries
    parser.add_argument("-p", "--pad", type=float, default=1.0, help="Padding in seconds around keyword hits")
    parser.add_argument("--pre-pad", type=float, default=None, help="Specific lead padding before keyword (overrides -p)")
    parser.add_argument("--post-pad", type=float, default=None, help="Specific tail padding after keyword (overrides -p)")
    parser.add_argument("--no-silence-snap", action="store_true", help="Disable acoustic pause boundary snapping")
    parser.add_argument("--sentence-mode", action="store_true", help="Expand clip to the entire enclosing sentence")

    # Matcher Features
    parser.add_argument("--case-sensitive", action="store_true", help="Enable case-sensitive matching")
    parser.add_argument("--fuzzy", type=float, default=0.0, help="Fuzzy match similarity threshold (0.0=off, 0.8=tolerant)")
    parser.add_argument("--regex", action="store_true", help="Treat keywords as regular expressions")

    # Model & Hardware
    parser.add_argument(
        "-m",
        "--model",
        default="crisper-turbo",
        help="ASR model: crisper-turbo, large-v3, large-v2, medium, small, base, tiny",
    )
    parser.add_argument(
        "-e",
        "--encoder",
        default="auto",
        choices=["auto", "hevc_nvenc", "h264_nvenc", "av1_nvenc", "libx264"],
        help="Video encoder (auto tries NVIDIA NVENC then falls back to CPU libx264)",
    )
    parser.add_argument("--fast-copy", action="store_true", help="Fast keyframe copy cuts when possible (no re-encode)")

    # Formatting & Social Media
    parser.add_argument(
        "-a",
        "--aspect",
        default="source",
        choices=["source", "16:9", "9:16", "1:1"],
        help="Aspect ratio conforming (9:16 generates vertical video with blurred background canvas)",
    )
    parser.add_argument(
        "-t",
        "--transition",
        default="cut",
        choices=["cut", "crossfade", "fade_black"],
        help="Transition style between clips",
    )
    parser.add_argument(
        "--transition-dur",
        type=float,
        default=0.3,
        help="Transition duration in seconds for crossfades",
    )

    # Subtitles & NLE
    parser.add_argument("--subtitles", action="store_true", help="Export .srt subtitles and burn styled captions")
    parser.add_argument("--nle", action="store_true", help="Export Premiere Pro / DaVinci Resolve XML and EDL timelines")

    # Output selection
    parser.add_argument(
        "--outputs",
        default="stitched,batch",
        help="Comma-separated outputs to keep: 'clips', 'stitched', 'batch'",
    )
    parser.add_argument("--open-folder", action="store_true", help="Open output folder upon job completion")

    parsed = parser.parse_args()
    if not parsed.keywords and parsed.auto_keywords is None:
        parser.error("Either -k/--keywords or --auto-keywords must be specified.")
    return parsed


def main():
    print_banner()
    args = parse_args()

    # Parse outputs
    outputs_set = {o.strip().lower() for o in args.outputs.split(",") if o.strip()}
    want_clips = "clips" in outputs_set
    want_stitched = "stitched" in outputs_set
    want_batch = "batch" in outputs_set

    # Parse padding
    pre_pad = args.pre_pad if args.pre_pad is not None else args.pad
    post_pad = args.post_pad if args.post_pad is not None else args.pad

    auto_kw_top_n = args.auto_keywords if args.auto_keywords is not None else 15
    auto_kw_enabled = args.auto_keywords is not None

    # Build config
    config = JobConfig(
        keywords=[k.strip() for k in args.keywords.split(",") if k.strip()],
        exclude_keywords=[k.strip() for k in args.exclude.split(",") if k.strip()],
        auto_keywords=auto_kw_enabled,
        auto_keywords_top_n=auto_kw_top_n,
        case_sensitive=args.case_sensitive,
        fuzzy_threshold=args.fuzzy,
        use_regex=args.regex,
        pre_pad=pre_pad,
        post_pad=post_pad,
        snap_to_silence=not args.no_silence_snap,
        sentence_mode=args.sentence_mode,
        model=args.model,
        encoder=EncoderChoice(args.encoder),
        aspect_ratio=AspectRatioMode(args.aspect),
        transition=TransitionMode(args.transition),
        transition_duration=args.transition_dur,
        fast_copy=args.fast_copy,
        export_srt=args.subtitles,
        burn_subtitles=args.subtitles,
        export_fcpxml=args.nle,
        export_edl=args.nle,
        save_individual_clips=want_clips,
        save_per_video_stitched=want_stitched,
        save_batch_stitched=want_batch,
        output_dir=args.output_dir,
        open_folder_when_done=args.open_folder,
    )

    if config.keywords:
        print(f"Target Keywords:      {config.keywords}")
    if config.auto_keywords:
        print(f"Auto-Discover:        Top {config.auto_keywords_top_n} keywords per transcript")
    if config.exclude_keywords:
        print(f"Excluding Keywords:   {config.exclude_keywords}")
    print(f"Model:                {config.model}")
    print(f"Aspect Ratio:         {config.aspect_ratio.value}")
    print(f"Padding:              -{config.pre_pad}s / +{config.post_pad}s (Silence snap: {config.snap_to_silence})")
    print(f"Outputs to Keep:      {sorted(outputs_set)}")
    print("-" * 70)

    last_pct = [0.0]

    def progress_callback(pct: float, msg: str):
        # Machine-readable output for parent processes / GUIs
        print(f"@@PROGRESS|{pct:.1f}|{msg}", flush=True)
        if abs(pct - last_pct[0]) >= 2.0 or pct == 100.0:
            last_pct[0] = pct
            print(f"[{pct:5.1f}%] {msg}", flush=True)

    pipeline = SuperCutPipeline(config=config, progress_callback=progress_callback)

    try:
        result = pipeline.run(args.input)
    except Exception as exc:
        print(f"\n❌ ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 70)
    print("🎬 JOB COMPLETE SUMMARY")
    print("=" * 70)
    print(f"Total Videos Analyzed: {len(result.stats)}")
    print(f"Total Keyword Hits:    {result.total_hits}")
    print(f"Total Highlight Clips: {result.total_clips_count} ({result.total_clip_footage_sec:.1f}s of footage)")
    print(f"Total Processing Time: {result.total_duration_sec:.1f}s")
    if result.batch_output_file:
        print(f"Master Batch Supercut: {result.batch_output_file}")
    print(f"JSON Report:           {result.report_json_path}")
    print(f"CSV Report:            {result.report_csv_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
