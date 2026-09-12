"""Unit tests for video pipeline: 9:16 vertical blur conforming, seamless transitions, and NVENC encoding."""

import os
import subprocess
import unittest
from unittest.mock import patch

from supercut.models import AspectRatioMode, ClipInterval, EncoderChoice, JobConfig, TransitionMode
from supercut.video.encoder import (
    CPU_X264_ARGS,
    CPU_X265_ARGS,
    NVENC_AV1_ARGS,
    NVENC_H264_ARGS,
    NVENC_HEVC_ARGS,
    get_encoder_attempts,
    is_nvenc_available,
    register_encoder_success,
    reset_encoder_cache,
)
from supercut.video.normalizer import build_aspect_ratio_filter, build_audio_filter
from supercut.video.stitcher import build_multi_clip_filter_complex
from supercut.video.transitions import TRANSITION_XFADE_MAP, build_transition_concat_filter


class TestAspectRatioFilters(unittest.TestCase):
    """Verify aspect ratio filter generation and odd-pixel conformance."""

    def test_vertical_9_16_cinematic_blur_filter(self):
        vf = build_aspect_ratio_filter(
            mode=AspectRatioMode.VERTICAL_9_16,
            target_w=1080,
            target_h=1920,
            fps=30,
        )
        # Check split and labels
        self.assertIn("split=2[bg_in][fg_in]", vf)
        # Check background conforms to 1080x1920 with blur, cinematic contrast, and SAR
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2", vf)
        self.assertIn("crop=1080:1920", vf)
        self.assertIn("boxblur=25:5", vf)
        self.assertIn("eq=brightness=-0.08:contrast=1.05", vf)
        # Check foreground conforms with decrease, even dimensions, and SAR
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2", vf)
        self.assertIn("scale=trunc(iw/2)*2:trunc(ih/2)*2", vf)
        # Check overlay, timebase, and pixel format
        self.assertIn("overlay=x='(W-w)/2':y='(H-h)/2'", vf)
        self.assertIn("settb=AVTB", vf)
        self.assertIn("setsar=1", vf)
        self.assertIn("format=yuv420p", vf)

    def test_vertical_odd_dimension_conforming(self):
        # Odd target dimensions should be safely rounded down to even integers
        vf = build_aspect_ratio_filter(
            mode=AspectRatioMode.VERTICAL_9_16,
            target_w=719,
            target_h=1279,
            fps=30,
        )
        self.assertIn("scale=718:1278", vf)
        self.assertIn("crop=718:1278", vf)

    def test_tag_scoping_prevents_label_collisions(self):
        vf_0 = build_aspect_ratio_filter(AspectRatioMode.VERTICAL_9_16, 1080, 1920, 30, tag="_0")
        vf_1 = build_aspect_ratio_filter(AspectRatioMode.VERTICAL_9_16, 1080, 1920, 30, tag="_1")

        self.assertIn("split=2[bg_in_0][fg_in_0]", vf_0)
        self.assertIn("[bg_in_0]", vf_0)
        self.assertIn("[fg_in_0]", vf_0)
        self.assertIn("[bg_0][fg_0]overlay", vf_0)

        self.assertIn("split=2[bg_in_1][fg_in_1]", vf_1)
        self.assertIn("[bg_in_1]", vf_1)
        self.assertIn("[fg_in_1]", vf_1)
        self.assertIn("[bg_1][fg_1]overlay", vf_1)

    def test_square_1_1_filter(self):
        vf = build_aspect_ratio_filter(AspectRatioMode.SQUARE_1_1, 1080, 1080, 30)
        self.assertIn("scale=1080:1080:force_original_aspect_ratio=increase", vf)
        self.assertIn("crop=1080:1080", vf)
        self.assertIn("boxblur=25:5", vf)
        self.assertIn("scale=trunc(iw/2)*2:trunc(ih/2)*2", vf)
        self.assertIn("overlay=x='(W-w)/2':y='(H-h)/2'", vf)

    def test_landscape_and_source_filter(self):
        # Odd dimension input (1921x1081) conformed to even (1920x1080)
        vf = build_aspect_ratio_filter(AspectRatioMode.LANDSCAPE_16_9, 1921, 1081, 30)
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease", vf)
        self.assertIn("pad=1920:1080:(ow-iw)/2:(oh-ih)/2", vf)
        self.assertIn("settb=AVTB", vf)

    def test_audio_filter_resampling_and_loudnorm(self):
        af_loud = build_audio_filter(normalize_loudness=True)
        self.assertIn("loudnorm=I=-16:LRA=11:TP=-1.5", af_loud)
        self.assertIn("aresample=48000:async=1", af_loud)
        self.assertIn("aformat=channel_layouts=stereo:sample_fmts=fltp", af_loud)

        af_raw = build_audio_filter(normalize_loudness=False)
        self.assertNotIn("loudnorm", af_raw)
        self.assertIn("aresample=48000:async=1", af_raw)


class TestTransitionFilters(unittest.TestCase):
    """Verify seamless crossfade, xfade/acrossfade chains, and fallback logic."""

    def test_cut_mode_uses_concat(self):
        fc, v_out, a_out = build_transition_concat_filter(
            num_clips=3,
            clip_durations=[2.0, 3.0, 4.0],
            transition=TransitionMode.CUT,
        )
        self.assertEqual(fc, "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[cv][ca]")
        self.assertEqual(v_out, "[cv]")
        self.assertEqual(a_out, "[ca]")

    def test_single_clip_and_zero_clips(self):
        fc_zero, _, _ = build_transition_concat_filter(0, [])
        self.assertEqual(fc_zero, "")

        fc_single, v_out, a_out = build_transition_concat_filter(1, [5.0])
        self.assertEqual(fc_single, "[v0]settb=AVTB[cv];[a0]anull[ca]")
        self.assertEqual(v_out, "[cv]")
        self.assertEqual(a_out, "[ca]")

    def test_crossfade_xfade_and_acrossfade_chain(self):
        # 3 clips of 3.0s each with 0.4s transition
        durations = [3.0, 3.0, 3.0]
        fc, v_out, a_out = build_transition_concat_filter(
            num_clips=3,
            clip_durations=durations,
            transition=TransitionMode.CROSSFADE,
            transition_duration=0.4,
        )
        self.assertEqual(v_out, "[cv]")
        self.assertEqual(a_out, "[ca]")

        # Video:
        # Clip 0 is 3.0s, offset 1 is 3.0 - 0.4 = 2.600s
        self.assertIn("[v0][v1]xfade=transition=fade:duration=0.400:offset=2.600[x_v1]", fc)
        # Clip 1 adds 3.0 - 0.4 = 2.6s, so offset 2 is 2.600 + 2.600 = 5.200s
        self.assertIn("[x_v1][v2]xfade=transition=fade:duration=0.400:offset=5.200[cv]", fc)

        # Audio:
        self.assertIn("[a0][a1]acrossfade=d=0.400:c1=tri:c2=tri[x_a1]", fc)
        self.assertIn("[x_a1][a2]acrossfade=d=0.400:c1=tri:c2=tri[ca]", fc)

    def test_fade_black_transition(self):
        fc, _, _ = build_transition_concat_filter(
            num_clips=2,
            clip_durations=[2.0, 2.0],
            transition=TransitionMode.FADE_BLACK,
            transition_duration=0.3,
        )
        self.assertIn("xfade=transition=fadeblack:duration=0.300:offset=1.700[cv]", fc)
        self.assertIn("acrossfade=d=0.300:c1=exp:c2=exp[ca]", fc)

    def test_swipe_left_transition(self):
        fc, _, _ = build_transition_concat_filter(
            num_clips=2,
            clip_durations=[2.0, 2.0],
            transition=TransitionMode.SWIPE_LEFT,
            transition_duration=0.3,
        )
        self.assertIn("xfade=transition=slideleft:duration=0.300:offset=1.700[cv]", fc)

    def test_short_clip_graceful_fallback_to_cut(self):
        # Clips are very short (0.08s); crossfade would fail or cause visual glitch
        fc, v_out, a_out = build_transition_concat_filter(
            num_clips=2,
            clip_durations=[0.08, 0.08],
            transition=TransitionMode.CROSSFADE,
            transition_duration=0.3,
        )
        # Should cleanly fallback to hard cut concat
        self.assertIn("concat=n=2:v=1:a=1[cv][ca]", fc)
        self.assertNotIn("xfade", fc)
        self.assertNotIn("acrossfade", fc)


class TestMultiClipFilterComplex(unittest.TestCase):
    """Verify multi-clip complex filter composition."""

    def test_build_multi_clip_filter_complex(self):
        intervals = [
            ClipInterval(start=1.0, end=4.0),
            ClipInterval(start=10.0, end=13.0),
        ]
        fc, out_v, out_a = build_multi_clip_filter_complex(
            intervals=intervals,
            has_audio=True,
            target_w=1080,
            target_h=1920,
            fps=30,
            aspect_mode=AspectRatioMode.VERTICAL_9_16,
            normalize_loudness=True,
            transition=TransitionMode.CROSSFADE,
            transition_dur=0.3,
        )

        # Check trims
        self.assertIn("[0:v]trim=start=1.000:end=4.000,setpts=PTS-STARTPTS[v0_raw]", fc)
        self.assertIn("[0:a]atrim=start=1.000:end=4.000,asetpts=PTS-STARTPTS[a0_raw]", fc)
        self.assertIn("[0:v]trim=start=10.000:end=13.000,setpts=PTS-STARTPTS[v1_raw]", fc)
        self.assertIn("[0:a]atrim=start=10.000:end=13.000,asetpts=PTS-STARTPTS[a1_raw]", fc)

        # Check scoped tags for 9:16
        self.assertIn("[v0_raw]split=2[bg_in_0][fg_in_0]", fc)
        self.assertIn("[v1_raw]split=2[bg_in_1][fg_in_1]", fc)

        # Check audio resampling on each clip
        self.assertIn("[a0_raw]aresample=48000:async=1,aformat=channel_layouts=stereo:sample_fmts=fltp[a0]", fc)
        self.assertIn("[a1_raw]aresample=48000:async=1,aformat=channel_layouts=stereo:sample_fmts=fltp[a1]", fc)

        # Check crossfade
        self.assertIn("xfade=transition=fade", fc)
        self.assertIn("acrossfade=d=0.300", fc)

        # Check global audio loudnorm pass
        self.assertEqual(out_v, "[cv]")
        self.assertEqual(out_a, "[final_a]")
        self.assertIn("[ca]loudnorm=", fc)

    def test_build_multi_clip_filter_complex_without_audio(self):
        intervals = [ClipInterval(start=0.0, end=2.0)]
        fc, out_v, out_a = build_multi_clip_filter_complex(
            intervals=intervals,
            has_audio=False,
            target_w=1920,
            target_h=1080,
            fps=30,
            aspect_mode=AspectRatioMode.LANDSCAPE_16_9,
            normalize_loudness=False,
            transition=TransitionMode.CUT,
            transition_dur=0.0,
        )
        # Should pull audio from 1:a (the silent generator stream)
        self.assertIn("[1:a]atrim=start=0.000:end=2.000", fc)


class TestHardwareEncoderTuning(unittest.TestCase):
    """Verify NVENC tuning arguments, probe capability, and fallback progression."""

    def setUp(self):
        reset_encoder_cache()

    def tearDown(self):
        reset_encoder_cache()

    def test_nvenc_tuning_parameters(self):
        # Verify tuned NVENC arguments
        for args in (NVENC_HEVC_ARGS, NVENC_H264_ARGS):
            self.assertIn("-preset", args)
            self.assertIn("p6", args)
            self.assertIn("-tune", args)
            self.assertIn("hq", args)
            self.assertIn("-rc", args)
            self.assertIn("vbr", args)
            self.assertIn("-spatial-aq", args)
            self.assertIn("1", args)
            self.assertIn("-temporal-aq", args)
            self.assertIn("1", args)
            self.assertIn("-pix_fmt", args)
            self.assertIn("yuv420p", args)

        self.assertIn("-spatial-aq", NVENC_AV1_ARGS)
        self.assertIn("-pix_fmt", CPU_X264_ARGS)
        self.assertIn("-pix_fmt", CPU_X265_ARGS)

    def test_explicit_encoder_choices_include_cpu_fallbacks(self):
        hevc_attempts = get_encoder_attempts(EncoderChoice.NVENC_HEVC)
        self.assertTrue(len(hevc_attempts) >= 3)
        self.assertEqual(hevc_attempts[0][0], "hevc_nvenc (NVIDIA GPU)")
        self.assertTrue(any("CPU" in a[0] for a in hevc_attempts))

        h264_attempts = get_encoder_attempts(EncoderChoice.NVENC_H264)
        self.assertEqual(h264_attempts[0][0], "h264_nvenc (NVIDIA GPU)")
        self.assertTrue(any("CPU" in a[0] for a in h264_attempts))

        av1_attempts = get_encoder_attempts(EncoderChoice.NVENC_AV1)
        self.assertEqual(av1_attempts[0][0], "av1_nvenc (NVIDIA GPU)")
        self.assertTrue(any("CPU" in a[0] for a in av1_attempts))

        cpu_x264 = get_encoder_attempts(EncoderChoice.CPU_X264)
        self.assertEqual(len(cpu_x264), 1)
        self.assertFalse(cpu_x264[0][1])  # hwaccel is False

    def test_encoder_success_caching(self):
        chosen = ("libx264 (CPU)", False, CPU_X264_ARGS)
        register_encoder_success(chosen)

        # In AUTO mode, cached encoder must be first
        attempts = get_encoder_attempts(EncoderChoice.AUTO)
        self.assertEqual(attempts[0], chosen)

    @patch("supercut.video.encoder.is_nvenc_available", return_value=False)
    def test_auto_mode_falls_back_to_cpu_when_no_nvenc(self, mock_probe):
        reset_encoder_cache()
        attempts = get_encoder_attempts(EncoderChoice.AUTO)
        # Since mock says NVENC is not available, CPU encoders should be prioritized first
        self.assertTrue(attempts[0][0].startswith("libx264"))


class TestFFmpegExecution(unittest.TestCase):
    """End-to-end execution of generated filtergraphs with FFmpeg null sink."""

    @classmethod
    def setUpClass(cls):
        # Check if ffmpeg is on PATH
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
            cls.ffmpeg_available = True
        except Exception:
            cls.ffmpeg_available = False

    def test_execute_vertical_9_16_blur_filter_with_odd_source(self):
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        vf = build_aspect_ratio_filter(
            mode=AspectRatioMode.VERTICAL_9_16,
            target_w=1080,
            target_h=1920,
            fps=30,
        )
        # Test against synthetic source with odd dimensions 641x361
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=0.5:size=641x361:rate=30",
            "-vf",
            vf,
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, f"FFmpeg failed: {res.stderr}")

    def test_execute_multi_clip_filter_complex_with_crossfade(self):
        if not self.ffmpeg_available:
            self.skipTest("FFmpeg binary not available on system PATH")

        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sample_video = os.path.join(tmp_dir, "sample.mp4")
            # Generate 3-second sample with both video and audio
            gen_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine=duration=3:frequency=440",
                "-c:v", "libx264", "-c:a", "aac",
                sample_video,
            ]
            subprocess.run(gen_cmd, capture_output=True, check=True)

            intervals = [
                ClipInterval(start=0.0, end=1.0),
                ClipInterval(start=1.0, end=2.0),
            ]
            fc, out_v, out_a = build_multi_clip_filter_complex(
                intervals=intervals,
                has_audio=True,
                target_w=1080,
                target_h=1920,
                fps=30,
                aspect_mode=AspectRatioMode.VERTICAL_9_16,
                normalize_loudness=False,
                transition=TransitionMode.CROSSFADE,
                transition_dur=0.2,
            )

            cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                sample_video,
                "-filter_complex",
                fc,
                "-map",
                out_v,
                "-map",
                out_a,
                "-f",
                "null",
                "-",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            self.assertEqual(res.returncode, 0, f"FFmpeg execution failed: {res.stderr}")


if __name__ == "__main__":
    unittest.main()
