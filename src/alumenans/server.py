"""The local HTTP server: the screen, the open recording, and text analysis.

Listens on 127.0.0.1 only and answers only the program's own window."""

import json
import re
import secrets
import socket
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from alumenans.current_audio import AUDIO_STATE, current_pseudonym
from alumenans.detection.curated import dict_spans
from alumenans.detection.models import MODEL_STATE, request_anonymizer_warmup
from alumenans.detection.morphology import inflect_word
from alumenans.detection.spans import entity_groups, find_spans
from alumenans.i18n import t
from alumenans.introduction import respondent_speaker
from alumenans.paths import RESOURCES
from alumenans.places import KINDS as PLACE_KINDS
from alumenans.proofreading import analyze_proofreading

SCREEN = RESOURCES / "anonymizer.html"


I18N_SCRIPT = RESOURCES / "i18n.js"


LOGO = RESOURCES / "ALumenans_logo.png"


LIMIT = 32 * 1024 * 1024  # a sensible ceiling on the size of a transcript


AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".wma": "audio/x-ms-wma",
}


AUDIO_BLOCK = 256 * 1024


def parse_byte_range(header, size):
    """Parses the Range header. Returns None if it is not applicable."""

    if not header:
        return None
    match = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", str(header))
    if not match or size <= 0:
        return None
    first, last = match.group(1), match.group(2)
    if not first and not last:
        return None
    if not first:
        length = min(int(last), size)
        if length <= 0:
            return None
        return size - length, size - 1
    start = int(first)
    if start >= size:
        return "unsatisfiable"
    stop = min(int(last), size - 1) if last else size - 1
    if stop < start:
        return None
    return start, stop


# The local server is reachable by every program on the machine, and by any
# web page through DNS rebinding (a site pointing its own name at 127.0.0.1).
# It serves the open interview's recording, so every request must name this
# server exactly in its Host header, and everything past the bare page must
# carry this launch's secret. The window gets the secret in its own URL;
# fetch() sends it as a header, the <audio> element in the query.
SESSION_SECRET = secrets.token_urlsafe(32)


SESSION_HEADER = "X-ALumenans-Session"


SESSION_PARAMETER = "session"


# What the window needs before it knows the secret: the page and its files.
PUBLIC_PATHS = frozenset({"/", "/index.html", "/i18n.js", "/ALumenans_logo.png", "/logo.png"})


class Handler(BaseHTTPRequestHandler):
    # The player needs HTTP/1.1: without a persistent connection, seeking
    # through a long recording turns into reopening the socket on every
    # move of the slider.
    protocol_version = "HTTP/1.1"
    # But persistent connections have a downside: the browser engine opens
    # sockets in reserve and often sends nothing over them. Without a
    # timeout, every such socket ties up a server thread forever.
    timeout = 30

    def log_message(self, *args):
        pass  # don't make noise in the console

    def _allowed(self):
        """Refuses the request (and returns False) unless it comes from the window."""

        port = self.server.server_address[1]
        address = urllib.parse.urlparse(self.path)
        given = self.headers.get(SESSION_HEADER) or (
            urllib.parse.parse_qs(address.query).get(SESSION_PARAMETER, [""])[0]
        )
        if self.headers.get("Host", "") == f"127.0.0.1:{port}" and (
            address.path in PUBLIC_PATHS
            or secrets.compare_digest(given.encode("utf-8"), SESSION_SECRET.encode("utf-8"))
        ):
            return True
        # The body of a refused request is never read, so the connection
        # can't be reused for another request.
        self.close_connection = True
        self._send(403, json.dumps({"error": "forbidden"}))
        return False

    def _send_audio(self, head_only=False):
        paths = AUDIO_STATE.get("paths") or []
        query = urllib.parse.urlparse(self.path).query
        try:
            relative = int(urllib.parse.parse_qs(query).get("part", ["0"])[0])
        except ValueError:
            relative = 0
        if not 0 <= relative < len(paths):
            relative = 0
        media = paths[relative] if paths else None
        if media is None or not Path(media).is_file():
            self._send(404, json.dumps({"error": t("http.no_recording")}))
            return
        media = Path(media)
        size = media.stat().st_size
        mime = AUDIO_MIME.get(media.suffix.lower(), "application/octet-stream")
        span = parse_byte_range(self.headers.get("Range"), size)

        if span == "unsatisfiable":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        start, stop = span if span else (0, size - 1)
        length = stop - start + 1
        self.send_response(206 if span else 200)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if span:
            self.send_header("Content-Range", f"bytes {start}-{stop}/{size}")
        self.end_headers()
        if head_only:
            return

        try:
            with media.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining > 0:
                    block = source.read(min(AUDIO_BLOCK, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Seeking cuts off the previous request — this is the player working normally.
            pass

    def do_HEAD(self):
        if not self._allowed():
            return
        if self.path.split("?", 1)[0] == "/audio":
            self._send_audio(head_only=True)
            return
        self._send(404, b"", "text/plain; charset=utf-8")

    def _send(self, code, body, mime="application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if not self._allowed():
            return
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            try:
                self._send(200, SCREEN.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, t("http.no_screen"), "text/plain; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/i18n.js":
            try:
                self._send(200, I18N_SCRIPT.read_bytes(), "text/javascript; charset=utf-8")
            except OSError:
                self._send(404, b"", "text/javascript; charset=utf-8")
        elif self.path in ("/ALumenans_logo.png", "/logo.png"):
            try:
                self._send(200, LOGO.read_bytes(), "image/png")
            except OSError:
                self._send(404, b"", "image/png")
        elif self.path.split("?", 1)[0] == "/audio":
            self._send_audio()
        elif self.path == "/health":
            self._send(200, json.dumps({"model": dict(MODEL_STATE)}))
        else:
            self._send(404, json.dumps({"error": t("http.not_found")}))

    def do_POST(self):
        if not self._allowed():
            return
        if self.path == "/warmup":
            request_anonymizer_warmup()
            self._send(202, json.dumps({"model": dict(MODEL_STATE)}))
            return

        if self.path == "/inflect":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                word = inflect_word(request.get("phrase") or "", request.get("gram") or {})
            except Exception:
                word = (request.get("phrase") if isinstance(request, dict) else "") or ""
            self._send(200, json.dumps({"phrase": word}, ensure_ascii=False))
            return

        if self.path == "/known-words":
            # Replacing throughout the text has to tell a proper name from
            # an ordinary word: «Морем» is a name, «морем» is the
            # instrumental case of «море» ("sea"). The Russian dictionary is
            # already installed alongside.
            length = int(self.headers.get("Content-Length") or 0)
            try:
                from alumenans.proofreading import is_known_russian_word

                request = json.loads(self.rfile.read(length).decode("utf-8"))
                words = request.get("words") or []
                known = {
                    str(word): is_known_russian_word(str(word))
                    for word in words[:400]
                    if isinstance(word, (str, int))
                }
            except Exception:
                known = {}
            self._send(200, json.dumps({"known": known}, ensure_ascii=False))
            return

        if self.path == "/guide-report":
            # The guide knows the order of the conversation. Alignment turns
            # that knowledge into an answer to the question "which of the
            # planned topics actually came up".
            length = int(self.headers.get("Content-Length") or 0)
            if length > LIMIT:
                self._send(413, json.dumps({"error": t("http.transcript_too_large")}))
                return
            try:
                from alumenans.guide.report import build_report

                request = json.loads(self.rfile.read(length).decode("utf-8"))
                guide_text = str(request.get("guide") or "").strip()
                if not guide_text:
                    self._send(200, json.dumps({"empty": True}, ensure_ascii=False))
                    return
                report = build_report(guide_text, request.get("turns") or [])
            except Exception as error:
                self._send(
                    500,
                    json.dumps(
                        {"error": str(error)[:600] or type(error).__name__},
                        ensure_ascii=False,
                    ),
                )
                return
            self._send(200, json.dumps(report, ensure_ascii=False))
            return

        if self.path == "/proofread":
            length = int(self.headers.get("Content-Length") or 0)
            if length > LIMIT:
                self._send(413, json.dumps({"error": t("http.transcript_too_large")}))
                return
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                result = analyze_proofreading(
                    request.get("turns") or [],
                    request.get("notes") or "",
                    request.get("guide") or "",
                )
            except Exception as error:
                self._send(
                    500,
                    json.dumps(
                        {"error": str(error)[:600] or type(error).__name__},
                        ensure_ascii=False,
                    ),
                )
                return
            self._send(200, json.dumps(result, ensure_ascii=False))
            return

        if self.path != "/analyze":
            self._send(404, json.dumps({"error": t("http.not_found")}))
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > LIMIT:
            self._send(413, json.dumps({"error": t("http.file_too_large")}))
            return

        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            text = request.get("text") or ""
        except Exception:
            self._send(400, json.dumps({"error": t("http.bad_request")}))
            return

        try:
            spans = find_spans(text)
        except ImportError:
            # No Natasha — serve at least the dictionary findings.
            spans = dict_spans(text)
        except Exception as error:
            self._send(500, json.dumps({"error": str(error)}))
            return

        self._send(
            200,
            json.dumps(
                {
                    "spans": spans,
                    "entities": entity_groups(text, spans),
                    "model": dict(MODEL_STATE),
                    # The pseudonym from the file name: the respondent is
                    # labelled with it everywhere they are talked about —
                    # and in the transcript itself it belongs wherever the
                    # respondent names themselves.
                    "pseudonym": current_pseudonym(),
                    # Which of the speakers is the respondent. Needed to
                    # label the turns with names rather than «Спикер 1»
                    # ("Speaker 1"): on import MAXQDA and ATLAS.ti create a
                    # code for every speaker, and the code «Спикер 1» across
                    # twenty interviews means twenty different people.
                    "respondent": respondent_speaker(text),
                    "place_kinds": PLACE_KINDS,
                },
                ensure_ascii=False,
            ),
        )


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
