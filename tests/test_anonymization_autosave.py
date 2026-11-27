#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Anonymisation saves itself.

Previously it lived only in the window's memory. On a sociologist's Mac
the window reset, and the work was lost entirely — there was nothing to
restore: only the proofreading turns, the guide and the notes went into
the project file.

What is saved now: the anonymised text. The "name → pseudonym" mapping
is not in the project and must not be — project files get passed
around, and the key lives under a password. So after a crash the
replacements remain made, but they can't be undone. It's the same rule
as with a forgotten password.

Numbering is checked separately. Pseudonyms assigned before the crash
stay in the text, and the count must continue from them: a second
«Персона 1» ("Person 1") would mean two different people can no longer
be told apart.
"""

import re
import unittest

from alumenans.paths import PACKAGE_DIR

FILES = PACKAGE_DIR

SCREEN = (FILES / "resources" / "anonymizer.html").read_text(encoding="utf-8")



def handler(element_id):
    """The whole top-level `$("…").onclick = …` handler, up to its closing `};`.

    Not a fixed number of characters: comments inside the handler may grow."""
    start = SCREEN.find(f'$("{element_id}").onclick')
    if start < 0:
        raise AssertionError(f"no onclick handler for {element_id}")
    return SCREEN[start:SCREEN.index("\n};", start) + 3]


def body(name):
    start = SCREEN.find(f"function {name}(")
    if start < 0:
        raise AssertionError(f"no function {name}")
    end = SCREEN.find("\nfunction ", start + 1)
    return SCREEN[start:end if end > 0 else len(SCREEN)]


class KeyStaysOutOfTheProjectTests(unittest.TestCase):
    """The study's rule: the key exists only under a password."""

    def test_nothing_sends_the_mapping_to_the_program(self):
        # The only way for the key to get out is the encrypted archive.
        calls = set(re.findall(r'appCall\("([a-z_]+)"', SCREEN))
        for name in calls:
            with self.subTest(call=name):
                self.assertNotIn("key", name.replace("key_archive", ""))

    def test_the_key_leaves_only_as_an_encrypted_archive(self):
        self.assertIn('appCall("save_key_archive"', SCREEN)
        # The password is asked for before saving, not after.
        saving = handler("btnSaveKey")
        self.assertLess(
            saving.find("await askPassword()"),
            saving.find('appCall("save_key_archive"'),
        )


class NumberingTests(unittest.TestCase):
    """Pseudonym numbers continue rather than start over."""

    def test_the_numbers_are_read_from_the_text(self):
        self.assertIn("const seen = takenNumbers(state.raw);", SCREEN)

    def test_an_inflected_pseudonym_counts_too(self):
        # «Работала с Персоной 5» ("worked with Person 5") — the same pseudonym, a different case.
        code = body("takenNumbers")
        self.assertIn("[а-яё]{0,3}", code)
        self.assertIn('.replace(/[аяоеиуыэюё]$/i, "")', code)

    def test_places_are_counted_by_their_own_kind(self):
        # «Город 1» ("City 1") and «Страна 1» ("Country 1") are separate counters, as in the suggestions.
        self.assertIn('labels.push(["place:" + placeKindKey, label])', body("takenNumbers"))


class CarryTests(unittest.TestCase):
    """Carrying replacements into the text must survive repeated calls."""

    def test_a_new_file_forgets_the_previous_map(self):
        self.assertIn("state.turns = [];", body("load"))


if __name__ == "__main__":
    unittest.main()
