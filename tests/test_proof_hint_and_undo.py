#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The hint about small edits, and undo.

In the proofreading window letters and punctuation are edited: a turn is
tied to the recording's words, and rewriting it wholesale isn't allowed
there. The hint about this was shown on switching to the tab and faded
after a minute. Whoever hadn't read it learned the rule only one way — by
starting to edit the wrong way, and nobody told them.

Undo: the program had a history mechanism, but nothing to press. Ctrl+Z
worked only outside the text, and a person who had mistakenly reassigned
a turn to another voice couldn't roll it back.
"""

import re
import unittest

from alumenans.i18n import screen_catalogs
from alumenans.paths import PACKAGE_DIR


def catalog(language="ru"):
    return screen_catalogs()[language]


def languages():
    return list(screen_catalogs())


FILES = PACKAGE_DIR

SCREEN = (FILES / "resources" / "anonymizer.html").read_text(encoding="utf-8")

HINT_TEXT = (
    "В этом окне можно вносить только мелкие правки — менять буквы в слове, "
    "знаки препинания. Чтобы отредактировать текст более масштабно, меняя "
    "число слов, выделите фрагмент для редактирования в этом окне, кликните "
    "по нему правой кнопкой мыши, и редактируйте в правом столбце, как если "
    "бы программа сама предложила этот фрагмент к вычитке."
)


def body(name):
    start = SCREEN.find(f"function {name}(")
    if start < 0:
        raise AssertionError(f"no function {name}")
    end = SCREEN.find("\nfunction ", start + 1)
    return SCREEN[start:end if end > 0 else len(SCREEN)]


class HintTests(unittest.TestCase):
    def test_the_wording_is_the_one_iuliia_wrote(self):
        block = re.search(
            r'<div class="proof-edit-hint".*?</div>', SCREEN, re.S
        ).group(0)
        self.assertIn('<p data-i18n="proof.edit_hint"></p>', block)
        self.assertEqual(catalog("ru")["proof.edit_hint"], HINT_TEXT)

    def test_it_can_be_closed_by_a_cross(self):
        self.assertIn('id="btnCloseEditHint"', SCREEN)
        self.assertIn('$("btnCloseEditHint").onclick = dismissEditHint;', SCREEN)

    def test_it_floats_above_the_text(self):
        # It has to appear when the person is already editing; the page
        # can't be shifted under them at that moment.
        style = re.search(r"\.proof-edit-hint\{[^}]*\}", SCREEN, re.S).group(0)
        self.assertIn("position:sticky", style)
        self.assertIn("z-index", style)

    def test_a_big_edit_brings_it_back(self):
        self.assertIn("watchBigEdit(editor);", SCREEN)
        code = body("watchBigEdit")
        self.assertIn("countWords(editor.textContent) !== wordsBefore", code)
        self.assertIn("showEditHint()", code)

    def test_the_word_count_is_taken_when_editing_starts(self):
        self.assertIn(
            "editor.dataset.wordsAtFocus = String(countWords(editor.textContent));",
            SCREEN,
        )

    def test_a_paste_counts_as_a_big_edit(self):
        # The word count may match by chance, but a paste is still a paste.
        self.assertIn('$("proofTurns").addEventListener("paste"', SCREEN)

    def test_the_returned_hint_does_not_vanish_on_a_timer(self):
        # A minute may not be enough: the person is busy editing at that moment.
        self.assertIn("clearTimeout(PROOF.hintTimer);", body("showEditHint"))
        self.assertNotIn("setTimeout", body("showEditHint"))


class UndoTests(unittest.TestCase):
    def test_there_is_a_button(self):
        self.assertIn('id="btnUndoProof"', SCREEN)
        self.assertIn('$("btnUndoProof").onclick = () => { undoProof(); };', SCREEN)

    def test_the_button_shows_a_curved_arrow(self):
        button = re.search(r'<button class="proof-undo".*?</button>', SCREEN, re.S).group(0)
        self.assertIn("<svg", button)
        self.assertIn("<path", button)
        self.assertNotIn("↶", button)          # the icon is drawn, not typed

    def test_the_shortcut_is_named_where_the_person_looks(self):
        button = re.search(r'<button class="proof-undo".*?</button>', SCREEN, re.S).group(0)
        self.assertIn('data-i18n-title="common.undo_shortcut"', button)
        for language in languages():
            self.assertIn("Ctrl+Z", catalog(language)["common.undo_shortcut"])

    def test_it_wakes_up_only_when_there_is_something_to_undo(self):
        self.assertIn('$("btnUndoProof").disabled = !HISTORY.past.length;', body("renderProof"))

    def test_a_running_check_does_not_block_it(self):
        # Undo asks the program nothing; there is nothing to wait for.
        self.assertNotIn(
            '$("btnUndoProof").disabled = PROOF.loading', SCREEN
        )

    def test_the_keyboard_still_works(self):
        self.assertIn("if (isUndo) undoProof(); else redoProof();", SCREEN)


if __name__ == "__main__":
    unittest.main()
