#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn boundaries and names in quotation marks.

Both checks grew out of comparing two proofreads of the same interview: a
full one done by a person, and one where they fixed only what the program
showed. The difference was 111 places, and a third of them turned out to
be not spelling but torn and glued-together turns.
"""

import unittest


from alumenans.proofreading import analyze_proofreading
from alumenans.transcription.core import (
    TranscriptTurn,
    merge_transcript_turns,
    unfinished_phrase,
)
from alumenans import i18n


def setUpModule():
    # These tests check Russian interface messages.
    previous = i18n.get_language()
    i18n.set_language("ru", persist=False)
    globals()["_previous_language"] = previous


def tearDownModule():
    i18n.set_language(_previous_language, persist=False)


def turn(start, end, speaker, text):
    return TranscriptTurn(start=start, end=end, speaker=speaker, text=text)


class UnfinishedPhraseTests(unittest.TestCase):
    def test_a_preposition_cannot_end_a_phrase(self):
        for tail in ("цепляют вот в", "узнала про это и", "думаю о том, что"):
            with self.subTest(tail=tail):
                self.assertTrue(unfinished_phrase(tail))

    def test_a_hanging_comma_is_the_same_thing(self):
        self.assertTrue(unfinished_phrase("это же сервис Яндекса,"))

    def test_a_finished_phrase_is_left_alone(self):
        for finished in ("Я отписалась.", "Хорошо!", "А почему?", "Ну вот…"):
            with self.subTest(finished=finished):
                self.assertFalse(unfinished_phrase(finished))

    def test_nothing_is_not_unfinished(self):
        self.assertFalse(unfinished_phrase(""))
        self.assertFalse(unfinished_phrase("   "))


class MergingTests(unittest.TestCase):
    """A pause in the middle of a thought is not the end of a turn."""

    def test_a_long_pause_inside_a_phrase_does_not_split_the_turn(self):
        # Three seconds of silence after «о том, что» ("about the fact that")
        # — the person is thinking.
        turns = [
            turn(0.0, 10.0, 1, "Я всё время думаю о том, что"),
            turn(13.4, 20.0, 1, "Травмы нашего общества никуда не делись."),
        ]
        merged = merge_transcript_turns(turns)
        self.assertEqual(len(merged), 1)
        self.assertIn("думаю о том, что Травмы", merged[0].text)

    def test_a_long_pause_after_a_finished_phrase_still_splits(self):
        turns = [
            turn(0.0, 10.0, 1, "Я отписалась."),
            turn(13.4, 20.0, 1, "А потом снова подписалась."),
        ]
        self.assertEqual(len(merge_transcript_turns(turns)), 2)

    def test_a_different_speaker_is_never_merged(self):
        # Even mid-word: another speaker's speech stays theirs.
        turns = [
            turn(0.0, 10.0, 1, "Я думаю о том, что"),
            turn(11.0, 20.0, 2, "Угу."),
        ]
        self.assertEqual(len(merge_transcript_turns(turns)), 2)

    def test_a_short_pause_merges_as_before(self):
        turns = [
            turn(0.0, 10.0, 1, "Я отписалась."),
            turn(10.5, 20.0, 1, "Совсем."),
        ]
        self.assertEqual(len(merge_transcript_turns(turns)), 1)


class BrokenPhraseIssueTests(unittest.TestCase):
    """What can't be stitched together, the person must be told about."""

    def remarks(self, turns):
        return analyze_proofreading(turns)["issues"]

    def test_a_phrase_broken_across_speakers_is_reported(self):
        turns = [
            {"id": "t1", "speaker": 1, "start": 0.0, "end": 10.0,
             "text": "Ну, в смысле, каналы связи в"},
            {"id": "t2", "speaker": 2, "start": 11.0, "end": 20.0,
             "text": "Именно, если посмотреть на подписки."},
        ]
        messages = [item["message"] for item in self.remarks(turns)]
        self.assertTrue(any("на полуслове" in text for text in messages))

    def test_a_finished_phrase_before_another_speaker_is_normal(self):
        turns = [
            {"id": "t1", "speaker": 1, "start": 0.0, "end": 10.0,
             "text": "А почему вы отписались?"},
            {"id": "t2", "speaker": 2, "start": 11.0, "end": 20.0,
             "text": "Денег стало не хватать."},
        ]
        messages = [item["message"] for item in self.remarks(turns)]
        self.assertFalse(any("на полуслове" in text for text in messages))


class UnquotedNameTests(unittest.TestCase):
    """«Перевела в нужна помощь» ("donated to help needed"): the reader trips up."""

    def remarks(self, text):
        turns = [{"id": "t1", "speaker": 1, "start": 0.0, "end": 10.0, "text": text}]
        return [
            item
            for item in analyze_proofreading(turns)["issues"]
            if "в кавычки" in item["message"]
        ]

    def test_a_known_organisation_is_offered_quotes(self):
        found = self.remarks("Я перевела деньги в Нужна помощь в том году.")
        self.assertTrue(found)
        self.assertEqual(found[0]["suggestion"], "«Нужна помощь»")

    def test_an_organisation_already_in_quotes_is_left_alone(self):
        self.assertEqual(self.remarks("Я перевела в «Нужна помощь»."), [])

    def test_a_one_word_name_is_not_touched(self):
        # A one-word name reads fine even without quotation marks.
        self.assertEqual(self.remarks("Подписалась на Марем."), [])


if __name__ == "__main__":
    unittest.main()
