#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The program folder disappeared while it was running.

A sociologist on a Mac had launched the app from Downloads. On the very
first transcription it responded:

    [Errno 2] No such file or directory:
    '/Users/.../Downloads/ALumenans.app/Contents/Resources/files'

That's what starting a child process looks like when its working folder
no longer exists. On the Mac, an app opened from Downloads is run by the
system from its own copy, which it removes without warning. The second
launch went fine — the copy was already gone.

The child process doesn't need a working folder: all paths are passed to
it in full. And if the program's own files have vanished too, the person
should read what happened, not an error number.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from alumenans.paths import PACKAGE_DIR

FILES = PACKAGE_DIR

from alumenans.transcription.core import existing_directory
from alumenans.transcription.service import TranscriptionManager
from alumenans import i18n


def setUpModule():
    # These tests check Russian interface messages.
    previous = i18n.get_language()
    i18n.set_language("ru", persist=False)
    globals()["_previous_language"] = previous


def tearDownModule():
    i18n.set_language(_previous_language, persist=False)


class WorkingDirectoryTests(unittest.TestCase):
    def test_a_real_directory_is_used(self):
        self.assertEqual(existing_directory(FILES), str(FILES))

    def test_a_missing_directory_gives_nothing(self):
        self.assertIsNone(existing_directory(FILES / "такой-папки-нет"))

    def test_a_file_is_not_a_directory(self):
        self.assertIsNone(existing_directory(FILES / "app.py"))

    def test_a_process_starts_without_a_working_directory(self):
        # This is exactly how the process gets started when the folder is gone.
        answer = subprocess.run(
            [sys.executable, "-c", "print('живой')"],
            cwd=existing_directory(FILES / "такой-папки-нет"),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(answer.returncode, 0, answer.stderr[:300])

    def test_every_child_process_asks_the_same_question(self):
        for name in ("app.py", "transcription/service.py"):
            source_text = (FILES / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotIn("cwd=str(INSTALL_ROOT)", source_text)
                self.assertIn("cwd=existing_directory(INSTALL_ROOT)", source_text)


class MessageTests(unittest.TestCase):
    def test_the_person_reads_words_not_an_error_number(self):
        with tempfile.TemporaryDirectory() as folder:
            missing_worker = Path(folder) / "transcriber_worker.py"
            manager = TranscriptionManager(missing_worker)
            manager._generation = 1
            manager._state = manager._idle_state()
            manager._run(1, [], 2, "")
        state = manager.status()
        self.assertEqual(state["status"], "error")
        self.assertIn("Загрузки", state["error"])
        self.assertIn("Программы", state["error"])
        self.assertNotIn("Errno", state["error"])


if __name__ == "__main__":
    unittest.main()
