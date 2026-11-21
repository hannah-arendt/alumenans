#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""«Реплика другого спикера» ("Another speaker's turn") in proofreading.

The button splits a turn into three parts — before the selection, the
selection, after — and gives the middle one to another voice. While a piece
of a turn is selected, everything works. But the most common case is a
different one: a person sees that the whole turn is attributed to the wrong
speaker and selects all of it.

Then there was only one part, and there was a check "fewer than two parts —
refuse". The click did nothing: no change, no message saying why.
Verified: it was like this in the 13 August build too, so the bug is
not new.

Now a fully selected turn simply changes its voice, without splitting.
"""

import unittest

from alumenans.paths import PACKAGE_DIR

FILES = PACKAGE_DIR

SCREEN = (FILES / "resources" / "anonymizer.html").read_text(encoding="utf-8")


def body(name):
    start = SCREEN.find(f"function {name}(")
    if start < 0:
        raise AssertionError(f"no function {name}")
    end = SCREEN.find("\nfunction ", start + 1)
    return SCREEN[start:end if end > 0 else len(SCREEN)]


class WholeTurnTests(unittest.TestCase):
    CODE = body("moveSelectionToSpeaker")

    def test_a_whole_turn_changes_its_voice(self):
        self.assertIn(
            "if (pieces.length === 1 && turn.text.slice(start, end).trim() === turn.text.trim())",
            self.CODE,
        )
        self.assertIn("turn.speaker = speaker;", self.CODE)

    def test_the_turn_counts_as_edited_by_hand(self):
        # Otherwise the proofreading check will offer to restore the previous speaker.
        tail = self.CODE[self.CODE.find("pieces.length === 1"):]
        self.assertIn("PROOF.manuallyEdited.add(turn.id)", tail)

    def test_a_part_of_a_turn_still_splits_it(self):
        # The old behaviour must stay: a piece is selected — the turn is split.
        self.assertIn("if (pieces.length < 2) return false;", self.CODE)
        self.assertIn("PROOF.turns.splice(spot.turnIndex, 1, ...created);", self.CODE)


class ButtonTests(unittest.TestCase):
    def test_two_voices_switch_without_asking(self):
        # With two voices there is nothing to choose: the other one is the second.
        self.assertIn("return Number(current) === 1 ? 2 : 1;", body("otherSpeaker"))

    def test_more_than_two_voices_ask_which_one(self):
        self.assertIn('speakerCount() > 2', SCREEN)
        self.assertIn('id="manualSpeaker"', SCREEN)

    def test_one_voice_recording_still_offers_a_second(self):
        """The recording is marked up with one voice, but in fact there are two.

        The counter deliberately doesn't go below two: otherwise there
        would be no one to reassign a turn to, and a markup error couldn't
        be fixed by hand."""

        self.assertIn("return Math.max(2, asked || 0, ...seen);", body("speakerCount"))


if __name__ == "__main__":
    unittest.main()
