#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Puts the Python that ships inside the Windows installer into python-runtime/.

    python packaging/windows/fetch-runtime.py

build-installer.ps1 expects a complete, relocatable Python in
`packaging/windows/python-runtime/` (it is not in git). This fetches the
same kind of build the macOS app uses: CPython from python-build-standalone,
here for x86_64 Windows. If the folder already holds a runtime, it is left
alone (delete it to fetch a fresh one).

The bundled packages are installed later, by build-installer.ps1.
"""

from __future__ import annotations

import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import standalone_python  # noqa: E402

PYTHON = "3.13"                        # same version as inside the macOS app
TRIPLE = "x86_64-pc-windows-msvc"
RUNTIME = HERE / "python-runtime"

# What the program needs from its own Python. tkinter draws the setup
# window and error boxes; pip installs the bundled packages at build time.
REQUIRED = (
    "python.exe",
    "pythonw.exe",
    "Lib/tkinter/__init__.py",
    "DLLs/_tkinter.pyd",
    "Lib/sqlite3/__init__.py",
    "Lib/site-packages/pip/__init__.py",
)


def check(runtime: Path) -> None:
    missing = [name for name in REQUIRED if not (runtime / name).is_file()]
    if missing:
        raise SystemExit(f"{runtime} is incomplete, missing: " + ", ".join(missing))
    if list((runtime / "Lib").glob("python3*t")) or (runtime / "python3.13t.exe").exists():
        raise SystemExit("Got a free-threaded build; regular wheels don't fit it.")


def fetch() -> Path:
    if (RUNTIME / "pythonw.exe").is_file():
        print(f"Runtime already present: {RUNTIME}")
        check(RUNTIME)
        return RUNTIME

    name, url = standalone_python.find_asset(PYTHON, TRIPLE)
    with tempfile.TemporaryDirectory(prefix="alumenans-runtime-") as folder:
        archive = Path(folder) / name
        print(f"Downloading {name}")
        standalone_python.download(url, archive)
        print("Unpacking")
        with tarfile.open(archive, "r:gz") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(folder, filter="data")
            else:  # Python < 3.12
                tar.extractall(folder)
        unpacked = Path(folder) / "python"
        if not (unpacked / "python.exe").is_file():
            raise SystemExit(f"{name} has no python/python.exe; the layout changed.")
        if RUNTIME.exists():
            shutil.rmtree(RUNTIME)
        shutil.move(str(unpacked), RUNTIME)

    check(RUNTIME)
    print(f"Runtime ready: {RUNTIME}")
    return RUNTIME


if __name__ == "__main__":
    fetch()
