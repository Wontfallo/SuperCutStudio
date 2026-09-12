"""Transition filter generation (Direct cut, Crossfade dissolve, Dip to black)."""

from __future__ import annotations

from typing import List, Tuple

from ..models import TransitionMode


TRANSITION_XFADE_MAP = {
    TransitionMode.CROSSFADE: "fade",
    TransitionMode.FADE_BLACK: "fadeblack",
    TransitionMode.SWIPE_LEFT: "slideleft",
}


def build_transition_concat_filter(
    num_clips: int,
    clip_durations: List[float],
    transition: TransitionMode = TransitionMode.CUT,
    transition_duration: float = 0.3,
) -> Tuple[str, str, str]:
    """
    Build FFmpeg complex filter strings for video & audio transition concat.
    Supports CUT, CROSSFADE (dissolve), FADE_BLACK, and SWIPE_LEFT.
    Guarantees strict audio/video timestamp synchronization without drift.
    Returns: (filter_complex_string, out_video_label, out_audio_label)
    """
    if num_clips <= 0:
        return "", "", ""

    if num_clips == 1:
        return "[v0]settb=AVTB[cv];[a0]anull[ca]", "[cv]", "[ca]"

    if transition == TransitionMode.CUT or not clip_durations or len(clip_durations) < num_clips:
        in_labels = "".join(f"[v{i}][a{i}]" for i in range(num_clips))
        fc = f"{in_labels}concat=n={num_clips}:v=1:a=1[cv][ca]"
        return fc, "[cv]", "[ca]"

    min_clip_d = min(clip_durations[:num_clips])
    if min_clip_d < 0.25:
        # Clips shorter than 0.25s cannot support clean crossfades without visual stutter
        in_labels = "".join(f"[v{i}][a{i}]" for i in range(num_clips))
        fc = f"{in_labels}concat=n={num_clips}:v=1:a=1[cv][ca]"
        return fc, "[cv]", "[ca]"

    # Check safe transition duration:
    # Outer clips need t_dur; interior clips need 2 * t_dur
    safe_limit = min(clip_durations[0], clip_durations[-1]) - 0.05
    if num_clips > 2:
        safe_limit = min(safe_limit, (min(clip_durations[1:num_clips - 1]) / 2.0) - 0.02)

    t_dur = min(transition_duration, safe_limit)
    if t_dur <= 0.05 or safe_limit <= 0.05:
        # Graceful fallback to hard cut if clips are too short for crossfades
        in_labels = "".join(f"[v{i}][a{i}]" for i in range(num_clips))
        fc = f"{in_labels}concat=n={num_clips}:v=1:a=1[cv][ca]"
        return fc, "[cv]", "[ca]"

    v_trans = TRANSITION_XFADE_MAP.get(transition, "fade")
    a_curve = "c1=exp:c2=exp" if transition == TransitionMode.FADE_BLACK else "c1=tri:c2=tri"

    fc_parts = []

    # Video crossfade chain
    last_v = "v0"
    current_offset = clip_durations[0] - t_dur
    for i in range(1, num_clips):
        next_v = f"v{i}"
        out_v = f"x_v{i}" if i < num_clips - 1 else "cv"
        fc_parts.append(
            f"[{last_v}][{next_v}]xfade=transition={v_trans}:duration={t_dur:.3f}:offset={max(0.0, current_offset):.3f}[{out_v}]"
        )
        last_v = out_v
        if i < num_clips - 1:
            current_offset += clip_durations[i] - t_dur

    # Audio crossfade chain
    last_a = "a0"
    for i in range(1, num_clips):
        next_a = f"a{i}"
        out_a = f"x_a{i}" if i < num_clips - 1 else "ca"
        fc_parts.append(f"[{last_a}][{next_a}]acrossfade=d={t_dur:.3f}:{a_curve}[{out_a}]")
        last_a = out_a

    return ";".join(fc_parts), "[cv]", "[ca]"
