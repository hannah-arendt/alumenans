#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Running speech recognition and speaker diarization at the same time.

The time saved isn't the main thing here. The main thing is that running
in parallel doesn't break everything else: stopping via the button, error
messages, and the progress bar, which mustn't jerk around from two sources
at once.
"""

import threading
import time
import unittest


from alumenans.transcription.core import TranscriptionCancelled, TranscriptionError
from alumenans.transcription.worker import (
    DIARIZATION_SHARE,
    JsonProgress,
    MergedProgress,
    _run_together,
)
from alumenans import i18n


def setUpModule():
    # These tests check Russian interface messages.
    previous = i18n.get_language()
    i18n.set_language("ru", persist=False)
    globals()["_previous_language"] = previous


def tearDownModule():
    i18n.set_language(_previous_language, persist=False)


class RecordingProgress(JsonProgress):
    """Records events instead of printing them to stdout."""

    def __init__(self):
        self.events = []

    def emit(self, stage, message, fraction=None, **details):
        self.events.append((stage, message, fraction))


class MergedProgressTests(unittest.TestCase):
    def setUp(self):
        self.sink = RecordingProgress()
        self.merged = MergedProgress(self.sink)

    def test_two_stages_become_one(self):
        self.merged.emit("speakers", "Различаю говорящих.", 0.5)
        self.merged.emit("speech", "Распознаю речь", 0.5)
        stages = {stage for stage, _, _ in self.sink.events}
        self.assertEqual(stages, {"listening"})

    def test_message_names_everything_still_running(self):
        self.merged.emit("speakers", "Различаю говорящих.", 0.3)
        self.assertEqual(self.sink.events[-1][1], "Различаю говорящих и распознаю речь")
        self.merged.finished("speakers")
        self.merged.emit("speech", "Распознаю речь", 0.4)
        self.assertEqual(self.sink.events[-1][1], "Распознаю речь")

    def test_progress_is_weighted_by_cost(self):
        self.merged.emit("speakers", "Голоса", 1.0)
        self.assertAlmostEqual(self.sink.events[-1][2], DIARIZATION_SHARE)
        self.merged.emit("speech", "Речь", 1.0)
        self.assertAlmostEqual(self.sink.events[-1][2], 1.0)

    def test_progress_never_moves_backwards(self):
        self.merged.emit("speech", "Речь", 0.8)
        high = self.sink.events[-1][2]
        self.merged.emit("speech", "Речь", 0.2)
        self.assertGreaterEqual(self.sink.events[-1][2], high)

    def test_after_both_stages_the_message_passes_through(self):
        self.merged.finished("speakers")
        self.merged.finished("speech")
        self.merged.emit("speech", "Собираю расшифровку.", 1.0)
        self.assertEqual(self.sink.events[-1][1], "Собираю расшифровку.")

    def test_finished_stage_counts_as_complete(self):
        self.merged.finished("speakers")
        self.merged.emit("speech", "Речь", 0.0)
        self.assertAlmostEqual(self.sink.events[-1][2], DIARIZATION_SHARE)

    def test_other_methods_still_reach_the_real_progress(self):
        # The wrapper replaces only emit; everything else must work as before.
        self.assertIs(self.merged.error.__self__, self.sink)
        self.assertIs(self.merged.result.__self__, self.sink)


class RunTogetherTests(unittest.TestCase):
    def test_both_jobs_run_and_results_are_kept(self):
        def slow(value):
            def action(_guard):
                time.sleep(0.05)
                return value
            return action

        result = _run_together({"a": slow(1), "b": slow(2)}, None)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_jobs_really_overlap_in_time(self):
        started = threading.Barrier(2, timeout=2)

        def action(_guard):
            started.wait()
            return True

        # The barrier releases only if both stages run at the same time.
        result = _run_together({"a": action, "b": action}, None)
        self.assertEqual(result, {"a": True, "b": True})

    def test_failure_in_one_job_stops_the_other(self):
        seen = []

        def failing(_guard):
            raise TranscriptionError("Модель не открылась.")

        def watching(guard):
            for _ in range(200):
                if guard():
                    seen.append("остановлен")
                    return "остановлен"
                time.sleep(0.01)
            return "досчитал"

        with self.assertRaises(TranscriptionError):
            _run_together({"bad": failing, "good": watching}, None)
        self.assertEqual(seen, ["остановлен"])

    def test_cancellation_wins_over_another_error(self):
        def cancelled(_guard):
            raise TranscriptionCancelled("Транскрибация остановлена.")

        def broken(_guard):
            time.sleep(0.02)
            raise TranscriptionError("Что-то сломалось.")

        # The person pressed «остановить» ("stop") — that's not a failure, and
        # that's exactly what must come out, otherwise the window shows an
        # error instead of a stop.
        with self.assertRaises(TranscriptionCancelled):
            _run_together({"a": cancelled, "b": broken}, None)

    def test_external_cancel_reaches_every_job(self):
        flag = {"stop": False}
        seen = []

        def watching(guard):
            for _ in range(200):
                if guard():
                    seen.append(True)
                    raise TranscriptionCancelled("Остановлено.")
                time.sleep(0.01)
            return "досчитал"

        def stopper(_guard):
            time.sleep(0.03)
            flag["stop"] = True
            return "готово"

        with self.assertRaises(TranscriptionCancelled):
            _run_together(
                {"watch": watching, "stop": stopper},
                lambda: flag["stop"],
            )
        self.assertEqual(seen, [True])

    def test_first_real_error_is_reported(self):
        def broken(_guard):
            raise TranscriptionError("Не найдена модель распознавания.")

        def fine(_guard):
            return "готово"

        with self.assertRaises(TranscriptionError) as raised:
            _run_together({"bad": broken, "good": fine}, None)
        self.assertIn("модель", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
