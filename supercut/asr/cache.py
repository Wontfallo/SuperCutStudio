"""Persistent disk cache for transcription results to avoid re-transcribing."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from ..models import Transcript
from ..paths import legacy_transcript_cache_dir, transcript_cache_dir


def get_cache_key(media_path: str, model_name: str) -> str:
    """Generate a unique deterministic cache key based on file stats and model."""
    path = Path(media_path)
    if not path.exists():
        return hashlib.sha256(f"{media_path}:{model_name}".encode("utf-8")).hexdigest()

    stat = path.stat()
    raw = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime}:{model_name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TranscriptCache:
    """Manages cached transcripts on disk (new per-user location + legacy fallback)."""

    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = transcript_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fallback_dirs: List[Path] = []
        if not cache_dir:
            legacy = legacy_transcript_cache_dir()
            if legacy.is_dir() and legacy != self.cache_dir:
                self._fallback_dirs.append(legacy)

    def _get_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _find_file(self, key: str) -> Optional[Path]:
        primary = self._get_path(key)
        if primary.exists():
            return primary
        for d in self._fallback_dirs:
            cand = d / f"{key}.json"
            if cand.exists():
                return cand
        return None

    def has(self, media_path: str, model_name: str) -> bool:
        return self._find_file(get_cache_key(media_path, model_name)) is not None

    def get(self, media_path: str, model_name: str) -> Optional[Transcript]:
        key = get_cache_key(media_path, model_name)
        cache_file = self._find_file(key)
        if not cache_file:
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            transcript = Transcript.from_dict(data)
            if not transcript.segments and transcript.duration <= 0:
                return None
            return transcript
        except Exception:
            return None

    def put(self, transcript: Transcript) -> None:
        key = get_cache_key(transcript.media_path, transcript.model_name)
        cache_file = self._get_path(key)
        tmp_file = cache_file.with_suffix(".json.tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(transcript.to_dict(), f, indent=2)
            os.replace(tmp_file, cache_file)
        except Exception:
            try:
                if tmp_file.exists():
                    tmp_file.unlink()
            except OSError:
                pass

    def invalidate(self, media_path: str, model_name: str) -> bool:
        key = get_cache_key(media_path, model_name)
        removed = False
        for d in [self.cache_dir] + self._fallback_dirs:
            cand = d / f"{key}.json"
            if cand.exists():
                try:
                    cand.unlink()
                    removed = True
                except OSError:
                    pass
        return removed

    def stats(self) -> Dict[str, object]:
        count = 0
        size = 0
        for d in [self.cache_dir] + self._fallback_dirs:
            for item in d.glob("*.json"):
                count += 1
                try:
                    size += item.stat().st_size
                except OSError:
                    pass
        return {"count": count, "size_bytes": size, "dir": str(self.cache_dir)}

    def clear(self) -> int:
        count = 0
        for d in [self.cache_dir] + self._fallback_dirs:
            for item in d.glob("*.json"):
                try:
                    item.unlink()
                    count += 1
                except OSError:
                    pass
        return count
