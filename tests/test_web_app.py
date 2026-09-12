"""Unit and integration tests for the SuperCut Studio server (web_app.py).

Covers:
  - SPA serving (offline single document, no CDN)
  - Config management (GET, POST, persistence, legacy key migration)
  - Media scanning, uploads, native picker and open endpoints
  - SSE streaming & status snapshots
  - Job triggers (analyze, render, 1-click, folder stitch, discovery) with mocked pipelines
  - Clip cockpit (retrieval, toggle, rename, trim, add, delete, batch operations)
  - Transcript inspector + exports, waveform endpoint
  - Media streaming with HTTP 206 range requests and session-scoped access control
  - Download / result / history / logs / health endpoints
"""

import io
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from supercut.models import (
    ClipInterval,
    HitSpan,
    MediaInfo,
    Segment,
    Transcript,
    VideoJobStats,
    Word,
)
from supercut.pipeline import Artifact, JobResult
import web_app
from web_app import app, sse_events, state


@pytest.fixture(autouse=True)
def reset_studio_state():
    """Reset server state between test runs."""
    with state.lock:
        state.status = "idle"
        state.stage = "Idle"
        state.progress_pct = 0.0
        state.progress_msg = "Ready"
        state.analyzed_stats = []
        state.job_result = None
        state.error_message = None
        state.current_pipeline = None
        state.subscribers.clear()
        state.known_media.clear()
        state.job_history.clear()
        state.log_buffer.clear()
        state.config_data["input_path"] = ""
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _wait_idle(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state.status in ("idle", "error", "cancelled"):
            return True
        time.sleep(0.02)
    return False


def _seed_clips():
    clip1 = ClipInterval(
        start=1.2, end=4.5,
        keyword_hits=[HitSpan(matched_text="quantum entanglement", keyword="quantum", start=2.0, end=3.0, confidence=0.98)],
        source_video="C:/media/test_video_1.mp4", index=1, selected=True,
    )
    clip2 = ClipInterval(
        start=8.0, end=11.2,
        keyword_hits=[HitSpan(matched_text="quantum computing", keyword="quantum", start=9.0, end=10.0, confidence=0.92)],
        source_video="C:/media/test_video_1.mp4", index=2, selected=False,
    )
    stats1 = VideoJobStats(
        path="C:/media/test_video_1.mp4",
        info=MediaInfo(width=1920, height=1080, fps=30.0, duration=60.0, has_audio=True),
        intervals=[clip1, clip2],
        total_hits=2,
    )
    with state.lock:
        state.analyzed_stats = [stats1]
    return stats1


# ---------------------------------------------------------------------------
# SPA & system
# ---------------------------------------------------------------------------

def test_index_page_loads_offline_single_document(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "SuperCut Studio" in html
    assert "Keyword Matrix" in html
    assert "Detected Clips" in html
    assert "Transcript Inspector" in html
    assert "Production &amp; Delivery" in html
    assert "EventSource('/api/events')" in html
    for element_id in ("toast-container", "player-waveform-canvas", "player-scrubber", "waveform-track",
                       "waveform-clip-region", "input-path", "keyword-chips", "clips-tbody", "log-console"):
        assert f'id="{element_id}"' in html
    assert "initKeyboardShortcuts" in html
    assert "showToast" in html
    # Fully offline: no CDN scripts or webfonts and assets are inlined
    assert "cdn.tailwindcss.com" not in html
    assert "fonts.googleapis.com" not in html
    assert 'src="app.js"' not in html and 'href="app.css"' not in html
    assert web_app.__version__ in html


def test_health_status_and_system(client):
    assert client.get("/api/health").json()["ok"] is True
    snap = client.get("/api/status").json()
    assert snap["status"] == "idle" and "version" in snap
    with patch("web_app.system_report") as mock_report:
        mock_report.return_value = {
            "app": {"name": "x", "version": "1", "frozen": False, "data_dir": "d"},
            "ffmpeg": {"available": True, "path": "ffmpeg", "version": "ffmpeg version 8"},
            "encoders": {"h264_nvenc": True}, "nvenc": True, "cuda": False, "gpu": None,
            "asr": {"crisper_available": False, "crisper_python": None, "models": []},
            "transcript_cache": {"count": 0, "size_bytes": 0},
        }
        web_app._system_cache = {}
        data = client.get("/api/system?refresh=true").json()
    assert data["nvenc"] is True
    assert "subtitle_styles" in data and "karaoke_amber" in data["subtitle_styles"]
    assert data["state"]["status"] == "idle"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_get_and_update_config(client, tmp_path):
    get_res = client.get("/api/config")
    assert get_res.status_code == 200
    cfg = get_res.json()
    assert "keywords" in cfg and "aspect_ratio" in cfg and "encoder" in cfg

    new_cfg = {
        "input_path": '"C:/TestVideos";',
        "input_mode": "Folder",
        "keywords": ["supercut", "epic", " highlights ", "epic"],
        "exclude_keywords": ["boring"],
        "output_dir": str(tmp_path),
        "model": "crisper-turbo",
        "aspect_ratio": "9:16",
        "transition": "crossfade",
        "transition_duration": 0.5,
        "encoder": "h264_nvenc",
        "quality": "high",
        "burn_subtitles": True,
        "subtitle_style": "karaoke_amber",
        "pre_pad": 1.5,
        "post_pad": 2.0,
        "fuzzy_threshold": 0.8,
        "save_individual_clips": True,
        "export_vtt": True,
    }
    with patch.object(web_app, "SETTINGS_FILE", tmp_path / "settings.json"):
        post_res = client.post("/api/config", json=new_cfg)
        assert post_res.status_code == 200
        saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    res_data = post_res.json()
    assert res_data["status"] == "ok"
    assert res_data["config"]["aspect_ratio"] == "9:16"
    assert res_data["config"]["keywords"] == ["supercut", "epic", "highlights"]  # deduped + trimmed
    assert res_data["config"]["input_path"] == "C:/TestVideos"  # sanitized
    assert res_data["config"]["encoder"] == "h264_nvenc"
    assert saved["quality"] == "high"

    job = state.to_job_config()
    assert job.aspect_ratio.value == "9:16"
    assert job.transition.value == "crossfade"
    assert job.encoder.value == "h264_nvenc"
    assert job.quality.value == "high"
    assert job.fuzzy_threshold == 0.8
    assert job.export_vtt is True


def test_legacy_settings_keys_are_migrated(tmp_path):
    legacy = {"out_clips": True, "out_stitched": False, "out_batch": True, "pad": 2.5, "keywords": ["a"]}
    (tmp_path / "settings.json").write_text(json.dumps(legacy), encoding="utf-8")
    with patch.object(web_app, "SETTINGS_FILE", tmp_path / "settings.json"):
        cfg = web_app.StudioState()._load_config()
    assert cfg["save_individual_clips"] is True
    assert cfg["save_per_video_stitched"] is False
    assert cfg["save_batch_stitched"] is True
    assert cfg["pre_pad"] == 2.5 and cfg["post_pad"] == 2.5


# ---------------------------------------------------------------------------
# Scan / upload / pick / open
# ---------------------------------------------------------------------------

def test_scan_validation_and_results(client):
    assert client.post("/api/scan", json={"input_path": ""}).status_code == 400
    assert client.post("/api/scan", json={"input_path": "Z:/NonExistentPath_12345"}).status_code == 404

    with tempfile.TemporaryDirectory() as tmpdir:
        dummy_mp4 = Path(tmpdir) / "sample_test.mp4"
        dummy_mp4.write_bytes(b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00")
        (Path(tmpdir) / "FINAL_BATCH_old.mp4").write_bytes(b"x")

        with patch("web_app.probe_media") as mock_probe:
            mock_probe.return_value = MediaInfo(width=1920, height=1080, fps=30.0, duration=12.5, has_audio=True, video_codec="h264")
            res_valid = client.post("/api/scan", json={"input_path": f'"{tmpdir}";'})
            assert res_valid.status_code == 200
            data = res_valid.json()
            assert data["count"] == 1
            assert data["is_file"] is False
            assert data["files"][0]["filename"] == "sample_test.mp4"
            assert data["files"][0]["resolution"] == "1920x1080"
            assert data["files"][0]["duration"] == 12.5
            assert data["total_duration"] == 12.5
        # scanned files become streamable
        assert str(dummy_mp4.resolve()) in state.known_media


def test_file_upload_staging(client):
    file_payload = ("test_dropped_video.mp4", io.BytesIO(b"fake video content"), "video/mp4")
    with patch("web_app.probe_media") as mock_probe:
        mock_probe.return_value = MediaInfo(width=1280, height=720, fps=24.0, duration=5.0, has_audio=True)
        res = client.post("/api/upload", files={"file": file_payload})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["filename"].startswith("test_dropped_video")
        assert os.path.exists(data["path"])
        os.remove(data["path"])


def test_pick_and_open_endpoints(client, tmp_path):
    with patch("web_app.pick_path", return_value=str(tmp_path)) as mock_pick:
        res = client.post("/api/pick", json={"kind": "folder", "title": "t", "initial": ""})
        assert res.json() == {"status": "ok", "path": str(tmp_path), "kind": "folder"}
        mock_pick.assert_called_once()
    with patch("web_app.pick_path", return_value=None):
        assert client.post("/api/pick", json={"kind": "file"}).json()["status"] == "cancelled"
    with patch("web_app.reveal_in_file_manager", return_value=True) as mock_open:
        assert client.post("/api/open", json={"path": str(tmp_path)}).json()["status"] == "ok"
        mock_open.assert_called_once()
    assert client.post("/api/open", json={"path": "Z:/nope"}).status_code == 404


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

def test_sse_events_stream():
    import asyncio
    from unittest.mock import AsyncMock
    req = MagicMock()
    req.is_disconnected = AsyncMock(return_value=False)
    resp = asyncio.run(sse_events(req))
    assert resp.status_code == 200
    assert resp.media_type == "text/event-stream"
    first_chunk = asyncio.run(anext(resp.body_iterator))
    assert "event: init" in first_chunk
    assert '"status": "idle"' in first_chunk


def test_broadcast_log_buffer_and_logs_endpoint(client):
    state.broadcast_log("info", "hello world")
    logs = client.get("/api/logs?limit=10").json()
    assert logs["count"] >= 1
    assert logs["logs"][-1]["text"] == "hello world"


# ---------------------------------------------------------------------------
# Clips cockpit
# ---------------------------------------------------------------------------

def test_clip_cockpit_workflows(client):
    _seed_clips()

    data = client.get("/api/clips").json()
    assert data["count"] == 2
    assert data["selected_count"] == 1
    assert data["clips"][0]["primary_keyword"] == "quantum"
    assert data["videos"][0]["filename"] == "test_video_1.mp4"
    assert data["selected_footage"] == 3.3

    res_toggle = client.post("/api/clips/update", json={
        "video_path": "C:/media/test_video_1.mp4", "clip_index": 2, "selected": True, "custom_title": "Quantum Leap",
    })
    assert res_toggle.status_code == 200
    assert res_toggle.json()["clip"]["selected"] is True
    assert res_toggle.json()["clip"]["custom_title"] == "Quantum Leap"
    assert client.get("/api/clips").json()["selected_count"] == 2

    # Trim in/out points with clamping to the media duration
    res_trim = client.post("/api/clips/update", json={
        "video_path": "C:/media/test_video_1.mp4", "clip_index": 1, "start": 0.5, "end": 999.0,
    })
    assert res_trim.json()["clip"]["start"] == 0.5
    assert res_trim.json()["clip"]["end"] == 60.0

    assert client.post("/api/clips/batch_toggle?select_all=false").status_code == 200
    assert client.get("/api/clips").json()["selected_count"] == 0
    assert client.post("/api/clips/batch_toggle?select_all=true").status_code == 200
    assert client.get("/api/clips").json()["selected_count"] == 2
    client.post("/api/clips/batch_toggle?invert=true")
    assert client.get("/api/clips").json()["selected_count"] == 0

    assert client.post("/api/clips/update", json={"video_path": "nope.mp4", "clip_index": 1, "selected": True}).status_code == 404


def test_add_and_delete_manual_clips(client):
    _seed_clips()
    res = client.post("/api/clips/add", json={"video_path": "C:/media/test_video_1.mp4", "start": 20.0, "end": 25.0, "title": "Intro"})
    assert res.status_code == 200
    clip = res.json()["clip"]
    assert clip["manual"] is True and clip["clip_index"] == 3 and clip["primary_keyword"] == "Intro"
    assert client.get("/api/clips").json()["count"] == 3

    assert client.post("/api/clips/add", json={"video_path": "C:/media/test_video_1.mp4", "start": 5.0, "end": 5.01}).status_code == 400
    assert client.post("/api/clips/add", json={"video_path": "missing.mp4", "start": 1, "end": 2}).status_code == 404

    res_del = client.post("/api/clips/delete", json={"video_path": "C:/media/test_video_1.mp4", "clip_index": 3})
    assert res_del.status_code == 200 and res_del.json()["remaining"] == 2
    assert client.post("/api/clips/delete", json={"video_path": "C:/media/test_video_1.mp4", "clip_index": 99}).status_code == 404


# ---------------------------------------------------------------------------
# Transcript & waveform
# ---------------------------------------------------------------------------

def _seed_transcript():
    seg = Segment(
        start=0.0, end=4.0, text="Exploring the fundamentals of quantum physics.",
        words=[Word(text="Exploring", start=0.0, end=0.8, confidence=0.99), Word(text="quantum", start=2.0, end=2.8, confidence=0.98),
               Word(text="physics", start=2.9, end=3.8, confidence=0.97)],
    )
    transcript = Transcript(media_path="C:/media/science.mp4", duration=4.0, language="en", segments=[seg], model_name="crisper-turbo")
    stats = VideoJobStats(path="C:/media/science.mp4", info=MediaInfo(duration=4.0), transcript=transcript, transcript_cached=True)
    with state.lock:
        state.analyzed_stats = [stats]
    return stats


def test_transcript_inspector_endpoint_and_exports(client):
    _seed_transcript()
    res = client.get("/api/transcript")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    t = data["transcripts"][0]
    assert t["filename"] == "science.mp4"
    assert "quantum" in t["full_text"]
    assert len(t["segments"][0]["words"]) == 3
    assert t["segments"][0]["words"][1]["text"] == "quantum"
    assert t["word_count"] == 3 and t["cached"] is True

    srt = client.get("/api/transcript/export?video_path=C:/media/science.mp4&fmt=srt")
    assert srt.status_code == 200 and "00:00:00,000 --> 00:00:04,000" in srt.text
    txt = client.get("/api/transcript/export?video_path=C:/media/science.mp4&fmt=txt")
    assert txt.text.startswith("[00:00] Exploring")
    js = client.get("/api/transcript/export?video_path=C:/media/science.mp4&fmt=json")
    assert js.json()["segments"][0]["text"].startswith("Exploring")
    assert client.get("/api/transcript/export?video_path=nope.mp4").status_code == 404


def test_waveform_endpoint(client):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"0" * 100)
        tmp_path = f.name
    try:
        # Not part of the session -> forbidden
        assert client.get(f"/api/waveform?path={tmp_path}").status_code == 403
        state.known_media.add(str(Path(tmp_path).resolve()))
        with patch("web_app.probe_media", return_value=MediaInfo(width=10, height=10, duration=3.0, has_audio=True)), \
             patch("web_app.compute_waveform", return_value={"buckets": 4, "peaks": [0.1, 0.5, 0.9, 0.2], "duration": 3.0, "cached": False}):
            data = client.get(f"/api/waveform?path={tmp_path}&buckets=4").json()
        assert data["peaks"] == [0.1, 0.5, 0.9, 0.2]
        assert client.get("/api/waveform?path=Z:/missing.mp4").status_code == 404
    finally:
        os.remove(tmp_path)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def test_analyze_pipeline_trigger(client, tmp_path):
    assert client.post("/api/analyze", json={"input_path": ""}).status_code == 400
    assert client.post("/api/analyze", json={"input_path": "Z:/does/not/exist"}).status_code == 404

    with state.lock:
        state.status = "analyzing"
    assert client.post("/api/analyze", json={"input_path": str(tmp_path)}).status_code == 409
    with state.lock:
        state.status = "idle"
        state.config_data["keywords"] = []
        state.config_data["auto_keywords"] = False
        state.config_data["high_energy"] = False
    assert client.post("/api/analyze", json={"input_path": str(tmp_path)}).status_code == 400  # no keywords
    with state.lock:
        state.config_data["keywords"] = ["test"]

    video = tmp_path / "test.mp4"
    video.write_bytes(b"x")
    with patch("web_app.SuperCutPipeline") as MockPipeline:
        mock_instance = MagicMock()
        mock_instance.cancelled = False
        mock_instance.analyze.return_value = [
            VideoJobStats(path=str(video), total_hits=1, intervals=[ClipInterval(start=1.0, end=3.0, index=1, selected=True)])
        ]
        MockPipeline.return_value = mock_instance
        res_ok = client.post("/api/analyze", json={"input_path": str(video)})
        assert res_ok.status_code == 200
        assert res_ok.json()["status"] == "started"
        assert _wait_idle()
    assert state.status == "idle"
    assert state.stage == "Analysis Complete"
    assert len(state.analyzed_stats) == 1
    assert client.get("/api/jobs").json()["jobs"][0]["kind"] == "analyze"


def test_render_pipeline_trigger(client, tmp_path):
    assert client.post("/api/render", json={}).status_code == 400

    clip_unselected = ClipInterval(start=1.0, end=2.0, index=1, selected=False)
    with state.lock:
        state.analyzed_stats = [VideoJobStats(path="C:/vid.mp4", intervals=[clip_unselected])]
    assert client.post("/api/render", json={}).status_code == 400

    clip_unselected.selected = True
    out_file = tmp_path / "FINAL_BATCH_SUPERCUT.mp4"
    out_file.write_bytes(b"mp4")
    report = tmp_path / "supercut_report.json"
    report.write_text("{}")
    with patch("web_app.SuperCutPipeline") as MockPipeline:
        mock_instance = MagicMock()
        mock_instance.render_clips_and_stitch.return_value = JobResult(
            batch_output_file=str(out_file), stitched_files=[str(out_file)], total_duration_sec=8.4,
            total_hits=1, total_clips_count=1, total_clip_footage_sec=1.0, report_json_path=str(report),
            artifacts=[Artifact(str(out_file), "master", "Master"), Artifact(str(report), "report", "Report")],
            output_dir=str(tmp_path), stats=list(state.analyzed_stats),
        )
        MockPipeline.return_value = mock_instance
        res_render = client.post("/api/render", json={})
        assert res_render.status_code == 200
        assert res_render.json()["status"] == "started"
        assert _wait_idle()

    result = client.get("/api/result").json()
    assert result["has_result"] is True
    assert result["primary_output"] == str(out_file)
    assert result["artifacts"][0]["kind"] == "master" and result["artifacts"][0]["exists"] is True
    assert result["per_video"][0]["video"] == "vid.mp4"
    # Rendered artifacts are streamable / downloadable
    assert client.get(f"/api/media?path={out_file}").status_code == 200
    assert client.get(f"/api/download?path={report}").status_code == 200


def test_one_click_pipeline_and_folder_stitch(client, tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    with state.lock:
        state.config_data["keywords"] = ["hello"]
    with patch("web_app.SuperCutPipeline") as MockPipeline:
        inst = MagicMock()
        inst.cancelled = False
        inst.run.return_value = JobResult(stats=[VideoJobStats(path=str(video), total_hits=2)], total_hits=2,
                                          total_clips_count=2, output_dir=str(tmp_path))
        MockPipeline.return_value = inst
        assert client.post("/api/pipeline/start", json={"input_path": str(video)}).status_code == 200
        assert _wait_idle()
    assert state.stage == "SuperCut Complete"

    # Folder stitch requires a folder
    assert client.post("/api/stitch-folder-only", json={"input_path": str(video)}).status_code == 400
    out = tmp_path / "FINAL_BATCH_SUPERCUT.mp4"
    with patch("web_app.stitch_folder_direct", return_value=str(out)) as mock_stitch:
        out.write_bytes(b"x")
        assert client.post("/api/stitch-folder-only", json={"input_path": str(tmp_path)}).status_code == 200
        assert _wait_idle()
        mock_stitch.assert_called_once()
    assert state.job_result is not None and state.job_result.batch_output_file == str(out)


def test_job_error_is_reported(client, tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    with state.lock:
        state.config_data["keywords"] = ["hello"]
    with patch("web_app.SuperCutPipeline") as MockPipeline:
        inst = MagicMock()
        inst.analyze.side_effect = RuntimeError("boom")
        MockPipeline.return_value = inst
        client.post("/api/analyze", json={"input_path": str(video)})
        assert _wait_idle()
    assert state.status == "error"
    assert state.error_message == "boom"
    assert client.get("/api/status").json()["error"] == "boom"


def test_cancel_endpoint(client):
    mock_pipeline = MagicMock()
    with state.lock:
        state.status = "analyzing"
        state.current_pipeline = mock_pipeline

    res_cancel = client.post("/api/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "cancelled"
    assert res_cancel.json()["was_busy"] is True
    assert state.status == "cancelled"
    mock_pipeline.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# Media streaming / downloads
# ---------------------------------------------------------------------------

def test_media_streaming_with_range_requests(client):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"0123456789ABCDEF" * 100)  # 1600 bytes
        tmp_path = f.name

    try:
        # Not registered with the session -> forbidden
        assert client.get(f"/api/media?path={tmp_path}").status_code == 403
        state.known_media.add(str(Path(tmp_path).resolve()))

        res_full = client.get(f"/api/media?path={tmp_path}")
        assert res_full.status_code == 200
        assert len(res_full.content) == 1600
        assert res_full.headers["accept-ranges"] == "bytes"

        res_range = client.get(f"/api/media?path={tmp_path}", headers={"Range": "bytes=0-49"})
        assert res_range.status_code == 206
        assert len(res_range.content) == 50
        assert res_range.content == (b"0123456789ABCDEF" * 100)[:50]
        assert "bytes 0-49/1600" in res_range.headers["content-range"]

        res_mid = client.get(f"/api/media?path={tmp_path}", headers={"Range": "bytes=100-199"})
        assert res_mid.status_code == 206
        assert res_mid.content == (b"0123456789ABCDEF" * 100)[100:200]

        res_open = client.get(f"/api/media?path={tmp_path}", headers={"Range": "bytes=1500-"})
        assert res_open.status_code == 206 and len(res_open.content) == 100

        res_suffix = client.get(f"/api/media?path={tmp_path}", headers={"Range": "bytes=-16"})
        assert res_suffix.status_code == 206 and res_suffix.content == b"0123456789ABCDEF"

        assert client.get(f"/api/media?path={tmp_path}", headers={"Range": "bytes=5000-"}).status_code == 416
        assert client.get("/api/media?path=Z:/missing_media.mp4").status_code == 404
    finally:
        os.remove(tmp_path)


def test_download_and_result_endpoints(client):
    assert client.get("/api/result").json()["has_result"] is False

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(b'{"supercut": "report"}')
        report_file = f.name
    try:
        with state.lock:
            state.job_result = JobResult(
                batch_output_file="output/FINAL_BATCH.mp4", stitched_files=[], total_duration_sec=14.2,
                total_hits=5, total_clips_count=3, total_clip_footage_sec=9.5, report_json_path=report_file,
                output_dir=str(Path(report_file).parent),
            )
        data = client.get("/api/result").json()
        assert data["has_result"] is True
        assert data["total_clips_count"] == 3

        res_dl = client.get(f"/api/download?path={report_file}")
        assert res_dl.status_code == 200
        assert 'attachment; filename="' in res_dl.headers["content-disposition"]
        assert res_dl.content == b'{"supercut": "report"}'
        assert client.get("/api/download?path=Z:/missing.json").status_code == 404
    finally:
        os.remove(report_file)


def test_download_rejects_non_artifact_types(client, tmp_path):
    secret = tmp_path / "secret.exe"
    secret.write_bytes(b"MZ")
    state.known_media.add(str(secret.resolve()))
    assert client.get(f"/api/download?path={secret}").status_code == 415
    assert client.get(f"/api/media?path={secret}").status_code == 415


def test_shutdown_requires_token(client):
    assert client.post("/api/shutdown").status_code == 403


def test_reset_session_clears_state(client):
    _seed_clips()
    with state.lock:
        state.job_result = JobResult(total_hits=2, total_clips_count=2)
    assert client.post("/api/reset").json()["status"] == "ok"
    assert state.analyzed_stats == [] and state.job_result is None
    assert client.get("/api/clips").json()["count"] == 0
    assert client.get("/api/result").json()["has_result"] is False
    with state.lock:
        state.status = "analyzing"
    assert client.post("/api/reset").status_code == 409


def test_excluded_files_flagged_and_skipped(client, tmp_path):
    keep = tmp_path / "keep.mp4"
    skip = tmp_path / "skip.mp4"
    keep.write_bytes(b"x")
    skip.write_bytes(b"x")
    with state.lock:
        state.config_data["excluded_files"] = [str(skip)]
    with patch("web_app.probe_media", return_value=MediaInfo(width=10, height=10, fps=30, duration=5.0, has_audio=True)):
        data = client.post("/api/scan", json={"input_path": str(tmp_path)}).json()
    assert data["count"] == 2 and data["active_count"] == 1
    assert {f["filename"]: f["excluded"] for f in data["files"]} == {"keep.mp4": False, "skip.mp4": True}
    assert data["total_duration"] == 5.0

    from supercut.pipeline import SuperCutPipeline
    from supercut.models import JobConfig
    pipe = SuperCutPipeline(JobConfig(keywords=["a"], excluded_files=[str(skip)]))
    assert [Path(f).name for f in pipe.scan_video_inputs(str(tmp_path))] == ["keep.mp4"]
    with state.lock:
        state.config_data["excluded_files"] = []
