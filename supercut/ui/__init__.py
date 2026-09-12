"""SuperCut Studio Pro CustomTkinter UI suite."""

from .chip_view import KeywordChipView
from .clip_reviewer import ClipReviewerWidget
from .file_scanner import MediaScannerWidget
from .performance_console import PerformanceConsoleWidget
from .theme import (
    COLOR_BG_CARD,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BG_ROOT,
    COLOR_BORDER,
    COLOR_CYAN,
    COLOR_GREEN,
    COLOR_PURPLE,
    COLOR_RED,
    COLOR_TEXT_CYAN,
    COLOR_TEXT_DIM,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_CODE,
    FONT_CODE_BOLD,
    FONT_SECTION,
    FONT_SMALL,
    FONT_SUBTITLE,
    FONT_TITLE,
)
from .transcript_inspector import TranscriptInspectorWidget

__all__ = [
    "KeywordChipView",
    "MediaScannerWidget",
    "ClipReviewerWidget",
    "TranscriptInspectorWidget",
    "PerformanceConsoleWidget",
]
