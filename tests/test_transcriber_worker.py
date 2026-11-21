#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from alumenans.transcription.core import DiarizedSpan, TimedWord
from alumenans.transcription.worker import (
    JsonProgress,
    diarize,
    build_turns,
    recognize_words,
    refine_dialogue_speakers,
)


class DiarizationWorkerTests(unittest.TestCase):
    @patch("alumenans.transcription.worker._cluster_speaker_embeddings")
    @patch("alumenans.transcription.worker._embedding_for_range")
    @patch("alumenans.transcription.worker._speaker_embedding_extractor")
    @patch("alumenans.transcription.worker._diarization_speech_ranges")
    @patch("alumenans.transcription.worker._load_diarization_vad")
    def test_voices_are_separated_frame_by_frame(
        self,
        load_vad,
        speech_ranges,
        extractor,
        embedding,
        cluster,
    ):
        """Labelling uses frequent overlapping frames, not a single window.

        The window used to be 2.2 seconds and decided on its own; because of
        that the boundary between voices landed on the edge of the window,
        and a short turn drowned entirely inside someone else's."""

        paths = object()
        load_vad.return_value = object()
        speech_ranges.return_value = [(0.0, 10.0)]
        extractor.return_value = object()

        first = np.asarray([1.0, 0.0], dtype=np.float32)
        second = np.asarray([0.0, 1.0], dtype=np.float32)
        # Twenty frames: the first fifteen are one voice, the rest another.
        embedding.side_effect = [first] * 15 + [second] * 5
        cluster.side_effect = lambda vectors, *, speaker_count: [
            0 if float(vector[0]) > 0.5 else 1 for vector in vectors
        ]

        spans = diarize(
            [0.0] * 160_000,
            paths,
            speaker_count=2,
            progress=JsonProgress(),
        )

        load_vad.assert_called_once_with(paths)
        cluster.assert_called_once()
        self.assertEqual(cluster.call_args.kwargs["speaker_count"], 2)
        self.assertEqual({span.speaker for span in spans}, {1, 2})
        # Many frames, but exactly two segments: there's one change of voice.
        self.assertGreater(embedding.call_count, 10)
        self.assertEqual(len(spans), 2)

    @patch("alumenans.transcription.worker._cluster_speaker_embeddings")
    @patch("alumenans.transcription.worker._embedding_for_range")
    @patch("alumenans.transcription.worker._speaker_embedding_extractor")
    @patch("alumenans.transcription.worker._diarization_speech_ranges")
    @patch("alumenans.transcription.worker._load_diarization_vad")
    def test_single_stray_frame_does_not_break_a_monologue(
        self,
        load_vad,
        speech_ranges,
        extractor,
        embedding,
        cluster,
    ):
        """A blurry frame in the middle of a monologue isn't worth a split."""

        load_vad.return_value = object()
        speech_ranges.return_value = [(0.0, 10.0)]
        extractor.return_value = object()

        first = np.asarray([1.0, 0.0], dtype=np.float32)
        blurred = np.asarray([0.45, 0.55], dtype=np.float32)
        embedding.side_effect = [first] * 9 + [blurred] + [first] * 10
        cluster.side_effect = lambda vectors, *, speaker_count: [
            0 if float(vector[0]) > 0.5 else 1 for vector in vectors
        ]

        spans = diarize(
            [0.0] * 160_000,
            paths=object(),
            speaker_count=2,
            progress=JsonProgress(),
        )

        self.assertEqual(len(spans), 1)


class RecognitionWorkerTests(unittest.TestCase):
    @patch("alumenans.transcription.worker._speaker_embedding_extractor")
    def test_phrase_crossing_a_voice_boundary_is_not_collapsed(
        self,
        extractor,
    ):
        overrides = refine_dialogue_speakers(
            np.zeros(5 * 16_000, dtype=np.float32),
            [
                TimedWord(1.0, 1.8, "Сколько"),
                TimedWord(1.8, 2.2, "лет,"),
                TimedWord(2.2, 2.8, "Мне"),
                TimedWord(2.8, 3.2, "тридцать."),
            ],
            [
                DiarizedSpan(0.0, 2.1, 1),
                DiarizedSpan(2.1, 5.0, 2),
            ],
            object(),
        )

        self.assertEqual(overrides, [None])
        extractor.assert_not_called()

    @patch("alumenans.transcription.worker._normalized_embedding")
    @patch("alumenans.transcription.worker._speaker_reference_ranges")
    @patch("alumenans.transcription.worker._speaker_embedding_extractor")
    def test_short_phrase_without_dialogue_dash_is_checked_by_voice(
        self,
        extractor,
        reference_ranges,
        normalized_embedding,
    ):
        extractor.return_value = object()
        reference_ranges.return_value = {
            1: [(0.0, 2.0)],
            2: [(2.0, 4.0)],
        }
        normalized_embedding.side_effect = [
            np.asarray([1.0, 0.0], dtype=np.float32),
            np.asarray([0.0, 1.0], dtype=np.float32),
            np.asarray([0.0, 1.0], dtype=np.float32),
        ]

        overrides = refine_dialogue_speakers(
            np.zeros(12 * 16_000, dtype=np.float32),
            [TimedWord(9.0, 10.0, "Да.")],
            [
                DiarizedSpan(0.0, 4.0, 1),
                DiarizedSpan(4.0, 8.5, 2),
                DiarizedSpan(8.5, 10.0, 1),
            ],
            object(),
        )

        self.assertEqual(overrides, [2])

    def test_audio_is_recognized_once_before_words_are_assigned_to_speakers(self):
        class Model:
            def __init__(self):
                self.calls = []

            def with_timestamps(self):
                return self

            def recognize(self, samples, *, sample_rate):
                self.calls.append((len(samples), sample_rate))
                self.sample_rate = sample_rate
                return SimpleNamespace(
                    text="Первая реплика. Вторая реплика.",
                    tokens=[
                        " Первая",
                        " реплика",
                        ".",
                        " Вторая",
                        " реплика",
                        ".",
                    ],
                    timestamps=[1, 3, 4, 12, 14, 15],
                )

        class Vad:
            @staticmethod
            def segment_batch(
                _samples,
                lengths,
                _sample_rate,
                **_kwargs,
            ):
                return iter([iter([(0, int(lengths[0]))])])

        model = Model()
        fake_onnx_asr = SimpleNamespace(
            load_model=lambda *_args, **_kwargs: model,
            load_vad=lambda *_args, **_kwargs: Vad(),
        )
        samples = np.zeros(23 * 16_000, dtype=np.float32)
        paths = SimpleNamespace(asr=Path("."), vad=Path("."))

        spans = [DiarizedSpan(0, 10, 1), DiarizedSpan(10, 23, 2)]
        with patch.dict(sys.modules, {"onnx_asr": fake_onnx_asr}):
            # Recognition no longer knows about speakers: they're assigned in
            # a separate step, so these two stages can be computed at once.
            words = recognize_words(samples, paths, progress=JsonProgress())
        turns = build_turns(
            samples, words, spans, paths, progress=JsonProgress()
        )

        self.assertEqual(model.calls, [(23 * 16_000, 16_000)])
        self.assertEqual(
            [(turn.speaker, turn.text) for turn in turns],
            [(1, "Первая реплика."), (2, "Вторая реплика.")],
        )




PROTOCOL_PROBE = r'''
import os, sys
from alumenans.transcription.worker import JsonProgress, open_protocol_channel

progress = JsonProgress(open_protocol_channel())
print("a library talking through Python")
os.write(1, b"a native library writing to descriptor 1\n")
progress.send({"type": "result", "transcript": {"text": "Меня зовут Мария."}})
'''


class ProtocolChannelTests(unittest.TestCase):
    """Only the protocol reaches stdout; whatever else is printed goes to stderr."""

    def test_library_output_cannot_land_in_the_result(self):
        import json
        import subprocess

        from alumenans.paths import INSTALL_ROOT, child_environment

        answer = subprocess.run(
            [sys.executable, "-c", PROTOCOL_PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(INSTALL_ROOT),
            env=child_environment(),
            timeout=60,
        )

        self.assertEqual(answer.returncode, 0, answer.stderr)
        lines = answer.stdout.splitlines()
        self.assertEqual(len(lines), 1, answer.stdout)
        self.assertEqual(json.loads(lines[0])["transcript"]["text"], "Меня зовут Мария.")
        self.assertIn("a library talking through Python", answer.stderr)
        self.assertIn("a native library writing to descriptor 1", answer.stderr)




def _tone_file(path, seconds, rate=44100, channels=2):
    """A short real recording in a format the worker must convert."""

    import math
    import struct
    import wave

    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(rate)
        frames = bytearray()
        for index in range(int(seconds * rate)):
            value = int(8000 * math.sin(2 * math.pi * 440 * index / rate))
            frames += struct.pack("<h", value) * channels
        target.writeframes(bytes(frames))


class SilentProgress:
    def emit(self, *args, **kwargs):
        pass


class DecodingTests(unittest.TestCase):
    """The decoded sound comes through a pipe, straight into memory."""

    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_a_recording_becomes_16_khz_mono_samples(self):
        from alumenans.transcription.worker import decode_media_parts

        source = self.folder / "interview.wav"
        _tone_file(source, 1.5)

        samples, offsets = decode_media_parts(
            [source], duration=1.5, progress=SilentProgress()
        )

        self.assertEqual(str(samples.dtype), "float32")
        self.assertAlmostEqual(len(samples) / 16000, 1.5, delta=0.02)
        self.assertGreater(float(abs(samples).max()), 0.1)
        self.assertLessEqual(float(abs(samples).max()), 1.0)
        self.assertEqual(offsets, [0.0])
        # Nothing but the recording itself is left in the folder.
        self.assertEqual(sorted(item.name for item in self.folder.iterdir()), ["interview.wav"])

    def test_parts_are_joined_and_their_starts_kept(self):
        from alumenans.transcription.worker import decode_media_parts

        first, second = self.folder / "part1.wav", self.folder / "part2.wav"
        _tone_file(first, 1.0)
        _tone_file(second, 0.5, rate=8000, channels=1)

        samples, offsets = decode_media_parts(
            [first, second], duration=1.5, progress=SilentProgress()
        )

        self.assertAlmostEqual(len(samples) / 16000, 1.5, delta=0.02)
        self.assertEqual(offsets[0], 0.0)
        self.assertAlmostEqual(offsets[1], 1.0, delta=0.02)

    def test_an_unknown_duration_still_fits(self):
        from alumenans.transcription.worker import decode_media_parts

        source = self.folder / "interview.wav"
        _tone_file(source, 2.0)

        samples, _ = decode_media_parts([source], duration=None, progress=SilentProgress())

        self.assertAlmostEqual(len(samples) / 16000, 2.0, delta=0.02)

    def test_a_file_without_sound_is_an_error(self):
        from alumenans.transcription.core import TranscriptionError
        from alumenans.transcription.worker import decode_media_parts

        source = self.folder / "notes.wav"
        source.write_bytes(b"this is not a recording at all" * 100)

        with self.assertRaises(TranscriptionError):
            decode_media_parts([source], duration=None, progress=SilentProgress())

    def test_cancelling_stops_the_decoding(self):
        from alumenans.transcription.core import TranscriptionCancelled
        from alumenans.transcription.worker import decode_media_parts

        source = self.folder / "interview.wav"
        _tone_file(source, 1.0)

        with self.assertRaises(TranscriptionCancelled):
            decode_media_parts(
                [source], duration=1.0, progress=SilentProgress(), cancel=lambda: True
            )


class PcmBufferTests(unittest.TestCase):
    def test_bytes_split_mid_sample_are_joined(self):
        from alumenans.transcription.worker import PcmBuffer

        buffer = PcmBuffer(None)
        raw = (16384).to_bytes(2, "little", signed=True) * 3
        buffer.write(raw[:3])
        buffer.write(raw[3:])

        self.assertEqual(buffer.length, 3)
        self.assertEqual([float(value) for value in buffer.samples()], [0.5, 0.5, 0.5])

    def test_the_buffer_grows_past_a_wrong_guess(self):
        from alumenans.transcription.worker import PcmBuffer

        buffer = PcmBuffer(0.0)
        buffer.write(b"\x00\x40" * 40000)

        self.assertEqual(buffer.length, 40000)


if __name__ == "__main__":
    unittest.main()
