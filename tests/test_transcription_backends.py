import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class FakeFasterModel:
    def transcribe(self, audio_path, **kwargs):
        self.call = (audio_path, kwargs)
        segments = [
            SimpleNamespace(
                end=2.0,
                words=[
                    SimpleNamespace(word=" Hello,", start=0.2, end=0.7),
                    SimpleNamespace(word="WORLD!", start=0.8, end=1.4),
                ],
            )
        ]
        return iter(segments), SimpleNamespace(duration=2.5)


class FakeCrisperModel:
    def transcribe(self, audio_path, **kwargs):
        self.call = (audio_path, kwargs)
        return SimpleNamespace(
            duration=3.0,
            words=[
                SimpleNamespace(word=" Sister's", start=0.4, end=1.0),
                SimpleNamespace(word="", start=1.1, end=1.2),
                SimpleNamespace(word="test.", start=1.3, end=1.8),
            ],
        )


class TranscriptionBackendTests(unittest.TestCase):
    def test_faster_whisper_adapter_flattens_segment_words(self):
        transcriber = object.__new__(main.FasterWhisperTranscriber)
        transcriber.model = FakeFasterModel()
        progress = []

        words, duration = transcriber.transcribe_words(
            "sample.wav", 99.0, lambda value, message: progress.append((value, message))
        )

        self.assertEqual(
            words,
            [(0.2, 0.7, "hello"), (0.8, 1.4, "world")],
        )
        self.assertEqual(duration, 2.5)
        self.assertEqual(
            transcriber.model.call,
            ("sample.wav", {"beam_size": 5, "word_timestamps": True}),
        )
        self.assertTrue(progress)

    def test_crisper_adapter_uses_english_word_timestamps(self):
        transcriber = object.__new__(main.CrisperWhisperTranscriber)
        transcriber.model = FakeCrisperModel()
        progress = []

        words, duration = transcriber.transcribe_words(
            "sample.wav", 99.0, lambda value, message: progress.append((value, message))
        )

        self.assertEqual(
            words,
            [(0.4, 1.0, "sisters"), (1.3, 1.8, "test")],
        )
        self.assertEqual(duration, 3.0)
        self.assertEqual(
            transcriber.model.call,
            ("sample.wav", {"language": "en", "word_timestamps": True}),
        )
        self.assertEqual(progress[-1][0], 1.0)

    def test_only_explicit_crisper_token_selects_new_backend(self):
        self.assertTrue(main.is_crisper_model("crisper-turbo"))
        self.assertTrue(main.is_crisper_model(" CRISPER-TURBO "))
        self.assertFalse(main.is_crisper_model("large-v3"))
        self.assertFalse(main.is_crisper_model("turbo"))

    def test_ffmpeg_watchdog_terminates_a_silent_stalled_process(self):
        started = time.monotonic()
        python_executable = getattr(sys, "_base_executable", sys.executable)
        code, output = main.run_ffmpeg(
            [python_executable, "-u", "-c", "import time; time.sleep(10)"],
            stall_timeout=0.1,
        )

        self.assertNotEqual(code, 0)
        self.assertIn("terminated the stalled attempt", output)
        self.assertLess(time.monotonic() - started, 5.0)

    def test_batch_reencode_reaches_cpu_after_nvenc_failure(self):
        attempts = [
            ("hevc_nvenc", False, ["-c:v", "hevc_nvenc"]),
            ("libx264 (cpu fallback)", False, ["-c:v", "libx264"]),
        ]
        results = [(-1, "copy failed"), (-1, "nvenc failed"), (0, "")]

        with tempfile.TemporaryDirectory() as output_dir:
            with (
                patch.object(main, "run_ffmpeg", side_effect=results) as run_mock,
                patch.object(main, "encoder_attempts", return_value=attempts),
                patch.object(main, "note_encoder_success") as success_mock,
                patch.object(
                    main,
                    "probe_video",
                    return_value=main.MediaInfo(duration=1.0),
                ),
            ):
                output = main.stitch_batch(
                    ["one.mp4", "two.mp4"], output_dir, lambda *_: None
                )

        self.assertTrue(output.endswith("FINAL_BATCH_STITCHED.mp4"))
        self.assertEqual(run_mock.call_count, 3)
        success_mock.assert_called_once_with(attempts[-1])


if __name__ == "__main__":
    unittest.main()
