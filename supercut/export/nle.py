"""Professional NLE export: Premiere Pro / Final Cut Pro XML and DaVinci Resolve EDL."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET

from ..models import ClipInterval


def is_high_energy_clip(clip: ClipInterval) -> bool:
    """Determine if a clip interval represents a high energy moment."""
    if getattr(clip, "is_high_energy", False):
        return True
    if clip.primary_keyword.lower() in ("high-energy", "high energy", "energy"):
        return True
    if clip.custom_title and "high energy" in clip.custom_title.lower():
        return True
    for hit in getattr(clip, "keyword_hits", []):
        if hit.keyword.lower() in ("high-energy", "high energy", "energy"):
            return True
    return False


def export_premiere_fcpxml(
    clips: Sequence[ClipInterval],
    sequence_name: str,
    output_xml_path: str,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    audio_sample_rate: int = 48000,
    audio_depth: int = 16,
) -> None:
    """Generate Final Cut Pro / Premiere Pro XML format for seamless import.
    
    Includes explicit stereo dual-mono audio track definitions and channel routing
    (Channel 1 / Left and Channel 2 / Right) mapped to discrete timeline audio tracks
    with proper clip linking for Premiere Pro and Final Cut Pro.
    """
    fps = max(1, int(fps))
    valid_clips = [c for c in clips if c.duration > 0.001]

    x_root = ET.Element("xmeml", version="4")
    x_seq = ET.SubElement(x_root, "sequence")
    ET.SubElement(x_seq, "name").text = sequence_name
    total_duration_frames = int(sum(c.duration for c in valid_clips) * fps)
    ET.SubElement(x_seq, "duration").text = str(total_duration_frames)

    rate_el = ET.SubElement(x_seq, "rate")
    ET.SubElement(rate_el, "timebase").text = str(fps)
    ET.SubElement(rate_el, "ntsc").text = "FALSE"

    media_el = ET.SubElement(x_seq, "media")

    # Video stream configuration
    video_el = ET.SubElement(media_el, "video")
    v_format = ET.SubElement(video_el, "format")
    v_sample = ET.SubElement(v_format, "samplecharacteristics")
    ET.SubElement(v_sample, "width").text = str(width)
    ET.SubElement(v_sample, "height").text = str(height)
    ET.SubElement(v_sample, "pixelaspectratio").text = "square"
    v_rate = ET.SubElement(v_sample, "rate")
    ET.SubElement(v_rate, "timebase").text = str(fps)
    ET.SubElement(v_rate, "ntsc").text = "FALSE"

    video_track_el = ET.SubElement(video_el, "track")

    # Audio stream configuration with explicit stereo dual-mono channel mapping
    audio_el = ET.SubElement(media_el, "audio")
    ET.SubElement(audio_el, "numOutputChannels").text = "2"
    a_format = ET.SubElement(audio_el, "format")
    a_sample = ET.SubElement(a_format, "samplecharacteristics")
    ET.SubElement(a_sample, "depth").text = str(audio_depth)
    ET.SubElement(a_sample, "samplerate").text = str(audio_sample_rate)

    # Dual-mono audio tracks: Track 1 (Left / Channel 1) and Track 2 (Right / Channel 2)
    audio_track1_el = ET.SubElement(audio_el, "track")
    audio_track2_el = ET.SubElement(audio_el, "track")

    curr_frame = 0
    for idx, clip in enumerate(valid_clips, start=1):
        c_dur_frames = max(1, int(clip.duration * fps))
        in_frame = max(0, int(clip.start * fps))
        out_frame = in_frame + c_dur_frames

        # 1. Video ClipItem
        v_item = ET.SubElement(video_track_el, "clipitem", id=f"clipitem-{idx}")
        ET.SubElement(v_item, "name").text = f"Clip {idx:03d} ({clip.primary_keyword})"
        ET.SubElement(v_item, "duration").text = str(c_dur_frames)
        v_item_rate = ET.SubElement(v_item, "rate")
        ET.SubElement(v_item_rate, "timebase").text = str(fps)
        ET.SubElement(v_item_rate, "ntsc").text = "FALSE"
        ET.SubElement(v_item, "start").text = str(curr_frame)
        ET.SubElement(v_item, "end").text = str(curr_frame + c_dur_frames)
        ET.SubElement(v_item, "in").text = str(in_frame)
        ET.SubElement(v_item, "out").text = str(out_frame)

        file_el = ET.SubElement(v_item, "file", id=f"file-{idx}")
        p = Path(clip.source_video).resolve() if clip.source_video else Path("clip.mp4").resolve()
        ET.SubElement(file_el, "name").text = p.name
        ET.SubElement(file_el, "pathurl").text = p.as_uri()
        f_rate = ET.SubElement(file_el, "rate")
        ET.SubElement(f_rate, "timebase").text = str(fps)
        ET.SubElement(f_rate, "ntsc").text = "FALSE"
        ET.SubElement(file_el, "duration").text = str(out_frame)

        # Video link references
        v_link_v = ET.SubElement(v_item, "link")
        ET.SubElement(v_link_v, "linkclipref").text = f"clipitem-{idx}"
        ET.SubElement(v_link_v, "mediatype").text = "video"
        ET.SubElement(v_link_v, "trackindex").text = "1"
        ET.SubElement(v_link_v, "clipindex").text = str(idx)

        v_link_a1 = ET.SubElement(v_item, "link")
        ET.SubElement(v_link_a1, "linkclipref").text = f"clipitem-a1-{idx}"
        ET.SubElement(v_link_a1, "mediatype").text = "audio"
        ET.SubElement(v_link_a1, "trackindex").text = "1"
        ET.SubElement(v_link_a1, "clipindex").text = str(idx)
        ET.SubElement(v_link_a1, "groupindex").text = "1"

        v_link_a2 = ET.SubElement(v_item, "link")
        ET.SubElement(v_link_a2, "linkclipref").text = f"clipitem-a2-{idx}"
        ET.SubElement(v_link_a2, "mediatype").text = "audio"
        ET.SubElement(v_link_a2, "trackindex").text = "2"
        ET.SubElement(v_link_a2, "clipindex").text = str(idx)
        ET.SubElement(v_link_a2, "groupindex").text = "1"

        # 2. Audio Track 1 ClipItem (Channel 1 / Dual Mono Left)
        a1_item = ET.SubElement(audio_track1_el, "clipitem", id=f"clipitem-a1-{idx}")
        ET.SubElement(a1_item, "name").text = f"Clip {idx:03d} ({clip.primary_keyword}) [A1]"
        ET.SubElement(a1_item, "duration").text = str(c_dur_frames)
        a1_rate = ET.SubElement(a1_item, "rate")
        ET.SubElement(a1_rate, "timebase").text = str(fps)
        ET.SubElement(a1_rate, "ntsc").text = "FALSE"
        ET.SubElement(a1_item, "start").text = str(curr_frame)
        ET.SubElement(a1_item, "end").text = str(curr_frame + c_dur_frames)
        ET.SubElement(a1_item, "in").text = str(in_frame)
        ET.SubElement(a1_item, "out").text = str(out_frame)
        ET.SubElement(a1_item, "file", id=f"file-{idx}")

        # Explicit sourcetrack mapping for Channel 1
        st1 = ET.SubElement(a1_item, "sourcetrack")
        ET.SubElement(st1, "mediatype").text = "audio"
        ET.SubElement(st1, "trackindex").text = "1"

        # Linking for Audio 1
        a1_link_v = ET.SubElement(a1_item, "link")
        ET.SubElement(a1_link_v, "linkclipref").text = f"clipitem-{idx}"
        ET.SubElement(a1_link_v, "mediatype").text = "video"
        ET.SubElement(a1_link_v, "trackindex").text = "1"
        ET.SubElement(a1_link_v, "clipindex").text = str(idx)

        a1_link_a1 = ET.SubElement(a1_item, "link")
        ET.SubElement(a1_link_a1, "linkclipref").text = f"clipitem-a1-{idx}"
        ET.SubElement(a1_link_a1, "mediatype").text = "audio"
        ET.SubElement(a1_link_a1, "trackindex").text = "1"
        ET.SubElement(a1_link_a1, "clipindex").text = str(idx)
        ET.SubElement(a1_link_a1, "groupindex").text = "1"

        a1_link_a2 = ET.SubElement(a1_item, "link")
        ET.SubElement(a1_link_a2, "linkclipref").text = f"clipitem-a2-{idx}"
        ET.SubElement(a1_link_a2, "mediatype").text = "audio"
        ET.SubElement(a1_link_a2, "trackindex").text = "2"
        ET.SubElement(a1_link_a2, "clipindex").text = str(idx)
        ET.SubElement(a1_link_a2, "groupindex").text = "1"

        # 3. Audio Track 2 ClipItem (Channel 2 / Dual Mono Right)
        a2_item = ET.SubElement(audio_track2_el, "clipitem", id=f"clipitem-a2-{idx}")
        ET.SubElement(a2_item, "name").text = f"Clip {idx:03d} ({clip.primary_keyword}) [A2]"
        ET.SubElement(a2_item, "duration").text = str(c_dur_frames)
        a2_rate = ET.SubElement(a2_item, "rate")
        ET.SubElement(a2_rate, "timebase").text = str(fps)
        ET.SubElement(a2_rate, "ntsc").text = "FALSE"
        ET.SubElement(a2_item, "start").text = str(curr_frame)
        ET.SubElement(a2_item, "end").text = str(curr_frame + c_dur_frames)
        ET.SubElement(a2_item, "in").text = str(in_frame)
        ET.SubElement(a2_item, "out").text = str(out_frame)
        ET.SubElement(a2_item, "file", id=f"file-{idx}")

        # Explicit sourcetrack mapping for Channel 2
        st2 = ET.SubElement(a2_item, "sourcetrack")
        ET.SubElement(st2, "mediatype").text = "audio"
        ET.SubElement(st2, "trackindex").text = "2"

        # Linking for Audio 2
        a2_link_v = ET.SubElement(a2_item, "link")
        ET.SubElement(a2_link_v, "linkclipref").text = f"clipitem-{idx}"
        ET.SubElement(a2_link_v, "mediatype").text = "video"
        ET.SubElement(a2_link_v, "trackindex").text = "1"
        ET.SubElement(a2_link_v, "clipindex").text = str(idx)

        a2_link_a1 = ET.SubElement(a2_item, "link")
        ET.SubElement(a2_link_a1, "linkclipref").text = f"clipitem-a1-{idx}"
        ET.SubElement(a2_link_a1, "mediatype").text = "audio"
        ET.SubElement(a2_link_a1, "trackindex").text = "1"
        ET.SubElement(a2_link_a1, "clipindex").text = str(idx)
        ET.SubElement(a2_link_a1, "groupindex").text = "1"

        a2_link_a2 = ET.SubElement(a2_item, "link")
        ET.SubElement(a2_link_a2, "linkclipref").text = f"clipitem-a2-{idx}"
        ET.SubElement(a2_link_a2, "mediatype").text = "audio"
        ET.SubElement(a2_link_a2, "trackindex").text = "2"
        ET.SubElement(a2_link_a2, "clipindex").text = str(idx)
        ET.SubElement(a2_link_a2, "groupindex").text = "1"

        curr_frame += c_dur_frames

    tree = ET.ElementTree(x_root)
    ET.indent(tree, space="  ")
    tree.write(output_xml_path, encoding="utf-8", xml_declaration=True)


def export_cmx3600_edl(
    clips: Sequence[ClipInterval],
    sequence_title: str,
    output_edl_path: str,
    fps: int = 30,
) -> None:
    """Generate standard CMX 3600 Edit Decision List for DaVinci Resolve & Avid.
    
    Includes DaVinci Resolve color-coded clip markers:
    - Cyan (ResolveColorCyan) for primary keywords.
    - Green (ResolveColorGreen) for high energy moments.
    """
    fps = max(1, int(fps))
    valid_clips = [c for c in clips if c.duration > 0.001]
    lines = [f"TITLE: {sequence_title}", "FCM: NON-DROP FRAME", ""]

    def frames_to_tc(frames: int) -> str:
        frames = max(0, int(frames))
        ff = frames % fps
        s_total = frames // fps
        ss = s_total % 60
        mm = (s_total // 60) % 60
        hh = s_total // 3600
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    record_frame = 0
    for idx, clip in enumerate(valid_clips, start=1):
        dur_frames = max(1, int(clip.duration * fps))
        src_in_f = max(0, int(clip.start * fps))
        src_out_f = src_in_f + dur_frames

        rec_in_f = record_frame
        rec_out_f = record_frame + dur_frames

        src_in_tc = frames_to_tc(src_in_f)
        src_out_tc = frames_to_tc(src_out_f)
        rec_in_tc = frames_to_tc(rec_in_f)
        rec_out_tc = frames_to_tc(rec_out_f)

        reel_name = f"AX{idx:02d}"[:8]
        lines.append(f"{idx:03d}  {reel_name:<8} V     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
        lines.append(f"* FROM CLIP NAME: {Path(clip.source_video).name if clip.source_video else 'clip.mp4'}")
        lines.append(f"* KEYWORD: {clip.primary_keyword}")

        # DaVinci Resolve color-coded markers
        # ResolveColorCyan: Primary keywords | ResolveColorGreen: High energy moments
        if clip.keyword_hits:
            for hit in clip.keyword_hits:
                hit_energy = (
                    getattr(clip, "is_high_energy", False)
                    or hit.keyword.lower() in ("high-energy", "high energy", "energy")
                    or bool(clip.custom_title and "high energy" in clip.custom_title.lower())
                )
                color = "ResolveColorGreen" if hit_energy else "ResolveColorCyan"
                label = f"High Energy: {hit.matched_text or hit.keyword}" if hit_energy else f"Keyword: {hit.keyword}"
                hit_dur_frames = max(1, int((hit.end - hit.start) * fps)) if hit.end > hit.start else dur_frames
                lines.append(f"* |C:{color} |M:{label} |D:{hit_dur_frames}")
        else:
            is_energy = is_high_energy_clip(clip)
            color = "ResolveColorGreen" if is_energy else "ResolveColorCyan"
            label = f"High Energy: {clip.primary_keyword}" if is_energy else f"Keyword: {clip.primary_keyword}"
            lines.append(f"* |C:{color} |M:{label} |D:{dur_frames}")

        lines.append("")

        record_frame += dur_frames

    with open(output_edl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

