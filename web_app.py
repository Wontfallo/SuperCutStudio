"""SuperCut Studio - browser/desktop studio server for the SuperCut pipeline.

Built with FastAPI + Uvicorn. Serves the single-page studio UI from ``static/``
(inlined into one HTML document so it works fully offline) and exposes a JSON
API with Server-Sent Events telemetry:

  Setup      : /api/system /api/config /api/scan /api/upload /api/pick /api/open
  Jobs       : /api/analyze /api/render /api/pipeline/start /api/stitch-folder-only
               /api/discover /api/cancel /api/status /api/events /api/jobs /api/logs
  Reviewer   : /api/clips (+ update/add/delete/batch_toggle) /api/waveform /api/media
  Transcript : /api/transcript /api/transcript/export
  Delivery   : /api/result /api/download /api/cache/clear
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Deque, Dict, List, Optional, Set

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from supercut import __version__
from supercut.audio import compute_waveform
from supercut.models import (
    AspectRatioMode,
    ClipInterval,
    HitSpan,
    JobConfig,
    MediaInfo,
    SUBTITLE_STYLES,
    Transcript,
    VideoJobStats,
)
from supercut.paths import (
    APP_NAME,
    default_output_dir,
    is_frozen,
    settings_file,
    static_dir,
    uploads_dir,
)
from supercut.pipeline import JobResult, SuperCutPipeline, VIDEO_EXTENSIONS
from supercut.server.dialogs import pick_path, reveal_in_file_manager
from supercut.server.logging_setup import get_logger, setup_logging
from supercut.server.sysinfo import system_report
from supercut.video import export_transcript_srt, export_transcript_txt, probe_media, stitch_folder_direct
from supercut.video.subtitles import transcript_to_srt_text

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = settings_file()
UPLOADS_DIR = uploads_dir()
OUTPUT_DIR = default_output_dir()
STATIC_DIR = static_dir()

os.makedirs(UPLOADS_DIR, exist_ok=True)

log = get_logger("supercut.web")

MEDIA_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts", ".wmv", ".flv", ".mp3", ".wav", ".m4a"}
DOWNLOAD_SUFFIXES = MEDIA_SUFFIXES | {".srt", ".vtt", ".ass", ".json", ".csv", ".xml", ".edl", ".txt", ".jpg", ".png"}


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class ConfigModel(BaseModel):
    input_path: str = ""
    input_mode: str = "Folder"
    keywords: List[str] = Field(default_factory=list)
    exclude_keywords: List[str] = Field(default_factory=list)
    output_dir: str = ""
    output_name: str = ""
    model: str = "crisper-turbo"
    language: str = "auto"
    aspect_ratio: str = "source"
    transition: str = "cut"
    transition_duration: float = 0.3
    encoder: str = "auto"
    quality: str = "balanced"
    loudness_normalize: bool = True
    burn_subtitles: bool = False
    subtitle_style: str = "karaoke_amber"
    subtitle_mode: str = "karaoke"
    pre_pad: float = 1.0
    post_pad: float = 1.0
    snap_to_silence: bool = True
    sentence_mode: bool = False
    merge_gap: float = 0.5
    max_clip_duration: float = 0.0
    case_sensitive: bool = False
    use_regex: bool = False
    fuzzy_threshold: float = 0.0
    min_confidence: float = 0.0
    auto_keywords: bool = False
    auto_keywords_top_n: int = 15
    high_energy: bool = False
    save_individual_clips: bool = False
    save_per_video_stitched: bool = True
    save_batch_stitched: bool = True
    export_srt: bool = True
    export_vtt: bool = False
    export_ass: bool = False
    export_transcript: bool = False
    export_fcpxml: bool = False
    export_edl: bool = False
    generate_thumbnails: bool = False
    contact_sheet: bool = False
    fast_seek: bool = True
    open_folder: bool = False


class ScanRequest(BaseModel):
    input_path: str


class AnalyzeRequest(BaseModel):
    input_path: Optional[str] = None
    config: Optional[ConfigModel] = None


class DiscoverRequest(BaseModel):
    input_path: Optional[str] = None
    top_n: int = 15
    config: Optional[ConfigModel] = None


class ClipToggleRequest(BaseModel):
    video_path: str
    clip_index: int
    selected: Optional[bool] = None
    custom_title: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None


class ClipAddRequest(BaseModel):
    video_path: str
    start: float
    end: float
    title: Optional[str] = None


class ClipDeleteRequest(BaseModel):
    video_path: str
    clip_index: int


class RenderRequest(BaseModel):
    config: Optional[ConfigModel] = None


class PickRequest(BaseModel):
    kind: str = "file"
    title: str = ""
    initial: str = ""


class OpenRequest(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_path(raw: str) -> str:
    """Strip quotes / trailing punctuation users paste along with paths."""
    s = (raw or "").strip()
    s = s.strip('"').strip("'").strip("`")
    s = s.rstrip(";,").strip()
    s = s.strip('"').strip("'")
    if s.startswith("file:///"):
        s = s[8:].replace("/", os.sep)
    return s


def resolve_output_dir(raw: Optional[str]) -> str:
    """Absolute output folder; relative paths resolve against a writable base, never Program Files."""
    out = clean_path(raw or "")
    if not out:
        return str(OUTPUT_DIR)
    if os.path.isabs(out):
        return out
    base = OUTPUT_DIR.parent if is_frozen() else APP_DIR
    return str(base / out)


def scan_video_files(target: str) -> List[str]:
    p = Path(target)
    if p.is_file():
        return [str(p)]
    found: List[str] = []
    for ext in VIDEO_EXTENSIONS:
        found.extend(glob.glob(os.path.join(str(p), ext)))
        found.extend(glob.glob(os.path.join(str(p), ext.upper())))
    found = sorted(set(found), key=lambda s: s.lower())
    return [f for f in found if not Path(f).name.startswith("FINAL_BATCH_")]


def media_summary(fpath: str, info: Optional[MediaInfo]) -> Dict[str, Any]:
    size = 0
    try:
        size = os.path.getsize(fpath)
    except OSError:
        pass
    return {
        "path": fpath,
        "filename": Path(fpath).name,
        "resolution": info.resolution_str if info else "Unknown",
        "width": info.width if info else 0,
        "height": info.height if info else 0,
        "duration": round(info.duration, 2) if info else 0.0,
        "fps": round(info.fps, 2) if info else 0.0,
        "has_audio": info.has_audio if info else False,
        "video_codec": info.video_codec if info else "",
        "audio_codec": info.audio_codec if info else "",
        "size_bytes": size,
        "readable": info is not None,
    }


# ---------------------------------------------------------------------------
# Server State Manager
# ---------------------------------------------------------------------------

class StudioState:
    def __init__(self):
        self.lock = threading.RLock()
        self.current_pipeline: Optional[SuperCutPipeline] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.status: str = "idle"  # idle, analyzing, rendering, stitching, discovering, cancelled, error
        self.stage: str = "Idle"
        self.progress_pct: float = 0.0
        self.progress_msg: str = "Ready"
        self.job_started_at: float = 0.0
        self.active_video_name: str = ""
        self.analyzed_stats: List[VideoJobStats] = []
        self.job_result: Optional[JobResult] = None
        self.error_message: Optional[str] = None
        self.folder_stitch_cancelled = False
        self.job_history: Deque[Dict[str, Any]] = deque(maxlen=25)
        self.log_buffer: Deque[Dict[str, Any]] = deque(maxlen=600)
        self.known_media: Set[str] = set()
        self.last_discovered: List[Dict[str, Any]] = []
        self.shutdown_token = os.environ.get("VKS_SHUTDOWN_TOKEN", "")
        self.desktop_mode = bool(os.environ.get("VKS_DESKTOP", ""))

        # SSE Subscribers
        self.subscribers: Set[asyncio.Queue] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        # Load configuration
        self.config_data = self._load_config()

    # ---- config ------------------------------------------------------
    def _load_config(self) -> Dict[str, Any]:
        default_cfg = ConfigModel().model_dump()
        default_cfg["output_dir"] = str(OUTPUT_DIR)
        saved: Dict[str, Any] = {}
        for candidate in (SETTINGS_FILE, APP_DIR / "settings.json"):
            if candidate.exists():
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        saved = json.load(f) or {}
                    break
                except Exception:
                    continue
        # Legacy keys from earlier releases
        if "out_clips" in saved and "save_individual_clips" not in saved:
            saved["save_individual_clips"] = bool(saved.get("out_clips"))
        if "out_stitched" in saved and "save_per_video_stitched" not in saved:
            saved["save_per_video_stitched"] = bool(saved.get("out_stitched"))
        if "out_batch" in saved and "save_batch_stitched" not in saved:
            saved["save_batch_stitched"] = bool(saved.get("out_batch"))
        if "pad" in saved and "pre_pad" not in saved:
            saved["pre_pad"] = saved["post_pad"] = float(saved.get("pad") or 1.0)
        for k, v in saved.items():
            if k in default_cfg:
                default_cfg[k] = v
        if not default_cfg.get("output_dir"):
            default_cfg["output_dir"] = str(OUTPUT_DIR)
        return default_cfg

    def save_config(self, new_cfg: Dict[str, Any]) -> None:
        with self.lock:
            for k, v in new_cfg.items():
                if k in self.config_data:
                    self.config_data[k] = v
            if not self.config_data.get("output_dir"):
                self.config_data["output_dir"] = str(OUTPUT_DIR)
            try:
                SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = SETTINGS_FILE.with_suffix(".json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.config_data, f, indent=2)
                os.replace(tmp, SETTINGS_FILE)
            except Exception as e:
                log.warning("Error saving settings: %s", e)

    def to_job_config(self, overrides: Optional[Dict[str, Any]] = None) -> JobConfig:
        cfg = dict(self.config_data)
        if overrides:
            cfg.update(overrides)
        cfg["open_folder_when_done"] = bool(cfg.get("open_folder", False))
        cfg["output_dir"] = resolve_output_dir(cfg.get("output_dir"))
        job = JobConfig.from_dict(cfg)
        return job

    # ---- events ------------------------------------------------------
    def broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        payload = json.dumps({"type": event_type, "timestamp": time.time(), **data}, default=str)
        msg = f"event: {event_type}\ndata: {payload}\n\n"
        loop = self.loop
        for q in list(self.subscribers):
            try:
                if loop and loop.is_running() and threading.current_thread() is not threading.main_thread():
                    loop.call_soon_threadsafe(q.put_nowait, msg)
                else:
                    q.put_nowait(msg)
            except Exception:
                self.subscribers.discard(q)

    def broadcast_log(self, level: str, text: str) -> None:
        entry = {"level": level, "text": text, "time": time.strftime("%H:%M:%S"), "ts": time.time()}
        self.log_buffer.append(entry)
        py_level = {"err": logging.ERROR, "warn": logging.WARNING}.get(level, logging.INFO)
        log.log(py_level, text)
        self.broadcast_event("log", entry)

    def snapshot(self) -> Dict[str, Any]:
        elapsed = (time.time() - self.job_started_at) if (self.job_started_at and self.status not in ("idle", "error", "cancelled")) else 0.0
        return {
            "status": self.status,
            "stage": self.stage,
            "pct": self.progress_pct,
            "msg": self.progress_msg,
            "elapsed": round(elapsed, 1),
            "analyzed_count": len(self.analyzed_stats),
            "clip_count": sum(len(s.intervals) for s in self.analyzed_stats),
            "has_result": self.job_result is not None,
            "error": self.error_message,
            "desktop": self.desktop_mode,
            "version": __version__,
        }

    def busy(self) -> bool:
        return self.status in ("analyzing", "rendering", "stitching", "discovering")

    def begin_job(self, status: str, stage: str, msg: str) -> None:
        self.status = status
        self.stage = stage
        self.progress_pct = 0.0
        self.progress_msg = msg
        self.error_message = None
        self.job_started_at = time.time()
        self.folder_stitch_cancelled = False

    def finish_job(self, stage: str, msg: str, status: str = "idle") -> None:
        self.status = status
        self.stage = stage
        self.progress_pct = 100.0 if status == "idle" else self.progress_pct
        self.progress_msg = msg
        elapsed = time.time() - self.job_started_at if self.job_started_at else 0.0
        self.broadcast_event("progress", {
            "pct": self.progress_pct, "msg": msg, "stage": stage, "elapsed": round(elapsed, 1), "eta": None,
        })

    def fail_job(self, kind: str, err: str) -> None:
        with self.lock:
            self.status = "error"
            self.stage = "Failed"
            self.error_message = err
            self.progress_msg = f"{kind} error: {err}"
        self.broadcast_log("err", f"ERROR: {kind} failed: {err}")
        self.broadcast_event("error", {"error": err, "kind": kind})

    def record_history(self, kind: str, result: Optional[JobResult], elapsed: float, extra: Optional[Dict[str, Any]] = None) -> None:
        entry: Dict[str, Any] = {
            "kind": kind,
            "finished_at": time.time(),
            "elapsed": round(elapsed, 1),
            "output": result.primary_output if result else None,
            "output_dir": result.output_dir if result else "",
            "clips": result.total_clips_count if result else 0,
            "hits": result.total_hits if result else 0,
            "cancelled": bool(result.cancelled) if result else False,
        }
        if extra:
            entry.update(extra)
        self.job_history.appendleft(entry)

    def is_media_allowed(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except Exception:
            return False
        rs = str(resolved).lower()
        if rs in {k.lower() for k in self.known_media}:
            return True
        roots = [UPLOADS_DIR, OUTPUT_DIR]
        roots.append(Path(resolve_output_dir(self.config_data.get("output_dir"))))
        if self.job_result and self.job_result.output_dir:
            roots.append(Path(self.job_result.output_dir))
        for s in self.analyzed_stats:
            roots.append(Path(s.path).parent)
        inp = clean_path(self.config_data.get("input_path") or "")
        if inp:
            ip = Path(inp)
            roots.append(ip if ip.is_dir() else ip.parent)
        for r in roots:
            try:
                rr = str(Path(r).resolve()).lower().rstrip("\\/") + os.sep
            except Exception:
                continue
            if rs.startswith(rr):
                return True
        return False


state = StudioState()

# ---------------------------------------------------------------------------
# FastAPI App Initialization
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    state.loop = asyncio.get_running_loop()
    yield


app = FastAPI(
    title=f"{APP_NAME} Studio",
    description="Browser-based multimodal video keyword stitcher cockpit",
    version=__version__,
    lifespan=_lifespan,
)


@app.middleware("http")
async def _no_cache_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# ---------------------------------------------------------------------------
# Media Streaming with HTTP 206 Partial Content (Range Support)
# ---------------------------------------------------------------------------

def stream_file_with_range(path: Path, request: Request) -> Response:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path.name}")

    file_size = path.stat().st_size
    content_type, _ = mimetypes.guess_type(str(path))
    if not content_type:
        content_type = "video/mp4"
    if path.suffix.lower() == ".mkv":
        content_type = "video/x-matroska"

    range_header = request.headers.get("range")
    if range_header:
        match = re.search(r"bytes=(\d*)-(\d*)", range_header)
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
            else:  # suffix range: bytes=-500
                length = int(match.group(2))
                start = max(0, file_size - length)
                end = file_size - 1
            if start >= file_size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
            start = max(0, min(start, file_size - 1))
            end = max(start, min(end, file_size - 1))
            length = end - start + 1

            def iter_chunk():
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    chunk_size = 1024 * 512
                    while remaining > 0:
                        data = f.read(min(chunk_size, remaining))
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
                "Content-Type": content_type,
            }
            return StreamingResponse(iter_chunk(), status_code=206, headers=headers)

    def iter_full():
        with open(path, "rb") as f:
            chunk_size = 1024 * 512
            while chunk := f.read(chunk_size):
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": content_type,
    }
    return StreamingResponse(iter_full(), status_code=200, headers=headers)


# ---------------------------------------------------------------------------
# System / Config
# ---------------------------------------------------------------------------

_system_cache: Dict[str, Any] = {}


@app.get("/api/system")
def get_system(refresh: bool = False):
    """Capability report: FFmpeg, encoders, GPU, ASR backends, caches."""
    global _system_cache
    if refresh or not _system_cache:
        _system_cache = system_report()
    data = dict(_system_cache)
    data["state"] = state.snapshot()
    data["subtitle_styles"] = list(SUBTITLE_STYLES)
    data["desktop"] = state.desktop_mode
    return data


@app.get("/api/status")
def get_status():
    return state.snapshot()


@app.get("/api/config")
def get_config():
    """Get active studio configuration."""
    return state.config_data


@app.post("/api/config")
def update_config(cfg: ConfigModel):
    """Update and persist studio configuration."""
    data = cfg.model_dump()
    data["input_path"] = clean_path(data.get("input_path", ""))
    data["output_dir"] = clean_path(data.get("output_dir", "")) or str(OUTPUT_DIR)
    data["keywords"] = list(dict.fromkeys(k.strip() for k in data.get("keywords", []) if k and k.strip()))
    data["exclude_keywords"] = list(dict.fromkeys(k.strip() for k in data.get("exclude_keywords", []) if k and k.strip()))
    state.save_config(data)
    state.broadcast_event("config_updated", {"config": state.config_data})
    return {"status": "ok", "config": state.config_data}


# ---------------------------------------------------------------------------
# Media discovery
# ---------------------------------------------------------------------------

@app.post("/api/scan")
def scan_media(req: ScanRequest):
    """Scan a local directory or file path for valid video assets."""
    target = clean_path(req.input_path)
    if not target:
        raise HTTPException(status_code=400, detail="Input path cannot be empty.")

    p = Path(target)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Target path does not exist: {target}")

    found_files = scan_video_files(target)
    media_list = []
    total_duration = 0.0
    for fpath in found_files:
        info = probe_media(fpath)
        entry = media_summary(fpath, info)
        media_list.append(entry)
        total_duration += entry["duration"]
        state.known_media.add(str(Path(fpath).resolve()))

    return {
        "input_path": target,
        "is_file": p.is_file(),
        "count": len(media_list),
        "total_duration": round(total_duration, 2),
        "files": media_list,
    }


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a video file directly to the studio staging area."""
    safe_name = Path(file.filename or "uploaded_video.mp4").name
    safe_name = re.sub(r'[\\/*?:"<>|]+', "_", safe_name) or "uploaded_video.mp4"
    dest_path = UPLOADS_DIR / safe_name
    if dest_path.exists():
        stem, suffix = dest_path.stem, dest_path.suffix
        dest_path = UPLOADS_DIR / f"{stem}_{int(time.time())}{suffix}"

    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    info = probe_media(str(dest_path))
    state.known_media.add(str(dest_path.resolve()))
    return {"status": "ok", **media_summary(str(dest_path), info)}


@app.post("/api/pick")
def pick_native_path(req: PickRequest):
    """Open a native file/folder picker on the server machine (local desktop use)."""
    initial = clean_path(req.initial)
    if initial and os.path.isfile(initial):
        initial = os.path.dirname(initial)
    chosen = pick_path(req.kind, req.title, initial)
    if not chosen:
        return {"status": "cancelled", "path": None}
    return {"status": "ok", "path": chosen, "kind": req.kind}


@app.post("/api/open")
def open_in_explorer(req: OpenRequest):
    """Reveal a file or folder in the OS file manager."""
    target = clean_path(req.path)
    if not target or not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Path not found")
    ok = reveal_in_file_manager(target)
    return {"status": "ok" if ok else "failed"}


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def sse_events(request: Request):
    """Server-Sent Events stream for real-time progress and telemetry."""
    q: asyncio.Queue = asyncio.Queue()
    state.subscribers.add(q)
    try:
        state.loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    async def event_generator() -> AsyncGenerator[str, None]:
        init_data = json.dumps({"type": "init", **state.snapshot()})
        yield f"event: init\ndata: {init_data}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            state.subscribers.discard(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _progress_dispatcher(pct: float, msg: str, stage: str = "Processing"):
    state.progress_pct = round(pct, 1)
    state.progress_msg = msg
    state.stage = stage
    elapsed = time.time() - state.job_started_at if state.job_started_at else 0.0
    eta = None
    if 3.0 < pct < 100.0 and elapsed > 2.0:
        eta = round(elapsed * (100.0 - pct) / pct, 1)
    state.broadcast_event("progress", {
        "pct": state.progress_pct,
        "msg": msg,
        "stage": stage,
        "elapsed": round(elapsed, 1),
        "eta": eta,
    })


def _pipeline_log(level: str, text: str, stage: str = "") -> None:
    prefix = f"[{stage}] " if stage else ""
    state.broadcast_log(level, f"{prefix}{text}")


def _make_pipeline(job_config: JobConfig, stage: str) -> SuperCutPipeline:
    return SuperCutPipeline(
        config=job_config,
        progress_callback=lambda p, m: _progress_dispatcher(p, m, stage=stage),
        log_callback=lambda level, text: _pipeline_log(level, text, stage),
    )


def _resolve_input(req_path: Optional[str]) -> str:
    raw_path = req_path if req_path is not None else state.config_data.get("input_path", "")
    input_path = clean_path(raw_path)
    if not input_path:
        raise HTTPException(status_code=400, detail="No input path provided.")
    if not os.path.exists(input_path):
        raise HTTPException(status_code=404, detail=f"Input path does not exist: {input_path}")
    return input_path


def _start_worker(fn: Callable[[], None]) -> None:
    thread = threading.Thread(target=fn, daemon=True)
    state.worker_thread = thread
    thread.start()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.post("/api/analyze")
def start_analysis(req: AnalyzeRequest):
    """Trigger Phase 1: Audio extraction, transcription, and keyword detection."""
    with state.lock:
        if state.busy():
            raise HTTPException(status_code=409, detail="A pipeline job is already in progress.")
        if req.config:
            state.save_config(req.config.model_dump())
        input_path = _resolve_input(req.input_path)
        job_config = state.to_job_config()
        if not job_config.keywords and not job_config.auto_keywords and not job_config.high_energy:
            raise HTTPException(status_code=400, detail="Add at least one keyword (or enable auto-discovery).")

        pipeline = _make_pipeline(job_config, "Analysis")
        state.current_pipeline = pipeline
        state.job_result = None
        state.begin_job("analyzing", "Analysis", "Starting analysis...")

    state.broadcast_log("hdr", "=== ANALYSIS STARTED ===")
    state.broadcast_log("info", f"Input: {input_path}")
    state.broadcast_log("hit", f"Keywords: {', '.join(job_config.keywords) or '(auto-discover)'}")
    state.broadcast_event("job_started", {"kind": "analyze", "input_path": input_path})

    def worker():
        t0 = time.time()
        try:
            stats = pipeline.analyze(input_path)
            cancelled = pipeline.cancelled
            with state.lock:
                state.analyzed_stats = stats
                for s in stats:
                    state.known_media.add(str(Path(s.path).resolve()))
                if cancelled:
                    state.finish_job("Cancelled", "Analysis cancelled.", status="cancelled")
                else:
                    state.finish_job("Analysis Complete", f"Analysis finished. Found clips across {len(stats)} media file(s).")

            total_hits = sum(s.total_hits for s in stats)
            total_clips = sum(len(s.intervals) for s in stats)
            state.record_history("analyze", None, time.time() - t0, {"clips": total_clips, "hits": total_hits, "cancelled": cancelled})
            if cancelled:
                state.broadcast_event("cancelled", {"msg": "Analysis cancelled."})
            else:
                state.broadcast_log("ok", f"Analysis complete: {total_hits} hit(s), {total_clips} clip(s) across {len(stats)} file(s)")
                state.broadcast_event("analyzed", {
                    "stats_count": len(stats),
                    "total_hits": total_hits,
                    "total_clips": total_clips,
                    "failed": [Path(s.path).name for s in stats if s.failed],
                })
        except Exception as e:
            log.exception("Analysis failed")
            state.fail_job("Analysis", str(e))

    _start_worker(worker)
    return {"status": "started", "stage": "analyzing"}


@app.post("/api/render")
def start_rendering(req: RenderRequest):
    """Trigger Phase 2: Render selected clips, per-video highlights, and master batch stitch."""
    with state.lock:
        if state.busy():
            raise HTTPException(status_code=409, detail="A pipeline job is already in progress.")
        if not state.analyzed_stats:
            raise HTTPException(status_code=400, detail="No analyzed video stats found. Run analysis first.")
        selected_count = sum(sum(1 for c in s.intervals if c.selected) for s in state.analyzed_stats)
        if selected_count == 0:
            raise HTTPException(status_code=400, detail="No clips are selected for rendering.")
        if req.config:
            state.save_config(req.config.model_dump())

        job_config = state.to_job_config()
        pipeline = _make_pipeline(job_config, "Rendering")
        state.current_pipeline = pipeline
        state.begin_job("rendering", "Rendering", "Starting rendering engine...")

    state.broadcast_log("hdr", f"=== RENDER STARTED ({selected_count} clips) ===")
    state.broadcast_event("job_started", {"kind": "render", "clips": selected_count})

    def worker():
        t0 = time.time()
        try:
            result = pipeline.render_clips_and_stitch(state.analyzed_stats)
            with state.lock:
                state.job_result = result
                for a in result.artifacts:
                    state.known_media.add(str(Path(a.path).resolve()))
                if result.cancelled:
                    state.finish_job("Cancelled", "Render cancelled.", status="cancelled")
                else:
                    state.finish_job("Render Complete", "Rendering complete! SuperCut ready.")
            state.record_history("render", result, time.time() - t0)
            if result.cancelled:
                state.broadcast_event("cancelled", {"msg": "Render cancelled."})
            else:
                state.broadcast_log("ok", f"Render finished in {result.total_duration_sec:.1f}s")
                state.broadcast_event("rendered", _result_payload(result))
        except Exception as e:
            log.exception("Render failed")
            state.fail_job("Render", str(e))

    _start_worker(worker)
    return {"status": "started", "stage": "rendering"}


@app.post("/api/pipeline/start")
def start_full_pipeline(req: AnalyzeRequest):
    """1-Click SuperCut: analyze + render + stitch in one go."""
    with state.lock:
        if state.busy():
            raise HTTPException(status_code=409, detail="A pipeline job is already in progress.")
        if req.config:
            state.save_config(req.config.model_dump())
        input_path = _resolve_input(req.input_path)
        job_config = state.to_job_config()
        if not job_config.keywords and not job_config.auto_keywords and not job_config.high_energy:
            raise HTTPException(status_code=400, detail="No target keywords provided for SuperCut.")

        pipeline = _make_pipeline(job_config, "SuperCut")
        state.current_pipeline = pipeline
        state.job_result = None
        state.begin_job("rendering", "SuperCut Starting", "Initializing 1-Click SuperCut Pipeline...")

    state.broadcast_log("hdr", "=== STARTING 1-CLICK SUPERCUT PIPELINE ===")
    state.broadcast_log("info", f"Input Media: {input_path}")
    state.broadcast_log("hit", f"Target Keywords: {', '.join(job_config.keywords) or '(auto-discover)'}")
    state.broadcast_log("info", f"Model: {job_config.model} | Aspect: {job_config.aspect_ratio.value} | "
                                f"Transition: {job_config.transition.value} | Encoder: {job_config.encoder.value}")
    state.broadcast_event("job_started", {"kind": "supercut", "input_path": input_path, "keywords": job_config.keywords})

    def worker():
        t0 = time.time()
        try:
            result = pipeline.run(input_path)
            with state.lock:
                state.analyzed_stats = result.stats
                for s in result.stats:
                    state.known_media.add(str(Path(s.path).resolve()))
                for a in result.artifacts:
                    state.known_media.add(str(Path(a.path).resolve()))
                state.job_result = result if not result.cancelled else None
                if result.cancelled:
                    state.finish_job("Cancelled", "SuperCut cancelled.", status="cancelled")
                else:
                    state.finish_job("SuperCut Complete",
                                     f"SuperCut finished! {result.total_hits} keyword hits across {len(result.stats)} video(s).")
            state.record_history("supercut", result, time.time() - t0)
            if result.cancelled:
                state.broadcast_event("cancelled", {"msg": "SuperCut cancelled."})
                return
            state.broadcast_log("ok", f"SuperCut generated in {result.total_duration_sec:.1f}s")
            if result.batch_output_file:
                state.broadcast_log("ok", f"Master output: {Path(result.batch_output_file).name}")
            for sf in result.stitched_files:
                state.broadcast_log("ok", f"Highlight reel: {Path(sf).name}")
            if result.total_clips_count == 0:
                state.broadcast_log("warn", "No keyword hits were found, so nothing was rendered.")
            state.broadcast_event("analyzed", {
                "stats_count": len(result.stats),
                "total_hits": result.total_hits,
                "total_clips": sum(len(s.intervals) for s in result.stats),
                "silent": True,
            })
            state.broadcast_event("rendered", _result_payload(result))
        except Exception as e:
            log.exception("SuperCut failed")
            state.fail_job("SuperCut", str(e))

    _start_worker(worker)
    return {"status": "started", "stage": "processing"}


@app.post("/api/stitch-folder-only")
def start_stitch_folder_only(req: AnalyzeRequest):
    """Normalize and stitch all videos in the input folder into one video (no transcription)."""
    with state.lock:
        if state.busy():
            raise HTTPException(status_code=409, detail="A pipeline job is already in progress.")
        if req.config:
            state.save_config(req.config.model_dump())
        input_path = _resolve_input(req.input_path)
        if not Path(input_path).is_dir():
            raise HTTPException(status_code=400, detail=f"Target path is not an existing folder: {input_path}")

        job_config = state.to_job_config()
        out_dir = job_config.output_dir
        state.current_pipeline = None
        state.job_result = None
        state.begin_job("stitching", "Stitching Folder", "Starting direct folder normalization & stitch...")

    state.broadcast_log("hdr", "=== STARTING DIRECT FOLDER STITCH (NO TRANSCRIPTION) ===")
    state.broadcast_log("info", f"Input Folder: {input_path}")
    state.broadcast_log("info", f"Output Folder: {out_dir}")
    state.broadcast_event("job_started", {"kind": "folder_stitch", "input_path": input_path})

    def worker():
        t0 = time.perf_counter()
        try:
            out_name = (job_config.output_name.strip() + ".mp4") if job_config.output_name.strip() else "FINAL_BATCH_SUPERCUT.mp4"
            final_file = stitch_folder_direct(
                folder_path=input_path,
                output_dir=out_dir,
                config=job_config,
                progress_callback=lambda p, m: _progress_dispatcher(p, m, stage="Folder Stitch"),
                cancel_check=lambda: state.folder_stitch_cancelled,
                output_name=out_name,
            )
            elapsed = time.perf_counter() - t0
            if state.folder_stitch_cancelled:
                with state.lock:
                    state.finish_job("Cancelled", "Folder stitch cancelled.", status="cancelled")
                state.broadcast_event("cancelled", {"msg": "Folder stitch cancelled."})
                return
            if not final_file:
                raise RuntimeError("Folder stitching produced no output video.")

            from supercut.pipeline import Artifact
            res = JobResult(
                batch_output_file=final_file,
                stitched_files=[final_file],
                total_duration_sec=elapsed,
                total_clips_count=1,
                artifacts=[Artifact(final_file, "master", "Stitched folder video")],
                output_dir=os.path.abspath(out_dir),
            )
            with state.lock:
                state.job_result = res
                state.known_media.add(str(Path(final_file).resolve()))
                state.finish_job("Stitch Complete", f"Folder stitch finished in {elapsed:.1f}s!")
            state.record_history("folder_stitch", res, elapsed)
            state.broadcast_log("ok", f"Final stitched video: {Path(final_file).name} ({elapsed:.1f}s)")
            state.broadcast_event("rendered", _result_payload(res))
            if job_config.open_folder_when_done:
                reveal_in_file_manager(os.path.abspath(out_dir))
        except Exception as e:
            log.exception("Folder stitch failed")
            state.fail_job("Folder stitch", str(e))

    _start_worker(worker)
    return {"status": "started", "stage": "stitching"}


@app.post("/api/discover")
def start_discovery(req: DiscoverRequest):
    """Transcribe the inputs and auto-discover the strongest keywords / phrases."""
    with state.lock:
        if state.busy():
            raise HTTPException(status_code=409, detail="A pipeline job is already in progress.")
        if req.config:
            state.save_config(req.config.model_dump())
        input_path = _resolve_input(req.input_path)
        job_config = state.to_job_config()
        pipeline = _make_pipeline(job_config, "Discovery")
        state.current_pipeline = pipeline
        state.begin_job("discovering", "Keyword Discovery", "Transcribing media to discover keywords...")
        top_n = max(3, min(50, int(req.top_n or 15)))

    state.broadcast_log("hdr", "=== AUTO KEYWORD DISCOVERY ===")
    state.broadcast_event("job_started", {"kind": "discover", "input_path": input_path})

    def worker():
        t0 = time.time()
        try:
            ranked, stats_list = pipeline.discover_keywords(input_path, top_n=top_n)
            keywords = [{"keyword": k, "score": round(s, 3)} for k, s in ranked]
            with state.lock:
                state.last_discovered = keywords
                for s in stats_list:
                    state.known_media.add(str(Path(s.path).resolve()))
                if not state.analyzed_stats:
                    state.analyzed_stats = stats_list  # transcripts available in the inspector
                if pipeline.cancelled:
                    state.finish_job("Cancelled", "Discovery cancelled.", status="cancelled")
                else:
                    state.finish_job("Discovery Complete", f"Discovered {len(keywords)} keyword(s).")
            state.record_history("discover", None, time.time() - t0, {"keywords": len(keywords)})
            if pipeline.cancelled:
                state.broadcast_event("cancelled", {"msg": "Discovery cancelled."})
            else:
                state.broadcast_log("hit", "Discovered: " + ", ".join(k["keyword"] for k in keywords[:12]))
                state.broadcast_event("discovered", {"keywords": keywords, "stats_count": len(stats_list)})
        except Exception as e:
            log.exception("Discovery failed")
            state.fail_job("Discovery", str(e))

    _start_worker(worker)
    return {"status": "started", "stage": "discovering"}


@app.post("/api/cancel")
def cancel_job():
    """Cancel any actively running analysis or render pipeline."""
    with state.lock:
        was_busy = state.busy()
        state.folder_stitch_cancelled = True
        if state.current_pipeline:
            state.current_pipeline.cancel()
        state.status = "cancelled"
        state.stage = "Cancelled"
        state.progress_msg = "Job cancelled by user."

    state.broadcast_log("warn", "Job cancelled by user.")
    state.broadcast_event("cancelled", {"msg": "Job cancelled."})
    return {"status": "cancelled", "was_busy": was_busy}


@app.get("/api/jobs")
def get_job_history():
    return {"jobs": list(state.job_history)}


@app.get("/api/logs")
def get_logs(limit: int = 200):
    items = list(state.log_buffer)[-max(1, min(600, limit)):]
    return {"count": len(items), "logs": items}


@app.get("/api/discovered")
def get_discovered():
    return {"keywords": state.last_discovered}


# ---------------------------------------------------------------------------
# Clips
# ---------------------------------------------------------------------------

def _clip_payload(stats: VideoJobStats, s_idx: int, clip: ClipInterval) -> Dict[str, Any]:
    d = clip.to_dict()
    d.update({
        "video_path": stats.path,
        "video_filename": Path(stats.path).name,
        "video_index": s_idx,
        "video_duration": round(stats.info.duration, 2) if stats.info else 0.0,
        "clip_index": clip.index,
        "start": round(clip.start, 3),
        "end": round(clip.end, 3),
        "duration": round(clip.duration, 3),
    })
    return d


@app.get("/api/clips")
def get_clips():
    """Retrieve all detected clip intervals from the most recent analysis."""
    clips_payload = []
    videos = []
    for s_idx, stats in enumerate(state.analyzed_stats):
        videos.append({
            "video_path": stats.path,
            "filename": Path(stats.path).name,
            "duration": round(stats.info.duration, 2) if stats.info else 0.0,
            "hits": stats.total_hits,
            "clips": len(stats.intervals),
            "failed": stats.failed,
            "error": stats.error_message,
            "cached": stats.transcript_cached,
        })
        for clip in stats.intervals:
            clips_payload.append(_clip_payload(stats, s_idx, clip))

    selected = [c for c in clips_payload if c["selected"]]
    return {
        "count": len(clips_payload),
        "selected_count": len(selected),
        "selected_footage": round(sum(c["duration"] for c in selected), 2),
        "videos": videos,
        "clips": clips_payload,
    }


def _find_stats(video_path: str) -> Optional[VideoJobStats]:
    for stats in state.analyzed_stats:
        if stats.path == video_path or os.path.normcase(os.path.abspath(stats.path)) == os.path.normcase(os.path.abspath(video_path)):
            return stats
    return None


@app.post("/api/clips/update")
def update_clip(req: ClipToggleRequest):
    """Toggle a clip's selection, rename it, or adjust its in/out points."""
    target_clip: Optional[ClipInterval] = None
    stats = _find_stats(req.video_path)
    with state.lock:
        if stats:
            for clip in stats.intervals:
                if clip.index == req.clip_index:
                    if req.selected is not None:
                        clip.selected = req.selected
                    if req.custom_title is not None:
                        clip.custom_title = req.custom_title.strip() or None
                    duration_limit = stats.info.duration if stats.info and stats.info.duration > 0 else None
                    if req.start is not None:
                        clip.start = round(max(0.0, float(req.start)), 3)
                    if req.end is not None:
                        clip.end = round(float(req.end), 3)
                    if duration_limit:
                        clip.end = min(clip.end, round(duration_limit, 3))
                        clip.start = min(clip.start, max(0.0, duration_limit - 0.1))
                    if clip.end - clip.start < 0.1:
                        clip.end = round(clip.start + 0.1, 3)
                    target_clip = clip
                    break
        if not target_clip:
            raise HTTPException(status_code=404, detail="Clip not found.")
        stats.clip_duration = sum(c.duration for c in stats.intervals)

    payload = _clip_payload(stats, state.analyzed_stats.index(stats), target_clip)
    state.broadcast_event("clip_updated", {"clip": payload})
    return {"status": "ok", "clip": payload}


@app.post("/api/clips/add")
def add_clip(req: ClipAddRequest):
    """Add a manual clip interval to a video."""
    stats = _find_stats(req.video_path)
    if not stats:
        raise HTTPException(status_code=404, detail="Video not found in the current analysis.")
    start = max(0.0, float(req.start))
    end = float(req.end)
    if stats.info and stats.info.duration > 0:
        end = min(end, stats.info.duration)
    if end - start < 0.1:
        raise HTTPException(status_code=400, detail="Clip must be at least 0.1s long.")
    with state.lock:
        title = (req.title or "").strip() or "manual"
        clip = ClipInterval(
            start=round(start, 3),
            end=round(end, 3),
            keyword_hits=[HitSpan(matched_text=title, keyword=title, start=start, end=end)],
            source_video=stats.path,
            index=(max((c.index for c in stats.intervals), default=0) + 1),
            selected=True,
            custom_title=(req.title or "").strip() or None,
            manual=True,
        )
        stats.intervals.append(clip)
        stats.intervals.sort(key=lambda c: c.start)
        stats.clip_duration = sum(c.duration for c in stats.intervals)
    payload = _clip_payload(stats, state.analyzed_stats.index(stats), clip)
    state.broadcast_event("clip_added", {"clip": payload})
    return {"status": "ok", "clip": payload}


@app.post("/api/clips/delete")
def delete_clip(req: ClipDeleteRequest):
    stats = _find_stats(req.video_path)
    if not stats:
        raise HTTPException(status_code=404, detail="Video not found.")
    with state.lock:
        before = len(stats.intervals)
        stats.intervals = [c for c in stats.intervals if c.index != req.clip_index]
        if len(stats.intervals) == before:
            raise HTTPException(status_code=404, detail="Clip not found.")
        stats.clip_duration = sum(c.duration for c in stats.intervals)
    state.broadcast_event("clip_deleted", {"video_path": stats.path, "clip_index": req.clip_index})
    return {"status": "ok", "remaining": len(stats.intervals)}


@app.post("/api/clips/batch_toggle")
def batch_toggle_clips(select_all: bool = True, keyword: Optional[str] = None, video_path: Optional[str] = None,
                       invert: bool = False):
    """Select / deselect / invert clips, optionally filtered by keyword or source video."""
    count = 0
    with state.lock:
        for stats in state.analyzed_stats:
            if video_path and stats.path != video_path:
                continue
            for clip in stats.intervals:
                if keyword and keyword.lower() not in [k.lower() for k in clip.all_keywords]:
                    continue
                clip.selected = (not clip.selected) if invert else select_all
                count += 1

    state.broadcast_event("clips_batch_toggled", {"selected_all": select_all, "count": count, "invert": invert})
    return {"status": "ok", "selected_all": select_all, "updated_count": count}


# ---------------------------------------------------------------------------
# Transcript & waveform
# ---------------------------------------------------------------------------

@app.get("/api/transcript")
def get_transcript(video_path: Optional[str] = None, words: bool = True):
    """Retrieve full transcript segments and words for a given video."""
    if not state.analyzed_stats:
        return {"count": 0, "transcripts": []}

    target_stats = state.analyzed_stats
    if video_path:
        target_stats = [s for s in state.analyzed_stats if s.path == video_path]

    results = []
    for s in target_stats:
        if not s.transcript:
            continue
        segments_data = []
        for seg in s.transcript.segments:
            item = {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text}
            if words:
                item["words"] = [
                    {"text": w.text, "start": round(w.start, 2), "end": round(w.end, 2), "confidence": round(w.confidence, 2)}
                    for w in seg.words
                ]
            segments_data.append(item)
        results.append({
            "video_path": s.path,
            "filename": Path(s.path).name,
            "duration": round(s.transcript.duration, 2),
            "language": s.transcript.language,
            "model_name": s.transcript.model_name,
            "word_count": len(s.transcript.all_words),
            "full_text": s.transcript.full_text,
            "segments": segments_data,
            "cached": s.transcript_cached,
        })

    return {"count": len(results), "transcripts": results}


@app.get("/api/transcript/export")
def export_transcript(video_path: str, fmt: str = "txt"):
    stats = _find_stats(video_path)
    if not stats or not stats.transcript:
        raise HTTPException(status_code=404, detail="Transcript not found.")
    stem = Path(stats.path).stem
    fmt = fmt.lower()
    if fmt == "json":
        return Response(content=stats.transcript.to_json(), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{stem}_transcript.json"'})
    if fmt == "srt":
        return PlainTextResponse(transcript_to_srt_text(stats.transcript),
                                 headers={"Content-Disposition": f'attachment; filename="{stem}_transcript.srt"'})
    lines = []
    for seg in stats.transcript.segments:
        if seg.text.strip():
            m, s = divmod(int(seg.start), 60)
            lines.append(f"[{m:02d}:{s:02d}] {seg.text.strip()}")
    return PlainTextResponse("\n".join(lines) + "\n",
                             headers={"Content-Disposition": f'attachment; filename="{stem}_transcript.txt"'})


_waveform_lock = threading.Lock()


@app.get("/api/waveform")
def get_waveform(path: str, buckets: int = 600):
    """Real audio peaks for the reviewer waveform."""
    clean = Path(clean_path(path))
    if not clean.exists() or not clean.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    if not state.is_media_allowed(clean):
        raise HTTPException(status_code=403, detail="Media path not part of the current session.")
    info = probe_media(str(clean))
    with _waveform_lock:
        data = compute_waveform(str(clean), buckets=buckets, duration_hint=info.duration if info else 0.0)
    if not data.get("peaks"):
        data["peaks"] = []
        data["silent"] = True
    return data


# ---------------------------------------------------------------------------
# Media & downloads
# ---------------------------------------------------------------------------

@app.get("/api/media")
def get_media_stream(path: str, request: Request):
    """Stream a local video file with Range support for HTML5 playback."""
    clean = Path(clean_path(path))
    if not clean.exists() or not clean.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if clean.suffix.lower() not in MEDIA_SUFFIXES:
        raise HTTPException(status_code=415, detail="Not a media file")
    if not state.is_media_allowed(clean):
        raise HTTPException(status_code=403, detail="Media path not part of the current session.")
    return stream_file_with_range(clean, request)


@app.get("/api/download")
def download_artifact(path: str):
    """Download a rendered file or report."""
    clean = Path(clean_path(path))
    if not clean.exists() or not clean.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if clean.suffix.lower() not in DOWNLOAD_SUFFIXES:
        raise HTTPException(status_code=415, detail="File type not downloadable")
    if not state.is_media_allowed(clean):
        raise HTTPException(status_code=403, detail="Path not part of the current session.")
    content_type, _ = mimetypes.guess_type(str(clean))
    return FileResponse(path=str(clean), filename=clean.name, media_type=content_type or "application/octet-stream")


_ARTIFACT_ORDER = {"master": 0, "stitched": 1, "subtitle": 2, "transcript": 3, "nle": 4, "contact_sheet": 5,
                   "clip": 6, "thumbnail": 7, "report": 8}


def _result_payload(r: JobResult) -> Dict[str, Any]:
    ordered = sorted(r.artifacts, key=lambda a: (_ARTIFACT_ORDER.get(a.kind, 9), a.path.lower()))
    artifacts = [a.to_dict() for a in ordered]
    return {
        "has_result": True,
        "batch_output_file": r.batch_output_file,
        "batch_filename": Path(r.batch_output_file).name if r.batch_output_file else None,
        "primary_output": r.primary_output,
        "stitched_files": [{"path": f, "filename": Path(f).name} for f in r.stitched_files if os.path.exists(f)],
        "total_duration_sec": round(r.total_duration_sec, 2),
        "total_hits": r.total_hits,
        "total_clips_count": r.total_clips_count,
        "total_clip_footage_sec": round(r.total_clip_footage_sec, 2),
        "report_json_path": r.report_json_path,
        "report_csv_path": r.report_csv_path,
        "output_dir": r.output_dir,
        "artifacts": artifacts,
        "encoder_used": r.encoder_used,
        "cancelled": r.cancelled,
        "per_video": [
            {
                "video": Path(s.path).name,
                "hits": s.total_hits,
                "clips": len([c for c in s.intervals if c.selected]),
                "transcribe_s": round(s.transcribe_duration, 1),
                "encode_s": round(s.encode_duration, 1),
                "output": Path(s.stitched_output_path).name if s.stitched_output_path else "",
                "failed": s.failed,
                "error": s.error_message,
            }
            for s in r.stats
        ],
    }


@app.get("/api/result")
def get_latest_result():
    """Get the latest rendered supercut artifacts and telemetry."""
    if not state.job_result:
        return {"has_result": False}
    return _result_payload(state.job_result)


@app.post("/api/cache/clear")
def clear_transcript_cache():
    from supercut.asr import TranscriptCache
    removed = TranscriptCache().clear()
    state.broadcast_log("info", f"Cleared {removed} cached transcript(s).")
    return {"status": "ok", "removed": removed}


@app.post("/api/shutdown")
def shutdown_server(request: Request):
    """Stop the server (desktop shell only, guarded by a token)."""
    token = request.headers.get("x-shutdown-token", "")
    if not state.shutdown_token or token != state.shutdown_token:
        raise HTTPException(status_code=403, detail="Not allowed")

    def _stop():
        time.sleep(0.2)
        os._exit(0)

    threading.Thread(target=_stop, daemon=True).start()
    return {"status": "bye"}


# ---------------------------------------------------------------------------
# Single-Page Application (inlined static assets)
# ---------------------------------------------------------------------------

def _read_static(name: str) -> str:
    p = STATIC_DIR / name
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def build_spa_html() -> str:
    """Inline CSS + JS into the HTML so the studio is a single offline document."""
    html = _read_static("index.html")
    css = _read_static("app.css")
    js = _read_static("app.js")
    if not html:
        return f"<html><body><h1>{APP_NAME}</h1><p>static/index.html is missing.</p></body></html>"
    html = html.replace("<!--APP_VERSION-->", __version__)
    html = html.replace('<link rel="stylesheet" href="app.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="app.js"></script>', f"<script>\n{js}\n</script>")
    return html


SPA_HTML = build_spa_html()


@app.head("/")
def head_studio_ui():
    return Response(status_code=200, media_type="text/html")


@app.get("/", response_class=HTMLResponse)
def get_studio_ui():
    """Serves the Single Page Application UI."""
    html = SPA_HTML if os.environ.get("VKS_STATIC_CACHE", "1") == "1" else build_spa_html()
    return HTMLResponse(content=html, status_code=200)


@app.get("/favicon.ico")
def favicon():
    ico = STATIC_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(str(ico), media_type="image/x-icon")
    return Response(status_code=204)


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "status": state.status}


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def run_server(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Web Studio Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--open-browser", action="store_true", default=True, help="Auto-open browser on launch")
    parser.add_argument("--no-browser", action="store_false", dest="open_browser", help="Do not open browser")
    args = parser.parse_args(argv)

    setup_logging()
    port = int(os.environ.get("PORT", args.port))
    host = os.environ.get("HOST", args.host)

    url = f"http://{host}:{port}"
    print("\n========================================================")
    print(f" {APP_NAME} v{__version__} - Web Studio")
    print(f" URL: {url}")
    print("========================================================\n")

    if args.open_browser:
        def opener():
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=opener, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
