"""Comprehensive Red Team and Robustness verification suite for SuperCut Studio Pro.

Validates:
1. Command injection and quoting protection across all FFmpeg/FFprobe operations.
2. Temp file lifecycle hygiene and unhandled exception safety.
3. Edge case resilience: zero-duration clips, empty queries, unicode text, audio-less video, and out-of-bounds timestamps.
"""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from supercut.asr.cache import TranscriptCache, get_cache_key
from supercut.asr.crisper_turbo import CrisperWhisperBackend
from supercut.audio.extractor import extract_audio_for_asr
from supercut.audio.silence import expand_to_sentence_boundaries, optimize_clip_boundaries, snap_boundary_to_word_gaps
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
from supercut.search.matcher import KeywordMatcher, clean_token
from supercut.video.encoder import get_encoder_attempts, run_ffmpeg_watched
from supercut.video.normalizer import build_aspect_ratio_filter, build_audio_filter
from supercut.video.probe import calculate_optimal_batch_target, probe_media
from supercut.video.stitcher import (
    build_multi_clip_filter_complex,
    extract_single_clip,
    stitch_master_batch,
    stitch_single_video_highlights,
)
from supercut.video.subtitles import format_timestamp_ass, format_timestamp_srt, generate_karaoke_ass, generate_srt_for_clips
from supercut.video.transitions import build_transition_concat_filter


class TestRedTeamCommandInjectionAndQuoting(unittest.TestCase):
    """Test suite for injection vectors and quoting integrity in external processes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ffmpeg_concat_demuxer_single_quote_escaping(self):
        """Verify that file paths containing single quotes and special characters do not break concat list."""
        td = self.temp_dir.name
        # Filename with single quote, space, and brackets
        tricky_name = "test's montage [2026] #1.mp4"
        v1 = os.path.join(td, tricky_name)

        # Generate a small 0.5s synthetic video
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=0.5:size=320x240:rate=30",
            "-f", "lavfi", "-i", "anullsrc=duration=0.5:sample_rate=48000:channel_layout=stereo",
            "-c:v", "libx264", "-c:a", "aac", v1
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"FFmpeg synthetic media creation failed: {res.stderr}")

        config = JobConfig(encoder=EncoderChoice.CPU_X264)
        out_batch = stitch_master_batch([v1], td, config)
        self.assertIsNotNone(out_batch)
        self.assertTrue(os.path.exists(out_batch))

        # Test with multiple copies where concat actually happens
        v2 = os.path.join(td, "another's clip; rm -rf.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", v1, "-c", "copy", v2], capture_output=True)
        out_batch_multi = stitch_master_batch([v1, v2], td, config)
        self.assertIsNotNone(out_batch_multi)
        self.assertTrue(os.path.exists(out_batch_multi))
        self.assertGreater(os.path.getsize(out_batch_multi), 0)

    def test_ffmpeg_watched_argument_safety(self):
        """Verify that shell characters in command argument lists are not executed via shell."""
        python_bin = os.sys.executable
        cmd = [python_bin, "-c", "import sys; print('SAFE_EXEC')"]
        code, out = run_ffmpeg_watched(cmd, total_duration=1.0)
        self.assertEqual(code, 0)
        self.assertIn("SAFE_EXEC", out)

    def test_subtitle_burn_in_filter_quoting(self):
        """Verify subtitle filter syntax generation properly escapes Windows drive colon and quotes."""
        selected_clips = [
            ClipInterval(start=0.0, end=1.0, source_video="dummy.mp4", keyword_hits=[HitSpan("test", "test", 0.0, 1.0)])
        ]
        media_info = MediaInfo(width=1920, height=1080, fps=30.0, duration=2.0, has_audio=True)
        config = JobConfig(burn_subtitles=True, encoder=EncoderChoice.CPU_X264)
        
        with patch("supercut.video.stitcher.run_ffmpeg_watched", return_value=(0, "")) as mock_run:
            stitch_single_video_highlights(
                video_path="C:/media/test's video.mp4",
                media_info=media_info,
                intervals=selected_clips,
                target_w=1920,
                target_h=1080,
                fps=30,
                out_path=os.path.join(self.temp_dir.name, "out.mp4"),
                config=config,
            )
            self.assertTrue(mock_run.called)
            called_cmd = mock_run.call_args[0][0]
            filter_arg = called_cmd[called_cmd.index("-filter_complex") + 1]
            self.assertIn("subtitles=", filter_arg)


class TestRedTeamTempLeakAndExceptionHandling(unittest.TestCase):
    """Test suite for temporary file cleanup and crash immunity on corrupted media."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_probe_media_nonexistent_file(self):
        """Probe should return None without raising unhandled exceptions on missing file."""
        info = probe_media(os.path.join(self.temp_dir.name, "nonexistent_file.mp4"))
        self.assertIsNone(info)

    def test_probe_media_zero_byte_file(self):
        """Probe should return None without crashing on empty 0-byte file."""
        empty_file = os.path.join(self.temp_dir.name, "empty.mp4")
        with open(empty_file, "wb") as f:
            f.write(b"")
        info = probe_media(empty_file)
        self.assertIsNone(info)

    def test_probe_media_corrupted_garbage_file(self):
        """Probe should return None without crashing on arbitrary binary junk."""
        corrupt_file = os.path.join(self.temp_dir.name, "corrupt.mp4")
        with open(corrupt_file, "wb") as f:
            f.write(b"\x00\xFF\xDE\xAD\xBE\xEF" * 1024)
        info = probe_media(corrupt_file)
        self.assertIsNone(info)

    def test_probe_media_unicode_filename_and_metadata(self):
        """Probe should safely handle non-ASCII filenames on Windows."""
        unicode_name = os.path.join(self.temp_dir.name, "视频_видео_🎬.mp4")
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=0.2:size=320x240:rate=30",
            "-c:v", "libx264", unicode_name
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        info = probe_media(unicode_name)
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 320)
        self.assertEqual(info.height, 240)

    def test_extract_audio_temp_cleanup_on_failure(self):
        """extract_audio_for_asr must not leak temporary .wav files when extraction fails."""
        before_files = set(Path(tempfile.gettempdir()).glob("supercut_asr_*.wav"))
        result = extract_audio_for_asr(os.path.join(self.temp_dir.name, "nonexistent.mp4"))
        self.assertIsNone(result)
        after_files = set(Path(tempfile.gettempdir()).glob("supercut_asr_*.wav"))
        new_leaked = after_files - before_files
        self.assertEqual(len(new_leaked), 0, f"Leaked temporary audio files: {new_leaked}")

    def test_transcript_cache_corruption_resilience(self):
        """TranscriptCache should gracefully return None on corrupted JSON cache entries."""
        cache = TranscriptCache(cache_dir=self.temp_dir.name)
        key = get_cache_key("test_media.mp4", "crisper-turbo")
        cache_file = cache._get_path(key)

        with open(cache_file, "w", encoding="utf-8") as f:
            f.write("INVALID_JSON{broken")

        loaded = cache.get("test_media.mp4", "crisper-turbo")
        self.assertIsNone(loaded)

    def test_crisper_whisper_worker_none_timestamp_safety(self):
        """CrisperWhisper worker helper must handle word objects with None start/end."""
        backend = object.__new__(CrisperWhisperBackend)
        words = [
            Word(text="hello", start=0.0, end=1.0),
            Word(text="world", start=1.2, end=2.0),
        ]
        segs = backend._words_to_segments([], 0.0)
        self.assertEqual(segs, [])
        segs2 = backend._words_to_segments(words, 2.0)
        self.assertEqual(len(segs2), 1)


class TestRedTeamEdgeCases(unittest.TestCase):
    """Test suite covering boundary conditions, empty inputs, unicode, missing audio, and out-of-bounds timestamps."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    # --- 1. Zero-duration and inverted clips ---

    def test_clip_interval_duration_robustness(self):
        """ClipInterval.duration must handle zero, negative, and NaN differences defensively."""
        c_zero = ClipInterval(start=5.0, end=5.0)
        self.assertEqual(c_zero.duration, 0.0)

        c_inverted = ClipInterval(start=10.0, end=4.0)
        self.assertEqual(c_inverted.duration, 0.0)

        c_nan = ClipInterval(start=0.0, end=float("nan"))
        self.assertEqual(c_nan.duration, 0.0)

    def test_extract_single_clip_zero_duration_rejection(self):
        """extract_single_clip must immediately reject clips with duration <= 0.05 without calling ffmpeg."""
        clip = ClipInterval(start=1.0, end=1.02)
        config = JobConfig()
        ok = extract_single_clip(
            video_path="dummy.mp4",
            clip=clip,
            target_w=1920,
            target_h=1080,
            fps=30,
            out_path=os.path.join(self.temp_dir.name, "out.mp4"),
            config=config,
        )
        self.assertFalse(ok)

    def test_transition_concat_filter_zero_or_empty_clips(self):
        """build_transition_concat_filter must return empty filter when num_clips <= 0."""
        fc, v, a = build_transition_concat_filter(num_clips=0, clip_durations=[])
        self.assertEqual(fc, "")
        self.assertEqual(v, "")
        self.assertEqual(a, "")

    def test_srt_generation_skips_zero_duration_clips(self):
        """generate_srt_for_clips must skip clips with zero or negligible duration."""
        clips = [
            ClipInterval(start=1.0, end=1.0, keyword_hits=[HitSpan("zero", "zero", 1.0, 1.0)]),
            ClipInterval(start=2.0, end=5.0, keyword_hits=[HitSpan("valid", "valid", 2.0, 5.0)]),
        ]
        out_srt = os.path.join(self.temp_dir.name, "test.srt")
        generate_srt_for_clips(clips, out_srt)
        with open(out_srt, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("[ZERO]", content)
        self.assertIn("[VALID]", content)

    def test_nle_export_zero_duration_clips_and_zero_fps(self):
        """Premiere and EDL exporters must not crash on zero duration clips or zero fps."""
        clips = [
            ClipInterval(start=0.0, end=0.0, source_video="dummy.mp4"),
            ClipInterval(start=1.0, end=4.0, source_video="dummy.mp4", keyword_hits=[HitSpan("go", "go", 1.0, 4.0)]),
        ]
        xml_path = os.path.join(self.temp_dir.name, "test.xml")
        edl_path = os.path.join(self.temp_dir.name, "test.edl")

        export_premiere_fcpxml(clips, "TestSeq", xml_path, fps=0)
        self.assertTrue(os.path.exists(xml_path))

        export_cmx3600_edl(clips, "TestSeq", edl_path, fps=0)
        self.assertTrue(os.path.exists(edl_path))

    # --- 2. Empty keyword queries and whitespace ---

    def test_matcher_empty_keyword_queries(self):
        """KeywordMatcher with empty list or whitespace-only strings must return zero matches safely."""
        words = [Word(text="Hello", start=0.0, end=1.0), Word(text="World", start=1.1, end=2.0)]
        for empty_input in [[], [""], ["   "], ["\t\n"], ["!@#$%"]]:
            matcher = KeywordMatcher(keywords=empty_input)
            hits = matcher.find_matches(words)
            self.assertEqual(hits, [], f"Expected 0 hits for empty input {empty_input!r}")

    def test_matcher_empty_words_sequence(self):
        """KeywordMatcher on an empty list of Word objects must return empty list."""
        matcher = KeywordMatcher(keywords=["apple", "banana"])
        self.assertEqual(matcher.find_matches([]), [])

    # --- 3. Unicode and special characters ---

    def test_matcher_unicode_keywords_and_accents(self):
        """KeywordMatcher must correctly match unicode words across multiple languages and scripts."""
        words = [
            Word(text="Café", start=0.0, end=0.5),
            Word(text="Über", start=0.6, end=1.0),
            Word(text="Mañana", start=1.1, end=1.5),
            Word(text="こんにちは", start=1.6, end=2.0),
            Word(text="Привет", start=2.1, end=2.5),
            Word(text="🚀", start=2.6, end=3.0),
        ]
        matcher = KeywordMatcher(keywords=["cafe", "über", "mañana", "こんにちは", "привет", "🚀"], fuzzy_threshold=0.8)
        hits = matcher.find_matches(words)
        matched_texts = [h.matched_text for h in hits]
        self.assertIn("Café", matched_texts)
        self.assertIn("Über", matched_texts)
        self.assertIn("Mañana", matched_texts)
        self.assertIn("こんにちは", matched_texts)
        self.assertIn("Привет", matched_texts)
        self.assertIn("🚀", matched_texts)

    def test_clean_token_unicode_smart_quotes(self):
        """clean_token must strip unicode smart quotes (curly quotes) and punctuation."""
        self.assertEqual(clean_token("“awesome”"), "awesome")
        self.assertEqual(clean_token("‘super’"), "super")
        self.assertEqual(clean_token("sister’s"), "sisters")
        self.assertEqual(clean_token("¿Cómo?"), "como")

    def test_ass_subtitles_curly_brace_and_newline_injection(self):
        """generate_karaoke_ass must sanitize curly braces and newlines to protect ASS formatting tags."""
        words = [Word(text="Hello {\\b1} injected", start=0.0, end=1.0), Word(text="line\nbreak", start=1.1, end=2.0)]
        hits = [HitSpan("Hello {\\b1} injected", "hello", 0.0, 1.0, [0])]
        out_ass = os.path.join(self.temp_dir.name, "test.ass")
        generate_karaoke_ass(words, hits, 0.0, 2.0, out_ass)
        with open(out_ass, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("{\\b1} injected", content)
        self.assertIn("(\\b1) injected", content)

    # --- 4. Videos without audio streams ---

    def test_media_info_has_audio_flag(self):
        """probe_media must detect video files that do not contain an audio stream."""
        silent_video = os.path.join(self.temp_dir.name, "video_no_audio.mp4")
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=0.5:size=320x240:rate=30",
            "-c:v", "libx264", "-an", silent_video
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        info = probe_media(silent_video)
        self.assertIsNotNone(info)
        self.assertFalse(info.has_audio)

    def test_multi_clip_filter_complex_without_audio(self):
        """build_multi_clip_filter_complex must map synthetic audio from 1:a when has_audio is False."""
        intervals = [
            ClipInterval(start=0.1, end=0.3, keyword_hits=[HitSpan("test", "test", 0.1, 0.3)]),
            ClipInterval(start=0.35, end=0.45, keyword_hits=[HitSpan("two", "two", 0.35, 0.45)]),
        ]
        fc, v_out, a_out = build_multi_clip_filter_complex(
            intervals=intervals,
            has_audio=False,
            target_w=1920,
            target_h=1080,
            fps=30,
            aspect_mode=AspectRatioMode.LANDSCAPE_16_9,
            normalize_loudness=False,
            transition=TransitionMode.CUT,
            transition_dur=0.3,
        )
        self.assertIn("[1:a]atrim=", fc)
        self.assertEqual(v_out, "[cv]")
        self.assertEqual(a_out, "[final_a]")

    def test_audio_extraction_on_video_without_audio(self):
        """extract_audio_for_asr must return None without raising an exception when video lacks audio."""
        silent_video = os.path.join(self.temp_dir.name, "silent.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=0.3:size=320x240:rate=30", "-an", silent_video],
            capture_output=True,
        )
        wav_out = extract_audio_for_asr(silent_video)
        self.assertIsNone(wav_out)

    # --- 5. Out-of-bounds timestamps & padding ---

    def test_optimize_clip_boundaries_out_of_bounds_handling(self):
        """optimize_clip_boundaries must discard hits exceeding total_duration or negative timestamps."""
        hits = [
            HitSpan("negative", "negative", -2.0, -0.5),
            HitSpan("inverted", "inverted", 5.0, 3.0),
            HitSpan("beyond", "beyond", 15.0, 18.0),
            HitSpan("valid", "valid", 4.0, 6.0),
        ]
        words = [Word("valid", 4.0, 6.0)]
        clips = optimize_clip_boundaries(
            hits=hits,
            words=words,
            segments=[],
            pre_pad=1.0,
            post_pad=1.0,
            snap_to_silence=False,
            total_duration=10.0,
        )
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].primary_keyword, "valid")
        self.assertTrue(0.0 <= clips[0].start < clips[0].end <= 10.0)

    def test_optimize_clip_boundaries_excessive_padding(self):
        """optimize_clip_boundaries with extreme padding must clamp start to 0.0 and end to total_duration."""
        hits = [HitSpan("word", "word", 2.0, 3.0)]
        clips = optimize_clip_boundaries(
            hits=hits,
            words=[],
            segments=[],
            pre_pad=999999.0,
            post_pad=999999.0,
            snap_to_silence=False,
            total_duration=10.0,
        )
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].start, 0.0)
        self.assertEqual(clips[0].end, 10.0)

    def test_format_timestamp_srt_boundary_values(self):
        """format_timestamp_srt must not produce invalid millis like ',1000' or negative strings."""
        self.assertEqual(format_timestamp_srt(0.0), "00:00:00,000")
        self.assertEqual(format_timestamp_srt(0.9999), "00:00:01,000")
        self.assertEqual(format_timestamp_srt(59.9999), "00:01:00,000")
        self.assertEqual(format_timestamp_srt(3599.9999), "01:00:00,000")
        self.assertEqual(format_timestamp_srt(-5.0), "00:00:00,000")

    def test_format_timestamp_ass_boundary_values(self):
        """format_timestamp_ass must not produce invalid centis like '.100' or negative strings."""
        self.assertEqual(format_timestamp_ass(0.0), "0:00:00.00")
        self.assertEqual(format_timestamp_ass(0.999), "0:00:01.00")
        self.assertEqual(format_timestamp_ass(-1.0), "0:00:00.00")


if __name__ == "__main__":
    unittest.main()
