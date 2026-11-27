#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replacement not everywhere but by choice. And undo on anonymisation.

One name is not always one person. Interviews have namesakes: one
«Наталья» (Natalya) is the respondent, another is the neighbour. Replacement
went by the whole group, and there was no way to separate them.

Undo: decisions on findings are irreversible by nature — a replaced name
can't be recovered from the text, and it's easy to make a mistake, because
findings are worked through one after another, with Enter.

Ctrl+Z: the key was recognised by its letter. With the Russian layout the
same key produces «я», and the shortcut failed for exactly those who work
with Russian text.
"""

import re
import unittest

from alumenans.i18n import screen_catalogs
from alumenans.paths import PACKAGE_DIR


def catalog(language="ru"):
    return screen_catalogs()[language]


FILES = PACKAGE_DIR

SCREEN = (FILES / "resources" / "anonymizer.html").read_text(encoding="utf-8")


def body(name):
    start = SCREEN.find(f"function {name}(")
    if start < 0:
        raise AssertionError(f"no function {name}")
    end = SCREEN.find("\nfunction ", start + 1)
    return SCREEN[start:end if end > 0 else len(SCREEN)]


class OccurrenceTests(unittest.TestCase):
    def test_every_place_can_be_switched_off(self):
        code = body("occurrenceList")
        self.assertIn('type="checkbox" data-occurrence=', code)
        self.assertIn('tr("anon.where_replace"', code)
        self.assertTrue(catalog("ru")["anon.where_replace"].startswith("Где заменять —"))

    def test_each_place_is_shown_with_its_surroundings(self):
        # Without the context, one «Наталья» can't be told from another.
        code = body("occurrenceList")
        self.assertIn("e.start - 42", code)
        self.assertIn("e.end, e.end + 42", code)

    def test_a_single_occurrence_needs_no_list(self):
        self.assertIn("if (g.members.length < 2) return \"\";", body("occurrenceList"))

    def test_the_switched_off_place_keeps_its_original_text(self):
        code = body("isReplaced")
        self.assertIn('g.status === "replaced"', code)
        self.assertIn("!(g.skip && g.skip.has(e.id))", code)

    def test_every_place_that_writes_text_asks_the_same_question(self):
        """Highlighting, the finished text and carrying into proofreading follow one rule.

        If they diverged, the screen would show one thing and the file another."""

        uses = SCREEN.count("isReplaced(g, e)") - SCREEN.count("function isReplaced(g, e)")
        self.assertEqual(uses, 3)

    def test_a_switched_off_place_does_not_reach_the_key(self):
        """It stayed unchanged in the text; it has no place in the key.

        Two places compute the key: the on-screen list and saving to a
        file. Plus the definition itself."""

        uses = (SCREEN.count("replacedForms(g)")
                     - SCREEN.count("function replacedForms(g)"))
        self.assertEqual(uses, 2)
        # A group with all its boxes unticked doesn't get into the key at all.
        self.assertGreaterEqual(SCREEN.count("if (!forms.length) continue;"), 2)
        self.assertIn("if (!forms.length) continue;", SCREEN)


class UndoTests(unittest.TestCase):
    def test_there_is_a_button_on_this_tab_too(self):
        self.assertIn('id="btnUndoAnon"', SCREEN)
        self.assertIn('$("btnUndoAnon").onclick = () => { undoAnon(); };', SCREEN)

    def test_the_state_is_remembered_before_every_decision(self):
        for place in ("apply", "recategorise", "markSelection"):
            with self.subTest(place=place):
                self.assertIn("anonRemember();", body(place))

    def test_a_checkbox_is_a_decision_too(self):
        self.assertIn("anonRemember();\n      if (!g.skip) g.skip = new Set();", SCREEN)

    def test_the_snapshot_keeps_the_key_and_the_choices(self):
        code = body("anonSnapshot")
        for what in ("ents", "groups", "carried", "roles", "active"):
            with self.subTest(what=what):
                self.assertIn(what + ":", code)
        # skip is a Set; in the snapshot it's a list, otherwise it won't survive the copy.
        self.assertIn("skip: [...(g.skip || [])]", code)

    def test_a_new_file_forgets_the_history(self):
        self.assertIn("ANON_HISTORY.past.length = 0;", body("load"))


class LayoutTests(unittest.TestCase):
    """Ctrl+Z and the Russian keyboard layout."""

    def test_the_key_is_recognised_by_its_place_not_its_letter(self):
        # With a Cyrillic layout the same key produces «я».
        self.assertIn('isKey("KeyZ", "z")', SCREEN)
        self.assertIn('isKey("KeyY", "y")', SCREEN)

    def test_the_letter_stays_as_a_fallback(self):
        self.assertIn("place ? place === code : keyLetter === latinKey", SCREEN)

    def test_the_anonymizer_listens_too(self):
        self.assertIn('if ($("anonymizerView").hidden', SCREEN)
        self.assertIn('event.code !== "KeyZ"', SCREEN)


if __name__ == "__main__":
    unittest.main()
