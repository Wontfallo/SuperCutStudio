"""Job summary and analytics reports (JSON and CSV)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import List

from ..models import VideoJobStats


def export_job_report_json(stats_list: List[VideoJobStats], output_path: str) -> None:
    data = []
    for s in stats_list:
        data.append({
            "video": s.path,
            "duration": s.info.duration if s.info else 0.0,
            "hits": s.total_hits,
            "clips_count": len(s.intervals),
            "clip_duration": s.clip_duration,
            "transcribe_seconds": s.transcribe_duration,
            "encode_seconds": s.encode_duration,
            "output": s.stitched_output_path,
            "failed": s.failed,
            "error": s.error_message,
        })
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def export_job_report_csv(stats_list: List[VideoJobStats], output_path: str) -> None:
    fieldnames = [
        "video",
        "duration_s",
        "hits",
        "clips_count",
        "clip_duration_s",
        "transcribe_s",
        "encode_s",
        "output",
        "status",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in stats_list:
            writer.writerow({
                "video": Path(s.path).name,
                "duration_s": f"{s.info.duration:.1f}" if s.info else "0.0",
                "hits": s.total_hits,
                "clips_count": len(s.intervals),
                "clip_duration_s": f"{s.clip_duration:.1f}",
                "transcribe_s": f"{s.transcribe_duration:.1f}",
                "encode_s": f"{s.encode_duration:.1f}",
                "output": Path(s.stitched_output_path).name if s.stitched_output_path else "",
                "status": "FAILED" if s.failed else "SUCCESS",
            })
