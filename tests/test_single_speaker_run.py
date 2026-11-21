#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A recording with a single voice really does get transcribed.

Allowing one speaker in two places wasn't enough. The separate
transcription process parses its command-line options with its own list
of allowed values, and one wasn't in it. Option parsing aborted with exit
code 2, and in the program that code meant "the person pressed stop". The
person pressed «Транскрибировать» ("Transcribe") and immediately got
«Транскрибация остановлена» ("Transcription stopped") without a single
word about the reason.

Hence two checks: one passes option parsing, and only a stop that was
asked for counts as a stop.
"""

import subprocess
import sys
import unittest

from alumenans.paths import INSTALL_ROOT, PACKAGE_DIR, child_environment
from alumenans.transcription.core import MAX_SPEAKERS, MIN_SPEAKERS
from alumenans import i18n


def setUpModule():
    # These tests check Russian interface messages.
    previous = i18n.get_language()
    i18n.set_language("ru", persist=False)
    globals()["_previous_language"] = previous


def tearDownModule():
    i18n.set_language(_previous_language, persist=False)

FILES = PACKAGE_DIR


def run_worker(count):
    """A real run of the process, not a reading of the source.

    The recording file is missing on purpose: things won't get as far as
    reading audio, and option parsing will be over by that point."""

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alumenans.transcription.worker",
            "transcribe",
            "нет-такого-файла.m4a",
            "--output",
            "нет-такого-файла.json",
            "--speakers",
            str(count),
        ],
        capture_output=True,
        text=True,
        cwd=str(INSTALL_ROOT),
        env=child_environment(),
        timeout=120,
    )


class ArgumentTests(unittest.TestCase):
    def test_one_speaker_passes_the_argument_check(self):
        answer = run_worker(1)
        # 2 is the option-parsing code. Any other means it got as far as the
        # actual work: here, a complaint about the missing file.
        self.assertNotEqual(answer.returncode, 2, answer.stderr[:300])
        self.assertIn("Выбранный файл не найден", answer.stdout + answer.stderr)

    def test_the_allowed_range_matches_the_rest_of_the_program(self):
        source_text = (FILES / "transcription" / "worker.py").read_text(encoding="utf-8")
        self.assertIn("choices=range(MIN_SPEAKERS, MAX_SPEAKERS + 1)", source_text)
        self.assertEqual(MIN_SPEAKERS, 1)
        self.assertEqual(MAX_SPEAKERS, 10)

    def test_a_bad_number_still_stops_the_run(self):
        self.assertEqual(run_worker(0).returncode, 2)


class ExitCodeTests(unittest.TestCase):
    """Exit code 2 is no longer passed off as a stop by the person."""

    SOURCE = (FILES / "transcription" / "service.py").read_text(encoding="utf-8")

    def test_only_a_requested_stop_counts_as_a_stop(self):
        self.assertNotIn("if was_cancelled or return_code == 2:", self.SOURCE)
        self.assertIn("if was_cancelled:", self.SOURCE)

    def test_the_process_output_is_kept_for_the_message(self):
        # Everything the process wrote outside our format used to be thrown
        # away, and the cause of the failure was lost along with it.
        self.assertIn("noise.append(line)", self.SOURCE)
        self.assertIn('" ".join(noise[-3:])', self.SOURCE)


if __name__ == "__main__":
    unittest.main()
