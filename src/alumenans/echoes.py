#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A name once found pulls in its repetitions throughout the text.

The sentence parser decides about each sentence separately. So it may
recognise the same word in one place and miss it in another.
For anonymisation this is the worst kind of error: a person sees that
the name has been handled and does not check the other occurrences.

The rule is simple and needs no dictionary: if a word has already been
recognised as personal data, then every identical word in the same text
is personal data too. Identity here is literal, character by character.
Other case forms of the word are not caught by this rule: morphology
handles those, and here what matters is not to make a mistake.

A repetition inherits the kind of finding from its sample: if that one
is an organisation, then all the repetitions are organisations too.
Confidence is inherited as well, because the source is the very same.
"""

from __future__ import annotations

import re


__all__ = ["echo_spans", "MINIMUM_LENGTH"]


# Short pieces give false matches: «Юг» ("South") will turn up inside «Юга»
# as a separate word with a different meaning. Three letters is the
# threshold below which the rule does more harm than good.
MINIMUM_LENGTH = 3


def _overlaps(start: int, stop: int, taken: list[tuple[int, int]]) -> bool:
    return any(start < span_end and stop > span_start for span_start, span_end in taken)


def echo_spans(text: str, spans: list[dict]) -> list[dict]:
    """Repetitions of already found names that are not among the findings."""

    taken = [(int(s["start"]), int(s["stop"])) for s in spans]
    samples: dict[str, dict] = {}
    for span in spans:
        piece = text[int(span["start"]):int(span["stop"])].strip()
        if len(piece) < MINIMUM_LENGTH:
            continue
        # The first finding of a word becomes the sample: it holds the kind
        # and confidence that all the repetitions will receive.
        samples.setdefault(piece, span)

    added: list[dict] = []
    for piece, sample in samples.items():
        pattern = re.compile(rf"(?<![\w-]){re.escape(piece)}(?![\w-])")
        for found in pattern.finditer(text):
            if _overlaps(found.start(), found.end(), taken):
                continue
            taken.append((found.start(), found.end()))
            new_row = {
                "start": found.start(),
                "stop": found.end(),
                "cat": sample.get("cat"),
                "conf": sample.get("conf", "strong"),
                "by": "echo",
            }
            for key in ("place_kind", "public"):
                if key in sample:
                    new_row[key] = sample[key]
            added.append(new_row)
    return added
