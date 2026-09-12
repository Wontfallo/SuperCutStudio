"""Acoustic silence snapping, sentence boundary expansion, and interval merging."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ..models import ClipInterval, HitSpan, Segment, Word


def snap_boundary_to_word_gaps(
    target_time: float,
    words: Sequence[Word],
    search_window: float = 0.8,
    prefer_after: bool = False,
) -> float:
    """Find the best silence gap between spoken words within a search window."""
    if not words:
        return target_time

    best_time = target_time
    max_gap = 0.0

    min_t = target_time - search_window
    max_t = target_time + search_window

    for i in range(len(words) - 1):
        gap_start = words[i].end
        gap_end = words[i + 1].start
        gap_duration = gap_end - gap_start

        # If there is a legitimate acoustic pause between words
        if gap_duration > 0.05:
            mid_gap = (gap_start + gap_end) / 2.0
            if min_t <= mid_gap <= max_t:
                # Prefer larger pause gaps
                if gap_duration > max_gap:
                    max_gap = gap_duration
                    best_time = mid_gap

    return best_time


def expand_to_sentence_boundaries(
    start: float,
    end: float,
    segments: Sequence[Segment],
) -> Tuple[float, float]:
    """Expand clip start and end to the sentence/segment boundaries enclosing the hit."""
    if not segments:
        return start, end

    new_start = start
    new_end = end

    for seg in segments:
        if seg.start <= end and seg.end >= start:
            # Match segment containing the hit
            new_start = min(new_start, seg.start)
            new_end = max(new_end, seg.end)

    return new_start, new_end


def expand_edges_to_sentences(
    start: float,
    end: float,
    segments: Sequence[Segment],
) -> Tuple[float, float]:
    """Push an already-padded window outward to the sentence boundaries its edges fall in.

    If an edge lands inside a sentence, it moves to that sentence's start (for the
    left edge) or end (for the right edge). An edge that lands in the silence
    between sentences stays where it is.
    """
    new_start, new_end = start, end
    for seg in segments:
        if seg.start <= start <= seg.end:
            new_start = min(new_start, seg.start)
        if seg.start <= end <= seg.end:
            new_end = max(new_end, seg.end)
    return new_start, new_end


def optimize_clip_boundaries(
    hits: Sequence[HitSpan],
    words: Sequence[Word],
    segments: Sequence[Segment],
    pre_pad: float = 1.0,
    post_pad: float = 1.0,
    snap_to_silence: bool = True,
    silence_window: float = 0.8,
    sentence_mode: bool = False,
    total_duration: float = 0.0,
    merge_gap: float = 0.5,
    source_video: str = "",
    merge: bool = True,
) -> List[ClipInterval]:
    """Convert raw HitSpans into optimized, padded, silence-snapped, and merged ClipIntervals."""
    if not hits:
        return []

    raw_intervals: List[Tuple[float, float, HitSpan]] = []

    for hit in hits:
        if hit.start < 0 or (total_duration > 0 and hit.start >= total_duration):
            continue
        if hit.end < hit.start:
            continue

        c_start = max(0.0, hit.start - pre_pad)
        c_end = hit.end + post_pad
        if total_duration > 0:
            c_end = min(total_duration, c_end)

        if sentence_mode and segments:
            # Order of authority: padding first, then sentence expansion.
            # The padded edges are pushed OUTWARD to the boundaries of whichever
            # sentence they land in:  [sentence start] <- pre-pad <- hit -> post-pad -> [sentence end]
            # Cuts now sit on sentence boundaries, so silence snapping is redundant here.
            c_start, c_end = expand_edges_to_sentences(c_start, c_end, segments)
            if total_duration > 0:
                c_end = min(total_duration, c_end)
        elif snap_to_silence and words:
            # Snap start to the nearest pause before or around start
            c_start = max(0.0, snap_boundary_to_word_gaps(c_start, words, silence_window, prefer_after=False))
            # Snap end to the nearest pause after or around end
            c_end = snap_boundary_to_word_gaps(c_end, words, silence_window, prefer_after=True)
        # Enforce valid bounds and strictly positive duration
        c_start = round(max(0.0, c_start), 3)
        if total_duration > 0:
            c_start = min(c_start, max(0.0, total_duration - 0.05))
            c_end = round(min(total_duration, max(c_start + 0.05, c_end)), 3)
        else:
            c_end = round(max(c_start + 0.05, c_end), 3)

        if (c_end - c_start) >= 0.0499:
            raw_intervals.append((c_start, c_end, hit))

    # Sort intervals by start time
    raw_intervals.sort(key=lambda x: x[0])

    # Merge overlapping or close intervals (unless the user wants one clip per hit)
    merged: List[ClipInterval] = []
    for c_start, c_end, hit in raw_intervals:
        if not merge:
            merged.append(ClipInterval(start=c_start, end=c_end, keyword_hits=[hit], source_video=source_video, index=len(merged) + 1))
            continue
        if not merged:
            merged.append(
                ClipInterval(
                    start=c_start,
                    end=c_end,
                    keyword_hits=[hit],
                    source_video=source_video,
                    index=1,
                )
            )
            continue

        last = merged[-1]
        if c_start <= last.end + merge_gap:
            # Merge
            last.end = max(last.end, c_end)
            last.keyword_hits.append(hit)
        else:
            merged.append(
                ClipInterval(
                    start=c_start,
                    end=c_end,
                    keyword_hits=[hit],
                    source_video=source_video,
                    index=len(merged) + 1,
                )
            )

    # Re-index
    for idx, clip in enumerate(merged, start=1):
        clip.index = idx

    return merged
