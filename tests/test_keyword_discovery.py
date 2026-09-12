"""Unit tests for SuperCut automatic keyword discovery and high-energy detection."""

import unittest
from unittest.mock import MagicMock, patch

from supercut.models import ClipInterval, Segment, Transcript, VideoJobStats, Word
from supercut.search.discovery import (
    STOPWORDS,
    _extract_ngrams_from_tokens,
    _split_text_into_tokens,
    detect_high_energy_moments,
    discover_top_keywords,
)
from supercut.ui.chip_view import KeywordChipView


class TestKeywordDiscoveryEngine(unittest.TestCase):
    """Test suite for TF-IDF keyword discovery, n-grams, stopwords, and density scoring."""

    def setUp(self):
        # Create a realistic multi-segment tech podcast transcript
        self.seg1 = Segment(
            start=0.0,
            end=6.0,
            text="Welcome to the show. Today we dive deep into artificial intelligence and machine learning.",
            words=[
                Word(text="Welcome", start=0.0, end=0.5),
                Word(text="to", start=0.5, end=0.7),
                Word(text="the", start=0.7, end=0.9),
                Word(text="show.", start=0.9, end=1.3),
                Word(text="Today", start=1.5, end=1.8),
                Word(text="we", start=1.8, end=2.0),
                Word(text="dive", start=2.0, end=2.4),
                Word(text="deep", start=2.4, end=2.8),
                Word(text="into", start=2.8, end=3.1),
                Word(text="artificial", start=3.1, end=3.8),
                Word(text="intelligence", start=3.8, end=4.6),
                Word(text="and", start=4.6, end=4.8),
                Word(text="machine", start=4.8, end=5.3),
                Word(text="learning.", start=5.3, end=6.0),
            ],
        )
        self.seg2 = Segment(
            start=6.5,
            end=12.0,
            text="Machine learning models and neural networks are transforming video editing and supercut automation.",
            words=[
                Word(text="Machine", start=6.5, end=7.0),
                Word(text="learning", start=7.0, end=7.5),
                Word(text="models", start=7.5, end=8.0),
                Word(text="and", start=8.0, end=8.2),
                Word(text="neural", start=8.2, end=8.7),
                Word(text="networks", start=8.7, end=9.3),
                Word(text="are", start=9.3, end=9.5),
                Word(text="transforming", start=9.5, end=10.2),
                Word(text="video", start=10.2, end=10.6),
                Word(text="editing", start=10.6, end=11.1),
                Word(text="and", start=11.1, end=11.3),
                Word(text="supercut", start=11.3, end=11.7),
                Word(text="automation.", start=11.7, end=12.0),
            ],
        )
        self.seg3 = Segment(
            start=12.5,
            end=18.0,
            text="Um like you know artificial intelligence and machine learning models are basically everywhere.",
            words=[
                Word(text="Um", start=12.5, end=12.8),
                Word(text="like", start=12.8, end=13.1),
                Word(text="you", start=13.1, end=13.3),
                Word(text="know", start=13.3, end=13.6),
                Word(text="artificial", start=13.6, end=14.3),
                Word(text="intelligence", start=14.3, end=15.1),
                Word(text="and", start=15.1, end=15.3),
                Word(text="machine", start=15.3, end=15.8),
                Word(text="learning", start=15.8, end=16.3),
                Word(text="models", start=16.3, end=16.9),
                Word(text="are", start=16.9, end=17.1),
                Word(text="basically", start=17.1, end=17.6),
                Word(text="everywhere.", start=17.6, end=18.0),
            ],
        )
        self.transcript = Transcript(
            media_path="ai_podcast.mp4",
            duration=18.5,
            language="en",
            segments=[self.seg1, self.seg2, self.seg3],
        )

    def test_stopwords_and_tokenization(self):
        """Verify stopwords filtering and clean tokenization."""
        text = "Um, like you know, artificial intelligence is REALLY awesome!"
        tokens = _split_text_into_tokens(text)
        self.assertIn("artificial", tokens)
        self.assertIn("intelligence", tokens)
        self.assertIn("awesome", tokens)

        # Confirm spoken fillers are in stopwords set
        self.assertIn("um", STOPWORDS)
        self.assertIn("like", STOPWORDS)
        self.assertIn("basically", STOPWORDS)

    def test_ngram_extraction(self):
        """Verify 1-word, 2-word, and 3-word punchy phrases extraction."""
        tokens = ["artificial", "intelligence", "neural", "networks"]
        candidates = _extract_ngrams_from_tokens(tokens)
        phrases = [c[0] for c in candidates]

        # 1-gram
        self.assertIn("artificial", phrases)
        self.assertIn("intelligence", phrases)

        # 2-gram
        self.assertIn("artificial intelligence", phrases)
        self.assertIn("neural networks", phrases)

        # 3-gram
        self.assertIn("artificial intelligence neural", phrases)
        self.assertIn("intelligence neural networks", phrases)

    def test_discover_top_keywords(self):
        """Verify TF-IDF and density scoring extracts relevant top keywords."""
        top_kws = discover_top_keywords(self.transcript, top_n=5)
        self.assertTrue(len(top_kws) > 0)
        self.assertLessEqual(len(top_kws), 5)

        discovered_terms = [kw for kw, score in top_kws]
        # "machine learning" and "artificial intelligence" appear across multiple segments
        self.assertTrue(
            any("machine learning" in term for term in discovered_terms)
            or any("artificial intelligence" in term for term in discovered_terms)
        )

        # Scores should be strictly positive floats sorted in descending order
        scores = [score for kw, score in top_kws]
        self.assertTrue(all(s > 0.0 for s in scores))
        self.assertEqual(scores, sorted(scores, reverse=True))

        # Stopwords must not appear as top keywords
        for stopword in ["the", "we", "into", "and", "are", "basically", "you", "know"]:
            self.assertNotIn(stopword, discovered_terms)

    def test_empty_and_edge_case_transcripts(self):
        """Verify graceful handling of empty or degenerate transcripts."""
        empty_tr = Transcript(media_path="", duration=0.0, language="en", segments=[])
        self.assertEqual(discover_top_keywords(empty_tr), [])

        # Transcript with only stopwords
        stopwords_seg = Segment(start=0.0, end=2.0, text="Um, you know like basically yeah yeah.")
        stopwords_tr = Transcript(media_path="", duration=2.0, language="en", segments=[stopwords_seg])
        res = discover_top_keywords(stopwords_tr)
        self.assertEqual(res, [])

        # Transcript with single segment
        single_seg = Segment(start=0.0, end=4.0, text="Quantum computing breakthrough enables faster cryptography.")
        single_tr = Transcript(media_path="", duration=4.0, language="en", segments=[single_seg])
        res_single = discover_top_keywords(single_tr, top_n=3)
        self.assertTrue(len(res_single) > 0)


class TestHighEnergyDetection(unittest.TestCase):
    """Test suite for acoustic speech-rate high energy moment detection."""

    def test_detect_high_energy_speech_burst(self):
        """Verify that speech exceeding the words_per_sec_threshold is detected as high energy."""
        # Normal speaking rate: 10 words in 5 seconds = 2.0 words/sec
        normal_words = [
            Word(text=f"word{i}", start=float(i) * 0.5, end=float(i) * 0.5 + 0.4)
            for i in range(10)
        ]
        normal_seg = Segment(start=0.0, end=5.0, text="This is slow normal speech.", words=normal_words)

        # Rapid high-energy speech: 12 words in 2.0 seconds = 6.0 words/sec (> 3.5 wps)
        rapid_words = [
            Word(text=f"fast{i}", start=10.0 + float(i) * 0.16, end=10.0 + float(i) * 0.16 + 0.14)
            for i in range(12)
        ]
        rapid_seg = Segment(start=10.0, end=12.0, text="Go go go hurry up quick run now right away fast!", words=rapid_words)

        tr = Transcript(
            media_path="speech.mp4",
            duration=15.0,
            language="en",
            segments=[normal_seg, rapid_seg],
        )

        clips = detect_high_energy_moments(tr, words_per_sec_threshold=3.5)
        self.assertEqual(len(clips), 1)
        self.assertAlmostEqual(clips[0].start, 10.0, delta=0.5)
        self.assertAlmostEqual(clips[0].end, 12.0, delta=0.5)
        self.assertEqual(clips[0].primary_keyword, "high-energy")
        self.assertIn("High Energy", clips[0].custom_title or "")

    def test_low_energy_returns_empty(self):
        """Verify that steady, slow conversational speech triggers zero high-energy intervals."""
        slow_words = [
            Word(text=f"word{i}", start=float(i) * 1.0, end=float(i) * 1.0 + 0.8)
            for i in range(5)
        ]
        slow_seg = Segment(start=0.0, end=5.0, text="Just a very calm and slow conversation.", words=slow_words)
        tr = Transcript(media_path="calm.mp4", duration=5.0, language="en", segments=[slow_seg])

        clips = detect_high_energy_moments(tr, words_per_sec_threshold=3.5)
        self.assertEqual(clips, [])

    def test_interval_merging_for_adjacent_bursts(self):
        """Verify that closely spaced rapid bursts are merged into a cohesive clip interval."""
        burst1_words = [
            Word(text=f"w{i}", start=1.0 + i * 0.2, end=1.0 + i * 0.2 + 0.18) for i in range(5)
        ]
        burst2_words = [
            Word(text=f"w{i}", start=2.2 + i * 0.2, end=2.2 + i * 0.2 + 0.18) for i in range(5)
        ]
        seg1 = Segment(start=1.0, end=2.0, text="burst one", words=burst1_words)
        seg2 = Segment(start=2.2, end=3.2, text="burst two", words=burst2_words)

        tr = Transcript(media_path="bursts.mp4", duration=5.0, language="en", segments=[seg1, seg2])
        clips = detect_high_energy_moments(tr, words_per_sec_threshold=3.5)

        # The 0.2s pause between burst1 (end=2.0) and burst2 (start=2.2) should be merged
        self.assertEqual(len(clips), 1)
        self.assertLessEqual(clips[0].start, 1.2)
        self.assertGreaterEqual(clips[0].end, 3.0)


class TestCliAndUiIntegration(unittest.TestCase):
    """Test CLI argument parsing and UI widget wiring for keyword discovery."""

    def test_cli_auto_keywords_arg(self):
        """Verify CLI argument parsing accepts --auto-keywords with or without explicit -k."""
        import cli

        # Case 1: --auto-keywords alone
        with patch("sys.argv", ["cli.py", "-i", "sample.mp4", "--auto-keywords"]):
            args = cli.parse_args()
            self.assertEqual(args.auto_keywords, 15)
            self.assertEqual(args.keywords, "")

        # Case 2: --auto-keywords with custom top_n
        with patch("sys.argv", ["cli.py", "-i", "sample.mp4", "--auto-keywords", "8"]):
            args = cli.parse_args()
            self.assertEqual(args.auto_keywords, 8)

        # Case 3: Both -k and --auto-keywords
        with patch("sys.argv", ["cli.py", "-i", "sample.mp4", "-k", "ai,robotics", "--auto-keywords"]):
            args = cli.parse_args()
            self.assertEqual(args.keywords, "ai,robotics")
            self.assertEqual(args.auto_keywords, 15)

    def test_chip_view_auto_discover_button(self):
        """Verify KeywordChipView renders auto-discover button and invokes callback."""
        import customtkinter as ctk

        root = ctk.CTk()
        root.withdraw()
        try:
            callback_called = [False]

            def on_auto():
                callback_called[0] = True

            chip_view = KeywordChipView(
                root,
                is_exclude=False,
                on_auto_discover=on_auto,
            )
            root.update()

            self.assertIsNotNone(chip_view.auto_discover_btn)
            chip_view.auto_discover_btn.invoke()
            self.assertTrue(callback_called[0])

            # Exclusion chips should not have auto-discover button
            exclude_view = KeywordChipView(root, is_exclude=True)
            root.update()
            self.assertIsNone(exclude_view.auto_discover_btn)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
