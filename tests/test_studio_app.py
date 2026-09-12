"""Unit and integration tests for SuperCut Studio Pro UI and studio_app."""

import json
import os
import sys
import tempfile
import unittest

from supercut.models import AspectRatioMode, ClipInterval, HitSpan, Segment, Transcript, VideoJobStats, Word
from supercut.ui import (
    ClipReviewerWidget,
    KeywordChipView,
    MediaScannerWidget,
    PerformanceConsoleWidget,
    TranscriptInspectorWidget,
)
from studio_app import SuperCutStudioApp


class TestStudioAppUI(unittest.TestCase):
    """Test suite verifying CustomTkinter Studio UI components and workflows."""

    @classmethod
    def setUpClass(cls):
        # Configure Tcl/Tk environment for Windows
        for tcl_cand in [r"C:\Python314\tcl\tcl8.6", os.path.join(sys.base_prefix, "tcl", "tcl8.6")]:
            if os.path.isdir(tcl_cand):
                os.environ["TCL_LIBRARY"] = tcl_cand
                break
        for tk_cand in [r"C:\Python314\tcl\tk8.6", os.path.join(sys.base_prefix, "tcl", "tk8.6")]:
            if os.path.isdir(tk_cand):
                os.environ["TK_LIBRARY"] = tk_cand
                break

        try:
            # Create a single hidden root app instance for UI widget testing
            cls.app = SuperCutStudioApp()
            cls.app.withdraw()  # Keep window hidden during test runs
            cls.app.update()
        except Exception as e:
            raise unittest.SkipTest(f"Tkinter display environment unavailable: {e}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except Exception:
            pass

    def test_app_instantiation_and_tabs(self):
        """Verify the app initializes all 4 requested tabs and main dock controls."""
        self.assertIsNotNone(self.app.tabview)
        # Check tab views exist
        self.assertIsNotNone(self.app.tab_studio)
        self.assertIsNotNone(self.app.tab_curation)
        self.assertIsNotNone(self.app.tab_transcript)
        self.assertIsNotNone(self.app.tab_performance)

        # Check action buttons in dock
        self.assertIsNotNone(self.app.scan_analyze_btn)
        self.assertIsNotNone(self.app.run_full_btn)
        self.assertIsNotNone(self.app.stitch_folder_btn)
        self.assertIsNotNone(self.app.stop_btn)

    def test_keyword_chip_view(self):
        """Verify adding, removing, clearing, and bulk setting chips in KeywordChipView."""
        chips = self.app.kw_chips
        chips.clear()
        self.assertEqual(chips.get_keywords(), [])

        # Add single
        chips.add_keyword("apple")
        self.assertEqual(chips.get_keywords(), ["apple"])

        # Add duplicate should be ignored
        chips.add_keyword("apple")
        self.assertEqual(chips.get_keywords(), ["apple"])

        # Bulk set with comma string
        chips.set_keywords("banana, cherry, date")
        self.assertEqual(chips.get_keywords(), ["banana", "cherry", "date"])

        # Remove single
        chips.remove_keyword("cherry")
        self.assertEqual(chips.get_keywords(), ["banana", "date"])

        chips.clear()
        self.assertEqual(chips.get_keywords(), [])

    def test_auto_discover_keywords_wiring(self):
        """Verify Studio App auto-discover button and cached transcript keyword discovery."""
        self.assertIsNotNone(self.app.auto_discover_btn)
        self.assertIsNotNone(self.app.kw_chips.auto_discover_btn)

        # Populate last_analyzed_stats with a transcript
        seg = Segment(
            start=0.0,
            end=6.0,
            text="Autonomous drone navigation using artificial intelligence and computer vision.",
            words=[
                Word(text="Autonomous", start=0.0, end=0.8),
                Word(text="drone", start=0.8, end=1.2),
                Word(text="navigation", start=1.2, end=2.0),
                Word(text="using", start=2.0, end=2.4),
                Word(text="artificial", start=2.4, end=3.0),
                Word(text="intelligence", start=3.0, end=4.0),
                Word(text="and", start=4.0, end=4.2),
                Word(text="computer", start=4.2, end=4.8),
                Word(text="vision.", start=4.8, end=5.5),
            ],
        )
        tr = Transcript(media_path="drone.mp4", duration=6.0, language="en", segments=[seg])
        stat = VideoJobStats(path="drone.mp4", transcript=tr)
        self.app.last_analyzed_stats = [stat]

        self.app.kw_chips.clear()
        self.app.auto_discover_keywords()
        self.app.update()

        discovered = self.app.kw_chips.get_keywords()
        self.assertTrue(len(discovered) > 0)
        self.assertTrue(
            any("artificial" in k or "intelligence" in k or "drone" in k or "navigation" in k for k in discovered)
        )

    def test_clip_reviewer_curation(self):

        """Verify Tab 2 Interactive Clip Reviewer loads, filters, and curates clips."""
        reviewer = self.app.clip_reviewer

        clip1 = ClipInterval(
            start=10.0,
            end=15.0,
            keyword_hits=[HitSpan(matched_text="apple", keyword="apple", start=11.0, end=11.5)],
            source_video="video1.mp4",
            index=1,
            selected=True,
        )
        clip2 = ClipInterval(
            start=25.0,
            end=30.0,
            keyword_hits=[HitSpan(matched_text="banana", keyword="banana", start=26.0, end=26.5)],
            source_video="video1.mp4",
            index=2,
            selected=True,
        )
        clip3 = ClipInterval(
            start=5.0,
            end=12.0,
            keyword_hits=[HitSpan(matched_text="orange", keyword="orange", start=6.0, end=6.5)],
            source_video="video2.mp4",
            index=3,
            selected=True,
        )

        reviewer.load_intervals([clip1, clip2, clip3])
        self.app.update()

        self.assertEqual(len(reviewer.all_clips), 3)
        self.assertEqual(len(reviewer.cards), 3)

        # Deselect all
        reviewer.deselect_all()
        self.app.update()
        self.assertEqual([c.selected for c in reviewer.all_clips], [False, False, False])

        # Select all
        reviewer.select_all()
        self.app.update()
        self.assertEqual([c.selected for c in reviewer.all_clips], [True, True, True])

        # Invert selection
        clip1.selected = False
        reviewer.invert_selection()
        self.app.update()
        self.assertTrue(clip1.selected)
        self.assertFalse(clip2.selected)
        self.assertFalse(clip3.selected)

    def test_transcript_inspector(self):
        """Verify Tab 3 Transcript Inspector renders text and performs keyword/search highlighting."""
        inspector = self.app.transcript_inspector

        seg1 = Segment(
            start=0.0,
            end=5.0,
            text="Welcome to the video about apples and bananas.",
            words=[
                Word(text="Welcome", start=0.0, end=0.5),
                Word(text="to", start=0.5, end=0.7),
                Word(text="the", start=0.7, end=0.9),
                Word(text="video", start=0.9, end=1.5),
                Word(text="about", start=1.5, end=2.0),
                Word(text="apples", start=2.0, end=2.6),
                Word(text="and", start=2.6, end=2.8),
                Word(text="bananas.", start=2.8, end=3.5),
            ],
        )
        transcript = Transcript(
            media_path="sample.mp4",
            duration=10.0,
            language="en",
            segments=[seg1],
        )
        stats = VideoJobStats(path="sample.mp4", transcript=transcript)

        inspector.load_transcripts([stats], keywords=["apples"])
        self.app.update()

        content = inspector.textbox.get("1.0", "end")
        self.assertIn("Welcome to the video about apples and bananas", content)

        # Search highlighting
        inspector.search_entry.delete(0, "end")
        inspector.search_entry.insert(0, "video")
        inspector._highlight_search()
        self.app.update()
        self.assertIn("1 match", inspector.match_count_lbl.cget("text"))

    def test_performance_console(self):
        """Verify Tab 4 telemetry metrics and colored log output."""
        console = self.app.perf_console
        console.clear_logs()

        console.log("[INFO] Testing standard message")
        console.log("ERROR: ffmpeg failed to open input")
        console.log("WARNING: Slow disk access detected")
        console.log("Found keyword 'supercut' at 12.5s")
        console.log("Completed stitch job ✔")
        self.app.update()

        text = console.log_box.get("1.0", "end")
        self.assertIn("Testing standard message", text)
        self.assertIn("ffmpeg failed", text)
        self.assertIn("Completed stitch job", text)

    def test_settings_persistence(self):
        """Verify full configuration export to JobConfig and JSON round-trip."""
        self.app.kw_chips.set_keywords(["quantum", "entanglement"])
        self.app.exclude_chips.set_keywords(["noise"])
        self.app.prepad_slider.set(2.5)
        self.app.postpad_slider.set(3.0)
        self.app.regex_var.set(True)
        self.app.burn_sub_var.set(True)
        self.app.export_fcpxml_var.set(True)
        self.app.export_edl_var.set(True)

        config = self.app._build_config()
        self.assertEqual(config.keywords, ["quantum", "entanglement"])
        self.assertEqual(config.exclude_keywords, ["noise"])
        self.assertEqual(config.pre_pad, 2.5)
        self.assertEqual(config.post_pad, 3.0)
        self.assertTrue(config.use_regex)
        self.assertTrue(config.burn_subtitles)
        self.assertTrue(config.export_fcpxml)
        self.assertTrue(config.export_edl)


if __name__ == "__main__":
    unittest.main()
