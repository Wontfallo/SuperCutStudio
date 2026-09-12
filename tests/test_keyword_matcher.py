"""Unit tests for the SuperCut KeywordMatcher engine."""

import unittest
from supercut.models import Word
from supercut.search.matcher import KeywordMatcher, levenshtein_ratio


class TestKeywordMatcher(unittest.TestCase):
    def setUp(self):
        # Sample word sequence: "I love delicious apple pies and juicy apples every morning"
        self.words = [
            Word(text="I", start=0.0, end=0.2),
            Word(text="love", start=0.25, end=0.5),
            Word(text="delicious", start=0.55, end=1.0),
            Word(text="apple", start=1.05, end=1.4),
            Word(text="pie", start=1.45, end=1.8),
            Word(text="and", start=1.85, end=2.0),
            Word(text="juicy", start=2.05, end=2.4),
            Word(text="apples", start=2.45, end=2.9),
            Word(text="every", start=2.95, end=3.3),
            Word(text="morning", start=3.35, end=3.9),
        ]

    def test_exact_single_keyword(self):
        matcher = KeywordMatcher(keywords=["apple"])
        hits = matcher.find_matches(self.words)
        # Should match "apple" AND automatically match plural "apples"
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].keyword, "apple")
        self.assertEqual(hits[1].keyword, "apple")

    def test_multi_word_phrase(self):
        matcher = KeywordMatcher(keywords=["apple pie", "every morning"])
        hits = matcher.find_matches(self.words)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].matched_text, "apple pie")
        self.assertAlmostEqual(hits[0].start, 1.05)
        self.assertAlmostEqual(hits[0].end, 1.8)
        self.assertEqual(hits[1].matched_text, "every morning")

    def test_negative_keyword_exclusion(self):
        # We want "apple", but NOT if it's "apple pie"
        matcher = KeywordMatcher(keywords=["apple"], exclude_keywords=["apple pie"])
        hits = matcher.find_matches(self.words)
        # Only "apples" should remain, "apple pie" hit excluded
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].matched_text, "apples")

    def test_regex_matching(self):
        matcher = KeywordMatcher(keywords=[r"jui\w+"], use_regex=True)
        hits = matcher.find_matches(self.words)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].matched_text, "juicy")

    def test_fuzzy_matching(self):
        # "aple" misspelled
        matcher = KeywordMatcher(keywords=["aple"], fuzzy_threshold=0.75)
        hits = matcher.find_matches(self.words)
        self.assertTrue(len(hits) >= 1)


if __name__ == "__main__":
    unittest.main()
