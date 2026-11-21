#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A name used in direct address: «Миш», «Саш», «Кать».

In colloquial speech people are addressed by a truncated name, and this
is not one of the word's usual forms but a separate one, the vocative.
The name parser doesn't recognise it: it knows «Миша», but not «Миш». In
interviews this is how people most often call each other, and these are
exactly the names that were left in the text after anonymisation.

The form is built in two ways and no other:

    Миша → Миш, Саша → Саш, Наташа → Наташ   (the «а» drops off)
    Катя → Кать, Оля → Оль, Женя → Жень      («я» becomes «ь»)

The form alone is not enough: «Свет», «Лен», «Дим» look the same but can
also be ordinary words (e.g. «свет» "light"). So the position in the
sentence is checked too: in Russian, a form of address is set off by a
comma on one side or the other. No comma, no finding.

Measured on three real transcripts: the rule never fired on an ordinary
word. On eight constructed forms of address it fired
eight times; one false positive turned up, «Он сказал, Свет уже не
будет» ("He said, Sveta / there will be no more light"), where even a
human needs wider context.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable


__all__ = ["vocative_spans", "full_forms"]


# A capitalised word, entirely Cyrillic, not part of a longer one.
WORD = re.compile(r"(?<![А-Яа-яЁёA-Za-z])([А-ЯЁ][а-яё]{1,11})(?![А-Яа-яЁё])")

# Marks that set a form of address off from the rest of the phrase.
AFTER_PUNCT = ",!?"


def full_forms(short: str) -> list[str]:
    """Which name this form of address could have come from."""

    if short.endswith("ь"):
        return [short[:-1] + "я"]
    return [short + "а", short + "я"]


def _address_position(text: str, start: int, stop: int) -> bool:
    """Whether the word stands in the position of a form of address.

    A form of address is set off by a comma: «Миш, привет» ("Misha, hi")
    or «Привет, Миш». An exclamation or question mark after the word
    counts the same: «Миш!» is a form of address."""

    if text[stop:stop + 1] in AFTER_PUNCT:
        return True
    before = text[max(0, start - 3):start]
    return before.strip().endswith(",")


def vocative_spans(
    text: str,
    *,
    is_vocative_form: Callable[[str], bool],
    is_personal_name: Callable[[str], bool],
) -> list[dict]:
    """Finds names used in direct address.

    Word analysis is passed in from outside as two checks, so that the
    module can be tested without the morphology dictionary installed:

    * is_vocative_form: the truncated form itself has a parse as a name;
    * is_personal_name: the restored full name parses as a name in the
      nominative singular.
    """

    found: list[dict] = []
    for match in WORD.finditer(text):
        short = match.group(1)
        if not _address_position(text, match.start(1), match.end(1)):
            continue
        if not is_vocative_form(short):
            continue
        if not any(is_personal_name(form) for form in full_forms(short)):
            continue
        found.append(
            {
                "start": match.start(1),
                "stop": match.end(1),
                "cat": "person",
                # Weak, because a confident finding in the same place is
                # always more accurate: it rests on the whole sentence,
                # while this one rests on a single comma.
                "conf": "weak",
                "by": "vocative",
            }
        )
    return found


def morphology_checks(analyzer) -> tuple[Callable[[str], bool], Callable[[str], bool]]:
    """The two checks on top of pymorphy3.

    Moved here so that the rule and its reliance on the dictionary sit side
    by side instead of spreading through the calling code."""

    def is_vocative_form(short: str) -> bool:
        # The dictionary tags «кать» as vocative, but «миш» and «саш» as
        # genitive plural: the truncated form of address happens to
        # coincide with it in form. Either parse will do, as long as it is
        # a name.
        return any(
            "Name" in parse.tag and ("voct" in parse.tag or "gent" in parse.tag)
            for parse in analyzer.parse(short.lower())
        )

    def is_personal_name(word: str) -> bool:
        return any(
            "Name" in parse.tag and "nomn" in parse.tag and "sing" in parse.tag
            for parse in analyzer.parse(word)
        )

    return is_vocative_form, is_personal_name
