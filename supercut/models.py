"""Core domain models and typed data structures for SuperCut Studio Pro."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class AspectRatioMode(str, Enum):
    SOURCE = "source"           # Preserve source aspect ratio
    LANDSCAPE_16_9 = "16:9"     # Standard 16:9 widescreen
    VERTICAL_9_16 = "9:16"      # Mobile vertical (TikTok/Reels/Shorts) with blurred background
    SQUARE_1_1 = "1:1"          # Instagram square post


class TransitionMode(str, Enum):
    CUT = "cut"                 # Direct hard cut
    CROSSFADE = "crossfade"     # Video dissolve + audio acrossfade
    FADE_BLACK = "fade_black"   # Dip to black
    SWIPE_LEFT = "swipe_left"   # Motion transition


class EncoderChoice(str, Enum):
    AUTO = "auto"
    NVENC_HEVC = "hevc_nvenc"
    NVENC_H264 = "h264_nvenc"
    NVENC_AV1 = "av1_nvenc"
    CPU_X264 = "libx264"
    CPU_X265 = "libx265"


class QualityPreset(str, Enum):
    """Encoder quality presets mapped onto CQ/CRF values."""
    DRAFT = "draft"          # fastest, larger quantizer
    BALANCED = "balanced"    # default
    HIGH = "high"            # near-transparent


SUBTITLE_STYLES = ("karaoke_amber", "karaoke_cyan", "clean_white", "bold_yellow", "minimal_caption")


def _enum_from_value(enum_cls, value, default):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except Exception:
        return default


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    confidence: float = 1.0
    speaker: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Word:
        return cls(
            text=data.get("text", ""),
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            confidence=float(data.get("confidence", 1.0)),
            speaker=data.get("speaker"),
        )


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)
    speaker: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
            "speaker": self.speaker,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Segment:
        words = [Word.from_dict(w) for w in data.get("words", [])]
        return cls(
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            text=str(data.get("text", "")),
            words=words,
            speaker=data.get("speaker"),
        )


@dataclass
class Transcript:
    media_path: str
    duration: float
    language: str
    segments: List[Segment] = field(default_factory=list)
    model_name: str = ""
    created_at: float = 0.0

    @property
    def all_words(self) -> List[Word]:
        words: List[Word] = []
        for s in self.segments:
            words.extend(s.words)
        return words

    @property
    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    def words_between(self, start: float, end: float, slack: float = 0.02) -> List[Word]:
        """Words that overlap the [start, end] window."""
        return [w for w in self.all_words if w.end >= start - slack and w.start <= end + slack]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "media_path": self.media_path,
            "duration": self.duration,
            "language": self.language,
            "model_name": self.model_name,
            "created_at": self.created_at,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Transcript:
        segments = [Segment.from_dict(s) for s in data.get("segments", [])]
        return cls(
            media_path=str(data.get("media_path", "")),
            duration=float(data.get("duration", 0.0)),
            language=str(data.get("language", "en")),
            segments=segments,
            model_name=str(data.get("model_name", "")),
            created_at=float(data.get("created_at", 0.0)),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, text: str) -> Transcript:
        return cls.from_dict(json.loads(text))


@dataclass
class MediaInfo:
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    has_audio: bool = False
    video_codec: str = ""
    audio_codec: str = ""
    sample_rate: int = 48000
    channels: int = 2
    bitrate: int = 0
    size_bytes: int = 0

    @property
    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0 and self.duration > 0

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["resolution"] = self.resolution_str
        return d


@dataclass
class HitSpan:
    matched_text: str
    keyword: str
    start: float
    end: float
    word_indices: List[int] = field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matched_text": self.matched_text,
            "keyword": self.keyword,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "confidence": round(self.confidence, 3),
        }


@dataclass
class ClipInterval:
    start: float
    end: float
    keyword_hits: List[HitSpan] = field(default_factory=list)
    source_video: str = ""
    index: int = 1
    selected: bool = True               # For interactive review UI toggle
    custom_title: Optional[str] = None  # Label/caption
    is_high_energy: bool = False        # High-energy / excited moment indicator
    manual: bool = False                # Added by hand in the reviewer

    @property
    def duration(self) -> float:
        try:
            diff = self.end - self.start
            if diff != diff:  # NaN check
                return 0.0
            return max(0.0, round(diff, 4))
        except (TypeError, ValueError):
            return 0.0

    @property
    def primary_keyword(self) -> str:
        if self.keyword_hits:
            return self.keyword_hits[0].keyword
        if self.custom_title:
            return self.custom_title
        return "clip"

    @property
    def all_keywords(self) -> List[str]:
        seen: List[str] = []
        for h in self.keyword_hits:
            if h.keyword not in seen:
                seen.append(h.keyword)
        return seen

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "selected": self.selected,
            "custom_title": self.custom_title or "",
            "primary_keyword": self.primary_keyword,
            "keywords": self.all_keywords,
            "is_high_energy": self.is_high_energy,
            "manual": self.manual,
            "source_video": self.source_video,
            "hits": [h.to_dict() for h in self.keyword_hits],
            "matched_text": self.keyword_hits[0].matched_text if self.keyword_hits else "",
            "confidence": round(min((h.confidence for h in self.keyword_hits), default=1.0), 3),
        }


@dataclass
class JobConfig:
    keywords: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    excluded_files: List[str] = field(default_factory=list)   # source files the user removed from a folder scan
    case_sensitive: bool = False
    fuzzy_threshold: float = 0.0        # 0.0 = exact / plural only; > 0 = Levenshtein similarity ratio
    use_regex: bool = False
    pre_pad: float = 1.0
    post_pad: float = 1.0
    snap_to_silence: bool = True
    snap_silence_window: float = 0.8     # seconds to search for acoustic pause
    sentence_mode: bool = False         # expand clip to full enclosing sentence
    merge_gap: float = 0.5              # merge clips closer than this (seconds)
    min_confidence: float = 0.0
    auto_keywords: bool = False
    auto_keywords_top_n: int = 15
    high_energy: bool = False           # also detect rapid / excited speech moments
    high_energy_wps: float = 3.5
    max_clip_duration: float = 0.0      # 0 = unlimited

    # Model & backend
    model: str = "crisper-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str = "auto"              # "auto" or ISO code, faster-whisper only

    # Conformance & Encoding
    aspect_ratio: AspectRatioMode = AspectRatioMode.SOURCE
    target_width: Optional[int] = None
    target_height: Optional[int] = None
    target_fps: Optional[int] = None
    encoder: EncoderChoice = EncoderChoice.AUTO
    quality: QualityPreset = QualityPreset.BALANCED
    loudness_normalize: bool = True     # EBU R128 loudnorm
    transition: TransitionMode = TransitionMode.CUT
    transition_duration: float = 0.3    # seconds for crossfade/dissolve
    fast_copy: bool = False             # lossless keyframe cuts when possible
    fast_seek: bool = True              # per-clip input seeking (decodes only clip ranges)

    # Subtitles / Overlays
    burn_subtitles: bool = False
    subtitle_style: str = "karaoke_amber"
    subtitle_mode: str = "karaoke"      # "karaoke" (word-level) or "keyword" (badge only)
    export_srt: bool = True
    export_vtt: bool = False
    export_ass: bool = False
    export_transcript: bool = False     # full transcript .txt / .srt next to outputs
    generate_thumbnails: bool = False
    contact_sheet: bool = False

    # NLE Export
    export_fcpxml: bool = False
    export_edl: bool = False

    # Output selection
    save_individual_clips: bool = False
    save_per_video_stitched: bool = True
    save_batch_stitched: bool = True
    defer_stitch: bool = False
    output_dir: str = "output"
    output_name: str = ""               # optional master output base name
    open_folder_when_done: bool = True

    def normalized(self) -> "JobConfig":
        """Coerce enum-like string fields into enums and clamp numeric ranges."""
        self.aspect_ratio = _enum_from_value(AspectRatioMode, self.aspect_ratio, AspectRatioMode.SOURCE)
        self.transition = _enum_from_value(TransitionMode, self.transition, TransitionMode.CUT)
        self.encoder = _enum_from_value(EncoderChoice, self.encoder, EncoderChoice.AUTO)
        self.quality = _enum_from_value(QualityPreset, self.quality, QualityPreset.BALANCED)
        self.pre_pad = max(0.0, float(self.pre_pad or 0.0))
        self.post_pad = max(0.0, float(self.post_pad or 0.0))
        self.fuzzy_threshold = max(0.0, min(1.0, float(self.fuzzy_threshold or 0.0)))
        self.transition_duration = max(0.05, min(3.0, float(self.transition_duration or 0.3)))
        self.keywords = [k.strip() for k in (self.keywords or []) if str(k).strip()]
        self.exclude_keywords = [k.strip() for k in (self.exclude_keywords or []) if str(k).strip()]
        if self.subtitle_style not in SUBTITLE_STYLES:
            self.subtitle_style = "karaoke_amber"
        return self

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            d[f.name] = v.value if isinstance(v, Enum) else v
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobConfig":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**kwargs).normalized()


@dataclass
class VideoJobStats:
    path: str
    info: Optional[MediaInfo] = None
    intervals: List[ClipInterval] = field(default_factory=list)
    transcript: Optional[Transcript] = None
    total_hits: int = 0
    clip_duration: float = 0.0
    transcribe_duration: float = 0.0
    encode_duration: float = 0.0
    clips_saved_count: int = 0
    stitched_output_path: str = ""
    clip_output_paths: List[str] = field(default_factory=list)
    artifact_paths: List[str] = field(default_factory=list)
    failed: bool = False
    error_message: str = ""
    transcript_cached: bool = False

    @property
    def selected_intervals(self) -> List[ClipInterval]:
        return [c for c in self.intervals if c.selected]
