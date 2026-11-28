#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Management of the separate transcription process for the app window."""

from __future__ import annotations

import copy
import json
import logging
import math
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from alumenans.paths import INSTALL_ROOT, child_environment
from alumenans.transcription.core import (
    TranscriptionError,
    existing_directory,
    validate_media_path,
    validate_speaker_count,
)
from alumenans.i18n import t


# The worker is started as a package module: a file inside a package run
# directly can't find its neighbours. The file path is needed only to
# notice that the program's files have disappeared (see _run).
WORKER_MODULE = "alumenans.transcription.worker"
WORKER = Path(__file__).resolve().with_name("worker.py")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ACTIVE_STATES = frozenset({"running", "cancelling"})
PROGRESS_RANGES = {
    "starting": (0.0, 0.02),
    "audio": (0.02, 0.10),
    # Recognition and voice separation run at the same time and report as a
    # single value. The two former stages remain: messages still arrive
    # under them from each step separately when it runs on its own.
    "listening": (0.10, 1.0),
    "speakers": (0.10, 0.42),
    "speech": (0.42, 1.0),
    "done": (1.0, 1.0),
}


def overall_progress(stage: str | None, fraction) -> float:
    start, end = PROGRESS_RANGES.get(stage, PROGRESS_RANGES["starting"])
    try:
        local = float(fraction)
    except (TypeError, ValueError):
        local = 0.0
    if not math.isfinite(local):
        local = 0.0
    local = max(0.0, min(1.0, local))
    return start + (end - start) * local


def worker_python() -> Path:
    # Without resolve(): in a virtualenv, python is a symlink to the base
    # interpreter, and following it starts the child outside the venv,
    # without the venv's packages ("No module named 'natasha'").
    executable = Path(sys.executable).absolute()
    if executable.name.lower() == "pythonw.exe":
        console = executable.with_name("python.exe")
        if console.is_file():
            return console
    return executable


log = logging.getLogger(__name__)


class TranscriptionManager:
    """Runs the heavy models separately and keeps a safe progress snapshot."""

    def __init__(self, worker: Path | None = None) -> None:
        # Without an argument: the package's real worker. With one: any
        # script speaking the same protocol (that is how tests plug in a fake).
        self.worker = (worker or WORKER).resolve()
        self._launch = (
            ["-m", WORKER_MODULE] if worker is None else [str(self.worker)]
        )
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._cancel_file: Path | None = None
        self._cancel_requested = False
        self._generation = 0
        self._state = self._idle_state()

    @staticmethod
    def _idle_state() -> dict:
        return {
            "status": "idle",
            "stage": None,
            "message": "",
            "fraction": None,
            "overall_fraction": 0.0,
            "source_name": None,
            "source_size": None,
            "requested_speakers": None,
            "result": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
        }

    def status(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def start(
        self,
        source_path,
        speaker_count: int = 2,
        guide: str = "",
    ) -> dict:
        # An interrupted interview arrives as several files. For everything
        # else it is one recording: one transcript, one set of speakers,
        # continuous time.
        given = (
            [source_path]
            if isinstance(source_path, (str, Path))
            else list(source_path or [])
        )
        try:
            sources = [validate_media_path(item) for item in given]
            if not sources:
                raise TranscriptionError(t("transcription.no_files"))
            source = sources[0]
            requested_speakers = validate_speaker_count(speaker_count)
        except TranscriptionError as error:
            return {"ok": False, "error": str(error)}

        with self._lock:
            if self._state["status"] in ACTIVE_STATES:
                return {"ok": False, "error": t("transcription.already_running")}
            self._generation += 1
            generation = self._generation
            self._cancel_requested = False
            self._cancel_file = None
            self._state = {
                "status": "running",
                "stage": "starting",
                "message": t("transcription.starting"),
                "fraction": 0.0,
                "overall_fraction": 0.0,
                "source_name": source.name,
                "source_size": sum(item.stat().st_size for item in sources),
                "parts": len(sources),
                "requested_speakers": requested_speakers,
                "result": None,
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(generation, sources, requested_speakers, str(guide or "")),
                daemon=True,
                name="transcription-manager",
            )
            self._thread.start()
        return {"ok": True}

    def cancel(self) -> dict:
        with self._lock:
            if self._state["status"] not in ACTIVE_STATES:
                return {"ok": False, "error": t("transcription.nothing_to_stop")}
            self._cancel_requested = True
            self._state["status"] = "cancelling"
            self._state["message"] = t("transcription.stopping")
            marker = self._cancel_file
        if marker is not None:
            marker.touch(exist_ok=True)
        return {"ok": True}

    def reset(self) -> dict:
        with self._lock:
            if self._state["status"] in ACTIVE_STATES:
                return {"ok": False, "error": t("transcription.stop_first")}
            self._state = self._idle_state()
        return {"ok": True}

    def shutdown(self) -> None:
        with self._lock:
            active = self._state["status"] in ACTIVE_STATES
        if active:
            self.cancel()

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)

        with self._lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

    def _set_progress(self, generation: int, event: dict) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._state["stage"] = event.get("stage")
            self._state["message"] = event.get("message") or ""
            self._state["fraction"] = event.get("fraction")
            candidate = overall_progress(
                self._state["stage"],
                self._state["fraction"],
            )
            self._state["overall_fraction"] = max(
                float(self._state.get("overall_fraction") or 0.0),
                candidate,
            )
            for key in (
                "processed_seconds",
                "total_seconds",
                "processed_chunks",
                "total_chunks",
            ):
                if key in event:
                    self._state[key] = event[key]

    def _finish(
        self,
        generation: int,
        status: str,
        *,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._state["status"] = status
            if status == "done":
                self._state["stage"] = "done"
                self._state["fraction"] = 1.0
                self._state["overall_fraction"] = 1.0
            self._state["result"] = result
            self._state["error"] = error
            self._state["message"] = {
                "done": t("transcription.done"),
                "cancelled": t("transcription.cancelled"),
                "error": error or t("transcription.not_finished"),
            }[status]
            self._state["finished_at"] = time.time()
            self._process = None
            self._cancel_file = None

    def _run(
        self,
        generation: int,
        sources: list[Path],
        requested_speakers: int,
        guide: str,
    ) -> None:
        last_error = None
        result = None
        noise: list[str] = []          # what the process wrote outside our format
        noise_lock = threading.Lock()

        def remember(raw_line: str) -> None:
            line = raw_line.strip()
            if line:
                log.info("worker: %s", line)
                with noise_lock:
                    noise.append(line)
                    del noise[:-8]
        if not self.worker.is_file():
            # This is what launching the app from Downloads on a Mac looks
            # like: the system runs it from its own copy and removes that
            # copy while the program is running. The program's files vanish.
            self._finish(
                generation,
                "error",
                error=t("transcription.files_unavailable"),
            )
            return
        try:
            with tempfile.TemporaryDirectory(prefix="interview-job-") as folder:
                work = Path(folder)
                # The folder holds only the empty cancel marker: the guide
                # goes over stdin and the transcript comes back over stdout.
                cancel_file = work / "cancel"
                with self._lock:
                    if generation != self._generation:
                        return
                    self._cancel_file = cancel_file
                    cancel_requested = self._cancel_requested
                if cancel_requested:
                    cancel_file.touch()

                environment = child_environment()
                environment["PYTHONIOENCODING"] = "utf-8"
                environment["PYTHONUTF8"] = "1"
                command = [
                    str(worker_python()),
                    *self._launch,
                    "transcribe",
                    *[str(item) for item in sources],
                    # No --output: the transcript comes back in the
                    # "result" message, so it never lies on disk in the clear.
                    "--speakers",
                    str(requested_speakers),
                    "--cancel-file",
                    str(cancel_file),
                ]
                has_guide = bool(guide.strip())
                if has_guide:
                    command.append("--guide-stdin")
                process = subprocess.Popen(
                    command,
                    cwd=existing_directory(INSTALL_ROOT),
                    stdin=subprocess.PIPE if has_guide else subprocess.DEVNULL,
                    # Separate streams: the transcript travels on stdout as one
                    # long line, and a library's message on stderr must not be
                    # able to land inside it.
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=environment,
                    creationflags=CREATE_NO_WINDOW,
                )
                with self._lock:
                    if generation != self._generation:
                        process.terminate()
                        return
                    self._process = process

                if has_guide:
                    # In a thread: a long guide could fill the pipe before
                    # the worker gets to reading it.
                    def hand_over_guide() -> None:
                        try:
                            process.stdin.write(guide)
                            process.stdin.close()
                        except OSError:
                            pass  # the worker is gone; its exit code says why

                    threading.Thread(target=hand_over_guide, daemon=True).start()
                assert process.stdout is not None and process.stderr is not None
                # Read all the time, or a chatty library would fill the pipe
                # and stall the worker.
                errors = threading.Thread(
                    target=lambda: [remember(line) for line in process.stderr],
                    daemon=True,
                )
                errors.start()
                for raw_line in process.stdout:
                    try:
                        event = json.loads(raw_line)
                    except json.JSONDecodeError:
                        # Not our message, so the process wrote something of
                        # its own: command-line argument parsing, a library
                        # crash. This used to be silently discarded, and the
                        # person saw a stop with no reason.
                        remember(raw_line)
                        continue
                    if event.get("type") == "progress":
                        self._set_progress(generation, event)
                    elif event.get("type") == "error":
                        last_error = str(event.get("message") or "")[:600]
                    elif event.get("type") == "result":
                        result = event.get("transcript")

                process.stdout.close()
                return_code = process.wait()
                errors.join(timeout=5)
                process.stderr.close()
                with self._lock:
                    was_cancelled = self._cancel_requested
                # A stop is only one the person asked for. Exit code 2
                # used to land here too, but argument parsing also
                # returns it: an unknown option value looked like "the
                # person pressed stop", and the real reason was never
                # shown at all.
                if was_cancelled:
                    self._finish(generation, "cancelled")
                    return
                if return_code:
                    self._finish(
                        generation,
                        "error",
                        error=(
                            last_error
                            or " ".join(noise[-3:])
                            or t("transcription.process_failed")
                        )[:600],
                    )
                    return
                # A process killed mid-message leaves a cut-off line, which
                # doesn't parse and so never becomes a result.
                if not isinstance(result, dict):
                    self._finish(
                        generation,
                        "error",
                        error=t("transcription.no_result"),
                    )
                    return
                self._finish(generation, "done", result=result)
        except Exception as error:
            self._finish(
                generation,
                "error",
                error=str(error)[:600] or type(error).__name__,
            )
