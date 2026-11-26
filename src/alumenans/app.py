#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The application shell.

Brings up a server on a local address, serves the editor screen and opens
it in its own window. To the person it looks like an ordinary program:
start it, drag a file in, get to work. No command line at all.

The network is used only inside the machine (127.0.0.1). Nothing goes
out: the server listens on the loopback interface and can do nothing else.

The heavy transcription stages run in a separate process. That way, once
the transcript is ready, the memory is freed before the anonymisation
models are loaded.
"""

import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from alumenans import i18n
from alumenans.api import AppApi
from alumenans.detection.models import prepare_anonymizer_models_cli
from alumenans.i18n import t
from alumenans.launch import (
    ANONYMIZER_SETUP_ARG,
    ICON,
    NL2,
    SETUP_WINDOW_ARG,
    _write_setup_status,
    cleanup_setup_window_process,
    configure_windows_identity,
    is_incomplete_build,
    prepare_launch,
    set_tk_icon,
    setup_window,
    signal_setup_window_close,
    start_setup_window_process,
)
from alumenans.server import (
    Handler,
    SCREEN,
    SESSION_PARAMETER,
    SESSION_SECRET,
    free_port,
)
from alumenans.transcription.service import TranscriptionManager

# The program is started through pythonw — as a window, with no console.
# There sys.stdout and sys.stderr are None, and any attempt to print
# something dies with an error inside someone else's library, often in its
# own thread. From the outside this looks like "the window didn't open and
# said nothing". We substitute a dummy: there is nowhere to print, but
# nothing to crash on either.
for _stream in ("stdout", "stderr"):
    if getattr(sys, _stream, None) is None:
        setattr(sys, _stream, open(os.devnull, "w", encoding="utf-8"))


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


SELF_TEST_ARG = "--self-test"


def open_window(url, api, *, hidden=False):
    """Opens the interface only in the program's own window."""
    try:
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            "ALumenans",
            url,
            js_api=api,
            width=1360,
            height=900,
            min_size=(900, 600),
            hidden=hidden,
            # Without this the window forbids selecting text: that is
            # pywebview's default. In proofreading it goes unnoticed — the
            # fields there are editable — but in anonymisation the text is
            # read-only, and manual markup via the right mouse button didn't
            # work at all: there was nothing to select.
            text_select=True,
        )
        api.bind_window(window)
        webview.start(icon=str(ICON) if ICON.exists() else None)
        return None
    except Exception as error:
        return str(error)[:400] or type(error).__name__


def show_error(message):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        set_tk_icon(root)
        messagebox.showerror("ALumenans", message)
        root.destroy()
    except Exception:
        pass


def main():
    # Before anything that opens a window or downloads models: CI runs this
    # on the installed program (see self_test.py).
    if len(sys.argv) > 1 and sys.argv[1] == SELF_TEST_ARG:
        from alumenans.self_test import run as run_self_test

        return run_self_test()
    if len(sys.argv) > 1 and sys.argv[1] == ANONYMIZER_SETUP_ARG:
        return prepare_anonymizer_models_cli()
    if len(sys.argv) > 3 and sys.argv[1] == SETUP_WINDOW_ARG:
        setup_window(Path(sys.argv[2]), Path(sys.argv[3]))
        return 0

    configure_windows_identity()

    if not SCREEN.exists():
        show_error(t("launch.no_screen"))
        return 1

    # The preparation window lives separately and stays visible until the
    # hidden main window confirms that the Python bridge is ready.
    setup_handle = start_setup_window_process()
    if setup_handle is None:
        error = setup_window()
    else:
        error = prepare_launch(
            lambda text: _write_setup_status(setup_handle, text)
        )
    if error:
        cleanup_setup_window_process(setup_handle)
        # Only the models need the internet; it won't fix an incomplete build.
        hint = (
            ""
            if is_incomplete_build(error)
            else NL2 + t("launch.check_internet")
        )
        show_error(t("launch.prepare_failed") + NL2 + error + hint)
        return 1

    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    transcriber = TranscriptionManager()
    api = AppApi(
        transcriber,
        ready_callback=lambda: signal_setup_window_close(setup_handle),
    )
    window_error = open_window(
        f"http://127.0.0.1:{port}/?lang={i18n.get_language()}&{SESSION_PARAMETER}={SESSION_SECRET}",
        api,
        hidden=setup_handle is not None,
    )
    cleanup_setup_window_process(setup_handle)
    transcriber.shutdown()
    server.shutdown()
    server.server_close()
    if window_error:
        show_error(t("launch.window_failed") + NL2 + window_error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
