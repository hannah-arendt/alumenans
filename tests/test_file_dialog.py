#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The response from the system file-selection dialog.

pywebview responds differently depending on the system: the save dialog
returns a single string, while choosing a file or folder returns a
sequence of paths. A string can be iterated like a sequence, so the bug
goes unnoticed: `selected[0]` gives not the first path but the first
character.

On Windows this never showed up — a list always comes back there. On the
Mac the first character of an absolute path is "/", and the program tried
to write the transcript to the root of the disk. The person saw
"[Errno 17] File exists: '/'" — a message from which the cause can't be
guessed.
"""

import unittest

from alumenans.paths import PACKAGE_DIR
from pathlib import Path


from alumenans.app import AppApi


class ChosenPathTests(unittest.TestCase):
    def test_a_sequence_gives_its_first_path(self):
        self.assertEqual(
            AppApi._chosen_path(("D:/Загрузки/расшифровка.txt",)),
            Path("D:/Загрузки/расшифровка.txt"),
        )

    def test_several_files_all_come_through(self):
        paths = AppApi._chosen_paths(["/дом/один.txt", "/дом/два.txt"])
        self.assertEqual([p.name for p in paths], ["один.txt", "два.txt"])

    def test_a_string_never_becomes_many_paths(self):
        paths = AppApi._chosen_paths("/дом/один.txt")
        self.assertEqual(len(paths), 1)

    def test_a_path_object_is_accepted(self):
        self.assertEqual(AppApi._chosen_path(Path("/tmp/файл.txt")), Path("/tmp/файл.txt"))

    def test_cancelling_gives_nothing(self):
        for empty in (None, "", (), []):
            with self.subTest(empty=empty):
                self.assertIsNone(AppApi._chosen_path(empty))
                self.assertEqual(AppApi._chosen_paths(empty), [])


class CallSiteTests(unittest.TestCase):
    """No selection dialog's result is parsed by hand."""

    SOURCE = (PACKAGE_DIR / "app.py").read_text(
        encoding="utf-8"
    )

    def test_no_indexing_of_the_dialog_answer(self):
        # Inside the parser itself iterating is legitimate; it's forbidden
        # in the callers, i.e. inside the bodies where a dialog is opened.
        self.assertNotIn("selected[0]", self.SOURCE)
        pieces = self.SOURCE.split("create_file_dialog(")[1:]
        for number, piece in enumerate(pieces, 1):
            following = piece[: piece.find("def ") if "def " in piece else len(piece)]
            with self.subTest(window=number):
                self.assertNotIn("for path in selected", following)

    def test_every_dialog_goes_through_the_same_parser(self):
        dialog_count = self.SOURCE.count("create_file_dialog(")
        parse_count = self.SOURCE.count("_chosen_path(") + self.SOURCE.count(
            "_chosen_paths("
        )
        # Each dialog gets its own parse; plus the two definitions of the methods themselves.
        self.assertGreaterEqual(parse_count, dialog_count)
        self.assertGreaterEqual(dialog_count, 6)


if __name__ == "__main__":
    unittest.main()
