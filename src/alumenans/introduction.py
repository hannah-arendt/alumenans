#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The place where the respondent says their own name.

Almost every interview starts with «меня зовут…» ("my name is…"), and
this is the only name in the transcript whose replacement is known in
advance: the pseudonym is in the file name.

There is one trap: following the guide, the interviewer introduces
themselves too. So it is not enough to find «меня зовут»; we have to work
out who said it. The speaker is determined from the «Спикер N» ("Speaker
N") labels and from a simple observation: over an interview the
respondent talks many times more than the interviewer.

The program only suggests. The replacement, like any other, happens
after a human has pressed the button.
"""

from __future__ import annotations

import re

__all__ = ["self_introduction_spans", "respondent_speaker"]

# Only what actually occurs. «Я Лена» ("I'm Lena") is left out on
# purpose: «я думаю» ("I think"), «я работала» ("I worked") are the same
# construction, and telling them apart by meaning would be guesswork.
INTRODUCTION_RE = re.compile(
    r"(?:меня\s+зовут|зовут\s+меня|меня\s+звать|"
    r"мо[её]\s+имя(?:\s*[-—–:])?)\s+"
    r"(?P<имя>[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){0,2})",
    re.IGNORECASE | re.UNICODE,
)

SPEAKER_LABEL = re.compile(r"^[ \t]*Спикер\s+(\d+)\s*:", re.MULTILINE)
WORD = re.compile(r"[А-Яа-яЁёA-Za-z]+")


def _blocks(text: str):
    """Pieces of text by speaker: (number, start, end)."""

    labels = list(SPEAKER_LABEL.finditer(text))
    if not labels:
        return []
    pieces = []
    for number, label in enumerate(labels):
        end = labels[number + 1].start() if number + 1 < len(labels) else len(text)
        pieces.append((int(label.group(1)), label.end(), end))
    return pieces


def respondent_speaker(text: str) -> int | None:
    """Who the respondent is here. The one who talks more than everyone else."""

    spoken: dict[int, int] = {}
    for number, start, end in _blocks(text):
        spoken[number] = spoken.get(number, 0) + len(WORD.findall(text[start:end]))
    if not spoken:
        return None
    return max(spoken, key=lambda number: spoken[number])


def self_introduction_spans(text: str, speaker: int | None = None) -> list[tuple[int, int]]:
    """Spans holding the name the respondent introduced themselves by.

    If the speaker is not given, it is determined by the amount of speech.
    The interviewer's turns are skipped: following the guide, they
    introduce themselves too, but their name in the transcript does not
    need replacing."""

    source = str(text or "")
    if not source:
        return []

    pieces = _blocks(source)
    whose = speaker if speaker is not None else respondent_speaker(source)

    found = []
    for match in INTRODUCTION_RE.finditer(source):
        start, end = match.span("имя")
        if pieces and whose is not None:
            own_machine = next(
                (
                    number
                    for number, from_, before_text in pieces
                    if from_ <= start < before_text
                ),
                None,
            )
            if own_machine != whose:
                continue
        found.append((start, end))
    return found
