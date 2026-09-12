"""Unit tests for NLE export, subtitles, and transcript caching."""

import os
import tempfile
import unittest
from supercut.asr.cache import TranscriptCache
from supercut.export.nle import export_cmx3600_edl, export_premiere_fcpxml
from supercut.models import ClipInterval, HitSpan, Segment, Transcript, Word
from supercut.video.subtitles import generate_karaoke_ass, generate_srt_for_clips


class TestExportAndSubtitles(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.clips = [
            ClipInterval(
                start=5.0,
                end=9.5,
                source_video="C:/media/video1.mp4",
                keyword_hits=[HitSpan("awesome", "awesome", 6.0, 7.0, [1])],
                index=1,
            ),
            ClipInterval(
                start=15.0,
                end=20.0,
                source_video="C:/media/video1.mp4",
                keyword_hits=[HitSpan("super", "super", 16.0, 17.0, [2])],
                index=2,
            ),
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_export_fcpxml(self):
        xml_path = os.path.join(self.temp_dir.name, "timeline.xml")
        export_premiere_fcpxml(self.clips, "TestMontage", xml_path, fps=30)
        self.assertTrue(os.path.exists(xml_path))
        with open(xml_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("<xmeml", content)
        self.assertIn("TestMontage", content)
        self.assertIn("clipitem-1", content)

    def test_export_edl(self):
        edl_path = os.path.join(self.temp_dir.name, "timeline.edl")
        export_cmx3600_edl(self.clips, "TestMontage", edl_path, fps=30)
        self.assertTrue(os.path.exists(edl_path))
        with open(edl_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("TITLE: TestMontage", content)
        self.assertIn("001", content)
        self.assertIn("002", content)

    def test_subtitles_srt(self):
        srt_path = os.path.join(self.temp_dir.name, "test.srt")
        generate_srt_for_clips(self.clips, srt_path)
        self.assertTrue(os.path.exists(srt_path))
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("-->", content)
        self.assertIn("[AWESOME]", content)

    def test_karaoke_ass(self):
        ass_path = os.path.join(self.temp_dir.name, "test.ass")
        words = [Word("This", 5.0, 5.5), Word("is", 5.6, 5.8), Word("awesome", 6.0, 7.0)]
        generate_karaoke_ass(words, self.clips[0].keyword_hits, 5.0, 9.5, ass_path)
        self.assertTrue(os.path.exists(ass_path))
        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[Script Info]", content)
        self.assertIn("awesome", content)

    def test_transcript_cache(self):
        cache = TranscriptCache(cache_dir=self.temp_dir.name)
        t = Transcript(
            media_path="fake_video.mp4",
            duration=42.0,
            language="en",
            segments=[Segment(0.0, 5.0, "Hello world", [Word("Hello", 0.0, 1.0), Word("world", 1.2, 2.0)])],
            model_name="test-model",
        )
        cache.put(t)
        loaded = cache.get("fake_video.mp4", "test-model")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.duration, 42.0)
        self.assertEqual(loaded.full_text, "Hello world")


if __name__ == "__main__":
    unittest.main()
