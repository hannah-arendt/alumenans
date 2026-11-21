#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Launching without a console.

The shortcut and "run.bat" open the program via pythonw — as a window,
with no console. There sys.stdout and sys.stderr are None. Any attempt to
print anything dies with an error, and often that happens inside someone
else's library, in its own thread. From the outside only one thing is
visible: the preparation window hangs forever and says nothing.

Through console python the same program did open — that's how the two
launch paths diverged, and that's how this got caught twice.
"""

import subprocess
import sys
import textwrap
import unittest

from alumenans.paths import PACKAGE_DIR

FILES = PACKAGE_DIR


class NoConsoleTests(unittest.TestCase):
    def test_the_program_survives_missing_streams(self):
        """A real launch in a separate process, where the streams are absent.

        They can't be swapped out inside the test: what needs checking is
        what happens at import time."""

        code = textwrap.dedent(
            """
            import sys
            sys.stdout = None
            sys.stderr = None
            sys.path.insert(0, %r)
            from alumenans import app
            assert sys.stdout is not None, "stdout не подставлен"
            assert sys.stderr is not None, "stderr не подставлен"
            print("печать не падает")
            print("и в stderr тоже", file=sys.stderr)
            raise SystemExit(0)
            """
        ) % str(FILES.parent)
        answer = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(FILES),
            timeout=180,
        )
        self.assertEqual(answer.returncode, 0, answer.stderr[-800:])

    def test_the_substitution_happens_before_anything_prints(self):
        source_text = (FILES / "app.py").read_text(encoding="utf-8")
        substitution_at = source_text.find('for _stream in ("stdout", "stderr"):')
        setup_at = source_text.find("sys.stdout.reconfigure")
        self.assertGreater(substitution_at, 0)
        self.assertLess(substitution_at, setup_at)


if __name__ == "__main__":
    unittest.main()
