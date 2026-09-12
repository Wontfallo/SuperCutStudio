"""End-to-end SuperCut Studio Pro processing pipeline orchestrator."""

from __future__ import annotations

import glob
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .asr import TranscriptCache, create_transcriber, transcribe_cached
from .audio import extract_audio_for_asr, optimize_clip_boundaries
from .export import export_cmx3600_edl, export_job_report_csv, export_job_report_json, export_premiere_fcpxml
from .models import AspectRatioMode, ClipInterval, HitSpan, JobConfig, MediaInfo, Transcript, VideoJobStats, Word
from .search import KeywordMatcher, detect_high_energy_moments, discover_top_keywords
from .video import (
    calculate_optimal_batch_target,
    extract_batch_thumbnails,
    extract_single_clip,
    export_transcript_srt,
    export_transcript_txt,
    generate_ass_for_clips,
    generate_contact_sheet,
    generate_srt_for_clips,
    generate_stitched_karaoke_ass,
    generate_stitched_srt_words,
    generate_vtt_for_clips,
    probe_media,
    stitch_master_batch,
    stitch_single_video_highlights,
)


VIDEO_EXTENSIONS = ["*.mp4", "*.mkv", "*.avi", "*.mov", "*.webm", "*.m4v", "*.ts", "*.wmv", "*.flv"]

ProgressCallback = Callable[[float, str], None]
LogCallback = Callable[[str, str], None]


class PipelineCancelled(RuntimeError):
    """Raised internally when the user cancels a job."""


@dataclass
class Artifact:
    path: str
    kind: str          # master | stitched | clip | subtitle | nle | report | thumbnail | transcript | contact_sheet
    label: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        size = 0
        try:
            size = os.path.getsize(self.path)
        except OSError:
            pass
        return {
            "path": self.path,
            "filename": Path(self.path).name,
            "kind": self.kind,
            "label": self.label or Path(self.path).name,
            "source": self.source,
            "size_bytes": size,
            "exists": os.path.exists(self.path),
        }


@dataclass
class JobResult:
    stats: List[VideoJobStats] = field(default_factory=list)
    batch_output_file: Optional[str] = None
    stitched_files: List[str] = field(default_factory=list)
    total_duration_sec: float = 0.0
    total_hits: int = 0
    total_clips_count: int = 0
    total_clip_footage_sec: float = 0.0
    report_json_path: Optional[str] = None
    report_csv_path: Optional[str] = None
    artifacts: List[Artifact] = field(default_factory=list)
    output_dir: str = ""
    cancelled: bool = False
    encoder_used: str = ""

    @property
    def primary_output(self) -> Optional[str]:
        if self.batch_output_file:
            return self.batch_output_file
        return self.stitched_files[0] if self.stitched_files else None


def safe_stem(text: str, max_len: int = 90) -> str:
    stem = re.sub(r'[\\/*?:"<>|]+', "_", text).strip().strip(".")
    stem = re.sub(r"\s+", " ", stem)
    return (stem or "clip")[:max_len]


class SuperCutPipeline:
    """Master pipeline controller for SuperCut Studio Pro."""

    def __init__(
        self,
        config: JobConfig,
        progress_callback: Optional[ProgressCallback] = None,
        log_callback: Optional[LogCallback] = None,
    ):
        self.config = config.normalized() if hasattr(config, "normalized") else config
        self.progress_callback = progress_callback or (lambda p, m: None)
        self.log_callback = log_callback or (lambda level, msg: None)
        self.transcript_cache = TranscriptCache()
        self._cancelled = False
        self._transcriber = None
        self.encoder_used: str = ""

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def cancel(self) -> None:
        self._cancelled = True
        t = self._transcriber
        if t is not None and hasattr(t, "cancel"):
            try:
                t.cancel()
            except Exception:
                pass

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _cancel_check(self) -> bool:
        return self._cancelled

    def emit_progress(self, pct: float, msg: str) -> None:
        if not self._cancelled:
            self.progress_callback(min(100.0, max(0.0, pct)), msg)

    def log(self, msg: str, level: str = "info") -> None:
        try:
            self.log_callback(level, msg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def scan_video_inputs(self, input_path_str: str) -> List[str]:
        p = Path(input_path_str)
        if p.is_file():
            return [str(p)]
        elif p.is_dir():
            files = []
            for ext in VIDEO_EXTENSIONS:
                files.extend(glob.glob(os.path.join(str(p), ext)))
                files.extend(glob.glob(os.path.join(str(p), ext.upper())))
            unique = sorted(set(files), key=lambda s: s.lower())
            return [f for f in unique if not Path(f).name.startswith("FINAL_BATCH_")]
        return []

    def _build_matcher(self, keywords: Sequence[str]) -> KeywordMatcher:
        return KeywordMatcher(
            keywords=list(keywords),
            exclude_keywords=self.config.exclude_keywords,
            case_sensitive=self.config.case_sensitive,
            fuzzy_threshold=self.config.fuzzy_threshold,
            use_regex=self.config.use_regex,
            min_confidence=self.config.min_confidence,
        )

    def _get_transcriber(self):
        if self._transcriber is None:
            self._transcriber = create_transcriber(
                model_name=self.config.model,
                device=self.config.device,
                compute_type=self.config.compute_type,
                language=getattr(self.config, "language", "auto"),
                log=lambda m: self.log(m, "warn"),
            )
            actual = getattr(self._transcriber, "model_name", self.config.model)
            if actual != self.config.model:
                self.log(f"Using ASR model '{actual}' instead of '{self.config.model}'.", "warn")
        return self._transcriber

    def transcribe_media(
        self,
        video_path: str,
        info: Optional[MediaInfo],
        progress: Optional[ProgressCallback] = None,
        force: bool = False,
    ) -> Tuple[Transcript, bool]:
        """Extract audio (if needed) and return (transcript, was_cached)."""
        transcriber = self._get_transcriber()
        model_name = getattr(transcriber, "model_name", self.config.model)
        if not force:
            cached = self.transcript_cache.get(video_path, model_name)
            if cached is not None:
                if progress:
                    progress(1.0, "Loaded cached transcript")
                return cached, True

        wav_path = extract_audio_for_asr(video_path)
        if not wav_path:
            raise RuntimeError("Audio extraction failed (no audio stream or unreadable file)")
        try:
            transcript = transcribe_cached(
                transcriber=transcriber,
                audio_path=wav_path,
                media_path=video_path,
                fallback_duration=info.duration if info else 0.0,
                progress_callback=progress,
                cache=self.transcript_cache,
                cancel_check=self._cancel_check,
                force=force,
            )
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass
        return transcript, False

    # ------------------------------------------------------------------
    # Phase 1: analysis
    # ------------------------------------------------------------------
    def analyze(self, input_path_str: str) -> List[VideoJobStats]:
        """Phase 1: Scan, probe, transcribe, and detect keyword clip intervals."""
        self.emit_progress(0.0, "Scanning input media...")

        video_paths = self.scan_video_inputs(input_path_str)
        if not video_paths:
            raise FileNotFoundError(f"No valid video files found in '{input_path_str}'")

        os.makedirs(self.config.output_dir, exist_ok=True)

        self.emit_progress(2.0, f"Probing {len(video_paths)} media file(s)...")
        media_infos: Dict[str, MediaInfo] = {}
        for vp in video_paths:
            info = probe_media(vp)
            if info:
                try:
                    info.size_bytes = os.path.getsize(vp)
                except OSError:
                    pass
                media_infos[vp] = info
            else:
                self.log(f"Skipping unreadable file: {Path(vp).name}", "warn")

        valid_videos = [vp for vp in video_paths if vp in media_infos]
        if not valid_videos:
            raise RuntimeError("No readable video files found.")

        self.emit_progress(5.0, f"Loading ASR engine ({self.config.model})...")
        transcriber = self._get_transcriber()
        self.log(f"ASR engine ready: {getattr(transcriber, 'model_name', self.config.model)}", "ok")

        matcher = self._build_matcher(self.config.keywords)

        n_videos = len(valid_videos)
        all_stats: List[VideoJobStats] = []

        for idx, vp in enumerate(valid_videos, start=1):
            if self._cancelled:
                break

            base_pct = 5.0 + ((idx - 1) / n_videos) * 55.0
            step_pct = (1.0 / n_videos) * 55.0
            name = Path(vp).name
            stats = VideoJobStats(path=vp, info=media_infos[vp])

            try:
                self.emit_progress(base_pct, f"[{idx}/{n_videos}] Extracting audio from {name}")
                t0 = time.perf_counter()
                transcript, was_cached = self.transcribe_media(
                    vp,
                    stats.info,
                    progress=lambda f, m: self.emit_progress(
                        base_pct + step_pct * (0.1 + f * 0.8), f"[{idx}/{n_videos}] {m}"
                    ),
                )
                stats.transcribe_duration = time.perf_counter() - t0
                stats.transcript = transcript
                stats.transcript_cached = was_cached
                if was_cached:
                    self.log(f"{name}: transcript loaded from cache", "ok")
                else:
                    self.log(f"{name}: transcribed {transcript.duration:.0f}s in {stats.transcribe_duration:.1f}s "
                             f"({len(transcript.all_words)} words)", "ok")
            except Exception as exc:
                if self._cancelled:
                    break
                stats.failed = True
                stats.error_message = str(exc)
                self.log(f"{name}: {exc}", "err")
                all_stats.append(stats)
                continue

            if self._cancelled:
                break

            self.emit_progress(base_pct + step_pct * 0.9, f"[{idx}/{n_videos}] Searching keywords in {name}")
            words = transcript.all_words

            current_matcher = matcher
            if self.config.auto_keywords:
                discovered = discover_top_keywords(transcript, top_n=self.config.auto_keywords_top_n)
                auto_kws = [kw for kw, _ in discovered]
                combined_kws = list(dict.fromkeys(self.config.keywords + auto_kws))
                if combined_kws:
                    current_matcher = self._build_matcher(combined_kws)
                    self.log(f"{name}: auto-discovered keywords: {', '.join(auto_kws[:8])}", "hit")

            hits = current_matcher.find_matches(words) if (current_matcher.raw_keywords or self.config.use_regex) else []
            stats.total_hits = len(hits)

            clips: List[ClipInterval] = []
            if hits:
                clips = optimize_clip_boundaries(
                    hits=hits,
                    words=words,
                    segments=transcript.segments,
                    pre_pad=self.config.pre_pad,
                    post_pad=self.config.post_pad,
                    snap_to_silence=self.config.snap_to_silence,
                    silence_window=self.config.snap_silence_window,
                    sentence_mode=self.config.sentence_mode,
                    total_duration=stats.info.duration,
                    merge_gap=getattr(self.config, "merge_gap", 0.5),
                    source_video=vp,
                )

            if getattr(self.config, "high_energy", False):
                energy_clips = detect_high_energy_moments(transcript, words_per_sec_threshold=self.config.high_energy_wps)
                for ec in energy_clips:
                    ec.source_video = vp
                    if not any(abs(ec.start - c.start) < 0.5 and abs(ec.end - c.end) < 0.5 for c in clips):
                        clips.append(ec)
                clips.sort(key=lambda c: c.start)
                for i, c in enumerate(clips, start=1):
                    c.index = i

            max_len = float(getattr(self.config, "max_clip_duration", 0.0) or 0.0)
            if max_len > 0:
                for c in clips:
                    if c.duration > max_len:
                        c.end = round(c.start + max_len, 3)

            stats.intervals = clips
            stats.clip_duration = sum(c.duration for c in clips)
            if hits:
                self.log(f"{name}: {len(hits)} keyword hit(s) -> {len(clips)} clip(s), "
                         f"{stats.clip_duration:.1f}s of footage", "hit")
            else:
                self.log(f"{name}: no keyword hits", "warn")
            all_stats.append(stats)

        if self._cancelled:
            self.log("Analysis cancelled by user.", "warn")
        return all_stats

    def discover_keywords(self, input_path_str: str, top_n: int = 15) -> Tuple[List[Tuple[str, float]], List[VideoJobStats]]:
        """Transcribe the inputs and return the strongest keywords / phrases across them."""
        video_paths = self.scan_video_inputs(input_path_str)
        if not video_paths:
            raise FileNotFoundError(f"No video files found in '{input_path_str}'")

        all_discovered: Dict[str, float] = {}
        stats_list: List[VideoJobStats] = []
        n = len(video_paths)
        self.emit_progress(2.0, f"Loading ASR engine ({self.config.model})...")
        self._get_transcriber()
        for idx, vp in enumerate(video_paths, start=1):
            if self._cancelled:
                break
            base = ((idx - 1) / n) * 95.0
            step = 95.0 / n
            name = Path(vp).name
            info = probe_media(vp)
            if not info:
                continue
            self.emit_progress(base, f"[{idx}/{n}] Transcribing {name}")
            try:
                transcript, cached = self.transcribe_media(
                    vp, info, progress=lambda f, m: self.emit_progress(base + step * f, f"[{idx}/{n}] {m}")
                )
            except Exception as exc:
                self.log(f"{name}: {exc}", "err")
                continue
            stats_list.append(VideoJobStats(path=vp, info=info, transcript=transcript, transcript_cached=cached))
            for kw, score in discover_top_keywords(transcript, top_n=top_n):
                all_discovered[kw] = max(all_discovered.get(kw, 0.0), score)

        ranked = sorted(all_discovered.items(), key=lambda x: -x[1])[:top_n]
        self.emit_progress(100.0, f"Discovered {len(ranked)} keyword(s)")
        return ranked, stats_list

    # ------------------------------------------------------------------
    # Phase 2: rendering
    # ------------------------------------------------------------------
    def _resolve_target(self, media_infos: Dict[str, MediaInfo]) -> Tuple[int, int, int]:
        if self.config.target_width and self.config.target_height and self.config.target_fps:
            target_w, target_h, fps = self.config.target_width, self.config.target_height, self.config.target_fps
        else:
            target_w, target_h, fps = calculate_optimal_batch_target(list(media_infos.values()))

        if self.config.aspect_ratio == AspectRatioMode.VERTICAL_9_16:
            target_w, target_h = 1080, 1920
        elif self.config.aspect_ratio == AspectRatioMode.SQUARE_1_1:
            target_w, target_h = 1080, 1080
        elif self.config.aspect_ratio == AspectRatioMode.LANDSCAPE_16_9:
            if target_h and abs(target_w / max(1, target_h) - 16 / 9) > 0.01:
                target_w, target_h = 1920, 1080
        return target_w, target_h, fps

    def render_clips_and_stitch(
        self,
        all_stats: List[VideoJobStats],
        start_time: Optional[float] = None,
    ) -> JobResult:
        """Phase 2: Render individual clips, per-video highlights, and master batch stitch."""
        if start_time is None:
            start_time = time.perf_counter()

        out_dir = os.path.abspath(self.config.output_dir)
        os.makedirs(out_dir, exist_ok=True)
        artifacts: List[Artifact] = []

        media_infos = {s.path: s.info for s in all_stats if s.info}
        target_w, target_h, fps = self._resolve_target(media_infos)
        self.log(f"Render target: {target_w}x{target_h} @ {fps}fps, aspect={self.config.aspect_ratio.value}, "
                 f"transition={self.config.transition.value}, encoder={self.config.encoder.value}", "info")

        # Reset previous render outputs so re-renders report accurately.
        for s in all_stats:
            s.stitched_output_path = ""
            s.clip_output_paths = []
            s.artifact_paths = []
            s.clips_saved_count = 0
            s.encode_duration = 0.0

        videos_with_clips = [s for s in all_stats if any(c.selected for c in s.intervals) and not s.failed]
        n_hits = len(videos_with_clips)
        if n_hits == 0:
            self.log("No selected clips to render.", "warn")

        for idx, stats in enumerate(videos_with_clips, start=1):
            if self._cancelled:
                break

            base_pct = 60.0 + ((idx - 1) / max(1, n_hits)) * 30.0
            step_pct = (1.0 / max(1, n_hits)) * 30.0
            name_stem = safe_stem(Path(stats.path).stem)
            selected_clips = sorted([c for c in stats.intervals if c.selected], key=lambda c: c.start)
            transcript_words: List[Word] = stats.transcript.all_words if stats.transcript else []

            # Individual clips
            if self.config.save_individual_clips:
                clips_dir = os.path.join(out_dir, f"{name_stem}_clips")
                os.makedirs(clips_dir, exist_ok=True)
                n_clips = len(selected_clips)
                for c_idx, clip in enumerate(selected_clips, start=1):
                    if self._cancelled:
                        break
                    kw_clean = safe_stem(clip.primary_keyword.replace(" ", "_"), 40)
                    clip_file = os.path.join(
                        clips_dir, f"clip_{c_idx:03d}_{kw_clean}_{clip.start:.1f}s-{clip.end:.1f}s.mp4"
                    )
                    self.emit_progress(base_pct + step_pct * 0.4 * (c_idx - 1) / max(1, n_clips),
                                       f"[{idx}/{n_hits}] Extracting clip {c_idx}/{n_clips} ({clip.primary_keyword})")
                    ok = extract_single_clip(
                        video_path=stats.path,
                        clip=clip,
                        target_w=target_w,
                        target_h=target_h,
                        fps=fps,
                        out_path=clip_file,
                        config=self.config,
                        has_audio=stats.info.has_audio if stats.info else True,
                        cancel_check=self._cancel_check,
                    )
                    if ok:
                        stats.clips_saved_count += 1
                        stats.clip_output_paths.append(clip_file)
                        artifacts.append(Artifact(clip_file, "clip", f"Clip {c_idx:03d} - {clip.primary_keyword}", stats.path))
                if stats.clips_saved_count:
                    self.log(f"{Path(stats.path).name}: saved {stats.clips_saved_count} individual clip(s)", "ok")

                if getattr(self.config, "generate_thumbnails", False) and not self._cancelled:
                    thumbs = extract_batch_thumbnails(selected_clips, clips_dir, default_video_path=stats.path)
                    for t in thumbs:
                        artifacts.append(Artifact(t, "thumbnail", Path(t).name, stats.path))

            # Per-video highlight reel
            if (self.config.save_per_video_stitched or self.config.save_batch_stitched) and not self._cancelled:
                stitched_file = os.path.join(out_dir, f"{name_stem}_stitched.mp4")
                t0 = time.perf_counter()
                ok = stitch_single_video_highlights(
                    video_path=stats.path,
                    media_info=stats.info or MediaInfo(),
                    intervals=selected_clips,
                    target_w=target_w,
                    target_h=target_h,
                    fps=fps,
                    out_path=stitched_file,
                    config=self.config,
                    progress_callback=lambda f, m: self.emit_progress(base_pct + step_pct * (0.4 + 0.6 * f), m),
                    cancel_check=self._cancel_check,
                    transcript_words=transcript_words,
                )
                stats.encode_duration = time.perf_counter() - t0
                if ok:
                    stats.stitched_output_path = stitched_file
                    artifacts.append(Artifact(stitched_file, "stitched", f"{name_stem} highlight reel", stats.path))
                    from .video.encoder import last_successful_encoder
                    self.encoder_used = last_successful_encoder() or self.encoder_used
                    self.log(f"{Path(stats.path).name}: highlight reel rendered in {stats.encode_duration:.1f}s "
                             f"({len(selected_clips)} clips)", "ok")
                    self._export_sidecars(stats, selected_clips, transcript_words, name_stem, out_dir, fps,
                                          target_w, target_h, artifacts)
                elif not self._cancelled:
                    stats.failed = True
                    stats.error_message = "Highlight stitching failed for every encoder"
                    self.log(f"{Path(stats.path).name}: stitching failed", "err")

            if getattr(self.config, "contact_sheet", False) and not self._cancelled and selected_clips:
                sheet = os.path.join(out_dir, f"{name_stem}_contact_sheet.jpg")
                try:
                    if generate_contact_sheet(selected_clips, sheet, video_path=stats.path,
                                              header_title=f"{Path(stats.path).name} - {len(selected_clips)} clips"):
                        artifacts.append(Artifact(sheet, "contact_sheet", f"{name_stem} contact sheet", stats.path))
                except Exception as exc:
                    self.log(f"Contact sheet failed: {exc}", "warn")

        # Master batch
        stitched_candidates = [s.stitched_output_path for s in all_stats if s.stitched_output_path]
        batch_file = None
        batch_name = (safe_stem(self.config.output_name) + ".mp4") if getattr(self.config, "output_name", "") else "FINAL_BATCH_SUPERCUT.mp4"

        if self.config.save_batch_stitched and stitched_candidates and not self._cancelled:
            if len(stitched_candidates) > 1:
                self.emit_progress(90.0, "Stitching master batch supercut...")
                batch_file = stitch_master_batch(
                    stitched_files=stitched_candidates,
                    output_dir=out_dir,
                    config=self.config,
                    progress_callback=lambda f, m: self.emit_progress(90.0 + f * 8.0, m),
                    cancel_check=self._cancel_check,
                    output_name=batch_name,
                )
                if batch_file:
                    self.log(f"Master batch supercut: {Path(batch_file).name}", "ok")
            elif not self.config.save_per_video_stitched or getattr(self.config, "output_name", ""):
                # Single source: promote the highlight reel to the master output name.
                target = os.path.join(out_dir, batch_name)
                try:
                    if self.config.save_per_video_stitched:
                        shutil.copyfile(stitched_candidates[0], target)
                    else:
                        os.replace(stitched_candidates[0], target)
                        for s in all_stats:
                            if s.stitched_output_path == stitched_candidates[0]:
                                s.stitched_output_path = target
                        for a in artifacts:
                            if a.path == stitched_candidates[0]:
                                a.path = target
                        stitched_candidates = [target]
                    batch_file = target
                except OSError as exc:
                    self.log(f"Could not rename master output: {exc}", "warn")
                    batch_file = stitched_candidates[0]
            else:
                batch_file = None  # single per-video reel already is the result

        if batch_file and batch_file not in [a.path for a in artifacts]:
            artifacts.insert(0, Artifact(batch_file, "master", "Master SuperCut", ""))

        # Remove per-video intermediates if user only wanted batch
        if not self.config.save_per_video_stitched and batch_file:
            for sf in list(stitched_candidates):
                if os.path.abspath(sf) == os.path.abspath(batch_file):
                    continue
                try:
                    os.remove(sf)
                except OSError:
                    pass
                for s in all_stats:
                    if s.stitched_output_path == sf:
                        s.stitched_output_path = ""
                artifacts[:] = [a for a in artifacts if a.path != sf]

        # Reports
        report_json = os.path.join(out_dir, "supercut_report.json")
        report_csv = os.path.join(out_dir, "supercut_report.csv")
        try:
            export_job_report_json(all_stats, report_json)
            export_job_report_csv(all_stats, report_csv)
            artifacts.append(Artifact(report_json, "report", "Job report (JSON)"))
            artifacts.append(Artifact(report_csv, "report", "Job report (CSV)"))
        except Exception as exc:
            self.log(f"Report export failed: {exc}", "warn")

        total_time = time.perf_counter() - start_time
        if self._cancelled:
            self.emit_progress(0.0, "Cancelled")
        else:
            self.emit_progress(100.0, "Done [OK]")

        stitched_files = [s.stitched_output_path for s in all_stats if s.stitched_output_path]
        if self.config.open_folder_when_done and (batch_file or stitched_files) and not self._cancelled:
            try:
                if os.name == "nt":
                    os.startfile(out_dir)
            except Exception:
                pass

        return JobResult(
            stats=all_stats,
            batch_output_file=batch_file,
            stitched_files=stitched_files,
            total_duration_sec=total_time,
            total_hits=sum(s.total_hits for s in all_stats),
            total_clips_count=sum(len([c for c in s.intervals if c.selected]) for s in all_stats),
            total_clip_footage_sec=sum(sum(c.duration for c in s.intervals if c.selected) for s in all_stats),
            report_json_path=report_json,
            report_csv_path=report_csv,
            artifacts=artifacts,
            output_dir=out_dir,
            cancelled=self._cancelled,
            encoder_used=self.encoder_used,
        )

    def _export_sidecars(
        self,
        stats: VideoJobStats,
        selected_clips: List[ClipInterval],
        transcript_words: Sequence[Word],
        name_stem: str,
        out_dir: str,
        fps: int,
        target_w: int,
        target_h: int,
        artifacts: List[Artifact],
    ) -> None:
        cfg = self.config
        try:
            if cfg.export_srt:
                srt_file = os.path.join(out_dir, f"{name_stem}_stitched.srt")
                if transcript_words and getattr(cfg, "subtitle_mode", "karaoke") == "karaoke":
                    generate_stitched_srt_words(selected_clips, transcript_words, srt_file)
                else:
                    generate_srt_for_clips(selected_clips, srt_file)
                artifacts.append(Artifact(srt_file, "subtitle", f"{name_stem} subtitles (SRT)", stats.path))
            if getattr(cfg, "export_vtt", False):
                vtt_file = os.path.join(out_dir, f"{name_stem}_stitched.vtt")
                generate_vtt_for_clips(selected_clips, vtt_file)
                artifacts.append(Artifact(vtt_file, "subtitle", f"{name_stem} subtitles (VTT)", stats.path))
            if getattr(cfg, "export_ass", False):
                ass_file = os.path.join(out_dir, f"{name_stem}_stitched.ass")
                if transcript_words:
                    generate_stitched_karaoke_ass(selected_clips, transcript_words, ass_file,
                                                  style_name=cfg.subtitle_style, play_w=target_w, play_h=target_h)
                else:
                    generate_ass_for_clips(selected_clips, ass_file, style_name=cfg.subtitle_style)
                artifacts.append(Artifact(ass_file, "subtitle", f"{name_stem} karaoke (ASS)", stats.path))
            if cfg.export_fcpxml:
                xml_file = os.path.join(out_dir, f"{name_stem}_timeline.xml")
                export_premiere_fcpxml(selected_clips, name_stem, xml_file, fps, target_w, target_h)
                artifacts.append(Artifact(xml_file, "nle", f"{name_stem} Premiere/FCP XML", stats.path))
            if cfg.export_edl:
                edl_file = os.path.join(out_dir, f"{name_stem}_timeline.edl")
                export_cmx3600_edl(selected_clips, name_stem, edl_file, fps)
                artifacts.append(Artifact(edl_file, "nle", f"{name_stem} EDL", stats.path))
            if getattr(cfg, "export_transcript", False) and stats.transcript:
                txt_file = os.path.join(out_dir, f"{name_stem}_transcript.txt")
                srt_full = os.path.join(out_dir, f"{name_stem}_transcript.srt")
                export_transcript_txt(stats.transcript, txt_file)
                export_transcript_srt(stats.transcript, srt_full)
                artifacts.append(Artifact(txt_file, "transcript", f"{name_stem} transcript (TXT)", stats.path))
                artifacts.append(Artifact(srt_full, "transcript", f"{name_stem} transcript (SRT)", stats.path))
        except Exception as exc:
            self.log(f"Sidecar export failed: {exc}", "warn")

    # ------------------------------------------------------------------
    def run(self, input_path_str: str) -> JobResult:
        """Run complete end-to-end analyze and stitch pipeline."""
        start_time = time.perf_counter()
        stats = self.analyze(input_path_str)
        if self._cancelled:
            return JobResult(stats=stats, cancelled=True, output_dir=os.path.abspath(self.config.output_dir),
                             total_hits=sum(s.total_hits for s in stats))
        return self.render_clips_and_stitch(stats, start_time=start_time)


__all__ = ["Artifact", "JobResult", "PipelineCancelled", "SuperCutPipeline", "VIDEO_EXTENSIONS", "safe_stem"]
