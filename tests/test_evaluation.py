#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testing the quality measure.

A metric deserves no more trust than any other code: if it is wrong itself,
the argument about whether things got better just moves one level up.
"""

import unittest


from alumenans.evaluation import (
    ReferenceTurn,
    SpeakerBlock,
    align_words,
    anonymization_scores,
    best_mapping,
    evaluate,
    evaluate_speakers,
    format_report,
    format_speaker_report,
    looks_timecoded,
    normalize_words,
    parse_reference,
    parse_speaker_reference,
    speaker_error_rate,
    speaker_word_error_rate,
    word_error_rate,
)


TIMECODED = """[00:00] Спикер 1: Угу.
[00:03] Спикер 2: А тогда я ещё раз повторю, что наша цель
понять опыт людей.
[00:20] Спикер 1: Потому что я совершенно без понятия.
[00:30]"""


class ReferenceParsingTests(unittest.TestCase):
    def test_timecodes_and_wrapped_lines(self):
        reference = parse_reference(TIMECODED)
        self.assertEqual(len(reference), 3)
        self.assertEqual(reference.end, 30.0)
        self.assertEqual([turn.start for turn in reference], [0.0, 3.0, 20.0])
        self.assertIn("понять опыт людей", reference[1].text)

    def test_hours_are_understood(self):
        reference = parse_reference("[01:02:03] Спикер 1: Да.\n[01:02:10]")
        self.assertAlmostEqual(reference[0].start, 3723.0)

    def test_closing_timecode_is_required(self):
        with self.assertRaises(ValueError):
            parse_reference("[00:00] Спикер 1: Да.")

    def test_closing_timecode_must_follow_the_last_turn(self):
        with self.assertRaises(ValueError):
            parse_reference("[00:30] Спикер 1: Да.\n[00:10]")

    def test_turn_without_a_timecode_is_reported(self):
        with self.assertRaises(ValueError) as raised:
            parse_reference("[00:00] Спикер 1: Да.\nСпикер 2: Нет.\n[00:30]")
        self.assertIn("without a timecode", str(raised.exception))

    def test_speaker_reference_without_timecodes(self):
        blocks, unlabelled = parse_speaker_reference(
            "Спикер 1: Угу.\n\nСпикер 2: Расскажите о себе.\n\nПродолжение мысли."
        )
        self.assertEqual([block.speaker for block in blocks], [1, 2])
        self.assertIn("Продолжение мысли", blocks[1].text)
        self.assertEqual(len(unlabelled), 1)

    def test_reference_kind_is_detected(self):
        self.assertTrue(looks_timecoded(TIMECODED))
        self.assertFalse(looks_timecoded("Спикер 1: Угу.\n\nСпикер 2: Да."))


class WordErrorTests(unittest.TestCase):
    def test_case_yo_and_punctuation_are_ignored(self):
        self.assertEqual(
            normalize_words("Ещё, «Мария»! Всё."),
            ["еще", "мария", "все"],
        )

    def test_fillers_can_be_dropped(self):
        self.assertEqual(
            normalize_words("Э-э, угу, работа.", keep_fillers=False),
            ["работа"],
        )

    def test_error_kinds_are_counted_separately(self):
        counts = align_words(
            ["один", "два", "три", "четыре"],
            ["один", "две", "четыре", "пять"],
        )
        self.assertEqual(counts.substitutions, 1)
        self.assertEqual(counts.deletions, 1)
        self.assertEqual(counts.insertions, 1)
        self.assertEqual(counts.reference_words, 4)
        self.assertAlmostEqual(counts.rate, 0.75)

    def test_identical_text_has_no_errors(self):
        counts = word_error_rate("Мне сорок лет.", "мне сорок лет")
        self.assertEqual(counts.errors, 0)
        self.assertAlmostEqual(counts.rate, 0.0)


class SpeakerMappingTests(unittest.TestCase):
    def test_renumbered_voices_are_matched(self):
        weights = {(1, 2): 90.0, (2, 1): 80.0, (1, 1): 5.0, (2, 2): 4.0}
        self.assertEqual(best_mapping(weights, [1, 2], [1, 2]), {1: 2, 2: 1})

    def test_extra_voice_is_left_unmatched(self):
        weights = {(1, 1): 50.0, (2, 2): 40.0, (3, 2): 1.0}
        mapping = best_mapping(weights, [1, 2, 3], [1, 2])
        self.assertEqual(mapping[1], 1)
        self.assertEqual(mapping[2], 2)
        self.assertNotIn(3, mapping)


class SpeakerTimeTests(unittest.TestCase):
    def setUp(self):
        self.reference = parse_reference(TIMECODED)

    def test_perfect_attribution_with_swapped_numbers(self):
        hypothesis = [
            {"start": 0.0, "end": 3.0, "speaker": 2, "text": "Угу."},
            {"start": 3.0, "end": 20.0, "speaker": 1, "text": "А тогда."},
            {"start": 20.0, "end": 30.0, "speaker": 2, "text": "Потому что."},
        ]
        score = speaker_error_rate(self.reference, hypothesis)
        self.assertLess(score.rate, 0.01)
        self.assertEqual(score.mapping, {2: 1, 1: 2})

    def test_swapped_turn_is_counted_by_time(self):
        hypothesis = [
            {"start": 0.0, "end": 3.0, "speaker": 1, "text": "Угу."},
            {"start": 3.0, "end": 20.0, "speaker": 1, "text": "А тогда."},
            {"start": 20.0, "end": 30.0, "speaker": 2, "text": "Потому что."},
        ]
        score = speaker_error_rate(self.reference, hypothesis)
        # The first three seconds are attributed to the wrong voice — a tenth of the window.
        self.assertAlmostEqual(score.rate, 0.1, places=1)

    def test_silence_in_the_hypothesis_counts_as_an_error(self):
        hypothesis = [{"start": 0.0, "end": 3.0, "speaker": 1, "text": "Угу."}]
        score = speaker_error_rate(self.reference, hypothesis)
        self.assertGreater(score.rate, 0.85)


class SpeakerWordTests(unittest.TestCase):
    def test_attribution_is_compared_word_by_word(self):
        blocks = [
            SpeakerBlock(1, "Угу."),
            SpeakerBlock(2, "Расскажите немного о себе, пожалуйста."),
            SpeakerBlock(1, "Я художница."),
        ]
        hypothesis = [
            {"speaker": 2, "text": "Угу. Расскажите немного о себе, пожалуйста."},
            {"speaker": 1, "text": "Я художница."},
        ]
        score = speaker_word_error_rate(blocks, hypothesis)
        # «Угу» ("uh-huh") went to the wrong voice: one word out of eight.
        self.assertEqual(score.misattributed, 1)
        self.assertEqual(score.scored, 8)
        self.assertAlmostEqual(score.rate, 0.125)

    def test_extra_text_outside_the_reference_is_ignored(self):
        blocks = [SpeakerBlock(1, "Угу."), SpeakerBlock(2, "Расскажите о себе.")]
        hypothesis = [
            {"speaker": 1, "text": "Угу."},
            {"speaker": 2, "text": "Расскажите о себе."},
            {"speaker": 1, "text": "Много позднего текста, которого в эталоне нет."},
        ]
        score = speaker_word_error_rate(blocks, hypothesis)
        self.assertEqual(score.misattributed, 0)
        self.assertAlmostEqual(score.coverage, 1.0)


class KnownWordTests(unittest.TestCase):
    """The basis for replacing across the whole text: telling a name from an ordinary word."""

    def setUp(self):
        from alumenans.proofreading import is_known_russian_word

        self.known = is_known_russian_word

    def test_ordinary_words_are_recognised(self):
        for word in ("морем", "хоспис", "работа"):
            with self.subTest(word=word):
                self.assertTrue(self.known(word))

    def test_invented_forms_are_not(self):
        for word in ("Марем", "Морел", "Федермессер", "Нюфедраместер"):
            with self.subTest(word=word):
                self.assertFalse(self.known(word))

    def test_case_and_yo_do_not_matter(self):
        self.assertEqual(self.known("Морем"), self.known("морем"))
        self.assertTrue(self.known("ёлка"))

    def test_junk_is_not_a_word(self):
        for word in ("", " ", "—", "1"):
            with self.subTest(word=word):
                self.assertFalse(self.known(word))


class AnonymizationTests(unittest.TestCase):
    def test_overlapping_span_counts_as_found(self):
        scores = anonymization_scores([[10, 26]], [[15, 26]])
        self.assertEqual(scores["matched"], 1)
        self.assertAlmostEqual(scores["recall"], 1.0)
        self.assertAlmostEqual(scores["precision"], 1.0)

    def test_misses_and_false_alarms_are_separated(self):
        scores = anonymization_scores([[0, 5], [10, 15]], [[0, 5], [90, 95]])
        self.assertEqual(scores["missed"], 1)
        self.assertEqual(scores["false_alarms"], 1)
        self.assertAlmostEqual(scores["f1"], 0.5)

    def test_empty_input_does_not_divide_by_zero(self):
        self.assertEqual(anonymization_scores([], [])["f1"], 0.0)


class ReportTests(unittest.TestCase):
    def test_timecoded_report_mentions_both_numbers(self):
        reference = parse_reference(TIMECODED)
        report = evaluate(
            reference,
            {
                "turns": [
                    {"start": 0.0, "end": 3.0, "speaker": 1, "text": "Угу."},
                    {"start": 3.0, "end": 20.0, "speaker": 2, "text": "А тогда."},
                    {"start": 20.0, "end": 30.0, "speaker": 1, "text": "Потому что."},
                ]
            },
        )
        text = format_report(report)
        self.assertIn("WER", text)
        self.assertIn("Speech on the wrong voice", text)
        self.assertEqual(report["window_seconds"], 30.0)

    def test_speaker_report_lists_unlabelled_blocks(self):
        blocks, unlabelled = parse_speaker_reference(
            "Спикер 1: Угу.\n\nСпикер 2: Расскажите.\n\nЗабытая метка."
        )
        report = evaluate_speakers(
            blocks,
            {"turns": [{"speaker": 1, "text": "Угу. Расскажите. Забытая метка."}]},
            unlabelled=unlabelled,
        )
        text = format_speaker_report(report)
        self.assertIn("without a speaker label", text)
        self.assertNotIn("WER", text)


if __name__ == "__main__":
    unittest.main()
