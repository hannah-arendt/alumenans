#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Study documents that are already sitting in the folder.

Before the interviews a researcher accumulates a stack of texts: the
guide, the programme, desk research, the «Поле» ("Field") table, meeting
notes, and, for individual conversations, notes. All of it usually
already sits in the same folder as the recordings. Asking the person to
show the program a second time what it can see for itself is extra work
and one more chance to forget.

Sorting goes by file name, and deliberately crudely. The program does not
guess the content: it suggests, the person confirms. The one thing that
needs precision here is not mistaking for a study document something the
program wrote itself: transcripts, keys, the list of new words.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

__all__ = [
    "READABLE_SUFFIXES",
    "classify_documents",
    "document_kind",
    "is_our_own_output",
    "read_document",
]

READABLE_SUFFIXES = {".txt", ".md", ".rtf", ".docx"}

# What the program wrote itself. Were it to end up among the study
# documents, a transcript would become the guide, and the names from the
# key would become the "subject of the study".
OUR_TRACES = re.compile(
    r"-расшифровк|-вычитан|-анонимизирован|-неаномизирован|^ключ-|^новые слова$",
    re.IGNORECASE,
)

KINDS_RU = (
    ("guide", r"гайд|guide|путеводител"),
    ("programme", r"программа|programme|program\b"),
    ("desk", r"кабинет|десk|desk|обзор литератур|аналитическ"),
    ("field", r"^поле$|таблица поле|«поле»|\bполе\b"),
    ("minutes", r"конспект|протокол|встреч|созвон|планёрк|планерк"),
)

def is_our_own_output(name: str) -> bool:
    """Transcripts, keys and word lists do not count as documents."""

    return bool(OUR_TRACES.search(Path(str(name)).stem))


def document_kind(name: str) -> str:
    """What kind of document this is: by file name, not by content."""

    base = Path(str(name)).stem.casefold()
    for kind, sample in KINDS_RU:
        if re.search(sample, base, re.IGNORECASE):
            return kind
    return "document"


def classify_documents(names, stems=()) -> dict:
    """Sorts the folder's files into their places.

    The guide and the programme are shared across the study. Notes are
    tied to an interview by the audio file's name"""

    bases = sorted((str(stem) for stem in stems if str(stem)), key=len, reverse=True)
    result = {"guide": "", "programme": "", "notes": {}, "documents": []}

    for name in sorted(str(name) for name in names):
        path = Path(name)
        if path.suffix.lower() not in READABLE_SUFFIXES or is_our_own_output(name):
            continue

        base = path.stem
        owner = next(
            (
                stem
                for stem in bases
                if base == stem or base.startswith(stem + "-")
            ),
            "",
        )
        if owner:
            # A file named after a recording is the notes for that interview,
            # even if the word «заметки» ("notes") isn't in the name: there's
            # nothing else it could be.
            result["notes"].setdefault(owner, name)
            continue

        kind = document_kind(name)
        if kind == "guide" and not result["guide"]:
            result["guide"] = name
        elif kind == "programme" and not result["programme"]:
            result["programme"] = name
        else:
            result["documents"].append({"name": name, "kind": kind})
    return result


def read_document(path) -> str:
    """Gets the text out. Knows about .docx, because guides are written in
    Word."""

    target = Path(path)
    if target.suffix.lower() == ".docx":
        return _read_docx(target)
    if target.suffix.lower() == ".rtf":
        return _read_rtf(target.read_text(encoding="utf-8", errors="replace"))
    return target.read_text(encoding="utf-8", errors="replace")


def _read_docx(target: Path) -> str:
    """A .docx is a zip with XML; no need to pull in a package just to read
    paragraphs."""

    with zipfile.ZipFile(target) as archive:
        coding = archive.read("word/document.xml").decode("utf-8", errors="replace")
    coding = re.sub(r"</w:p>", "\n", coding)
    coding = re.sub(r"<w:tab[^>]*/>", "\t", coding)
    coding = re.sub(r"<w:br[^>]*/>", "\n", coding)
    text = re.sub(r"<[^>]+>", "", coding)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _read_rtf(coding: str) -> str:
    """Crude but sufficient: rtf only occasionally comes from old archives."""

    without_groups = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), coding)
    without_groups = re.sub(r"\\par[d]?\b", "\n", without_groups)
    without_groups = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", without_groups)
    without_groups = without_groups.replace("{", "").replace("}", "")
    return re.sub(r"\n{3,}", "\n\n", without_groups).strip()
