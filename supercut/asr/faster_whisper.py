"""faster-whisper GPU / CPU transcription backend."""

from __future__ import annotations

import time
from typing import Callable, Optional

from ..models import Segment, Transcript, Word

MODEL_ALIASES = {
    "turbo": "large-v3-turbo",
    "large": "large-v3",
    "whisper-large-v3": "large-v3",
    "whisper-large-v3-turbo": "large-v3-turbo",
}

KNOWN_MODELS = ["large-v3-turbo", "large-v3", "large-v2", "distil-large-v3", "medium", "small", "base", "tiny"]


def resolve_model_name(name: str) -> str:
    n = (name or "").strip().lower()
    return MODEL_ALIASES.get(n, n or "large-v3-turbo")


class FasterWhisperBackend:
    def __init__(self, model_name: str = "large-v3", device: str = "cuda", compute_type: str = "float16",
                 language: str = "auto"):
        self.model_name = resolve_model_name(model_name)
        self.device = device
        self.compute_type = compute_type
        self.language = language or "auto"

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed. Please run setup.bat.") from exc

        print(f"Loading faster-whisper '{self.model_name}' on {device} ({compute_type})...")
        try:
            self.model = WhisperModel(self.model_name, device=device, compute_type=compute_type)
        except Exception as exc:
            if device == "cpu":
                raise
            print(f"WARNING: GPU load failed ({exc}). Falling back to CPU int8...")
            self.device = "cpu"
            self.compute_type = "int8"
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")

    def cancel(self) -> None:  # cooperative; checked between segments
        pass

    def transcribe(
        self,
        audio_path: str,
        media_path: str,
        fallback_duration: float = 0.0,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Transcript:
        kwargs = dict(
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        if self.language and self.language != "auto":
            kwargs["language"] = self.language

        segments_iter, info = self.model.transcribe(audio_path, **kwargs)

        duration = float(getattr(info, "duration", 0.0) or fallback_duration)
        language = str(getattr(info, "language", "en") or "en")

        result_segments = []
        for s in segments_iter:
            if cancel_check and cancel_check():
                raise RuntimeError("Transcription cancelled")
            if duration and progress_callback:
                frac = min(1.0, float(s.end) / duration)
                progress_callback(frac, f"Transcribing {float(s.end):.0f}s / {duration:.0f}s ({self.model_name})")

            words = []
            for w in s.words or []:
                clean_t = w.word.strip()
                if clean_t and w.start is not None and w.end is not None:
                    words.append(
                        Word(
                            text=clean_t,
                            start=float(w.start),
                            end=float(w.end),
                            confidence=float(getattr(w, "probability", 1.0) or 1.0),
                        )
                    )

            result_segments.append(
                Segment(
                    start=float(s.start),
                    end=float(s.end),
                    text=s.text.strip(),
                    words=words,
                )
            )

        if progress_callback:
            progress_callback(1.0, f"Transcribed {duration:.0f}s")

        return Transcript(
            media_path=media_path,
            duration=duration,
            language=language,
            segments=result_segments,
            model_name=self.model_name,
            created_at=time.time(),
        )
