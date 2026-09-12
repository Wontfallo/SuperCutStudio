"""Unit tests for thumbnail generation and batch keyframe extraction."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

from supercut.models import ClipInterval, HitSpan
from supercut.video.probe import probe_media
from supercut.video.thumbnail import (
    batch_extract_thumbnails,
    batch_generate_thumbnails,
    extract_batch_thumbnails,
    extract_clip_thumbnail,
    extract_clip_thumbnails,
    generate_thumbnails_for_clips,
)


class TestThumbnailExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify ffmpeg is accessible
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
            cls.ffmpeg_available = True
        except Exception:
            cls.ffmpeg_available = False

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = self.temp_dir.name
        self.sample_video = os.path.join(self.td, "sample_test.mp4")

        # Synthesize a real 3-second 640x360 playable MP4 video
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=duration=3:frequency=440",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            self.sample_video,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extract_clip_thumbnail_success(self):
        """Verify fast seeking thumbnail extraction returns True and creates valid 320x180 image."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        out_thumb = os.path.join(self.td, "thumb_default.jpg")
        ok = extract_clip_thumbnail(
            video_path=self.sample_video,
            timestamp=1.2,
            output_path=out_thumb,
            width=320,
            height=180,
        )

        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(out_thumb))
        self.assertGreater(os.path.getsize(out_thumb), 0)

        info = probe_media(out_thumb)
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 320)
        self.assertEqual(info.height, 180)

    def test_extract_clip_thumbnail_custom_dimensions(self):
        """Verify arbitrary custom resolution conforms to even integers."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        out_thumb = os.path.join(self.td, "thumb_custom.jpg")
        ok = extract_clip_thumbnail(
            video_path=self.sample_video,
            timestamp=0.8,
            output_path=out_thumb,
            width=480,
            height=270,
        )

        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(out_thumb))

        info = probe_media(out_thumb)
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 480)
        self.assertEqual(info.height, 270)

    def test_extract_clip_thumbnail_png_format(self):
        """Verify PNG format extraction."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        out_png = os.path.join(self.td, "thumb.png")
        ok = extract_clip_thumbnail(
            video_path=self.sample_video,
            timestamp=0.5,
            output_path=out_png,
            width=320,
            height=180,
        )

        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(out_png))
        self.assertGreater(os.path.getsize(out_png), 0)

        info = probe_media(out_png)
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 320)
        self.assertEqual(info.height, 180)

    def test_extract_clip_thumbnail_vertical_and_odd_source(self):
        """Verify vertical video with odd dimensions (361x641) safely letterboxes/pillarboxes."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        odd_vert = os.path.join(self.td, "odd_vertical.mp4")
        gen_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=361x641:rate=30",
            "-c:v", "libx264",
            "-pix_fmt", "yuv444p",
            odd_vert,
        ]

        subprocess.run(gen_cmd, check=True, capture_output=True)

        out_vert_thumb = os.path.join(self.td, "thumb_vert.jpg")
        ok = extract_clip_thumbnail(
            video_path=odd_vert,
            timestamp=0.3,
            output_path=out_vert_thumb,
            width=320,
            height=180,
        )

        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(out_vert_thumb))

        info = probe_media(out_vert_thumb)
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 320)
        self.assertEqual(info.height, 180)

    def test_extract_clip_thumbnail_nonexistent_and_empty_file(self):
        """Verify graceful failure on invalid input files."""
        fake_path = os.path.join(self.td, "does_not_exist.mp4")
        out_path = os.path.join(self.td, "fail.jpg")
        self.assertFalse(extract_clip_thumbnail(fake_path, 1.0, out_path))
        self.assertFalse(os.path.exists(out_path))

        empty_file = os.path.join(self.td, "empty.mp4")
        with open(empty_file, "wb") as f:
            pass
        self.assertFalse(extract_clip_thumbnail(empty_file, 1.0, out_path))
        self.assertFalse(os.path.exists(out_path))

    def test_extract_clip_thumbnail_creates_nested_directory(self):
        """Verify parent directories are automatically created if they do not exist."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        nested_out = os.path.join(self.td, "sub1", "sub2", "thumb.jpg")
        ok = extract_clip_thumbnail(
            video_path=self.sample_video,
            timestamp=0.2,
            output_path=nested_out,
        )
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(nested_out))

    def test_batch_thumbnail_generation(self):
        """Verify batch thumbnail extraction across multiple ClipIntervals."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        clips = [
            ClipInterval(
                start=0.2,
                end=1.2,
                source_video=self.sample_video,
                keyword_hits=[HitSpan("quantum", "quantum", 0.5, 0.9)],
                index=1,
            ),
            ClipInterval(
                start=1.5,
                end=2.8,
                source_video=self.sample_video,
                keyword_hits=[HitSpan("fusion", "fusion", 2.0, 2.4)],
                index=2,
            ),
        ]

        thumb_dir = os.path.join(self.td, "batch_thumbs")
        generated = extract_batch_thumbnails(clips, output_dir=thumb_dir)

        self.assertEqual(len(generated), 2)
        for path in generated:
            self.assertTrue(os.path.isfile(path))
            self.assertGreater(os.path.getsize(path), 0)
            info = probe_media(path)
            self.assertIsNotNone(info)
            self.assertEqual(info.width, 320)
            self.assertEqual(info.height, 180)

        # Check filenames contain index and keyword
        self.assertTrue(any("001_quantum" in p for p in generated))
        self.assertTrue(any("002_fusion" in p for p in generated))

    def test_batch_thumbnail_with_default_video_path(self):
        """Verify default_video_path fallback when clip.source_video is empty."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        clips = [
            ClipInterval(start=0.5, end=1.5, source_video="", index=1),
            ClipInterval(start=1.5, end=2.5, source_video="", index=2),
        ]

        thumb_dir = os.path.join(self.td, "default_vpath_thumbs")
        generated = extract_batch_thumbnails(
            clips,
            output_dir=thumb_dir,
            default_video_path=self.sample_video,
        )

        self.assertEqual(len(generated), 2)
        for path in generated:
            self.assertTrue(os.path.isfile(path))

    def test_batch_thumbnail_selected_only_filtering(self):
        """Verify selected_only parameter respects clip.selected state."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        clips = [
            ClipInterval(start=0.2, end=1.0, source_video=self.sample_video, selected=True, index=1),
            ClipInterval(start=1.2, end=2.0, source_video=self.sample_video, selected=False, index=2),
            ClipInterval(start=2.0, end=2.8, source_video=self.sample_video, selected=True, index=3),
        ]

        thumb_dir = os.path.join(self.td, "selected_thumbs")
        selected_only_res = extract_batch_thumbnails(clips, output_dir=thumb_dir, selected_only=True)
        self.assertEqual(len(selected_only_res), 2)

        all_res = extract_batch_thumbnails(clips, output_dir=thumb_dir, selected_only=False)
        self.assertEqual(len(all_res), 3)

    def test_batch_thumbnail_aliases(self):
        """Verify alias imports are identical functions."""
        self.assertIs(batch_extract_thumbnails, extract_batch_thumbnails)
        self.assertIs(batch_generate_thumbnails, extract_batch_thumbnails)
        self.assertIs(extract_clip_thumbnails, extract_batch_thumbnails)
        self.assertIs(generate_thumbnails_for_clips, extract_batch_thumbnails)


if __name__ == "__main__":
    unittest.main()
