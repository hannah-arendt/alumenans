#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testing how audio is served to the proofreading tab.

Without Range requests the WebView downloads the whole file and can't seek:
for a two-hour recording that's the difference between a working player and
a broken one.
"""

import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


from alumenans import current_audio, server
from alumenans.server import parse_byte_range


class ByteRangeTests(unittest.TestCase):
    def test_plain_range(self):
        self.assertEqual(parse_byte_range("bytes=100-199", 1000), (100, 199))

    def test_open_ended_range(self):
        self.assertEqual(parse_byte_range("bytes=900-", 1000), (900, 999))

    def test_suffix_range(self):
        self.assertEqual(parse_byte_range("bytes=-100", 1000), (900, 999))

    def test_range_beyond_the_file(self):
        self.assertEqual(parse_byte_range("bytes=5000-", 1000), "unsatisfiable")

    def test_range_end_is_clamped(self):
        self.assertEqual(parse_byte_range("bytes=900-99999", 1000), (900, 999))

    def test_unusable_headers_are_ignored(self):
        for header in (None, "", "bytes=", "items=0-10", "bytes=abc-def"):
            self.assertIsNone(parse_byte_range(header, 1000))

    def test_backwards_range_is_ignored(self):
        self.assertIsNone(parse_byte_range("bytes=500-100", 1000))


class AudioEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="alumenans-audio-")
        cls.media = Path(cls.folder.name) / "запись.mp3"
        cls.payload = bytes(range(256)) * 64
        cls.media.write_bytes(cls.payload)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        current_audio.set_current_audio(None)
        cls.folder.cleanup()

    def setUp(self):
        current_audio.set_current_audio(self.media)

    def request(self, headers=None, method="GET"):
        request = urllib.request.Request(
            f"{self.base}/audio?session={server.SESSION_SECRET}",
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_whole_file_is_served(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(headers["Content-Type"], "audio/mpeg")

    def test_partial_content_matches_the_file(self):
        status, headers, body = self.request({"Range": "bytes=1000-1099"})
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], f"bytes 1000-1099/{len(self.payload)}")
        self.assertEqual(body, self.payload[1000:1100])

    def test_range_beyond_the_file_is_refused(self):
        status, headers, _ = self.request({"Range": "bytes=99999-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], f"bytes */{len(self.payload)}")

    def test_head_reports_the_size_without_the_body(self):
        status, headers, body = self.request(method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Length"], str(len(self.payload)))
        self.assertEqual(body, b"")

    def test_nothing_is_served_when_no_recording_is_chosen(self):
        current_audio.set_current_audio(None)
        status, _, _ = self.request()
        self.assertEqual(status, 404)

    def raw(self, path, headers, method="GET", body=None):
        request = urllib.request.Request(
            f"{self.base}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_the_recording_needs_the_session_secret(self):
        for path, headers in (
            ("/audio", {}),
            ("/audio?session=guess", {}),
            ("/audio", {server.SESSION_HEADER: "guess"}),
        ):
            with self.subTest(path=path, headers=headers):
                status, body = self.raw(path, headers)
                self.assertEqual(status, 403)
                self.assertNotIn(self.payload[:64], body)

    def test_the_secret_also_works_as_a_header(self):
        status, body = self.raw("/audio", {server.SESSION_HEADER: server.SESSION_SECRET})
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)

    def test_another_host_name_is_refused_even_with_the_secret(self):
        # DNS rebinding: a web page's own name pointed at 127.0.0.1.
        for host in ("evil.example:%d", "localhost:%d", "127.0.0.1"):
            with self.subTest(host=host):
                status, _ = self.raw(
                    f"/audio?session={server.SESSION_SECRET}",
                    {"Host": host % self.server.server_port if "%d" in host else host},
                )
                self.assertEqual(status, 403)

    def test_text_endpoints_need_the_secret_too(self):
        for path in ("/health",):
            with self.subTest(path=path):
                self.assertEqual(self.raw(path, {})[0], 403)
        status, _ = self.raw(
            "/warmup", {"Content-Type": "application/json"}, method="POST", body=b"{}"
        )
        self.assertEqual(status, 403)

    def test_the_page_itself_loads_without_the_secret(self):
        status, body = self.raw("/?lang=ru", {})
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body.lower())

    def test_choosing_a_recording_gives_a_fresh_token(self):
        first = current_audio.set_current_audio(self.media)
        second = current_audio.set_current_audio(self.media)
        self.assertTrue(first)
        self.assertTrue(second)


if __name__ == "__main__":
    unittest.main()
