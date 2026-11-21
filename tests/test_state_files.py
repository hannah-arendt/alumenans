#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""State files next to the program got English names in 0.3.0.

Existing installs still have the Russian-named files; they must be picked
up, not silently reset (a lost first-run mark re-runs the long setup, a
lost researcher name unsigns future codes)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alumenans import paths


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.patch = patch.object(paths, "INSTALL_ROOT", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.folder.cleanup()

    def test_a_legacy_file_is_renamed_and_keeps_its_content(self):
        (self.root / ".исследователь").write_text("Анна", encoding="utf-8")
        path = paths.state_file(".researcher", legacy=".исследователь")
        self.assertEqual(path, self.root / ".researcher")
        self.assertEqual(path.read_text(encoding="utf-8"), "Анна")
        self.assertFalse((self.root / ".исследователь").exists())

    def test_the_new_file_wins_over_a_leftover_legacy_one(self):
        (self.root / ".last-folder").write_text("new", encoding="utf-8")
        (self.root / ".последняя-папка").write_text("old", encoding="utf-8")
        path = paths.state_file(".last-folder", legacy=".последняя-папка")
        self.assertEqual(path.read_text(encoding="utf-8"), "new")

    def test_nothing_on_disk_gives_the_new_name(self):
        self.assertEqual(paths.state_file(".installed", legacy=".установлено"),
                         self.root / ".installed")

    def test_a_read_only_folder_keeps_using_the_legacy_file(self):
        (self.root / ".установлено").write_text("готово", encoding="utf-8")
        with patch.object(Path, "replace", side_effect=OSError("read-only")):
            path = paths.state_file(".installed", legacy=".установлено")
        self.assertEqual(path, self.root / ".установлено")


if __name__ == "__main__":
    unittest.main()
