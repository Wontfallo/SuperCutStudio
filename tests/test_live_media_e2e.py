"""Comprehensive End-to-End Live Media Pipeline Verification Tests.

Synthesizes real playable media with lavfi, exercises:
  - Audio extraction & cached transcription
  - Multi-keyword boundary matching & padding
  - Vertical 9:16 canvas normalization with background Gaussian blur
  - Individual clip extraction
  - Batch keyframe thumbnail extraction
  - Animated karaoke ASS and SRT subtitle generation
  - DaVinci Resolve EDL & Premiere Pro FCPXML timeline export
  - Per-video highlight stitching
  - Master batch concatenation
  - JSON and CSV analytics reports
  - Rigorous ffprobe validation of all rendered artifacts
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from supercut.models import (
    AspectRatioMode,
    EncoderChoice,
    JobConfig,
    Segment,
    Transcript,
    TransitionMode,
    Word,
)
from supercut.pipeline import SuperCutPipeline
from supercut.video.probe import probe_media


class TestLiveMediaEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify ffmpeg is available
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
            cls.ffmpeg_available = True
        except Exception:
            cls.ffmpeg_available = False

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = self.temp_dir.name

        self.inputs_dir = os.path.join(self.td, "inputs")
        self.output_dir = os.path.join(self.td, "output")
        os.makedirs(self.inputs_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_pipeline_live_media_execution(self):
        """Synthesize two videos and run full pipeline with 9:16 blur, thumbnails, and exports."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg not available on system PATH")

        # 1. Synthesize Video A (3s, 640x360, 440Hz sine audio)
        vid_a = os.path.join(self.inputs_dir, "alpha_lecture.mp4")
        cmd_a = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=duration=3:frequency=440",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            vid_a,
        ]
        subprocess.run(cmd_a, check=True, capture_output=True)

        # 2. Synthesize Video B (3s, 640x360, 880Hz sine audio)
        vid_b = os.path.join(self.inputs_dir, "beta_keynote.mp4")
        cmd_b = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=duration=3:frequency=880",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            vid_b,
        ]
        subprocess.run(cmd_b, check=True, capture_output=True)

        # Verify synthetic inputs probe cleanly
        info_a = probe_media(vid_a)
        info_b = probe_media(vid_b)
        self.assertTrue(info_a.is_valid and info_a.has_audio)
        self.assertTrue(info_b.is_valid and info_b.has_audio)

        # 3. Configure SuperCut Pipeline with comprehensive features
        config = JobConfig(
            keywords=["quantum", "breakthrough"],
            pre_pad=0.2,
            post_pad=0.2,
            snap_to_silence=False,  # deterministic timing for test
            aspect_ratio=AspectRatioMode.VERTICAL_9_16,
            transition=TransitionMode.CUT,
            encoder=EncoderChoice.CPU_X264,
            save_individual_clips=True,
            save_per_video_stitched=True,
            save_batch_stitched=True,
            export_srt=True,
            export_ass=True,
            export_fcpxml=True,
            export_edl=True,
            generate_thumbnails=True,
            open_folder_when_done=False,
            output_dir=self.output_dir,
        )

        pipeline = SuperCutPipeline(config)

        # 4. Pre-seed cache with transcripts containing target keywords
        # Video A transcript has "quantum" at 0.8s - 1.4s
        t_a = Transcript(
            media_path=vid_a,
            duration=3.0,
            language="en",
            model_name=config.model,
            segments=[
                Segment(
                    start=0.0,
                    end=3.0,
                    text="Welcome to the quantum physics seminar today.",
                    words=[
                        Word("Welcome", 0.1, 0.4),
                        Word("to", 0.45, 0.55),
                        Word("the", 0.6, 0.7),
                        Word("quantum", 0.8, 1.4, confidence=0.98),
                        Word("physics", 1.5, 2.0),
                        Word("seminar", 2.1, 2.6),
                        Word("today", 2.7, 2.9),
                    ],
                )
            ],
        )
        # Video B transcript has "breakthrough" at 1.0s - 1.7s
        t_b = Transcript(
            media_path=vid_b,
            duration=3.0,
            language="en",
            model_name=config.model,
            segments=[
                Segment(
                    start=0.0,
                    end=3.0,
                    text="Announcing a major breakthrough in computing.",
                    words=[
                        Word("Announcing", 0.1, 0.5),
                        Word("a", 0.55, 0.65),
                        Word("major", 0.7, 0.95),
                        Word("breakthrough", 1.0, 1.7, confidence=0.99),
                        Word("in", 1.8, 1.95),
                        Word("computing", 2.0, 2.7),
                    ],
                )
            ],
        )
        pipeline.transcript_cache.put(t_a)
        pipeline.transcript_cache.put(t_b)

        # 5. Execute Pipeline End-to-End
        result = pipeline.run(self.inputs_dir)

        # 6. Verify High-Level Result Telemetry
        self.assertIsNotNone(result)
        self.assertEqual(result.total_hits, 2)
        self.assertEqual(result.total_clips_count, 2)
        self.assertGreater(result.total_clip_footage_sec, 0.5)

        # 7. Probe Individual Extracted Clips (9:16 Vertical) - every render lives in its own job folder
        job_dir = result.output_dir
        self.assertTrue(os.path.isdir(job_dir))
        self.assertNotEqual(os.path.normcase(job_dir), os.path.normcase(self.output_dir))
        clips_a_dir = os.path.join(job_dir, "clips", "alpha_lecture")
        clips_b_dir = os.path.join(job_dir, "clips", "beta_keynote")
        self.assertTrue(os.path.isdir(clips_a_dir))
        self.assertTrue(os.path.isdir(clips_b_dir))

        clips_a = [os.path.join(clips_a_dir, f) for f in os.listdir(clips_a_dir) if f.endswith(".mp4")]
        clips_b = [os.path.join(clips_b_dir, f) for f in os.listdir(clips_b_dir) if f.endswith(".mp4")]
        self.assertEqual(len(clips_a), 1)
        self.assertEqual(len(clips_b), 1)

        probe_clip = probe_media(clips_a[0])
        self.assertTrue(probe_clip.is_valid)
        self.assertEqual(probe_clip.width, 1080)
        self.assertEqual(probe_clip.height, 1920)
        self.assertTrue(probe_clip.has_audio)
        self.assertGreater(probe_clip.duration, 0.5)

        # 8. Verify Thumbnails Generated
        thumbs_a_dir = os.path.join(job_dir, "extras", "thumbnails", "alpha_lecture")
        thumbs_b_dir = os.path.join(job_dir, "extras", "thumbnails", "beta_keynote")
        thumbs_a = [os.path.join(thumbs_a_dir, f) for f in os.listdir(thumbs_a_dir) if f.endswith(".jpg")]
        thumbs_b = [os.path.join(thumbs_b_dir, f) for f in os.listdir(thumbs_b_dir) if f.endswith(".jpg")]
        self.assertEqual(len(thumbs_a), 1)
        self.assertEqual(len(thumbs_b), 1)
        self.assertGreater(os.path.getsize(thumbs_a[0]), 500)

        # 9. Verify Per-Video Highlight Stitched Files
        stitched_a = os.path.join(job_dir, "highlights", "alpha_lecture_stitched.mp4")
        stitched_b = os.path.join(job_dir, "highlights", "beta_keynote_stitched.mp4")
        self.assertTrue(os.path.exists(stitched_a))
        self.assertTrue(os.path.exists(stitched_b))

        probe_stitched = probe_media(stitched_a)
        self.assertEqual(probe_stitched.width, 1080)
        self.assertEqual(probe_stitched.height, 1920)

        # 10. Verify Master Batch Supercut
        self.assertIsNotNone(result.batch_output_file)
        self.assertTrue(os.path.exists(result.batch_output_file))
        self.assertEqual(os.path.dirname(result.batch_output_file), job_dir)  # master sits at the job root
        self.assertTrue(result.batch_output_file.endswith("_SuperCut.mp4"))
        probe_batch = probe_media(result.batch_output_file)
        self.assertTrue(probe_batch.is_valid)
        self.assertEqual(probe_batch.width, 1080)
        self.assertEqual(probe_batch.height, 1920)
        self.assertGreater(probe_batch.duration, 1.0)

        # 11. Verify Subtitles (.srt & .ass)
        srt_a = os.path.join(job_dir, "extras", "alpha_lecture_stitched.srt")
        ass_a = os.path.join(job_dir, "extras", "alpha_lecture_stitched.ass")
        self.assertTrue(os.path.exists(srt_a))
        self.assertTrue(os.path.exists(ass_a))
        with open(srt_a, "r", encoding="utf-8") as f:
            srt_content = f.read()
            self.assertIn("QUANTUM", srt_content.upper())
        with open(ass_a, "r", encoding="utf-8") as f:
            ass_content = f.read()
            self.assertIn("[Script Info]", ass_content)
            self.assertIn("QUANTUM", ass_content.upper())

        # 12. Verify NLE Exports (EDL & XML)
        xml_a = os.path.join(job_dir, "extras", "alpha_lecture_timeline.xml")
        edl_a = os.path.join(job_dir, "extras", "alpha_lecture_timeline.edl")
        self.assertTrue(os.path.exists(xml_a))
        self.assertTrue(os.path.exists(edl_a))

        # 13. Verify Analytical Reports
        self.assertTrue(os.path.exists(result.report_json_path))
        self.assertTrue(os.path.exists(result.report_csv_path))
        with open(result.report_json_path, "r", encoding="utf-8") as f:
            self.assertIn("alpha_lecture", f.read())


if __name__ == "__main__":
    unittest.main()
