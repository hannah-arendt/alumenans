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

SCREEN = (FILES / "resources" / "app.html").read_text(encoding="utf-8")

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
        self.assertIn('$("btnUndoProof").disabled = !PROOF.canUndo;', body("renderProof"))

    def test_a_running_check_does_not_block_it(self):
        # Undo asks the program nothing; there is nothing to wait for.
        self.assertNotIn(
            '$("btnUndoProof").disabled = PROOF.loading', SCREEN
        )

    def test_the_keyboard_still_works(self):
        self.assertIn("if (isUndo) void undoProof(); else void redoProof();", SCREEN)


if __name__ == "__main__":
    unittest.main()
