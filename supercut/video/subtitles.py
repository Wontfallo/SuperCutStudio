"""Subtitle generation (.srt, .vtt, and animated karaoke .ass) with keyword highlighting.

Two flavours are produced:

* **Keyword badges** – one caption per clip showing the matched keyword(s).
* **Word-level karaoke** – the actual spoken words inside each clip, timed on the
  *stitched* timeline, with matched keywords glowing in the accent colour.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import ClipInterval, HitSpan, Segment, Transcript, Word


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def format_timestamp_srt(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    millis = total_ms % 1000
    total_s = total_ms // 1000
    secs = total_s % 60
    total_m = total_s // 60
    minutes = total_m % 60
    hours = total_m // 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    return format_timestamp_srt(seconds).replace(",", ".")


def format_timestamp_ass(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    centis = total_cs % 100
    total_s = total_cs // 100
    secs = total_s % 60
    total_m = total_s // 60
    minutes = total_m % 60
    hours = total_m // 60
    return f"{hours:01d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ensure_parent(path: str) -> None:
    parent = Path(path).parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)


def _ass_escape(text: str) -> str:
    """Neutralise ASS override tags and line breaks inside spoken text."""
    out = text.replace("{", "(").replace("}", ")").replace("\r", " ").replace("\n", " ")
    # "\N" / "\n" / "\h" are ASS control sequences outside braces; break them apart.
    for seq in ("\\N", "\\n", "\\h"):
        out = out.replace(seq, seq[0] + "​" + seq[1])
    return out.strip()


# ---------------------------------------------------------------------------
# Style presets (ASS colours are &HAABBGGRR)
# ---------------------------------------------------------------------------

STYLE_PRESETS: Dict[str, Dict[str, object]] = {
    "karaoke_amber": {
        "font": "Segoe UI", "size": 56, "primary": "&H00FFFFFF", "highlight": "&H0018E8F8",
        "outline": "&H00000000", "back": "&H80000000", "outline_w": 4, "shadow": 2, "bold": -1,
    },
    "karaoke_cyan": {
        "font": "Segoe UI", "size": 56, "primary": "&H00FFFFFF", "highlight": "&H00FFF000",
        "outline": "&H00000000", "back": "&H80000000", "outline_w": 4, "shadow": 2, "bold": -1,
    },
    "clean_white": {
        "font": "Segoe UI", "size": 50, "primary": "&H00FFFFFF", "highlight": "&H00FFFFFF",
        "outline": "&H00202020", "back": "&H00000000", "outline_w": 2, "shadow": 0, "bold": 0,
    },
    "bold_yellow": {
        "font": "Impact", "size": 64, "primary": "&H0000E5FF", "highlight": "&H00FFFFFF",
        "outline": "&H00000000", "back": "&H80000000", "outline_w": 5, "shadow": 3, "bold": -1,
    },
    "minimal_caption": {
        "font": "Consolas", "size": 42, "primary": "&H00E6E6E6", "highlight": "&H0000D2FF",
        "outline": "&H00101010", "back": "&H00000000", "outline_w": 2, "shadow": 0, "bold": 0,
    },
}


def _style(style_name: str) -> Dict[str, object]:
    return STYLE_PRESETS.get(style_name, STYLE_PRESETS["karaoke_amber"])


def _ass_header(style_name: str, play_w: int = 1920, play_h: int = 1080, highlight_override: Optional[str] = None,
                default_override: Optional[str] = None) -> str:
    st = _style(style_name)
    primary = default_override or st["primary"]
    highlight = highlight_override or st["highlight"]
    margin_v = max(40, int(play_h * 0.07))
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_w}\n"
        f"PlayResY: {play_h}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Default,{st['font']},{st['size']},{primary},&H000000FF,{st['outline']},{st['back']},{st['bold']},0,0,0,"
        f"100,100,0,0,1,{st['outline_w']},{st['shadow']},2,40,40,{margin_v},1\n"
        f"Style: Highlight,{st['font']},{int(st['size']) + 4},{highlight},&H000000FF,{st['outline']},{st['back']},-1,0,0,0,"
        f"105,105,1,0,1,{int(st['outline_w']) + 1},{int(st['shadow']) + 1},2,40,40,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


# ---------------------------------------------------------------------------
# Clip badge captions (SRT / VTT / ASS)
# ---------------------------------------------------------------------------

def _clip_caption(clip: ClipInterval) -> str:
    if clip.custom_title:
        return clip.custom_title
    kw_text = ", ".join(clip.all_keywords) or clip.primary_keyword
    return f"[{kw_text.upper()}]"


def _stitched_windows(clips: Sequence[ClipInterval]) -> List[Tuple[ClipInterval, float, float]]:
    """(clip, stitched_start, stitched_end) for each clip with a positive duration."""
    out: List[Tuple[ClipInterval, float, float]] = []
    offset = 0.0
    for clip in clips:
        d = clip.duration
        if d <= 0.001:
            continue
        out.append((clip, offset, offset + d))
        offset += d
    return out


def generate_srt_for_clips(clips: Sequence[ClipInterval], output_path: str) -> None:
    """Generate a standard .srt subtitle file with one keyword caption per stitched clip."""
    lines: List[str] = []
    for idx, (clip, c_start, c_end) in enumerate(_stitched_windows(clips), start=1):
        lines.append(str(idx))
        lines.append(f"{format_timestamp_srt(c_start)} --> {format_timestamp_srt(c_end)}")
        lines.append(_clip_caption(clip))
        lines.append("")
    _ensure_parent(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_vtt_for_clips(clips: Sequence[ClipInterval], output_path: str) -> None:
    lines: List[str] = ["WEBVTT", ""]
    for idx, (clip, c_start, c_end) in enumerate(_stitched_windows(clips), start=1):
        lines.append(str(idx))
        lines.append(f"{format_timestamp_vtt(c_start)} --> {format_timestamp_vtt(c_end)}")
        lines.append(_clip_caption(clip))
        lines.append("")
    _ensure_parent(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_ass_for_clips(
    clips: Sequence[ClipInterval],
    output_path: str,
    highlight_color: str = "&H0018E8F8",
    default_color: str = "&H00FFFFFF",
    style_name: str = "karaoke_amber",
) -> None:
    """Generate an .ass file with one glowing keyword badge per stitched clip."""
    header = _ass_header(style_name, highlight_override=highlight_color, default_override=default_color)
    dialogue_lines: List[str] = []
    for clip, c_start, c_end in _stitched_windows(clips):
        caption = _ass_escape(_clip_caption(clip))
        text = r"{\c" + highlight_color + r"\b1}" + caption + r"{\r}"
        dialogue_lines.append(
            f"Dialogue: 0,{format_timestamp_ass(c_start)},{format_timestamp_ass(c_end)},Highlight,,0,0,0,,{text}"
        )
    _ensure_parent(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(dialogue_lines) + "\n")


# ---------------------------------------------------------------------------
# Word-level karaoke
# ---------------------------------------------------------------------------

def _is_hit_word(word: Word, hits: Iterable[HitSpan]) -> bool:
    clean = word.text.strip().lower().strip(".,!?;:\"'()[]")
    for h in hits:
        if h.start <= word.start + 0.01 and word.end - 0.01 <= h.end:
            return True
        for tok in h.keyword.lower().split():
            if clean == tok or (clean.endswith("s") and clean[:-1] == tok):
                return True
    return False


def _karaoke_lines_for_window(
    words: Sequence[Word],
    hits: Sequence[HitSpan],
    src_start: float,
    src_end: float,
    dst_offset: float,
    highlight_color: str,
    chunk_words: int = 4,
    max_gap: float = 1.2,
) -> List[str]:
    """Build Dialogue lines for words in [src_start, src_end] remapped to dst_offset."""
    clip_words = [w for w in words if w.end > src_start + 0.005 and w.start < src_end - 0.005]
    lines: List[str] = []
    if not clip_words:
        return lines

    # Chunk into short caption groups, breaking on long pauses or punctuation.
    chunks: List[List[Word]] = []
    cur: List[Word] = []
    for w in clip_words:
        if cur:
            gap = w.start - cur[-1].end
            ends_sentence = cur[-1].text.strip().endswith((".", "!", "?"))
            if len(cur) >= chunk_words or gap > max_gap or ends_sentence:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)

    for chunk in chunks:
        c_start = max(src_start, chunk[0].start) - src_start + dst_offset
        c_end = min(src_end, chunk[-1].end) - src_start + dst_offset
        c_end = max(c_start + 0.35, c_end)
        parts: List[str] = []
        for w in chunk:
            clean = _ass_escape(w.text)
            if not clean:
                continue
            if _is_hit_word(w, hits):
                parts.append(r"{\c" + highlight_color + r"\b1}" + clean + r"{\r}")
            else:
                parts.append(clean)
        if not parts:
            continue
        lines.append(
            f"Dialogue: 0,{format_timestamp_ass(c_start)},{format_timestamp_ass(c_end)},Default,,0,0,0,,{' '.join(parts)}"
        )
    return lines


def generate_karaoke_ass(
    words: Sequence[Word],
    hits: Sequence[HitSpan],
    start_time: float,
    end_time: float,
    output_path: str,
    highlight_color: str = "&H0018E8F8",  # Neon Gold/Yellow in BGR
    default_color: str = "&H00FFFFFF",    # White
    style_name: str = "karaoke_amber",
) -> None:
    """Generate an .ass file with glowing keyword highlights for a single clip window."""
    header = _ass_header(style_name, highlight_override=highlight_color, default_override=default_color)
    lines = _karaoke_lines_for_window(words, hits, start_time, end_time, 0.0, highlight_color)
    _ensure_parent(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")


def generate_stitched_karaoke_ass(
    clips: Sequence[ClipInterval],
    words: Sequence[Word],
    output_path: str,
    style_name: str = "karaoke_amber",
    play_w: int = 1920,
    play_h: int = 1080,
    include_badges: bool = False,
) -> int:
    """Word-level karaoke captions across the whole stitched highlight reel.

    Returns the number of dialogue lines written.
    """
    st = _style(style_name)
    highlight = str(st["highlight"])
    header = _ass_header(style_name, play_w=play_w, play_h=play_h)
    lines: List[str] = []
    for clip, c_start, c_end in _stitched_windows(clips):
        lines.extend(
            _karaoke_lines_for_window(words, clip.keyword_hits, clip.start, clip.end, c_start, highlight)
        )
        if include_badges or not words:
            caption = _ass_escape(_clip_caption(clip))
            badge = r"{\an8\c" + highlight + r"\b1\fs38}" + caption + r"{\r}"
            lines.append(
                f"Dialogue: 1,{format_timestamp_ass(c_start)},{format_timestamp_ass(min(c_end, c_start + 2.5))},Default,,0,0,0,,{badge}"
            )
    _ensure_parent(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
    return len(lines)


def generate_stitched_srt_words(clips: Sequence[ClipInterval], words: Sequence[Word], output_path: str) -> int:
    """Plain word-chunk SRT for the stitched reel (useful for YouTube upload)."""
    entries: List[Tuple[float, float, str]] = []
    for clip, c_start, c_end in _stitched_windows(clips):
        clip_words = [w for w in words if w.end > clip.start + 0.005 and w.start < clip.end - 0.005]
        if not clip_words:
            entries.append((c_start, c_end, _clip_caption(clip)))
            continue
        chunk: List[Word] = []
        for w in clip_words:
            if chunk and (len(chunk) >= 7 or w.start - chunk[-1].end > 1.2 or chunk[-1].text.strip().endswith((".", "!", "?"))):
                s = max(clip.start, chunk[0].start) - clip.start + c_start
                e = max(s + 0.4, min(clip.end, chunk[-1].end) - clip.start + c_start)
                entries.append((s, e, " ".join(x.text.strip() for x in chunk)))
                chunk = []
            chunk.append(w)
        if chunk:
            s = max(clip.start, chunk[0].start) - clip.start + c_start
            e = max(s + 0.4, min(clip.end, chunk[-1].end) - clip.start + c_start)
            entries.append((s, e, " ".join(x.text.strip() for x in chunk)))

    lines: List[str] = []
    for idx, (s, e, text) in enumerate(entries, start=1):
        lines.extend([str(idx), f"{format_timestamp_srt(s)} --> {format_timestamp_srt(e)}", text, ""])
    _ensure_parent(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return len(entries)


# ---------------------------------------------------------------------------
# Full transcript exports
# ---------------------------------------------------------------------------

def export_transcript_srt(transcript: Transcript, output_path: str) -> None:
    lines: List[str] = []
    idx = 1
    for seg in transcript.segments:
        text = seg.text.strip()
        if not text or seg.end <= seg.start:
            continue
        lines.extend([str(idx), f"{format_timestamp_srt(seg.start)} --> {format_timestamp_srt(seg.end)}", text, ""])
        idx += 1
    _ensure_parent(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def export_transcript_txt(transcript: Transcript, output_path: str, timestamps: bool = True) -> None:
    lines: List[str] = []
    for seg in transcript.segments:
        text = seg.text.strip()
        if not text:
            continue
        if timestamps:
            m, s = divmod(int(seg.start), 60)
            h, m = divmod(m, 60)
            stamp = f"[{h:02d}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"
            lines.append(f"{stamp} {text}")
        else:
            lines.append(text)
    _ensure_parent(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def transcript_to_srt_text(transcript: Transcript) -> str:
    parts: List[str] = []
    idx = 1
    for seg in transcript.segments:
        text = seg.text.strip()
        if not text or seg.end <= seg.start:
            continue
        parts.extend([str(idx), f"{format_timestamp_srt(seg.start)} --> {format_timestamp_srt(seg.end)}", text, ""])
        idx += 1
    return "\n".join(parts)


__all__ = [
    "STYLE_PRESETS",
    "export_transcript_srt",
    "export_transcript_txt",
    "format_timestamp_ass",
    "format_timestamp_srt",
    "format_timestamp_vtt",
    "generate_ass_for_clips",
    "generate_karaoke_ass",
    "generate_srt_for_clips",
    "generate_stitched_karaoke_ass",
    "generate_stitched_srt_words",
    "generate_vtt_for_clips",
    "transcript_to_srt_text",
]
