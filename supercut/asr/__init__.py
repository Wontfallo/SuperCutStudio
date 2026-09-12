"""ASR backends and caching package."""

from __future__ import annotations

import importlib.util
from typing import Callable, Dict, List, Optional

from ..models import Transcript
from .base import BaseTranscriber
from .cache import TranscriptCache
from .crisper_turbo import CRISPER_MODEL_NAME, CrisperWhisperBackend, crisper_available, find_crisper_python
from .faster_whisper import KNOWN_MODELS, FasterWhisperBackend, resolve_model_name

FALLBACK_WHISPER_MODEL = "large-v3-turbo"


def faster_whisper_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def cuda_available() -> bool:
    """Best-effort CUDA detection without importing torch."""
    try:
        import ctranslate2  # type: ignore

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


def describe_models() -> List[Dict[str, object]]:
    """Catalogue of selectable ASR models with availability flags for the UI."""
    crisper_ok = crisper_available()
    fw_ok = faster_whisper_available()
    models: List[Dict[str, object]] = [
        {
            "id": CRISPER_MODEL_NAME,
            "label": "CrisperWhisper Turbo (0.8B)",
            "detail": "Verbatim, precise word timestamps. Best for keyword cutting.",
            "available": crisper_ok,
            "backend": "crisperwhisper",
            "recommended": True,
        }
    ]
    labels = {
        "large-v3-turbo": ("Whisper large-v3 turbo", "Fast and accurate. Great default when CrisperWhisper is unavailable."),
        "large-v3": ("Whisper large-v3", "Most accurate multilingual Whisper. Slower."),
        "large-v2": ("Whisper large-v2", "Previous generation large model."),
        "distil-large-v3": ("Distil-Whisper large-v3", "English-only distilled model, very fast."),
        "medium": ("Whisper medium", "Balanced speed and accuracy."),
        "small": ("Whisper small", "Fast, good for clean speech."),
        "base": ("Whisper base", "Very fast, lower accuracy."),
        "tiny": ("Whisper tiny", "Fastest, lowest accuracy."),
    }
    for mid in KNOWN_MODELS:
        label, detail = labels.get(mid, (mid, ""))
        models.append({
            "id": mid,
            "label": label,
            "detail": detail,
            "available": fw_ok,
            "backend": "faster-whisper",
            "recommended": mid == FALLBACK_WHISPER_MODEL,
        })
    return models


def create_transcriber(
    model_name: str = "crisper-turbo",
    device: str = "cuda",
    compute_type: str = "float16",
    language: str = "auto",
    log: Optional[Callable[[str], None]] = None,
    allow_fallback: bool = True,
) -> BaseTranscriber:
    norm = (model_name or "").lower().strip()
    if norm == CRISPER_MODEL_NAME:
        try:
            return CrisperWhisperBackend(model_name=norm, device=device, language=language)
        except RuntimeError as exc:
            if not allow_fallback or not faster_whisper_available():
                raise
            msg = f"CrisperWhisper unavailable ({exc}). Falling back to faster-whisper '{FALLBACK_WHISPER_MODEL}'."
            print(f"WARNING: {msg}")
            if log:
                log(msg)
            norm = FALLBACK_WHISPER_MODEL
    return FasterWhisperBackend(model_name=norm, device=device, compute_type=compute_type, language=language)


def transcribe_cached(
    transcriber: BaseTranscriber,
    audio_path: str,
    media_path: str,
    fallback_duration: float = 0.0,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cache: Optional[TranscriptCache] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    force: bool = False,
) -> Transcript:
    """Transcribe audio with zero-delay disk caching."""
    if cache is None:
        cache = TranscriptCache()

    model_name = getattr(transcriber, "model_name", "unknown")
    if not force:
        cached = cache.get(media_path, model_name)
        if cached is not None:
            if progress_callback:
                progress_callback(1.0, "Loaded cached transcript (0.0s)")
            print(f"[OK] Using cached transcript for {media_path}")
            return cached

    try:
        transcript = transcriber.transcribe(
            audio_path=audio_path,
            media_path=media_path,
            fallback_duration=fallback_duration,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except TypeError:
        # Older backend signature without cancel_check
        transcript = transcriber.transcribe(
            audio_path=audio_path,
            media_path=media_path,
            fallback_duration=fallback_duration,
            progress_callback=progress_callback,
        )
    cache.put(transcript)
    return transcript


__all__ = [
    "BaseTranscriber",
    "CRISPER_MODEL_NAME",
    "CrisperWhisperBackend",
    "FALLBACK_WHISPER_MODEL",
    "FasterWhisperBackend",
    "TranscriptCache",
    "create_transcriber",
    "crisper_available",
    "cuda_available",
    "describe_models",
    "faster_whisper_available",
    "find_crisper_python",
    "resolve_model_name",
    "transcribe_cached",
]
