#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import tempfile
import time
import unittest
from pathlib import Path

import os
import sys
from unittest.mock import patch

from alumenans.transcription.service import (
    ACTIVE_STATES,
    TranscriptionManager,
    worker_python,
)
from alumenans import i18n


def setUpModule():
    # These tests check Russian interface messages.
    previous = i18n.get_language()
    i18n.set_language("ru", persist=False)
    globals()["_previous_language"] = previous


def tearDownModule():
    i18n.set_language(_previous_language, persist=False)


FAKE_WORKER = r'''
import argparse
import json
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("source", type=Path)
# No --output: the manager must not ask for a file (argparse would exit 2).
parser.add_argument("--speakers", type=int, required=True)
parser.add_argument("--cancel-file", type=Path, required=True)
parser.add_argument("--guide-stdin", action="store_true")
args = parser.parse_args()

print(json.dumps({
    "type": "progress",
    "stage": "speech",
    "message": "Распознаю речь",
    "fraction": 0.5,
}, ensure_ascii=False), flush=True)

# Libraries talk on stderr, sometimes a lot: the manager must keep reading it.
if args.source.stem == "chatty":
    for number in range(20000):
        print(f"warning {number}: something a library says", file=sys.stderr)
    sys.stderr.flush()

if args.source.stem == "broken":
    print(json.dumps({"type": "progress", "stage": "speech", "message": "x"}), flush=True)
    sys.stdout.write('{"type": "result", "transcript": {"text": "обрыв')
    sys.stdout.flush()
    print("Fatal: killed", file=sys.stderr)
    sys.exit(0)

if args.source.stem == "failing":
    print("RuntimeError: the model file is damaged", file=sys.stderr, flush=True)
    sys.exit(1)

if args.source.stem == "slow":
    for _ in range(200):
        if args.cancel_file.is_file():
            print(json.dumps({
                "type": "error",
                "message": "Транскрибация остановлена.",
            }, ensure_ascii=False), flush=True)
            sys.exit(2)
        time.sleep(0.01)

result = {
    "schema": 1,
    "source_name": args.source.name,
    "duration": 4.0,
    "moderator_speaker": None,
    "requested_speakers": args.speakers,
    "guide": sys.stdin.buffer.read().decode("utf-8") if args.guide_stdin else "",
    "turns": [],
    "text": "Спикер 1: Тест.",
}
print(json.dumps({"type": "result", "transcript": result}, ensure_ascii=False), flush=True)
'''


class TranscriptionManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.worker = self.folder / "fake_worker.py"
        self.worker.write_text(FAKE_WORKER, encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def wait(manager, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = manager.status()
            if state["status"] not in ACTIVE_STATES:
                return state
            time.sleep(0.02)
        raise AssertionError("The manager did not finish in time")

    def test_collects_progress_and_result_from_worker(self):
        source = self.folder / "sample.mp3"
        source.touch()
        manager = TranscriptionManager(self.worker)

        self.assertEqual(manager.start(source, 4), {"ok": True})
        state = self.wait(manager)

        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["text"], "Спикер 1: Тест.")
        self.assertEqual(state["result"]["requested_speakers"], 4)
        self.assertEqual(state["requested_speakers"], 4)
        self.assertEqual(state["source_name"], "sample.mp3")

    def test_a_chatty_stderr_does_not_stall_the_worker(self):
        source = self.folder / "chatty.mp3"
        source.touch()
        manager = TranscriptionManager(self.worker)

        manager.start(source, 2)
        state = self.wait(manager, timeout=20)

        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["text"], "Спикер 1: Тест.")

    def test_a_cut_off_result_is_an_error_not_a_transcript(self):
        source = self.folder / "broken.mp3"
        source.touch()
        manager = TranscriptionManager(self.worker)

        manager.start(source, 2)
        state = self.wait(manager)

        self.assertEqual(state["status"], "error")
        self.assertIsNone(state["result"])
        self.assertIn("без результата", state["error"])

    def test_stderr_explains_a_failure(self):
        source = self.folder / "failing.mp3"
        source.touch()
        manager = TranscriptionManager(self.worker)

        manager.start(source, 2)
        state = self.wait(manager)

        self.assertEqual(state["status"], "error")
        self.assertIn("the model file is damaged", state["error"])

    def test_long_guide_reaches_the_worker_over_stdin(self):
        source = self.folder / "sample.mp3"
        source.touch()
        manager = TranscriptionManager(self.worker)
        guide = "\n".join(f"{number}. Расскажите о работе?" for number in range(20000))

        self.assertEqual(manager.start(source, 2, guide), {"ok": True})
        state = self.wait(manager, timeout=20)

        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["guide"], guide)

    def test_passes_guide_to_worker(self):
        source = self.folder / "sample.mp3"
        source.touch()
        manager = TranscriptionManager(self.worker)

        self.assertEqual(
            manager.start(source, 2, "1. Расскажите о работе?"),
            {"ok": True},
        )
        state = self.wait(manager)

        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["guide"], "1. Расскажите о работе?")

    def test_cancellation_is_forwarded_to_worker(self):
        source = self.folder / "slow.wav"
        source.touch()
        manager = TranscriptionManager(self.worker)

        self.assertEqual(manager.start(source), {"ok": True})
        deadline = time.monotonic() + 2
        while manager.status()["stage"] == "starting" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(manager.cancel(), {"ok": True})
        state = self.wait(manager)

        self.assertEqual(state["status"], "cancelled")
        self.assertIsNone(state["result"])
        self.assertGreater(state["overall_fraction"], 0)

    def test_rejects_invalid_speaker_count(self):
        source = self.folder / "sample.ogg"
        source.touch()
        manager = TranscriptionManager(self.worker)

        response = manager.start(source, 11)

        self.assertFalse(response["ok"])
        self.assertIn("от 1 до 10", response["error"])

    def test_a_single_voice_is_allowed(self):
        # A monologue is an ordinary case: a story without an interviewer, dictation.
        source = self.folder / "sample.ogg"
        source.touch()
        manager = TranscriptionManager(self.worker)

        self.assertTrue(manager.start(source, 1)["ok"])

    def test_progress_never_moves_backwards(self):
        manager = TranscriptionManager(self.worker)
        manager._generation = 1
        manager._state = manager._idle_state()
        manager._state["status"] = "running"

        manager._set_progress(
            1,
            {"stage": "speakers", "message": "Первый шаг", "fraction": 0.8},
        )
        high = manager.status()["overall_fraction"]
        manager._set_progress(
            1,
            {"stage": "speakers", "message": "Повтор", "fraction": 0.2},
        )

        self.assertEqual(manager.status()["overall_fraction"], high)


@unittest.skipIf(os.name == "nt", "symlinked interpreters are a POSIX venv thing")
class WorkerPythonTests(unittest.TestCase):
    def test_a_venv_symlink_is_not_followed(self):
        """In a venv, python is a symlink to the base interpreter.

        Following it starts child processes outside the venv, without its
        packages: `make run` failed with "No module named 'natasha'"."""

        with tempfile.TemporaryDirectory() as folder:
            link = Path(folder) / "bin" / "python"
            link.parent.mkdir()
            link.symlink_to(sys.executable)
            with patch("alumenans.transcription.service.sys.executable", str(link)):
                self.assertEqual(worker_python(), link)


if __name__ == "__main__":
    unittest.main()
