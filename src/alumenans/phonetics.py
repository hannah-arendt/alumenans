#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""How alike two words sound.

Speech recognition makes mistakes with its ears, not its eyes.
So words are first converted into a rough sound notation, and the
comparison is done on that.

The notation is deliberately rough. The goal is not to transcribe the
word correctly (we don't know the stress anyway) but to make the words
that the ear hears the same come out equal.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

# Vowels are reduced to two series. Unstressed «о» and «а» sound the same
# in Russian, as do «е» and «и»; stress cannot be recovered from the text,
# so differences within a series are erased while the series stay
# distinct: «Марем» and «Морем» are one word, «Марем» and «Мурам» are
# different.
SOUNDS = {
    "а": "A", "о": "A", "ы": "A", "у": "A", "э": "A",
    "е": "I", "ё": "I", "и": "I", "ю": "I", "я": "I", "й": "I",
    # Voiced/voiceless pairs: at the end of a word and before a consonant
    # they coincide, and recognition confuses them all the time.
    "б": "P", "п": "P",
    "в": "F", "ф": "F",
    "г": "K", "к": "K",
    "д": "T", "т": "T",
    "ж": "W", "ш": "W", "щ": "W",
    "з": "S", "с": "S",
    "ц": "S",
    "ч": "Q",
    "х": "H",
    "л": "L", "м": "M", "н": "N", "р": "R",
    # The soft and hard signs have no sound of their own.
    "ь": "", "ъ": "",
}

# Latin script occurs in names and titles; we map it to the same sounds.
LATIN = {
    "a": "A", "e": "I", "i": "I", "o": "A", "u": "A", "y": "I",
    "b": "P", "p": "P", "v": "F", "f": "F", "w": "F",
    "g": "K", "k": "K", "c": "K", "q": "K", "x": "KS",
    "d": "T", "t": "T",
    "z": "S", "s": "S",
    "j": "I", "h": "H",
    "l": "L", "m": "M", "n": "N", "r": "R",
}

LETTERS = re.compile(r"[^a-zа-яё]+")


@lru_cache(maxsize=8192)
def phonetic_key(word: str) -> str:
    """Rough sound notation of a word.

    Words that the ear hears the same get the same notation."""

    clean = LETTERS.sub("", str(word or "").lower())
    sounds = []
    for letter in clean:
        sound = SOUNDS.get(letter)
        if sound is None:
            sound = LATIN.get(letter, "")
        # Doubling can't be heard: «Ммультик» and «Мультик» ("cartoon") are
        # one word.
        if sound and (not sounds or sounds[-1] != sound):
            sounds.append(sound)
    return "".join(sounds)


def sounds_like(left: str, right: str) -> bool:
    """The words sound the same."""

    key = phonetic_key(left)
    return bool(key) and key == phonetic_key(right)


def _edit_distance(left_text: str, right_text: str) -> int:
    """How many sounds have to be substituted, inserted or removed."""

    if left_text == right_text:
        return 0
    previous_row = list(range(len(right_text) + 1))
    for i, sound in enumerate(left_text, start=1):
        current_row = [i]
        for j, other in enumerate(right_text, start=1):
            current_row.append(
                min(
                    previous_row[j] + 1,
                    current_row[j - 1] + 1,
                    previous_row[j - 1] + (sound != other),
                )
            )
        previous_row = current_row
    return previous_row[-1]


def phonetic_similarity(left: str, right: str) -> float:
    """How close two words are by ear, from 0 to 1.

    Computed from the number of edits, not from shared chunks: shared
    chunks overstate closeness where a word has merely grown an ending.
    «Марем» and «Марина» share four sounds out of six, but they are
    different names, and it is precisely the edit count that tells them
    apart.

    Words of too different a length are not compared at all: «Марем» and
    «Мариамна» sound alike at the start, but they are never one word."""

    left_text = phonetic_key(left)
    right_text = phonetic_key(right)
    if not left_text or not right_text:
        return 0.0
    ratio = min(len(left_text), len(right_text)) / max(len(left_text), len(right_text))
    if ratio < 0.48:
        return 0.0
    if left_text == right_text:
        return 1.0
    return 1.0 - _edit_distance(left_text, right_text) / max(len(left_text), len(right_text))
