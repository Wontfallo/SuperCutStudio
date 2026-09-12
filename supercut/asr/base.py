"""Base interface for transcription backends."""

from __future__ import annotations

from typing import Callable, Optional, Protocol

from ..models import Transcript


class BaseTranscriber(Protocol):
    def transcribe(
        self,
        audio_path: str,
        media_path: str,
        fallback_duration: float = 0.0,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Transcript:
        ...
