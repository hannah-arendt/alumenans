#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The recording's file name as a source of facts about the interview.

Researchers name recordings by convention, and the name already holds
almost everything the program would otherwise ask for separately.

From it we get the series name, the interviewer's tag, the number and,
most importantly, the link between recordings: «Татьяна1» and «Татьяна2»
("Tatiana1", "Tatiana2") are one interview, interrupted and resumed.
Without parsing the name, the person would have to tell the program
about this by hand twenty times in a row.

The parsing is deliberately lenient. Each team has its own convention,
and it drifts over time, so an unintelligible name is not an error: some
of the fields simply stay empty, and the person fills them in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence



# The separator can be a hyphen or an underscore; spaces around them are
# tolerated too.
SEPARATOR = re.compile(r"\s*[-_]\s*")

# Interview number: usually just digits, but a leading letter also occurs,
# as in «С8».
NUMBER = re.compile(r"^([A-Za-zА-Яа-яЁё]{0,2})(\d{1,3})$")

# A trailing digit on the pseudonym is the recording's number within an
# interrupted interview.
PART = re.compile(r"^(.+?)(\d{1,2})$")

# Pieces that turn up at the end of a name and are beside the point.
TAILS = {
    "р", "расшифровка", "аудио", "запись", "final", "финал",
    "копия", "copy", "черновик",
}


@dataclass(frozen=True)
class InterviewName:
    """What could be read from the file name."""

    study: str = ""
    interviewer: str = ""
    number: str = ""
    respondent: str = ""
    part: int = 0
    tail: tuple[str, ...] = ()
    stem: str = ""

    @property
    def recognised(self) -> bool:
        """The name counts as parsed if at least a number and a pseudonym
        were found."""

        return bool(self.number and self.respondent)

    @property
    def label(self) -> str:
        """What to call the interview in the series list."""

        if not self.recognised:
            return self.stem
        return f"{self.number}. {self.respondent}"

    @property
    def key(self) -> str:
        """The interview key: the parts of one recording share it."""

        if not self.recognised:
            return self.stem.casefold()
        return "|".join(
            (
                self.study.casefold(),
                self.interviewer.casefold(),
                self.number.casefold(),
                self.respondent.casefold(),
            )
        )


def parse_interview_name(path: str | Path) -> InterviewName:
    """Parses a recording's file name. An unintelligible name is not an
    error."""

    stem = Path(str(path)).stem.strip()
    if not stem:
        return InterviewName()

    parts = [piece for piece in SEPARATOR.split(stem) if piece]
    if len(parts) < 3:
        return InterviewName(stem=stem)

    # Trailing marks like «-Р» or «-копия» ("-copy") are dropped, but
    # remembered.
    tail: list[str] = []
    while len(parts) > 3 and parts[-1].casefold() in TAILS:
        tail.insert(0, parts.pop())

    study, interviewer = parts[0], parts[1]
    number_match = NUMBER.match(parts[2])
    if number_match is None:
        return InterviewName(stem=stem, tail=tuple(tail))
    number = (number_match.group(1) + number_match.group(2)).strip()

    respondent = " ".join(parts[3:]).strip()
    part = 0
    piece = PART.match(respondent)
    if piece is not None:
        # «Татьяна2» is the second recording of the same interview. But
        # «Р2Д2» ("R2D2") is never a name, so the digit is split off only
        # from a meaningful word.
        name = piece.group(1).strip()
        if len(name) >= 3:
            respondent, part = name, int(piece.group(2))

    return InterviewName(
        study=study,
        interviewer=interviewer,
        number=number,
        respondent=respondent,
        part=part,
        tail=tuple(tail),
        stem=stem,
    )


def group_parts(paths: Iterable[str | Path]) -> list[list[Path]]:
    """Gathers the recordings of one interview together, in part order.

    An interrupted interview sits in the folder as two files; the program
    must transcribe them as one recording, not as two separate
    conversations."""

    groups: dict[str, list[tuple[int, Path]]] = {}
    order: list[str] = []
    for item in paths:
        path = Path(str(item))
        parsed = parse_interview_name(path)
        if parsed.key not in groups:
            groups[parsed.key] = []
            order.append(parsed.key)
        groups[parsed.key].append((parsed.part, path))

    result = []
    for key in order:
        pieces = sorted(groups[key], key=lambda pair: (pair[0], pair[1].name))
        result.append([path for _, path in pieces])
    return result


def study_title_from(paths: Sequence[str | Path]) -> str:
    """The study designation shared by the recordings in the folder."""

    codes = {
        parse_interview_name(path).study
        for path in paths
        if parse_interview_name(path).recognised
    }
    codes.discard("")
    return next(iter(codes)) if len(codes) == 1 else ""

