"""Errors reach the log, wherever they happen."""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from alumenans import server
from alumenans.api import AppApi
from alumenans.storage.cipher import StudySecret


class WindowErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, body):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"X-ALumenans-Session": server.SESSION_SECRET},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status

    def test_an_error_in_the_window_is_logged(self):
        with self.assertLogs("alumenans.server", "ERROR") as logged:
            status = self.post("/page-error", {"message": "page: TypeError: x is undefined"})
        self.assertEqual(status, 204)
        self.assertIn("TypeError: x is undefined", logged.output[0])

    def test_a_failed_reply_is_logged_as_a_warning(self):
        with self.assertLogs("alumenans.server", "WARNING") as logged:
            self.post("/page-error", {"level": "warning", "message": "interview_load: no interview"})
        self.assertTrue(logged.output[0].startswith("WARNING"))

    def test_an_exception_in_a_request_is_logged_with_its_traceback(self):
        with patch("alumenans.server.find_spans", side_effect=ValueError("boom")):
            with self.assertLogs("alumenans.server", "ERROR") as logged:
                with self.assertRaises(urllib.error.HTTPError):
                    self.post("/analyze", {"text": "Text."})
        self.assertIn("Traceback", logged.output[0])
        self.assertIn("boom", logged.output[0])


class ApplicationErrorTests(unittest.TestCase):
    def test_a_request_that_raises_is_logged_with_its_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder, "interview.m4a")
            media.touch()
            api = AppApi(transcriber=None)
            api._study_folder = Path(folder)
            api._study_secret = StudySecret(os.urandom(16), os.urandom(32))
            api.selected_media = media
            api.transcriber = MagicMock()
            api.transcriber.status.return_value = {"result": {"turns": []}}
            with patch(
                "alumenans.storage.interview_store.InterviewStore.create",
                side_effect=ValueError("boom"),
            ):
                with self.assertLogs("alumenans.api", "ERROR") as logged:
                    reply = api.interview_create()
        self.assertEqual(reply, {"ok": False, "error": "boom"})
        self.assertIn("Traceback", logged.output[0])


if __name__ == "__main__":
    unittest.main()
