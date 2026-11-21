#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slips of live speech that cause names to get lost.

The name parser was trained on written texts. An interview transcript is
speech, and speech works differently: a person starts a phrase, cuts
themselves off and starts over. For the parser this breaks the sentence,
and it stops recognising a name that it recognises confidently in an
intact phrase.

The difference is an abandoned preposition «на» ("on"). The person began
«работать над» ("to work on"), changed their mind and said «с» ("with").
This is neither a typo nor a rarity: that is how speech works.

Here the text is rewritten clean, for the parser only. The transcript
text itself is never changed: it is edited only by a human hand during
proofreading. Hence the position map: whatever is found in the clean
copy is put back in its place in the real text.

What gets removed is only an abandoned preposition before another
preposition. Nothing broader: every extra rule is one more way to lose a
word that was actually there.
"""

from __future__ import annotations

import re


__all__ = ["smooth", "restore_span", "FALSE_START"]


PREPOSITIONS = (
    "в|во|на|над|под|перед|при|про|для|до|из|из-за|от|к|ко|о|об|обо|"
    "по|с|со|у|за|без|через|между"
)

# A preposition immediately followed by another preposition: the first
# one was abandoned.
FALSE_START = re.compile(
    rf"(?<![\w-])(?:{PREPOSITIONS})(\s+)(?=(?:{PREPOSITIONS})(?![\w-])\s)",
    re.IGNORECASE,
)


def smooth(text: str) -> tuple[str, list[int]]:
    """Text without abandoned prepositions, plus the position map back.

    The map is as long as the clean copy: map[i] is where that character
    sits in the original text."""

    pieces: list[str] = []
    offsets: list[int] = []
    position = 0
    for found in FALSE_START.finditer(text):
        pieces.append(text[position:found.start()])
        offsets.extend(range(position, found.start()))
        position = found.end()
    pieces.append(text[position:])
    offsets.extend(range(position, len(text)))
    return "".join(pieces), offsets


def restore_span(start: int, stop: int, offsets: list[int]) -> tuple[int, int] | None:
    """Maps a finding's boundaries from the clean copy back to the real text."""

    if start < 0 or stop <= start or stop > len(offsets):
        return None
    return offsets[start], offsets[stop - 1] + 1
