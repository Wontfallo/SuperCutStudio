"""Unit tests for NLE enhancements (DaVinci Resolve markers, FCPXML audio mapping) and contact sheets."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from PIL import Image

from supercut.export.nle import export_cmx3600_edl, export_premiere_fcpxml, is_high_energy_clip
from supercut.models import ClipInterval, HitSpan
from supercut.video.thumbnail import (
    create_contact_sheet,
    create_sprite_sheet,
    extract_clip_thumbnail,
    generate_contact_sheet,
    generate_sprite_sheet,
    generate_thumbnail_sprite_sheet,
)


class TestNLEAndSprites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
            cls.ffmpeg_available = True
        except Exception:
            cls.ffmpeg_available = False

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = self.temp_dir.name
        self.sample_video = os.path.join(self.td, "sample_clip.mp4")

        # Create a short 3-second test video if ffmpeg is available
        if self.ffmpeg_available:
            cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=duration=3:size=320x180:rate=30",
                "-f", "lavfi", "-i", "sine=duration=3:frequency=440",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                self.sample_video,
            ]
            subprocess.run(cmd, check=True, capture_output=True)

        self.clips = [
            ClipInterval(
                start=1.0,
                end=2.5,
                source_video=self.sample_video,
                keyword_hits=[HitSpan("quantum", "quantum", 1.2, 1.8)],
                index=1,
                is_high_energy=False,
            ),
            ClipInterval(
                start=3.0,
                end=5.0,
                source_video=self.sample_video,
                keyword_hits=[HitSpan("rapid excitement", "high-energy", 3.2, 4.8)],
                index=2,
                custom_title="High Energy (4.2 wps)",
                is_high_energy=True,
            ),
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    # =========================================================================
    # Task 1A: DaVinci Resolve EDL Markers
    # =========================================================================

    def test_edl_markers_cyan_for_keywords_and_green_for_high_energy(self):
        """Verify DaVinci Resolve color-coded markers in EDL: Cyan for keywords, Green for energy."""
        edl_path = os.path.join(self.td, "markers_test.edl")
        export_cmx3600_edl(self.clips, "ResolveMarkerMontage", edl_path, fps=30)
        self.assertTrue(os.path.isfile(edl_path))

        with open(edl_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check sequence title
        self.assertIn("TITLE: ResolveMarkerMontage", content)

        # Clip 1 is a primary keyword hit: must have ResolveColorCyan marker
        self.assertIn("* |C:ResolveColorCyan |M:Keyword: quantum", content)

        # Clip 2 is high energy: must have ResolveColorGreen marker
        self.assertIn("* |C:ResolveColorGreen", content)
        self.assertIn("High Energy", content)

    def test_edl_markers_duration_calculation(self):
        """Verify marker duration matches clip/hit frame calculation."""
        edl_path = os.path.join(self.td, "marker_dur.edl")
        # 1.8 - 1.2 = 0.6s -> 0.6 * 30 = 18 frames
        export_cmx3600_edl(self.clips, "DurationTest", edl_path, fps=30)
        with open(edl_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("|D:18", content)

    def test_edl_multiple_hits_per_clip(self):
        """Verify multi-hit clips emit markers for all contained keywords."""
        multi_clip = ClipInterval(
            start=0.0,
            end=10.0,
            source_video=self.sample_video,
            keyword_hits=[
                HitSpan("alpha", "alpha", 1.0, 2.0),
                HitSpan("burst", "high-energy", 4.0, 6.0),
            ],
            index=1,
        )
        edl_path = os.path.join(self.td, "multi_hit.edl")
        export_cmx3600_edl([multi_clip], "MultiHitTest", edl_path, fps=30)

        with open(edl_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("* |C:ResolveColorCyan |M:Keyword: alpha |D:30", content)
        self.assertIn("* |C:ResolveColorGreen |M:High Energy: burst |D:60", content)

    def test_is_high_energy_clip_helper(self):
        """Verify helper detection logic across multiple signal sources."""
        c1 = ClipInterval(0.0, 2.0, is_high_energy=True)
        self.assertTrue(is_high_energy_clip(c1))

        c2 = ClipInterval(0.0, 2.0, keyword_hits=[HitSpan("test", "high-energy", 0.5, 1.5)])
        self.assertTrue(is_high_energy_clip(c2))

        c3 = ClipInterval(0.0, 2.0, custom_title="High Energy burst")
        self.assertTrue(is_high_energy_clip(c3))

        c4 = ClipInterval(0.0, 2.0, keyword_hits=[HitSpan("apple", "apple", 0.5, 1.5)])
        self.assertFalse(is_high_energy_clip(c4))

    # =========================================================================
    # Task 1B: FCPXML Audio Channel Mapping (Dual-Mono Stereo)
    # =========================================================================

    def test_fcpxml_dual_mono_stereo_tracks(self):
        """Verify FCPXML generates explicit stereo dual-mono audio tracks (Channel 1 and Channel 2)."""
        xml_path = os.path.join(self.td, "fcpxml_audio.xml")
        export_premiere_fcpxml(
            self.clips,
            "DualMonoSequence",
            xml_path,
            fps=30,
            width=1920,
            height=1080,
            audio_sample_rate=48000,
            audio_depth=16,
        )

        self.assertTrue(os.path.isfile(xml_path))

        # Parse XML to validate structure
        tree = ET.parse(xml_path)
        root = tree.getroot()
        self.assertEqual(root.tag, "xmeml")
        self.assertEqual(root.attrib.get("version"), "4")

        seq = root.find("sequence")
        self.assertIsNotNone(seq)
        self.assertEqual(seq.find("name").text, "DualMonoSequence")

        media = seq.find("media")
        self.assertIsNotNone(media)

        # Check video stream
        video = media.find("video")
        self.assertIsNotNone(video)
        v_track = video.find("track")
        v_items = v_track.findall("clipitem")
        self.assertEqual(len(v_items), 2)

        # Check audio stream
        audio = media.find("audio")
        self.assertIsNotNone(audio)
        self.assertEqual(audio.find("numOutputChannels").text, "2")

        audio_format = audio.find("format/samplecharacteristics")
        self.assertIsNotNone(audio_format)
        self.assertEqual(audio_format.find("depth").text, "16")
        self.assertEqual(audio_format.find("samplerate").text, "48000")

        # Must have exactly 2 discrete audio tracks (dual-mono: Channel 1 and Channel 2)
        a_tracks = audio.findall("track")
        self.assertEqual(len(a_tracks), 2)

        track_1 = a_tracks[0]
        track_2 = a_tracks[1]

        # Verify Channel 1 mapping on Track 1
        t1_items = track_1.findall("clipitem")
        self.assertEqual(len(t1_items), 2)
        for item in t1_items:
            sourcetrack = item.find("sourcetrack")
            self.assertIsNotNone(sourcetrack)
            self.assertEqual(sourcetrack.find("mediatype").text, "audio")
            self.assertEqual(sourcetrack.find("trackindex").text, "1")

        # Verify Channel 2 mapping on Track 2
        t2_items = track_2.findall("clipitem")
        self.assertEqual(len(t2_items), 2)
        for item in t2_items:
            sourcetrack = item.find("sourcetrack")
            self.assertIsNotNone(sourcetrack)
            self.assertEqual(sourcetrack.find("mediatype").text, "audio")
            self.assertEqual(sourcetrack.find("trackindex").text, "2")

    def test_fcpxml_clipitem_linking(self):
        """Verify video clipitem and dual audio clipitems contain synchronization link references."""
        xml_path = os.path.join(self.td, "linking_test.xml")
        export_premiere_fcpxml(self.clips, "LinkSequence", xml_path, fps=30)

        tree = ET.parse(xml_path)
        root = tree.getroot()
        first_video_clip = root.find(".//video/track/clipitem")
        self.assertIsNotNone(first_video_clip)

        links = first_video_clip.findall("link")
        self.assertEqual(len(links), 3)  # Self (video), Audio Track 1, Audio Track 2

        cliprefs = [l.find("linkclipref").text for l in links]
        self.assertIn("clipitem-1", cliprefs)
        self.assertIn("clipitem-a1-1", cliprefs)
        self.assertIn("clipitem-a2-1", cliprefs)

    # =========================================================================
    # Task 2: Thumbnail Contact Sheet / Sprite Sheet Generation
    # =========================================================================

    def test_contact_sheet_from_clips(self):
        """Verify contact sheet generation from a sequence of ClipIntervals."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        sheet_out = os.path.join(self.td, "contact_sheet_clips.jpg")
        result = generate_contact_sheet(
            intervals=self.clips,
            output_path=sheet_out,
            columns=2,
            tile_width=160,
            tile_height=90,
            header_title="Test SuperCut Index",
            show_labels=True,
        )

        self.assertIsNotNone(result)
        self.assertTrue(os.path.isfile(sheet_out))
        self.assertGreater(os.path.getsize(sheet_out), 0)

        with Image.open(sheet_out) as img:
            w, h = img.size
            # 2 columns * 160 = 320 + padding/margins > 340
            self.assertGreaterEqual(w, 320)
            self.assertGreaterEqual(h, 150)

    def test_contact_sheet_from_video_sampling(self):
        """Verify contact sheet uniform sampling directly from a video file."""
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        sheet_out = os.path.join(self.td, "contact_sheet_video.jpg")
        result = generate_contact_sheet(
            video_path=self.sample_video,
            output_path=sheet_out,
            columns=3,
            max_frames=6,
            tile_width=120,
            tile_height=68,
            header_title="Direct Video Sample",
        )

        self.assertIsNotNone(result)
        self.assertTrue(os.path.isfile(sheet_out))
        self.assertGreater(os.path.getsize(sheet_out), 0)

    def test_contact_sheet_from_existing_images(self):
        """Verify contact sheet stitching from pre-extracted image files."""
        # Create 4 test image files
        img_paths = []
        for i in range(4):
            ip = os.path.join(self.td, f"source_thumb_{i}.png")
            im = Image.new("RGB", (100, 100), color=(i * 40, 100, 200 - i * 30))
            im.save(ip)
            img_paths.append(ip)

        sheet_out = os.path.join(self.td, "from_images.jpg")
        result = generate_sprite_sheet(
            image_paths=img_paths,
            output_path=sheet_out,
            columns=2,
            tile_width=100,
            tile_height=100,
            show_labels=False,
            margin=10,
            padding=5,
        )

        self.assertIsNotNone(result)
        self.assertTrue(os.path.isfile(sheet_out))
        with Image.open(sheet_out) as sheet:
            # 2 cols * 100 + 1 * 5 padding + 2 * 10 margin = 225
            # 2 rows * 100 + 1 * 5 padding + 2 * 10 margin = 225
            self.assertEqual(sheet.size, (225, 225))

    def test_sprite_sheet_aliases(self):
        """Verify all caller aliases function identically."""
        self.assertIs(generate_sprite_sheet, generate_contact_sheet)
        self.assertIs(create_sprite_sheet, generate_contact_sheet)
        self.assertIs(create_contact_sheet, generate_contact_sheet)
        self.assertIs(generate_thumbnail_sprite_sheet, generate_contact_sheet)

    def test_contact_sheet_empty_input_graceful_handling(self):
        """Verify empty clips or invalid input gracefully returns None without raising."""
        sheet_out = os.path.join(self.td, "empty.jpg")
        result = generate_contact_sheet(intervals=[], output_path=sheet_out)
        self.assertIsNone(result)
        self.assertFalse(os.path.exists(sheet_out))

        result2 = generate_contact_sheet(video_path="nonexistent.mp4", output_path=sheet_out)
        self.assertIsNone(result2)
        self.assertFalse(os.path.exists(sheet_out))


if __name__ == "__main__":
    unittest.main()
