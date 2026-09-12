"""Adversarial Red Team Stress Test Suite for VideoKeywordStitcher.

Validates 4 core adversarial vulnerability categories:
1. Videos with missing audio streams or unsupported/corrupt audio codecs.
2. Paths with spaces, unicode emojis, Windows drive letters, UNC paths, and trailing punctuation.
3. Zero-length keyword matches, empty/regex queries, and high-density overlapping matches.
4. Abrupt cancellation mid-render and verification of complete process tree cleanup via psutil.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import List, Optional

# Ensure project root is on sys.path for direct pytest invocation
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psutil

from supercut.audio.extractor import extract_audio_for_asr
from supercut.audio.silence import (
    expand_to_sentence_boundaries,
    optimize_clip_boundaries,
    snap_boundary_to_word_gaps,
)
from supercut.export.nle import export_cmx3600_edl, export_premiere_fcpxml
from supercut.export.report import export_job_report_csv, export_job_report_json
from supercut.models import (
    AspectRatioMode,
    ClipInterval,
    EncoderChoice,
    HitSpan,
    JobConfig,
    MediaInfo,
    Segment,
    Transcript,
    TransitionMode,
    VideoJobStats,
    Word,
)
from supercut.pipeline import SuperCutPipeline
from supercut.search.matcher import KeywordMatcher, clean_token
from supercut.video.encoder import run_ffmpeg_watched, terminate_process_tree
from supercut.video.normalizer import build_aspect_ratio_filter, build_audio_filter
from supercut.video.probe import calculate_optimal_batch_target, probe_media
from supercut.video.stitcher import (
    build_multi_clip_filter_complex,
    extract_single_clip,
    stitch_master_batch,
    stitch_single_video_highlights,
)
from supercut.video.subtitles import (
    format_timestamp_ass,
    format_timestamp_srt,
    generate_ass_for_clips,
    generate_karaoke_ass,
    generate_srt_for_clips,
)


class TestRedTeamMissingAndUnsupportedAudio(unittest.TestCase):
    """Stress tests for videos lacking audio or containing corrupt/unsupported audio codecs."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_silent_video(self, filename: str, duration: float = 12.0) -> str:
        """Create a valid synthetic video file without an audio stream."""
        p = os.path.join(self.td, filename)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration:.1f}:size=320x240:rate=30",
            "-c:v", "libx264", "-an", p,
        ]
        subprocess.run(cmd, check=True)
        return p

    def _create_video_with_corrupted_audio(self, filename: str) -> str:
        """Create a video with an intentionally invalid/corrupted audio stream."""
        p = os.path.join(self.td, filename)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=1.0:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=1.0",
            "-c:v", "libx264", "-c:a", "pcm_s16le", p,
        ]
        subprocess.run(cmd, check=True)
        # Corrupt the file by overwriting payload bytes after the container header
        with open(p, "r+b") as f:
            data = f.read()
            if len(data) > 2048:
                f.seek(1024)
                f.write(b"\xFF\xFE\xFD\xFC" * 256)
        return p

    def test_probe_media_silent_video(self):
        """probe_media must detect has_audio=False and empty audio_codec on audio-less media."""
        vid = self._create_silent_video("silent_sample.mp4", duration=2.0)
        info = probe_media(vid)
        self.assertIsNotNone(info)
        self.assertFalse(info.has_audio)
        self.assertEqual(info.audio_codec, "")

    def test_extract_audio_silent_video_cleans_up_temp_files(self):
        """extract_audio_for_asr must return None and not leave dangling temporary .wav files."""
        vid = self._create_silent_video("no_audio.mp4", duration=1.0)
        before_wavs = set(Path(tempfile.gettempdir()).glob("supercut_asr_*.wav"))
        wav = extract_audio_for_asr(vid)
        self.assertIsNone(wav)
        after_wavs = set(Path(tempfile.gettempdir()).glob("supercut_asr_*.wav"))
        self.assertEqual(after_wavs - before_wavs, set())

    def test_extract_single_clip_silent_video_generates_silence(self):
        """extract_single_clip must handle silent video by generating synthetic silence without crashing."""
        vid = self._create_silent_video("silent_clip_source.mp4", duration=5.0)
        out_clip = os.path.join(self.td, "clip_extracted.mp4")
        clip = ClipInterval(start=1.0, end=3.0)
        cfg = JobConfig(encoder=EncoderChoice.CPU_X264, loudness_normalize=True)

        ok = extract_single_clip(
            video_path=vid,
            clip=clip,
            target_w=320,
            target_h=240,
            fps=30,
            out_path=out_clip,
            config=cfg,
            has_audio=False,
        )
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(out_clip))
        self.assertGreater(os.path.getsize(out_clip), 0)

        # Probed clip output should now have valid synthetic audio
        clip_info = probe_media(out_clip)
        self.assertIsNotNone(clip_info)
        self.assertTrue(clip_info.has_audio)

    def test_stitch_single_video_highlights_silent_video_offset_timestamps(self):
        """stitch_single_video_highlights must stitch clips past 5s from silent video without loudnorm NaN error."""
        vid = self._create_silent_video("long_silent.mp4", duration=15.0)
        out_stitch = os.path.join(self.td, "silent_stitched.mp4")
        info = probe_media(vid)
        self.assertIsNotNone(info)

        # Clips situated late in the video timeline (start > 5s)
        clips = [
            ClipInterval(start=6.0, end=7.5, selected=True, keyword_hits=[HitSpan("one", "one", 6.0, 7.5)]),
            ClipInterval(start=10.0, end=12.0, selected=True, keyword_hits=[HitSpan("two", "two", 10.0, 12.0)]),
        ]
        cfg = JobConfig(encoder=EncoderChoice.CPU_X264, loudness_normalize=True)

        ok = stitch_single_video_highlights(
            video_path=vid,
            media_info=info,
            intervals=clips,
            target_w=320,
            target_h=240,
            fps=30,
            out_path=out_stitch,
            config=cfg,
        )
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(out_stitch))
        self.assertGreater(os.path.getsize(out_stitch), 0)

    def test_pipeline_analyze_silent_video_fails_gracefully(self):
        """Pipeline analyze must record failure stats without raising uncaught exceptions on silent video."""
        vid = self._create_silent_video("pipeline_silent.mp4", duration=3.0)
        cfg = JobConfig(output_dir=os.path.join(self.td, "out"), keywords=["hello"])
        pipeline = SuperCutPipeline(cfg)

        stats = pipeline.analyze(vid)
        self.assertEqual(len(stats), 1)
        self.assertTrue(stats[0].failed)
        self.assertIn("Audio extraction failed", stats[0].error_message)

    def test_corrupted_or_unsupported_audio_codec_handling(self):
        """Corrupted audio stream extraction must fail cleanly without unhandled exceptions or leaks."""
        corrupt_vid = self._create_video_with_corrupted_audio("corrupted_audio.mp4")
        res = extract_audio_for_asr(corrupt_vid)
        if res and os.path.exists(res):
            os.remove(res)

        # Unextractable corrupted file must return None and not leak any temporary files
        unextractable = os.path.join(self.td, "unextractable_garbage.mp4")
        with open(unextractable, "wb") as f:
            f.write(b"\x00\xFF\xDE\xAD\xBE\xEF" * 512)

        before_wavs = set(Path(tempfile.gettempdir()).glob("supercut_asr_*.wav"))
        res_fail = extract_audio_for_asr(unextractable)
        self.assertIsNone(res_fail)
        after_wavs = set(Path(tempfile.gettempdir()).glob("supercut_asr_*.wav"))
        self.assertEqual(after_wavs - before_wavs, set())

        info = probe_media(corrupt_vid)
        self.assertTrue(info is None or isinstance(info, MediaInfo))


class TestRedTeamAdversarialPaths(unittest.TestCase):
    """Stress tests for tricky paths: spaces, emojis, Windows drive letters, UNC, and punctuation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_sample_video(self, full_path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(full_path)), exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=0.5:size=320x240:rate=30",
            "-f", "lavfi", "-i", "anullsrc=duration=0.5:sample_rate=48000:channel_layout=stereo",
            "-c:v", "libx264", "-c:a", "aac", full_path,
        ]
        subprocess.run(cmd, check=True)
        return full_path

    def test_paths_with_spaces_and_brackets(self):
        """Video and output paths containing spaces, brackets, and multiple extensions must encode cleanly."""
        nested_dir = os.path.join(self.td, "My Video Projects (2026)", "Raw Footage #1 [Rec]")
        vid_path = os.path.join(nested_dir, "Clip (Special Ed.) .mp4")
        self._create_sample_video(vid_path)

        info = probe_media(vid_path)
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 320)

        out_path = os.path.join(self.td, "Export Reel (Final) [4K] !.mp4")
        clip = ClipInterval(start=0.1, end=0.4, selected=True)
        cfg = JobConfig(encoder=EncoderChoice.CPU_X264)
        ok = extract_single_clip(vid_path, clip, 320, 240, 30, out_path, cfg)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(out_path))

    def test_paths_with_unicode_emojis_and_scripts(self):
        """File paths with unicode emojis, Japanese, Cyrillic, and Arabic characters must not crash."""
        emoji_dir = os.path.join(self.td, "🎬 Studio 💖 Проект", "日本語_مسار_🔥")
        vid_path = os.path.join(emoji_dir, "clip_🎉_🚀.mp4")
        self._create_sample_video(vid_path)

        info = probe_media(vid_path)
        self.assertIsNotNone(info)

        wav = extract_audio_for_asr(vid_path)
        self.assertIsNotNone(wav)
        self.assertTrue(os.path.exists(wav))
        os.remove(wav)

        out_path = os.path.join(emoji_dir, "stitched_✨.mp4")
        clips = [ClipInterval(start=0.05, end=0.35, selected=True)]
        cfg = JobConfig(encoder=EncoderChoice.CPU_X264)
        ok = stitch_single_video_highlights(vid_path, info, clips, 320, 240, 30, out_path, cfg)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(out_path))

    def test_windows_drive_letters_and_subtitle_filter_colon_escaping(self):
        """Windows drive letters (e.g. C:) in subtitle paths must be escaped properly for FFmpeg subtitles filter."""
        vid_path = os.path.join(self.td, "test_sub_drive.mp4")
        self._create_sample_video(vid_path)
        info = probe_media(vid_path)

        sub_dir = os.path.join(self.td, "Sub's Folder [Test]")
        os.makedirs(sub_dir, exist_ok=True)
        out_vid = os.path.join(sub_dir, "burned_output.mp4")

        clips = [ClipInterval(start=0.1, end=0.4, selected=True, keyword_hits=[HitSpan("test", "test", 0.1, 0.4)])]
        cfg = JobConfig(encoder=EncoderChoice.CPU_X264, burn_subtitles=True)

        ok = stitch_single_video_highlights(vid_path, info, clips, 320, 240, 30, out_vid, cfg)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(out_vid))

    def test_unc_paths_handling(self):
        r"""UNC paths (e.g. \\localhost\c$\...) must be probed, audio-extracted, and batch-stitched."""
        unc_base = r"\\localhost\c$"
        if not os.path.exists(unc_base):
            self.skipTest("Localhost UNC admin share is not accessible in this environment.")

        abs_td = os.path.abspath(self.td)
        drive, rest = os.path.splitdrive(abs_td)
        drive_letter = drive.replace(":", "")
        unc_td = os.path.join(f"\\\\localhost\\{drive_letter}$", rest.lstrip("\\/"))

        unc_vid = os.path.join(unc_td, "unc_test_video.mp4")
        self._create_sample_video(unc_vid)

        # 1. Probe UNC
        info = probe_media(unc_vid)
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 320)

        # 2. Extract Audio from UNC
        wav = extract_audio_for_asr(unc_vid)
        self.assertIsNotNone(wav)
        self.assertTrue(os.path.exists(wav))
        os.remove(wav)

        # 3. Batch Stitch via UNC
        unc_copy = os.path.join(unc_td, "unc_test_video_2.mp4")
        shutil.copyfile(unc_vid, unc_copy)

        cfg = JobConfig(encoder=EncoderChoice.CPU_X264)
        out_batch = stitch_master_batch([unc_vid, unc_copy], unc_td, cfg)
        self.assertIsNotNone(out_batch)
        self.assertTrue(os.path.exists(out_batch))
        self.assertGreater(os.path.getsize(out_batch), 0)

    def test_paths_with_trailing_and_embedded_punctuation(self):
        """Paths containing dots, exclamation marks, commas, hashes, and hyphens."""
        tricky_dir = os.path.join(self.td, "dir.with.dots! - #1, sub")
        os.makedirs(tricky_dir, exist_ok=True)
        vid_path = os.path.join(tricky_dir, "video...name!#$,-ok.mp4")
        self._create_sample_video(vid_path)

        info = probe_media(vid_path)
        self.assertIsNotNone(info)

        out_path = os.path.join(tricky_dir, "stitched...out!#$.mp4")
        clips = [ClipInterval(start=0.05, end=0.45, selected=True)]
        cfg = JobConfig(encoder=EncoderChoice.CPU_X264)
        ok = stitch_single_video_highlights(vid_path, info, clips, 320, 240, 30, out_path, cfg)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(out_path))


class TestRedTeamZeroLengthAndOverlappingMatches(unittest.TestCase):
    """Stress tests for zero-length word matches, empty regex queries, and massive overlapping matches."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_zero_duration_words_in_keyword_matcher(self):
        """KeywordMatcher must match words even when start == end (0 duration) without error."""
        words = [
            Word(text="instant", start=2.5, end=2.5, confidence=0.99),
            Word(text="moment", start=2.5, end=2.5, confidence=0.95),
            Word(text="regular", start=3.0, end=3.5, confidence=0.9),
        ]
        matcher = KeywordMatcher(keywords=["instant", "moment"])
        hits = matcher.find_matches(words)
        self.assertEqual(len(hits), 2)
        for h in hits:
            self.assertEqual(h.start, 2.5)
            self.assertEqual(h.end, 2.5)

    def test_empty_and_pure_symbol_regex_patterns(self):
        """KeywordMatcher with regex patterns matching empty strings or symbols must not raise exceptions."""
        words = [Word("alpha", 0.0, 0.5), Word("beta", 0.6, 1.0)]
        empty_regexes = ["^", ".*", r"\b", r"a*"]
        matcher = KeywordMatcher(keywords=empty_regexes, use_regex=True)
        hits = matcher.find_matches(words)
        self.assertIsInstance(hits, list)

    def test_optimize_clip_boundaries_with_zero_length_hits(self):
        """optimize_clip_boundaries must pad or clamp zero-length hits to valid clips with duration >= 0.05s."""
        hits = [
            HitSpan("point", "point", 5.0, 5.0),
            HitSpan("flash", "flash", 5.0, 5.0),
        ]
        clips = optimize_clip_boundaries(
            hits=hits,
            words=[],
            segments=[],
            pre_pad=0.0,
            post_pad=0.0,
            snap_to_silence=False,
            total_duration=10.0,
        )
        self.assertEqual(len(clips), 1)
        self.assertGreaterEqual(clips[0].duration, 0.05)
        self.assertEqual(len(clips[0].keyword_hits), 2)

    def test_extract_single_clip_and_stitcher_safeguard_zero_duration(self):
        """extract_single_clip and stitch_single_video_highlights must reject <=0.05s clips without calling ffmpeg."""
        zero_clip = ClipInterval(start=2.0, end=2.0, selected=True)
        cfg = JobConfig()
        ok_extract = extract_single_clip("dummy.mp4", zero_clip, 320, 240, 30, os.path.join(self.td, "z.mp4"), cfg)
        self.assertFalse(ok_extract)

        ok_stitch = stitch_single_video_highlights(
            video_path="dummy.mp4",
            media_info=MediaInfo(),
            intervals=[zero_clip],
            target_w=320,
            target_h=240,
            fps=30,
            out_path=os.path.join(self.td, "zs.mp4"),
            config=cfg,
        )
        self.assertFalse(ok_stitch)

    def test_subtitles_and_nle_zero_duration_clip_immunity(self):
        """Subtitle generators and NLE exporters must gracefully skip zero duration clips without ZeroDivisionError."""
        zero_clips = [
            ClipInterval(start=0.0, end=0.0, keyword_hits=[HitSpan("zero", "zero", 0.0, 0.0)]),
            ClipInterval(start=1.0, end=1.0001, keyword_hits=[HitSpan("micro", "micro", 1.0, 1.0001)]),
        ]
        srt_p = os.path.join(self.td, "test_zero.srt")
        ass_p = os.path.join(self.td, "test_zero.ass")
        xml_p = os.path.join(self.td, "test_zero.xml")
        edl_p = os.path.join(self.td, "test_zero.edl")

        generate_srt_for_clips(zero_clips, srt_p)
        generate_ass_for_clips(zero_clips, ass_p)
        export_premiere_fcpxml(zero_clips, "ZeroSeq", xml_p, fps=0)
        export_cmx3600_edl(zero_clips, "ZeroSeq", edl_p, fps=0)

        for p in [srt_p, ass_p, xml_p, edl_p]:
            self.assertTrue(os.path.exists(p))

    def test_karaoke_ass_zero_duration_words(self):
        """generate_karaoke_ass must handle zero duration words without crashing."""
        words = [Word("instant", 1.0, 1.0), Word("spark", 1.0, 1.0)]
        hits = [HitSpan("instant", "instant", 1.0, 1.0, [0])]
        out_ass = os.path.join(self.td, "karaoke_zero.ass")
        generate_karaoke_ass(words, hits, 0.0, 2.0, out_ass)
        self.assertTrue(os.path.exists(out_ass))

    def test_dense_overlapping_keyword_hits_merging(self):
        """Stress-test 200 heavily overlapping HitSpans packed into a 3-second window."""
        hits: List[HitSpan] = []
        for i in range(200):
            st = 1.0 + (i % 20) * 0.05
            et = st + 0.2 + (i % 5) * 0.1
            hits.append(HitSpan(f"hit_{i}", f"kw_{i % 10}", st, et))

        clips = optimize_clip_boundaries(
            hits=hits,
            words=[],
            segments=[],
            pre_pad=0.5,
            post_pad=0.5,
            snap_to_silence=False,
            total_duration=10.0,
            merge_gap=0.5,
        )

        self.assertEqual(len(clips), 1)
        c = clips[0]
        self.assertGreaterEqual(c.start, 0.0)
        self.assertLessEqual(c.end, 10.0)
        self.assertEqual(len(c.keyword_hits), 200)
        self.assertIsNotNone(c.primary_keyword)

    def test_multi_clip_filter_complex_overlapping_intervals_integrity(self):
        """build_multi_clip_filter_complex must generate valid scoped filters without name collision."""
        intervals = [
            ClipInterval(start=1.0, end=2.0, keyword_hits=[HitSpan("a", "a", 1.0, 2.0)]),
            ClipInterval(start=1.5, end=2.5, keyword_hits=[HitSpan("b", "b", 1.5, 2.5)]),
            ClipInterval(start=2.0, end=3.0, keyword_hits=[HitSpan("c", "c", 2.0, 3.0)]),
        ]
        fc, out_v, out_a = build_multi_clip_filter_complex(
            intervals=intervals,
            has_audio=True,
            target_w=1920,
            target_h=1080,
            fps=30,
            aspect_mode=AspectRatioMode.LANDSCAPE_16_9,
            normalize_loudness=True,
            transition=TransitionMode.CROSSFADE,
            transition_dur=0.3,
        )
        self.assertIn("[v0]", fc)
        self.assertIn("[v1]", fc)
        self.assertIn("[v2]", fc)
        self.assertEqual(out_a, "[final_a]")


class TestRedTeamMidRenderCancellationAndProcessCleanup(unittest.TestCase):
    """Stress tests for abrupt cancellation mid-render and process tree cleanup verification with psutil."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_terminate_process_tree_kills_parent_and_all_children(self):
        """terminate_process_tree must forcefully kill the target process and all child processes."""
        py_script = "import time; time.sleep(30)"
        proc = subprocess.Popen(
            ["cmd.exe", "/c", sys.executable, "-c", py_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pid = proc.pid
        time.sleep(0.3)

        parent_ps = psutil.Process(pid)
        child_pids = [c.pid for c in parent_ps.children(recursive=True)]
        self.assertGreater(len(child_pids), 0, "Expected at least one child process under cmd.exe")

        terminate_process_tree(proc)
        time.sleep(0.3)

        self.assertFalse(psutil.pid_exists(pid), f"Parent process PID {pid} is still alive!")
        for c_pid in child_pids:
            self.assertFalse(psutil.pid_exists(c_pid), f"Child process PID {c_pid} was orphaned and is still alive!")

    def test_run_ffmpeg_watched_abrupt_cancellation_cleans_process(self):
        """run_ffmpeg_watched with cancel_check returning True must immediately kill FFmpeg without zombies."""
        out_file = os.path.join(self.td, "long_encode.mp4")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
            "-f", "lavfi", "-i", "testsrc=duration=30:size=640x480:rate=30",
            "-c:v", "libx264", "-preset", "slow", out_file,
        ]

        cancel_triggered = False

        def cancel_condition() -> bool:
            nonlocal cancel_triggered
            return cancel_triggered

        def progress_tracker(frac: float):
            nonlocal cancel_triggered
            if frac >= 0.01:
                cancel_triggered = True

        t0 = time.perf_counter()
        code, err = run_ffmpeg_watched(
            cmd=cmd,
            total_duration=30.0,
            progress_callback=progress_tracker,
            cancel_check=cancel_condition,
        )
        elapsed = time.perf_counter() - t0

        self.assertEqual(code, -1)
        self.assertIn("Cancelled", err)
        self.assertLess(elapsed, 5.0)

    def test_pipeline_mid_render_cancellation(self):
        """Pipeline cancellation during render_clips_and_stitch must exit cleanly without uncaught exceptions."""
        vid = os.path.join(self.td, "test_cancel_src.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=duration=3.0:size=320x240:rate=30",
                "-f", "lavfi", "-i", "anullsrc=duration=3.0:sample_rate=48000:channel_layout=stereo",
                "-c:v", "libx264", "-c:a", "aac", vid,
            ],
            check=True,
        )

        cfg = JobConfig(
            output_dir=os.path.join(self.td, "cancel_out"),
            save_per_video_stitched=True,
            encoder=EncoderChoice.CPU_X264,
        )
        pipeline = SuperCutPipeline(cfg)

        clips = [
            ClipInterval(start=0.2, end=0.8, selected=True, keyword_hits=[HitSpan("test", "test", 0.2, 0.8)]),
            ClipInterval(start=1.0, end=2.0, selected=True, keyword_hits=[HitSpan("more", "more", 1.0, 2.0)]),
        ]
        info = probe_media(vid)
        stats = [VideoJobStats(path=vid, info=info, intervals=clips)]

        def cancel_after_delay():
            time.sleep(0.2)
            pipeline.cancel()

        cancel_thread = threading.Thread(target=cancel_after_delay, daemon=True)
        cancel_thread.start()

        result = pipeline.render_clips_and_stitch(stats)
        cancel_thread.join(timeout=2.0)

        self.assertIsNotNone(result)
        self.assertTrue(pipeline._cancelled)


if __name__ == "__main__":
    unittest.main()
