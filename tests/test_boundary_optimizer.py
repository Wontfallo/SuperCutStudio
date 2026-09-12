"""Unit tests for boundary snapping and sentence mode."""

import unittest
from supercut.audio.silence import expand_to_sentence_boundaries, optimize_clip_boundaries, snap_boundary_to_word_gaps
from supercut.models import HitSpan, Segment, Word


class TestBoundaryOptimizer(unittest.TestCase):
    def setUp(self):
        self.words = [
            Word(text="This", start=1.0, end=1.3),
            Word(text="is", start=1.35, end=1.5),
            Word(text="a", start=1.55, end=1.65),
            Word(text="target", start=1.7, end=2.1),
            Word(text="sentence.", start=2.15, end=2.8),
            # Notice 1.2 second pause here
            Word(text="Next", start=4.0, end=4.3),
            Word(text="section", start=4.35, end=4.8),
        ]
        self.segments = [
            Segment(start=1.0, end=2.8, text="This is a target sentence.", words=self.words[:5]),
            Segment(start=4.0, end=4.8, text="Next section.", words=self.words[5:]),
        ]

    def test_silence_snapping(self):
        # Target was at 1.7 - 2.1. With 1.0s post-pad, un-snapped end is 3.1s.
        # The word gap is between 2.8s and 4.0s (midpoint 3.4s).
        snapped = snap_boundary_to_word_gaps(3.1, self.words, search_window=0.8)
        self.assertAlmostEqual(snapped, 3.4, delta=0.1)

    def test_sentence_expansion(self):
        start, end = expand_to_sentence_boundaries(1.7, 2.1, self.segments)
        self.assertEqual(start, 1.0)
        self.assertEqual(end, 2.8)

    def test_optimize_clip_boundaries_pipeline(self):
        hits = [HitSpan(matched_text="target", keyword="target", start=1.7, end=2.1, word_indices=[3])]
        clips = optimize_clip_boundaries(
            hits=hits,
            words=self.words,
            segments=self.segments,
            pre_pad=0.5,
            post_pad=0.5,
            snap_to_silence=True,
            sentence_mode=False,
            total_duration=10.0,
        )
        self.assertEqual(len(clips), 1)
        self.assertTrue(clips[0].start < 1.7)
        self.assertTrue(clips[0].end > 2.1)


if __name__ == "__main__":
    unittest.main()
