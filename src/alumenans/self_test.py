#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`python -m alumenans --self-test`: prove that a build works, without a window.

CI runs this on the installed Windows program and on the unpacked macOS
app; developers can run it in a checkout (`make self-test`). It needs no
network and downloads no models. What it does check is everything that
has broken on people's machines before:

* every bundled package really imports (a missing DLL or a wrong-arch
  `.so` only shows up on import, not in `find_spec`);
* the shipped resources are where the app looks for them;
* the text pipeline that runs without downloads (Natasha NER, pymorphy3,
  proofreading) works on a Russian sample;
* the local HTTP server serves the screen;
* a child process (`-m alumenans.transcription.worker`) starts with the
  same interpreter and sees the same packages;
* the bundled ffmpeg runs (on the Mac this proves the exec bit survived
  the zip);
* the study files really are encrypted on disk, WAL included, and open
  again only with the right password (SQLCipher is a native extension).

Each check prints one `ok` / `FAIL` line; the exit code is the number of
failures, capped at 1.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from typing import Callable

SAMPLE = "Вчера я говорил с Иваном Петровым из Москвы о работе фонда."


def _check_imports() -> str:
    from alumenans.dependencies import missing_modules, required_modules

    missing = missing_modules()
    if missing:
        raise AssertionError("not bundled: " + ", ".join(missing))
    for module in required_modules():
        importlib.import_module(module)
    return f"{len(required_modules())} modules"


def _check_resources() -> str:
    from alumenans.paths import RESOURCES

    for name in ("anonymizer.html", "i18n.js", "names-and-organizations.txt", "ALumenans_logo.ico"):
        if not (RESOURCES / name).is_file():
            raise AssertionError(f"missing {RESOURCES / name}")
    return str(RESOURCES)


def _check_text_pipeline() -> str:
    from alumenans import app
    from alumenans.proofreading import analyze_proofreading

    people = [s for s in app.natasha_spans(SAMPLE) if s["cat"] == "person"]
    if not people:
        raise AssertionError(f"Natasha found no person in {SAMPLE!r}")
    _, pymorphy = app.morph_tools()
    lemma = pymorphy.parse("Петровым")[0].normal_form
    if lemma != "петров":
        raise AssertionError(f"pymorphy3 lemma of 'Петровым' is {lemma!r}")
    result = analyze_proofreading(
        [{"start": 0, "end": 4, "speaker": 1, "text": "Это мжно исправить."}]
    )
    if not isinstance(result, dict):
        raise AssertionError("proofreading returned no result")
    return f"{len(people)} person span(s), lemma {lemma!r}"


def _check_http() -> str:
    from http.server import ThreadingHTTPServer

    from alumenans import app

    server = ThreadingHTTPServer(("127.0.0.1", app.free_port()), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Never go through a proxy for loopback, whatever the environment says.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with opener.open(base + "/", timeout=30) as response:
            screen = response.read()
        if b"<html" not in screen.lower():
            raise AssertionError("GET / did not return the screen")
        # The screen's text lives in a separate script; without it every label is a bare key.
        with opener.open(base + "/i18n.js", timeout=30) as response:
            if b"const I18N" not in response.read():
                raise AssertionError("GET /i18n.js did not return the interface catalog")
        health = urllib.request.Request(
            base + "/health", headers={app.SESSION_HEADER: app.SESSION_SECRET}
        )
        with opener.open(health, timeout=30) as response:
            json.loads(response.read())
        # Without the launch's secret the server must refuse.
        try:
            opener.open(base + "/health", timeout=30).close()
        except urllib.error.HTTPError as error:
            if error.code != 403:
                raise
        else:
            raise AssertionError("GET /health answered without the session secret")
    finally:
        server.shutdown()
        server.server_close()
    return f"{len(screen)} bytes of UI"


def _check_worker_process() -> str:
    from alumenans.paths import INSTALL_ROOT, child_environment
    from alumenans.transcription.core import existing_directory
    from alumenans.transcription.service import WORKER_MODULE, worker_python

    answer = subprocess.run(
        [str(worker_python()), "-m", WORKER_MODULE, "check"],
        cwd=existing_directory(INSTALL_ROOT),
        env=child_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    # `check` exits 1 when the models are not downloaded yet; that is
    # expected here. What matters is that the child started and answered.
    lines = [line for line in answer.stdout.splitlines() if line.startswith("{")]
    events = [json.loads(line) for line in lines]
    if answer.returncode not in (0, 1) or not any(e.get("type") == "check" for e in events):
        raise AssertionError(
            f"worker exited {answer.returncode}: {(answer.stderr or answer.stdout)[-600:]}"
        )
    ready = next(e for e in events if e.get("type") == "check")["ready"]
    return f"{worker_python()} (models {'ready' if ready else 'not downloaded'})"


def _check_ffmpeg() -> str:
    import imageio_ffmpeg

    executable = imageio_ffmpeg.get_ffmpeg_exe()
    answer = subprocess.run(
        [executable, "-version"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if answer.returncode != 0:
        raise AssertionError(f"{executable} -version exited {answer.returncode}")
    return answer.stdout.splitlines()[0] if answer.stdout else executable


def _check_encrypted_storage() -> str:
    import tempfile
    from contextlib import closing
    from pathlib import Path

    from alumenans.storage import cipher
    from alumenans.storage.cipher import StudySecret
    from alumenans.storage.study_store import STUDY_FILE_NAME, StudyStore

    with tempfile.TemporaryDirectory(prefix="alumenans-self-test-") as folder:
        path = Path(folder) / STUDY_FILE_NAME
        secret = StudySecret.create("self-test password")
        store = StudyStore(path, secret)
        try:
            study_id = store.ensure_study()
            store.save_study(study_id, title=SAMPLE)
            # Read while still open: the newest pages are in the WAL then.
            for item in (path, path.with_name(path.name + "-wal")):
                if item.is_file() and SAMPLE.encode("utf-8") in item.read_bytes():
                    raise AssertionError(f"plaintext found in {item.name}")
            if path.read_bytes()[:16] != secret.salt:
                raise AssertionError("the file does not start with the study salt")
        finally:
            store.close()
        again = StudyStore(path, StudySecret.for_file(path, "self-test password"))
        try:
            title = (again.load_study() or {}).get("title")
        finally:
            again.close()
        if title != SAMPLE:
            raise AssertionError("the encrypted study did not read back")
        try:
            cipher.connect(path, StudySecret.for_file(path, "wrong password")).close()
        except cipher.WrongPassword:
            pass
        else:
            raise AssertionError("a wrong password opened the study")
        with closing(cipher.connect(path, secret)) as connection:
            details = cipher.cipher_details(connection)
    return f"SQLCipher {details['version']}, {details['provider_version']}"


CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("bundled packages import", _check_imports),
    ("resources present", _check_resources),
    ("text pipeline (no downloads)", _check_text_pipeline),
    ("local HTTP server", _check_http),
    ("worker child process", _check_worker_process),
    ("bundled ffmpeg", _check_ffmpeg),
    ("encrypted study files", _check_encrypted_storage),
]


def run() -> int:
    from alumenans import __version__
    from alumenans.paths import INSTALL_ROOT

    print(f"ALumenans {__version__} self-test")
    print(f"python: {sys.executable} ({sys.version.split()[0]}, {sys.platform})")
    print(f"install root: {INSTALL_ROOT}")
    failures = 0
    for name, check in CHECKS:
        started = time.monotonic()
        try:
            detail = check()
        except Exception:
            failures += 1
            print(f"FAIL  {name}")
            print("      " + traceback.format_exc().strip().replace("\n", "\n      "))
            continue
        print(f"ok    {name} [{time.monotonic() - started:.1f}s] {detail}")
    print("self-test " + ("passed" if not failures else f"FAILED ({failures} check(s))"))
    sys.stdout.flush()
    return 1 if failures else 0
