"""The transcript value: how it is built, read and checked."""

import unittest
from dataclasses import replace

from alumenans.document.editing import SplitTurn, apply, retext
from alumenans.document.transcript import (
    Author,
    InvalidTranscript,
    Transcript,
    Turn,
    Word,
    bare,
    changed_turns,
    divide_time,
    from_machine_turns,
    letters,
    occurrences,
    span_words,
    validate,
    word_ranges,
    words_in_range,
)


def machine_turn(speaker, text, start=0.0, step=1.0, length=0.8):
    """A turn as transcription returns it: one word per second."""

    return {
        "speaker": speaker,
        "words": [
            {
                "text": word,
                "start": start + step * index,
                "end": start + step * index + length,
                "confidence": 0.9,
            }
            for index, word in enumerate(text.split())
        ],
    }


def sample():
    return from_machine_turns(
        [
            machine_turn(1, "Привет, как дела?", start=0.0),
            machine_turn(2, "Хорошо. А у тебя?", start=10.0),
        ]
    )


class BuildingTests(unittest.TestCase):
    def test_machine_words_get_identifiers_and_point_at_themselves(self):
        transcript = sample()
        validate(transcript)
        self.assertEqual([word.id for word in transcript.words()], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(transcript.next_word_id, 8)
        self.assertEqual([turn.id for turn in transcript.turns], [1, 2])
        self.assertEqual(transcript.next_turn_id, 3)
        for word in transcript.words():
            self.assertEqual(word.origin, (word.id,))
            self.assertEqual(word.author, Author.SPEECH_MODEL)
            self.assertEqual(word.confidence, 0.9)

    def test_a_turn_has_the_text_and_times_of_its_words(self):
        turn = sample().turns[1]
        self.assertEqual(turn.text, "Хорошо. А у тебя?")
        self.assertEqual(turn.start, 10.0)
        self.assertEqual(turn.end, 13.8)
        self.assertEqual(turn.speaker, 2)

    def test_a_turn_without_words_is_dropped(self):
        transcript = from_machine_turns(
            [machine_turn(1, "Да."), {"speaker": 2, "words": []}, machine_turn(1, "Нет.", start=5.0)]
        )
        validate(transcript)
        self.assertEqual([turn.text for turn in transcript.turns], ["Да.", "Нет."])

    def test_an_entry_holding_two_words_is_split_with_its_time(self):
        transcript = from_machine_turns(
            [{"speaker": 1, "words": [{"text": "в общем", "start": 2.0, "end": 2.6}]}]
        )
        validate(transcript)
        first, second = transcript.turns[0].words
        self.assertEqual((first.text, first.start), ("в", 2.0))
        self.assertAlmostEqual(first.end, 2.1)
        self.assertEqual((second.text, second.end), ("общем", 2.6))
        self.assertAlmostEqual(second.start, 2.1)

    def test_turns_are_put_in_order_of_start(self):
        transcript = from_machine_turns(
            [machine_turn(1, "второй", start=5.0), machine_turn(2, "первый", start=1.0)]
        )
        validate(transcript)
        self.assertEqual([turn.text for turn in transcript.turns], ["первый", "второй"])

    def test_words_that_go_back_in_time_are_straightened(self):
        transcript = from_machine_turns(
            [
                {
                    "speaker": 1,
                    "words": [
                        {"text": "раз", "start": 3.0, "end": 3.5},
                        {"text": "два", "start": 2.9, "end": 3.2},
                    ],
                }
            ]
        )
        validate(transcript)
        self.assertEqual(transcript.turns[0].words[1].start, 3.0)

    def test_time_is_divided_by_letters_and_a_dash_takes_none(self):
        shares = divide_time(10.0, 16.0, ["да", "—", "нет,"])
        self.assertEqual(shares[0][0], 10.0)
        self.assertAlmostEqual(shares[0][1], 12.4)
        self.assertEqual(shares[1][0], shares[1][1])
        self.assertAlmostEqual(shares[2][0], 12.4)
        self.assertEqual(shares[2][1], 16.0)


class ReadingTests(unittest.TestCase):
    def test_letters_ignore_case_and_punctuation_in_any_alphabet(self):
        self.assertEqual(letters("Привет,"), "привет")
        self.assertEqual(letters("«Что-то»"), "чтото")
        self.assertEqual(letters("Қазақстанда!"), "қазақстанда")
        self.assertEqual(letters("—"), "")

    def test_the_bare_word_keeps_what_is_inside(self):
        self.assertEqual(bare("«Что-то»,"), "Что-то")
        self.assertEqual(bare("Нұрғалиқызы."), "Нұрғалиқызы")
        self.assertEqual(bare("—"), "")

    def test_each_word_has_a_place_in_the_text(self):
        turn = sample().turns[0]
        ranges = word_ranges(turn)
        self.assertEqual(ranges, ((0, 7), (8, 11), (12, 17)))
        for word, (start, stop) in zip(turn.words, ranges):
            self.assertEqual(turn.text[start:stop], word.text)

    def test_a_character_range_is_widened_to_whole_words(self):
        turn = sample().turns[0]  # three words, the first with a comma
        self.assertEqual(words_in_range(turn, 0, 6), (0, 1))  # the first word without its comma
        self.assertEqual(words_in_range(turn, 3, 9), (0, 2))
        self.assertEqual(words_in_range(turn, 8, 17), (1, 2))
        self.assertEqual(words_in_range(turn, 7, 8), (1, 0))  # only the space

    def test_a_caret_touches_a_word_only_from_inside(self):
        turn = sample().turns[0]
        self.assertEqual(words_in_range(turn, 9, 9), (1, 1))
        self.assertEqual(words_in_range(turn, 8, 8), (1, 0))
        self.assertEqual(words_in_range(turn, 7, 7), (1, 0))
        self.assertEqual(words_in_range(turn, 17, 17), (3, 0))

    def test_a_span_gives_its_words(self):
        transcript = sample()
        self.assertEqual([word.text for word in span_words(transcript, (2, 3))], ["как", "дела?"])
        self.assertEqual([word.text for word in span_words(transcript, (5, 5))], ["А"])

    def test_a_span_with_a_missing_or_reversed_end_is_no_span(self):
        transcript = sample()
        self.assertIsNone(span_words(transcript, (2, 99)))
        self.assertIsNone(span_words(transcript, (3, 2)))

    def test_a_span_survives_a_split_of_its_turn(self):
        transcript, _ = apply(sample(), SplitTurn(1, 2))
        validate(transcript)
        self.assertEqual(len(transcript.turns), 3)
        self.assertEqual([word.text for word in span_words(transcript, (2, 3))], ["как", "дела?"])

    def test_occurrences_ignore_case_and_punctuation(self):
        transcript = from_machine_turns([machine_turn(1, "Море, морем и «море». Марем")])
        self.assertEqual(occurrences(transcript, "море"), [1, 4])

    def test_only_the_edited_turn_counts_as_changed(self):
        before = sample()
        after, _ = retext(before, 2, "Хорошо. А у вас?")
        self.assertEqual(changed_turns(before, after), ([2], []))
        self.assertIs(after.turns[0], before.turns[0])
        gone, _ = retext(before, 2, "")
        self.assertEqual(changed_turns(before, gone), ([], [2]))
        self.assertEqual(changed_turns(before, before), ([], []))

class ValidationTests(unittest.TestCase):
    def test_a_sound_transcript_passes(self):
        validate(sample())
        validate(Transcript())

    def test_each_broken_rule_is_reported(self):
        transcript = sample()
        first, second = transcript.turns
        word = first.words[0]
        cases = {
            r"word \d+ appears twice": (first, replace(second, words=second.words + (word,))),
            r"turn \d+ appears twice": (first, replace(second, id=first.id)),
            "has no words": (first, replace(second, words=())),
            "has text": (replace(first, words=(replace(word, text="два слова"),)), second),
            "ends before it starts": (replace(first, words=(replace(word, end=-1.0),)), second),
            "starts before the word": (replace(first, words=first.words[::-1]), second),
            "starts before the turn": (second, first),
            "was not issued": (first, Turn(99, 1, second.words)),
        }
        for message, turns in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(InvalidTranscript, message):
                    validate(replace(transcript, turns=turns))

    def test_an_empty_word_is_reported(self):
        transcript = sample()
        turn = Turn(1, 1, (Word(1, "", 0.0, 1.0),))
        with self.assertRaisesRegex(InvalidTranscript, "has text"):
            validate(replace(transcript, turns=(turn,)))


if __name__ == "__main__":
    unittest.main()
