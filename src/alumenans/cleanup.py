#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clearing away the non-anonymised text once it is no longer needed.

Anonymisation is finished and the file is saved. From
this point on the original text serves no work at all, yet it carries as
much risk as it did on day one: real names, the town, places of work.
There is no reason to keep it any longer.

Only what the program wrote itself is deleted, and by its exact name, not
by a pattern. Everything else in the folder belongs to someone else:
notes, desk research, files the person named in their own way. Sorting
them into "looks like it / doesn't look like it" means one day deleting
someone's work.

The audio file stays: it may still come in handy, and the person deletes
it themselves once the interview is completely finished.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["own_transcript_names", "unanonymised_files"]


def own_transcript_names(stem: str) -> list[str]:
    """The names under which the program saves the non-anonymised text.

    There are exactly two, and they are built here from the same words as
    in the window. If the name changes in the window but not here, the
    cleanup will stop finding the file — which is why there is a test that
    they match."""

    base = str(stem or "").strip()
    if not base:
        return []
    return [
        f"{base}-расшифровка.txt",
        f"{base}-расшифровка-вычитана-неаномизирована.txt",
    ]


def unanonymised_files(folder, stems) -> list[Path]:
    """Interview files that are due for removal. Only our own, only by name."""

    folder_path = Path(folder) if folder else None
    if folder_path is None or not folder_path.is_dir():
        return []
    found = []
    for base in stems or []:
        for name in own_transcript_names(base):
            path = folder_path / name
            if path.is_file():
                found.append(path)
    return found
