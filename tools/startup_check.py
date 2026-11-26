#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Why startup takes so long.

Repeats exactly the checks the program does before opening its window,
and shows how long each one takes. Installs nothing and downloads nothing.
"""

from __future__ import annotations

import sys
import time
from importlib import metadata
from pathlib import Path

# The script lives outside the package; in a working copy the package is
# taken from src/, even if `pip install -e .` hasn't been run yet.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alumenans import launch  # noqa: E402


def timed(label, action):
    started = time.monotonic()
    result = action()
    return result, time.monotonic() - started, label


def main() -> int:
    print("ALumenans startup check")
    print("=" * 62)
    print(f"Python: {sys.version.split()[0]}")
    print(f"From:   {sys.executable}")
    print(f"Folder: {launch.INSTALL_ROOT}")
    print()

    missing, elapsed, _ = timed("packages", launch.missing_packages)
    print(f"[{elapsed:5.2f} s] Packages")
    if missing:
        print("   NOT FOUND — the build is incomplete, the program won't start.")
        print("   It does not install them itself: reinstall the program or,")
        print("   in a source checkout, run pip install -e .")
        for module in missing:
            print(f"     missing  {module}")
    else:
        print("   all present")
    print()

    ready, elapsed, _ = timed("models", launch.transcriber_models_ready)
    print(f"[{elapsed:5.2f} s] Transcription models: "
          f"{'present' if ready else 'NOT FOUND'}")
    if not ready:
        root = launch.TRANSCRIBER_MODELS
        for relative in (
            Path("asr") / "v3_e2e_ctc.int8.onnx",
            Path("vad") / "silero_vad.onnx",
            Path("embedding")
            / "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx",
        ):
            mark = "ok  " if (root / relative).is_file() else "MISS"
            print(f"     {mark}  {relative}")
        print("   while any of them is missing, the program will try to")
        print("   download them on every launch")
    print()

    first_run = not launch.FIRST_RUN_MARK.exists()
    print(f"[       ] First-run mark: "
          f"{'MISSING — counts as a first run' if first_run else 'present'}")
    print(f"          {launch.FIRST_RUN_MARK}")
    print()

    # Versions are for the email to the developer: the build installs them
    # exactly per pyproject.toml, so a mismatch means someone touched the
    # runtime.
    print("[       ] Package versions")
    for distribution in ("natasha", "pymorphy3", "gliner", "pywebview", "torch",
                         "onnxruntime", "onnx-asr", "sherpa-onnx"):
        try:
            installed = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            installed = "MISSING"
        print(f"     {distribution:<14} {installed}")
    print()

    print("=" * 62)
    if missing:
        print("RESULT: the build is incomplete — the program will say so and not open.")
    elif first_run or not ready:
        print("RESULT: launch runs the long preparation — downloading the models.")
        print("Reason: whatever is marked NOT FOUND or MISSING above.")
    else:
        print("RESULT: there should be no long preparation; the window must")
        print("open right away. If it still hangs, send the file")
        print(f"{launch.STARTUP_LOG.name} — the program writes it on every launch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
