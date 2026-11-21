#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A name used in direct address.

«Миш» is not one of the forms of the word «Миша» but a separate,
vocative one. The name parser doesn't recognize it, and such names stayed
in the anonymized text. It's the most common form of address in
interviews.

The morphology checks are stubbed: the tests mustn't depend on whether
the dictionary is installed on the machine where they run.
"""

import re
import unittest

from alumenans.paths import PACKAGE_DIR


from alumenans.vocative import full_forms, vocative_spans


# A small dictionary instead of morphology: full names are listed, and the
# truncated ones are derived from them by the same rule as in the program.
NAMES = {"Миша", "Саша", "Катя", "Оля", "Женя", "Наташа", "Дима", "Света", "Лена"}
TRUNCATED = {short
             for name in NAMES
             for short in ([name[:-1]] if name.endswith("а") else [name[:-1] + "ь"])}


def find(text):
    spans = vocative_spans(
        text,
        is_vocative_form=lambda word: word in TRUNCATED,
        is_personal_name=lambda word: word in NAMES,
    )
    return [text[s["start"]:s["stop"]] for s in spans]


class FormTests(unittest.TestCase):
    def test_a_name_in_a_loses_its_last_letter(self):
        self.assertIn("Миша", full_forms("Миш"))
        self.assertIn("Наташа", full_forms("Наташ"))

    def test_a_name_in_ya_ends_in_a_soft_sign(self):
        self.assertEqual(full_forms("Кать"), ["Катя"])
        self.assertEqual(full_forms("Оль"), ["Оля"])


class VocativeTests(unittest.TestCase):
    def test_the_form_iuliia_named(self):
        # The case reported explicitly: «Миш» wasn't recognized as a name.
        self.assertEqual(find("Миш, привет."), ["Миш"])

    def test_address_at_the_end(self):
        self.assertEqual(find("Привет, Миш!"), ["Миш"])

    def test_address_in_the_middle(self):
        self.assertEqual(find("Я говорю, Наташ, не надо."), ["Наташ"])

    def test_a_soft_sign_name(self):
        self.assertEqual(find("Кать, ты чего?"), ["Кать"])
        self.assertEqual(find("Спасибо, Оль."), ["Оль"])

    def test_without_a_comma_there_is_no_find(self):
        """A comma is the only thing that tells an address from a word.

        «Свет» and «Лен» look both like truncated names and like ordinary
        words ("light", "flax"). Without punctuation there's nothing to
        tell them apart, and then it's better not to match: the person
        sorts through false matches by hand."""

        self.assertEqual(find("Миш ушёл домой"), [])

    def test_an_ordinary_capitalised_word_is_not_a_name(self):
        self.assertEqual(find("Мы были в Питере, Мурманске и Сочи."), [])

    def test_a_full_name_is_left_to_the_ordinary_finder(self):
        # «Лена» is a full name; the sentence parser finds it.
        self.assertEqual(find("Я думаю, Лена придёт."), [])

    def test_the_find_is_marked_uncertain(self):
        spans = vocative_spans(
            "Миш, привет.",
            is_vocative_form=lambda word: word in TRUNCATED,
            is_personal_name=lambda word: word in NAMES,
        )
        self.assertEqual(spans[0]["cat"], "person")
        self.assertEqual(spans[0]["conf"], "weak")

    def test_offsets_point_at_the_word(self):
        text = "Ну, Саш, слушай."
        spans = vocative_spans(
            text,
            is_vocative_form=lambda word: word in TRUNCATED,
            is_personal_name=lambda word: word in NAMES,
        )
        self.assertEqual(text[spans[0]["start"]:spans[0]["stop"]], "Саш")


class WiringTests(unittest.TestCase):
    """The rule must be wired in, not merely written."""

    SOURCE = (PACKAGE_DIR / "app.py").read_text(
        encoding="utf-8"
    )

    def test_the_rule_is_used_when_spans_are_collected(self):
        self.assertIn("spans.extend(vocative_name_spans(text))", self.SOURCE)

    def test_a_missing_morphology_does_not_break_the_rest(self):
        # The morphology dictionary is not a mandatory part of parsing.
        start = self.SOURCE.find("def vocative_name_spans(")
        end = self.SOURCE.find("\ndef ", start + 1)
        body = self.SOURCE[start:end]
        self.assertIn("except Exception:", body)
        self.assertIn("return []", body)


if __name__ == "__main__":
    unittest.main()
