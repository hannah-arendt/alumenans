#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`--self-test` is what CI runs on the installed Windows program and the
unpacked macOS app, so it has to fail loudly and never open a window."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from alumenans import app, self_test
from alumenans.dependencies import missing_modules


class DispatchTests(unittest.TestCase):
    @patch("alumenans.app.start_setup_window_process")
    @patch("alumenans.app.open_window")
    @patch("alumenans.self_test.run", return_value=0)
    def test_the_flag_runs_the_self_test_and_nothing_else(self, run, window, setup):
        with patch("sys.argv", ["alumenans", app.SELF_TEST_ARG]):
            self.assertEqual(app.main(), 0)
        run.assert_called_once_with()
        window.assert_not_called()
        setup.assert_not_called()

    @patch("alumenans.self_test.run", return_value=1)
    def test_a_failure_becomes_the_exit_code(self, _run):
        with patch("sys.argv", ["alumenans", app.SELF_TEST_ARG]):
            self.assertEqual(app.main(), 1)


class RunTests(unittest.TestCase):
    def test_a_failing_check_fails_the_run_but_the_rest_still_run(self):
        ran = []

        def broken():
            raise AssertionError("boom")

        def fine():
            ran.append("fine")
            return "detail"

        checks = [("broken", broken), ("fine", fine)]
        with patch.object(self_test, "CHECKS", checks), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self_test.run(), 1)
        self.assertEqual(ran, ["fine"])
        self.assertIn("FAIL  broken", out.getvalue())
        self.assertIn("AssertionError: boom", out.getvalue())
        self.assertIn("ok    fine", out.getvalue())
        self.assertIn("self-test FAILED (1 check(s))", out.getvalue())

    @unittest.skipIf(missing_modules(), "runtime dependencies are not installed")
    def test_the_real_self_test_passes_in_a_complete_environment(self):
        with redirect_stdout(io.StringIO()) as out:
            code = self_test.run()
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
