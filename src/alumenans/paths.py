#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where things live: one place instead of `Path(__file__)` in every module.

The program distinguishes three folders.

* PACKAGE_DIR — the `alumenans` package itself, code only.
* RESOURCES — what the program reads but does not write: the screen,
  icons, the dictionary of names. Travels together with the package.
* INSTALL_ROOT — the folder the package lives in: `{app}` for an installed
  program, `src/` in a working copy. Next to it the program keeps its
  state — the first-run mark, the log, the last folder — and, if it can
  write there, the models. It was like this before the package too, so
  already installed copies lose nothing when they update.

The ALUMENANS_HOME environment variable overrides INSTALL_ROOT — for
tests and for running from a folder that cannot be written to.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
RESOURCES = PACKAGE_DIR / "resources"
INSTALL_ROOT = (
    Path(os.environ["ALUMENANS_HOME"]).expanduser().resolve()
    if os.environ.get("ALUMENANS_HOME", "").strip()
    else PACKAGE_DIR.parent
)


def state_file(name: str, legacy: str | None = None) -> Path:
    """A file the program keeps next to itself (first-run mark, last folder, …).

    Before 0.3.0 these files had Russian names. If only the legacy file
    exists, it is renamed to `name` so existing installs keep their state;
    if renaming is impossible (a read-only app bundle), the legacy path is
    returned and keeps working."""

    path = INSTALL_ROOT / name
    if legacy:
        old = INSTALL_ROOT / legacy
        if old.exists() and not path.exists():
            try:
                old.replace(path)
            except OSError:
                return old
    return path


def models_home() -> Path:
    """Where the transcription models live: next to the program, if it is writable.

    On the Mac the program usually ends up in /Applications, which an
    ordinary user cannot write to, while the models are half a gigabyte and
    are downloaded on first launch. In that case they move to the
    Application Support folder in the home directory. On Windows everything
    stays as it was."""

    beside = INSTALL_ROOT / "models" / "transcriber"
    if beside.is_dir() or os.access(INSTALL_ROOT, os.W_OK):
        return beside
    if sys.platform == "darwin":
        home = Path.home() / "Library" / "Application Support" / "ALumenans"
    else:
        home = Path.home() / ".alumenans"
    return home / "models" / "transcriber"


def child_environment(base: dict | None = None) -> dict:
    """Environment for a child process in which `python -m alumenans…` finds the package.

    Child processes are started as modules of the package, not as files: a
    file inside the package that is run directly cannot see its neighbours.
    So that `-m` works from any working folder (or with none at all — the
    folder may have been deleted), INSTALL_ROOT is put first in PYTHONPATH."""

    environment = dict(os.environ if base is None else base)
    previous = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        str(INSTALL_ROOT) + (os.pathsep + previous if previous else "")
    )
    # The child speaks the same interface language (progress, errors).
    from alumenans import i18n

    environment[i18n.ENV_VAR] = i18n.get_language()
    return environment
