"""Automatic Topic & Keyword Discovery Engine for SuperCut Studio Pro.

Analyzes transcript text using TF-IDF, stopwords filtering, multi-scale n-gram extraction
(1-word, 2-word, 3-word punchy phrases), keyword density scoring, and acoustic speech-rate
high energy moment detection.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..models import ClipInterval, HitSpan, Segment, Transcript, Word
from .matcher import clean_token

# Comprehensive English stopwords + spoken conversational filler words
STOPWORDS: Set[str] = {
    # Common English stop words
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or",
    "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same",
    "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so",
    "some", "such", "than", "that", "that's", "the", "their", "theirs", "them",
    "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll",
    "they're", "they've", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves",
    # Spoken audio fillers and colloquial conversational hedges
    "um", "uh", "uhh", "umm", "like", "yeah", "yep", "yup", "oh", "ah", "ahh",
    "okay", "ok", "right", "well", "gonna", "wanna", "gotta", "really", "actually",
    "basically", "literally", "totally", "definitely", "just", "know", "mean",
    "thing", "things", "stuff", "lot", "lots", "kind", "sort", "maybe", "probably",
    "guess", "sure", "see", "look", "say", "said", "says", "tell", "told", "also",
    "even", "much", "many", "way", "ways", "something", "anything", "nothing",
    "everything", "someone", "anyone", "everyone", "one", "two", "three", "first",
    "second", "third", "going", "come", "came", "get", "got", "getting", "make",
    "made", "making", "take", "took", "taking", "put", "go", "went", "gone",
    "saw", "seen", "let", "already", "always", "ever", "never", "still", "back",
    "bit", "anyway", "alright", "hey", "hi", "hello", "bye", "thanks", "thank",
}


def _split_text_into_tokens(text: str) -> List[str]:
    """Extract cleaned individual word tokens from text."""
    raw_tokens = re.findall(r"\b[\w'-]+\b", text)
    tokens: List[str] = []
    for tok in raw_tokens:
        cleaned = clean_token(tok)
        if cleaned and not cleaned.isnumeric():
            tokens.append(cleaned)
    return tokens


def _extract_ngrams_from_tokens(tokens: Sequence[str]) -> List[Tuple[str, int]]:
    """Extract 1-word, 2-word, and 3-word candidate punchy phrases with n-gram order.
    
    Returns list of (phrase, n_gram_size).
    """
    candidates: List[Tuple[str, int]] = []
    n = len(tokens)

    # 1-grams (unigrams)
    for tok in tokens:
        if len(tok) >= 3 and tok not in STOPWORDS:
            candidates.append((tok, 1))

    # 2-grams (bigrams)
    for i in range(n - 1):
        w1, w2 = tokens[i], tokens[i + 1]
        # At least neither is a stopword and both have reasonable length
        if w1 not in STOPWORDS and w2 not in STOPWORDS:
            if len(w1) >= 2 and len(w2) >= 2:
                phrase = f"{w1} {w2}"
                candidates.append((phrase, 2))

    # 3-grams (trigrams)
    for i in range(n - 2):
        w1, w2, w3 = tokens[i], tokens[i + 1], tokens[i + 2]
        # First and last words must not be stopwords, middle can be connector
        if w1 not in STOPWORDS and w3 not in STOPWORDS:
            if len(w1) >= 2 and len(w3) >= 2:
                # Disallow all 3 words identical or trivial repetition
                if w1 != w2 or w2 != w3:
                    phrase = f"{w1} {w2} {w3}"
                    candidates.append((phrase, 3))

    return candidates


def discover_top_keywords(transcript: Transcript, top_n: int = 15) -> List[Tuple[str, float]]:
    """Automatically discover the top keywords and punchy phrases from a transcript.
    
    Uses TF-IDF, stopwords filtering, n-gram extraction (1-word, 2-word, 3-word),
    and keyword density scoring.
    
    Args:
        transcript: Transcript model containing segments or words.
        top_n: Number of top keywords/phrases to return (default 15).
        
    Returns:
        List of (keyword_or_phrase, relevance_score) sorted descending by score.
    """
    # 1. Gather documents: use segments as individual documents, or split sentences
    documents_tokens: List[List[str]] = []

    if transcript.segments:
        for seg in transcript.segments:
            seg_text = seg.text.strip()
            if seg_text:
                toks = _split_text_into_tokens(seg_text)
                if toks:
                    documents_tokens.append(toks)
    elif transcript.all_words:
        # Fallback to chunks of words
        full_toks = [clean_token(w.text) for w in transcript.all_words if clean_token(w.text)]
        chunk_size = 20
        for i in range(0, len(full_toks), chunk_size):
            chunk = full_toks[i : i + chunk_size]
            if chunk:
                documents_tokens.append(chunk)

    if not documents_tokens:
        # Fallback to full_text if available
        full_text = getattr(transcript, "full_text", "")
        if full_text:
            toks = _split_text_into_tokens(full_text)
            if toks:
                documents_tokens.append(toks)

    if not documents_tokens:
        return []

    total_docs = len(documents_tokens)
    total_tokens_count = sum(len(d) for d in documents_tokens)
    if total_tokens_count == 0:
        return []

    # 2. Extract n-grams per document and compute Document Frequency (DF) and Term Frequency (TF)
    doc_term_counts: List[Counter] = []
    df_counts: Counter = Counter()
    total_tf: Counter = Counter()
    term_ngram_order: Dict[str, int] = {}

    for doc_toks in documents_tokens:
        doc_candidates = _extract_ngrams_from_tokens(doc_toks)
        counts = Counter()
        for phrase, order in doc_candidates:
            counts[phrase] += 1
            term_ngram_order[phrase] = order
            total_tf[phrase] += 1

        for phrase in counts:
            df_counts[phrase] += 1
        doc_term_counts.append(counts)

    if not total_tf:
        return []

    # 3. Calculate TF-IDF, Keyword Density, and N-gram boost
    # Smooth IDF: log((1 + N) / (1 + DF)) + 1.0
    scores: Dict[str, float] = {}

    for term, count in total_tf.items():
        order = term_ngram_order.get(term, 1)
        df = df_counts.get(term, 1)

        # Term frequency weighting: sublinear scale 1 + ln(count)
        tf_weight = 1.0 + math.log(count)

        # Inverse Document Frequency
        idf = math.log((1.0 + total_docs) / (1.0 + df)) + 1.0

        # Keyword density: fraction of total transcript words covered by this term
        density = (count * order) / total_tokens_count

        # N-gram length bonus: multi-word phrases (e.g. "machine learning") are punchier
        # and more distinctive than generic isolated single words
        if order == 1:
            ngram_bonus = 1.0
        elif order == 2:
            ngram_bonus = 1.45
        else:
            ngram_bonus = 1.70

        # Density boost factor
        density_boost = 1.0 + min(1.5, density * 15.0)

        # Raw composite score
        score = tf_weight * idf * ngram_bonus * density_boost
        scores[term] = score

    # 4. Prune redundant unigrams that are subsumed by higher-scoring multi-word phrases
    # Example: If "artificial intelligence" scores high, drop "artificial" if count matches
    sorted_terms = sorted(scores.items(), key=lambda x: (-x[1], -len(x[0]), x[0]))

    pruned_terms: List[Tuple[str, float]] = []
    seen_multigrams: Set[str] = set()

    for term, score in sorted_terms:
        order = term_ngram_order.get(term, 1)
        if order > 1:
            seen_multigrams.add(term)
            pruned_terms.append((term, round(score, 3)))
        else:
            # Check if this unigram is just part of a multi-word phrase with similar frequency
            subsumed = False
            for multi in seen_multigrams:
                if term in multi.split():
                    if total_tf[term] <= total_tf[multi] * 1.2:
                        subsumed = True
                        break
            if not subsumed:
                pruned_terms.append((term, round(score, 3)))

        if len(pruned_terms) >= top_n * 2:
            break

    # Sort final result by score descending and take top_n
    pruned_terms.sort(key=lambda x: (-x[1], x[0]))
    return pruned_terms[:top_n]


def detect_high_energy_moments(
    transcript: Transcript,
    words_per_sec_threshold: float = 3.5,
) -> List[ClipInterval]:
    """Detect moments of high energy and rapid, excited delivery in speech.
    
    Analyzes speaking rate (words per second) across segments and rolling word sequences,
    merging overlapping bursts into cohesive highlight intervals.
    
    Args:
        transcript: Transcript model with word and segment timestamps.
        words_per_sec_threshold: Speech rate threshold in words per second (default 3.5).
        
    Returns:
        List of ClipInterval objects representing high-energy speech moments.
    """
    words = transcript.all_words
    source_video = transcript.media_path or ""
    candidate_spans: List[Tuple[float, float, float, str]] = []  # (start, end, wps, text)

    # 1. Analyze segment-level pacing
    if transcript.segments:
        for seg in transcript.segments:
            dur = seg.end - seg.start
            if dur <= 0.2:
                continue

            seg_words = seg.words if seg.words else [
                w for w in words if seg.start <= w.start and w.end <= seg.end + 0.05
            ]
            w_count = len(seg_words) if seg_words else len(seg.text.split())

            if w_count >= 3:
                wps = w_count / dur
                if wps >= words_per_sec_threshold:
                    candidate_spans.append((seg.start, seg.end, wps, seg.text.strip()))

    # 2. Analyze sliding word windows for intense rapid bursts (windows of 4, 6, 8 words)
    if words and len(words) >= 4:
        for window_size in (4, 6, 8):
            for i in range(len(words) - window_size + 1):
                sub = words[i : i + window_size]
                span_dur = sub[-1].end - sub[0].start
                if span_dur >= 0.25:
                    wps = window_size / span_dur
                    if wps >= words_per_sec_threshold:
                        text_snippet = " ".join(w.text for w in sub)
                        candidate_spans.append((sub[0].start, sub[-1].end, wps, text_snippet))

    if not candidate_spans:
        return []

    # 3. Sort candidate spans chronologically
    candidate_spans.sort(key=lambda s: s[0])

    # 4. Merge overlapping or near-adjacent spans (within 0.8s pause)
    merged_intervals: List[Tuple[float, float, float, str]] = []
    cur_start, cur_end, cur_max_wps, cur_text = candidate_spans[0]

    for s_start, s_end, s_wps, s_txt in candidate_spans[1:]:
        if s_start <= cur_end + 0.8:
            cur_end = max(cur_end, s_end)
            cur_max_wps = max(cur_max_wps, s_wps)
            if s_txt not in cur_text:
                cur_text = f"{cur_text} ... {s_txt}"
        else:
            merged_intervals.append((cur_start, cur_end, cur_max_wps, cur_text))
            cur_start, cur_end, cur_max_wps, cur_text = s_start, s_end, s_wps, s_txt

    merged_intervals.append((cur_start, cur_end, cur_max_wps, cur_text))

    # 5. Build ClipInterval objects
    result_clips: List[ClipInterval] = []
    for idx, (m_start, m_end, m_wps, m_txt) in enumerate(merged_intervals, start=1):
        m_start = max(0.0, round(m_start, 2))
        m_end = round(m_end, 2)
        if m_end <= m_start:
            continue

        # Find word objects within this interval to compute accurate words and confidence
        sub_words = [w for w in words if w.start >= m_start - 0.1 and w.end <= m_end + 0.1]
        confidence = (
            min((w.confidence for w in sub_words), default=1.0)
            if sub_words
            else 1.0
        )
        matched_text = " ".join(w.text for w in sub_words) if sub_words else m_txt

        hit = HitSpan(
            matched_text=matched_text,
            keyword="high-energy",
            start=m_start,
            end=m_end,
            word_indices=[],
            confidence=confidence,
        )

        clip = ClipInterval(
            start=m_start,
            end=m_end,
            keyword_hits=[hit],
            source_video=source_video,
            index=idx,
            selected=True,
            custom_title=f"High Energy ({m_wps:.1f} wps)",
            is_high_energy=True,
        )
        result_clips.append(clip)

    return result_clips
