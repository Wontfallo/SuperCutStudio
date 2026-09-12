"""Advanced keyword, phrase, regex, and fuzzy pattern matching engine."""

from __future__ import annotations

import difflib
import re
import string
from typing import List, Sequence, Set, Tuple

from ..models import HitSpan, Word


import unicodedata

def clean_token(text: str) -> str:
    """Strip all ASCII and Unicode punctuation, fold accents (é->e, ñ->n), and lowercase."""
    norm = unicodedata.normalize("NFKC", text)
    # Strip any unicode punctuation character
    stripped = "".join(ch for ch in norm if not unicodedata.category(ch).startswith("P"))
    # Fold combining diacritics for Latin scripts while preserving CJK, Cyrillic, emojis
    folded = "".join(c for c in unicodedata.normalize("NFD", stripped) if unicodedata.category(c) != "Mn")
    return folded.lower().strip()


def levenshtein_ratio(s1: str, s2: str) -> float:
    """Compute string similarity ratio between 0.0 and 1.0."""
    return difflib.SequenceMatcher(None, s1, s2).ratio()


class KeywordMatcher:
    """High-performance keyword, phrase, regex, and fuzzy matcher across word sequences."""

    def __init__(
        self,
        keywords: Sequence[str],
        exclude_keywords: Sequence[str] = (),
        case_sensitive: bool = False,
        fuzzy_threshold: float = 0.0,
        use_regex: bool = False,
        min_confidence: float = 0.0,
    ):
        self.raw_keywords = [k.strip() for k in keywords if k.strip()]
        self.exclude_keywords = [k.strip() for k in exclude_keywords if k.strip()]
        self.case_sensitive = case_sensitive
        self.fuzzy_threshold = max(0.0, min(1.0, fuzzy_threshold))
        self.use_regex = use_regex
        self.min_confidence = min_confidence

        self._singles: Set[str] = set()
        self._phrases: List[Tuple[str, ...]] = []
        self._compiled_regexes: List[Tuple[str, re.Pattern]] = []
        self._exclude_singles: Set[str] = set()
        self._exclude_phrases: List[Tuple[str, ...]] = []

        self._parse_patterns()

    def _normalize(self, text: str) -> str:
        return text.strip() if self.case_sensitive else clean_token(text)

    def _parse_patterns(self) -> None:
        if self.use_regex:
            flags = 0 if self.case_sensitive else re.IGNORECASE
            for kw in self.raw_keywords:
                try:
                    pattern = re.compile(kw, flags)
                    self._compiled_regexes.append((kw, pattern))
                except re.error as exc:
                    # Fallback to escaped literal if invalid regex
                    self._compiled_regexes.append((kw, re.compile(re.escape(kw), flags)))
        else:
            for item in self.raw_keywords:
                tokens = tuple(self._normalize(w) for w in item.split() if self._normalize(w))
                if not tokens:
                    continue
                if len(tokens) == 1:
                    self._singles.add(tokens[0])
                else:
                    self._phrases.append(tokens)

        # Parse exclusions
        for item in self.exclude_keywords:
            tokens = tuple(self._normalize(w) for w in item.split() if self._normalize(w))
            if not tokens:
                continue
            if len(tokens) == 1:
                self._exclude_singles.add(tokens[0])
            else:
                self._exclude_phrases.append(tokens)

    def find_matches(self, words: Sequence[Word]) -> List[HitSpan]:
        """Find all matching keyword occurrences across a list of Word objects."""
        if not words:
            return []

        hits: List[HitSpan] = []
        matched_ranges: Set[Tuple[int, int]] = set()
        matched_indices: Set[int] = set()

        # Pre-process word texts
        word_texts = [self._normalize(w.text) for w in words]
        n_words = len(words)

        # 1. Regex Mode
        if self.use_regex:
            for kw_label, pattern in self._compiled_regexes:
                for idx, w in enumerate(words):
                    if w.confidence < self.min_confidence:
                        continue
                    text_to_test = w.text if self.case_sensitive else word_texts[idx]
                    if pattern.search(text_to_test):
                        span = (idx, idx)
                        if span not in matched_ranges:
                            matched_ranges.add(span)
                            matched_indices.add(idx)
                            hits.append(
                                HitSpan(
                                    matched_text=w.text,
                                    keyword=kw_label,
                                    start=w.start,
                                    end=w.end,
                                    word_indices=[idx],
                                    confidence=w.confidence,
                                )
                            )
            return self._filter_exclusions(words, hits)

        # 2. Phrase Matching (Longest phrases first)
        sorted_phrases = sorted(self._phrases, key=lambda p: len(p), reverse=True)
        for phrase in sorted_phrases:
            p_len = len(phrase)
            for i in range(n_words - p_len + 1):
                span = (i, i + p_len - 1)
                if any(k in matched_indices for k in range(i, i + p_len)):
                    continue

                window = tuple(word_texts[i : i + p_len])
                matched = False
                matched_conf = min(words[j].confidence for j in range(i, i + p_len))
                if matched_conf < self.min_confidence:
                    continue

                if window == phrase:
                    matched = True
                elif self.fuzzy_threshold > 0.0:
                    joined_window = " ".join(window)
                    joined_phrase = " ".join(phrase)
                    if levenshtein_ratio(joined_window, joined_phrase) >= self.fuzzy_threshold:
                        matched = True

                if matched:
                    matched_ranges.add(span)
                    matched_indices.update(range(i, i + p_len))
                    orig_text = " ".join(words[j].text for j in range(i, i + p_len))
                    hits.append(
                        HitSpan(
                            matched_text=orig_text,
                            keyword=" ".join(phrase),
                            start=words[i].start,
                            end=words[i + p_len - 1].end,
                            word_indices=list(range(i, i + p_len)),
                            confidence=matched_conf,
                        )
                    )

        # 3. Single Word Matching (Exact, Plural, Fuzzy)
        for idx, w in enumerate(words):
            span = (idx, idx)
            if idx in matched_indices:
                continue
            if w.confidence < self.min_confidence:
                continue

            cleaned = word_texts[idx]
            if not cleaned:
                continue

            found_kw = None
            # Exact match
            if cleaned in self._singles:
                found_kw = cleaned
            # Simple plural check (e.g. "dogs" matches "dog", "boxes" matches "box")
            elif cleaned.endswith("s") and cleaned[:-1] in self._singles:
                found_kw = cleaned[:-1]
            elif cleaned.endswith("es") and cleaned[:-2] in self._singles:
                found_kw = cleaned[:-2]
            # Fuzzy match
            elif self.fuzzy_threshold > 0.0:
                for target_kw in self._singles:
                    if levenshtein_ratio(cleaned, target_kw) >= self.fuzzy_threshold:
                        found_kw = target_kw
                        break

            if found_kw is not None:
                matched_ranges.add(span)
                matched_indices.add(idx)
                hits.append(
                    HitSpan(
                        matched_text=w.text,
                        keyword=found_kw,
                        start=w.start,
                        end=w.end,
                        word_indices=[idx],
                        confidence=w.confidence,
                    )
                )

        # Sort hits chronologically
        hits.sort(key=lambda h: h.start)
        return self._filter_exclusions(words, hits)

    def _filter_exclusions(self, words: Sequence[Word], hits: List[HitSpan]) -> List[HitSpan]:
        """Filter out matches that are part of an excluded phrase or keyword."""
        if not self._exclude_singles and not self._exclude_phrases:
            return hits

        word_texts = [self._normalize(w.text) for w in words]
        filtered: List[HitSpan] = []

        for hit in hits:
            is_excluded = False
            # Check single exclusions
            for idx in hit.word_indices:
                if word_texts[idx] in self._exclude_singles:
                    is_excluded = True
                    break
            if is_excluded:
                continue

            # Check phrase exclusions within a window of surrounding words
            min_idx = max(0, min(hit.word_indices) - 5)
            max_idx = min(len(words), max(hit.word_indices) + 6)
            surrounding = word_texts[min_idx:max_idx]

            for excl_phrase in self._exclude_phrases:
                p_len = len(excl_phrase)
                for j in range(len(surrounding) - p_len + 1):
                    if tuple(surrounding[j : j + p_len]) == excl_phrase:
                        # Check if this exclusion overlaps the hit's word indices
                        excl_indices = set(range(min_idx + j, min_idx + j + p_len))
                        if set(hit.word_indices).intersection(excl_indices):
                            is_excluded = True
                            break
                if is_excluded:
                    break

            if not is_excluded:
                filtered.append(hit)

        return filtered
