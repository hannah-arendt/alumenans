#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A turn must end with a punctuation mark.

Recognition leaves out the final mark on about a quarter of turns, and
the text reads as if cut off mid-word. Not just any mark will do: a turn
ending in «в» ("in") or «что» ("that") really was cut off — the person was
interrupted, and a full stop there would be a lie.
"""

import unittest


from alumenans.proofreading import close_the_sentence, normalize_transcript_text


class ClosingTests(unittest.TestCase):
    def close_sentence(self, text):
        return close_the_sentence(text)[0]

    def test_a_finished_thought_gets_a_full_stop(self):
        self.assertEqual(self.close_sentence("Это очень страшно"), "Это очень страшно.")

    def test_an_interrupted_phrase_gets_an_ellipsis(self):
        # A sentence doesn't end on a preposition or conjunction — speech was cut off.
        for cut_off in (
            "было а-а читательницам, там, в",
            "я не помню точно, потому что",
            "мы говорили о том, как",
        ):
            with self.subTest(cut_off=cut_off):
                self.assertTrue(self.close_sentence(cut_off).endswith("…"))

    def test_a_hanging_comma_is_not_a_sentence_end(self):
        # «…и по каким-то там,» ("…and for some sort of,") — a comma at the end
        # of a turn doesn't count.
        self.assertEqual(self.close_sentence("и по каким-то там,"), "и по каким-то там…")

    def test_nothing_to_close_is_not_an_error(self):
        self.assertEqual(self.close_sentence(""), "")
        self.assertEqual(self.close_sentence("   "), "")
        self.assertEqual(self.close_sentence("2024"), "2024")

    def test_the_change_is_counted_as_a_fix(self):
        self.assertEqual(close_the_sentence("Да")[1], 1)
        self.assertEqual(close_the_sentence("Да.")[1], 0)


class TogetherWithTheRestTests(unittest.TestCase):
    """The closing mark is added in the general cleanup, not separately."""

    def test_the_cleanup_closes_the_sentence_too(self):
        ready, fixes = normalize_transcript_text("Потому что я не знаю")
        self.assertTrue(ready.endswith("."))
        self.assertGreaterEqual(fixes, 1)

    def test_words_are_not_touched(self):
        # A punctuation mark is not a word: aligning the text to the recording
        # relies on the word count, and it must not get thrown off.
        before = "мы от кого-то отписываемся или не подписываемся"
        after, _ = normalize_transcript_text(before)
        self.assertEqual(len(before.split()), len(after.rstrip(".…").split()))


if __name__ == "__main__":
    unittest.main()
