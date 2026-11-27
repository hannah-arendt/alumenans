#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import unittest

from alumenans.document.editing import retext
from alumenans.document.transcript import Author, from_machine_turns, span_words, validate
from alumenans.paths import PACKAGE_DIR
from alumenans.proofreading import (
    _load_lexicon,
    analyze_proofreading,
    check,
    clean,
    normalize_transcript_text,
    render_reviewed_transcript,
)


class MechanicalCleanupTests(unittest.TestCase):
    def test_repairs_spacing_punctuation_and_clear_sentence_case(self):
        text, fixes = normalize_transcript_text(
            "— Да. мультик сейчас,сейчас. Э-э,, дальше.. ээ, хмм. Правда?!"
        )

        self.assertEqual(
            text,
            "Да. Мультик сейчас, сейчас. Э-э, дальше… Э-э, хм-м. Правда?!",
        )
        self.assertGreaterEqual(fixes, 5)

    def test_does_not_remove_spoken_repetitions(self):
        text, _ = normalize_transcript_text(
            "Я уже уже думала. А, угу, угу."
        )

        self.assertIn("уже уже", text)
        self.assertIn("угу, угу", text)


class SuggestionTests(unittest.TestCase):
    def test_curated_people_and_organizations_are_loaded(self):
        references = _load_lexicon()
        by_canonical = {
            item["canonical"]: item
            for item in references
        }

        self.assertIn("Аббас Галлямов", by_canonical)
        self.assertIn("Роскомсвобода", by_canonical)
        self.assertEqual(by_canonical["Аббас Галлямов"]["kind"], "person")
        self.assertEqual(
            by_canonical["Роскомсвобода"]["kind"],
            "organization",
        )

    def test_unknown_word_gets_a_plausible_spelling_suggestion(self):
        result = analyze_proofreading(
            [
                {
                    "start": 0,
                    "end": 4,
                    "speaker": 1,
                    "text": "Это мжно исправить.",
                }
            ]
        )

        issue = next(item for item in result["issues"] if item["text"] == "мжно")
        self.assertEqual(issue["suggestion"], "можно")

    def test_interview_guide_headings_do_not_become_name_suggestions(self):
        result = analyze_proofreading(
            [
                {
                    "start": 0,
                    "end": 10,
                    "speaker": 1,
                    "text": "Наверное, это можно сделать. Можно продолжить.",
                }
            ],
            (
                "О себе (2-3 мин)\n"
                "1. Расскажите немного о себе, если вам комфортно.\n"
                "Возможно\n"
                "Завершение"
            ),
        )

        term_issues = [
            issue for issue in result["issues"] if issue["kind"] == "term"
        ]
        self.assertEqual(term_issues, [])
        self.assertNotIn("Возможно", result["notes_terms"])
        self.assertNotIn("Завершение", result["notes_terms"])


    def test_direct_repeat_is_a_suggestion_not_an_automatic_edit(self):
        result = analyze_proofreading(
            [
                {
                    "start": 0,
                    "end": 10,
                    "speaker": 1,
                    "text": "Я уже уже не помню.",
                }
            ]
        )

        repeat = next(issue for issue in result["issues"] if issue["kind"] == "repeat")
        self.assertEqual(repeat["suggestion"], "уже")
        self.assertIn("уже уже", result["text"])

    def test_low_acoustic_confidence_becomes_a_review_marker(self):
        result = analyze_proofreading(
            [
                {
                    "start": 0,
                    "end": 10,
                    "speaker": 1,
                    "text": "Это транстрибирование.",
                    "words": [
                        {
                            "start": 0,
                            "end": 1,
                            "text": "Это",
                            "confidence": 0.99,
                            "boundary_risk": False,
                        },
                        {
                            "start": 1,
                            "end": 3,
                            "text": "транстрибирование.",
                            "confidence": 0.54,
                            "boundary_risk": False,
                        },
                    ],
                }
            ]
        )

        hearing = next(issue for issue in result["issues"] if issue["kind"] == "hearing")
        self.assertEqual(hearing["text"], "транстрибирование")

    def test_moderately_uncertain_normal_word_is_not_highlighted(self):
        result = analyze_proofreading(
            [
                {
                    "start": 0,
                    "end": 3,
                    "speaker": 1,
                    "text": "Можно продолжить.",
                    "words": [
                        {
                            "start": 0,
                            "end": 1,
                            "text": "Можно",
                            "confidence": 0.61,
                            "boundary_risk": False,
                        }
                    ],
                }
            ]
        )

        self.assertFalse(
            any(
                issue["kind"] == "hearing" and issue["text"] == "Можно"
                for issue in result["issues"]
            )
        )

    def test_very_uncertain_normal_word_is_still_highlighted(self):
        result = analyze_proofreading(
            [
                {
                    "start": 0,
                    "end": 3,
                    "speaker": 1,
                    "text": "Можно продолжить.",
                    "words": [
                        {
                            "start": 0,
                            "end": 1,
                            "text": "Можно",
                            "confidence": 0.31,
                            "boundary_risk": False,
                        }
                    ],
                }
            ]
        )

        self.assertTrue(
            any(
                issue["kind"] == "hearing" and issue["text"] == "Можно"
                for issue in result["issues"]
            )
        )

    def test_guide_can_restore_an_uncertain_question(self):
        result = analyze_proofreading(
            [
                {
                    "start": 0,
                    "end": 5,
                    "speaker": 1,
                    "text": "Расскажите немного о сибе?",
                    "words": [
                        {
                            "start": 3,
                            "end": 4,
                            "text": "сибе",
                            "confidence": 0.48,
                            "boundary_risk": False,
                        }
                    ],
                }
            ],
            guide="1. Расскажите немного о себе?",
        )

        guide_issue = next(
            issue for issue in result["issues"] if issue["kind"] == "guide"
        )
        self.assertEqual(
            guide_issue["suggestion"],
            "Расскажите немного о себе?",
        )

    def test_full_text_is_checked_without_a_global_issue_limit(self):
        turns = [
            {
                "start": index,
                "end": index + 1,
                "speaker": 1,
                "text": f"Ошибка абракадабризм{index}.",
            }
            for index in range(125)
        ]

        result = analyze_proofreading(turns)

        self.assertGreater(len(result["issues"]), 120)
        self.assertTrue(
            any(issue["turn_index"] == 124 for issue in result["issues"])
        )


class TopicTests(unittest.TestCase):
    def test_explicit_topic_change_can_be_started_by_any_speaker(self):
        result = analyze_proofreading(
            [
                {"start": 0, "end": 40, "speaker": 1, "text": "Мы говорим о работе."},
                {"start": 40, "end": 120, "speaker": 2, "text": "Большой ответ о работе."},
                {
                    "start": 190,
                    "end": 205,
                    "speaker": 2,
                    "text": "А теперь давайте поговорим про переезд.",
                },
            ]
        )

        self.assertEqual([topic["time"] for topic in result["topics"]], [190])

    def test_uncertain_short_question_does_not_force_a_timecode(self):
        result = analyze_proofreading(
            [
                {"start": 0, "end": 100, "speaker": 1, "text": "Начало разговора."},
                {"start": 200, "end": 205, "speaker": 2, "text": "Как дела?"},
                {"start": 205, "end": 260, "speaker": 1, "text": "Всё хорошо."},
            ]
        )

        self.assertEqual(result["topics"], [])

    def test_reviewed_render_uses_exact_time_and_one_label_per_block(self):
        text = render_reviewed_transcript(
            [
                {"start": 0, "speaker": 1, "text": "Первая фраза.", "topic_start": False},
                {"start": 10, "speaker": 1, "text": "Вторая фраза.", "topic_start": False},
                {"start": 185, "speaker": 2, "text": "Новая тема.", "topic_start": True},
            ]
        )

        self.assertEqual(text.count("Спикер 1:"), 1)
        self.assertIn("[03:05]", text)


def machine_turn(speaker, words, start=0.0):
    """Words are texts, or (text, confidence) pairs; one word per second."""

    entries = []
    for index, word in enumerate(words):
        text, confidence = word if isinstance(word, tuple) else (word, 0.95)
        entries.append(
            {"text": text, "start": start + index, "end": start + index + 0.8, "confidence": confidence}
        )
    return {"speaker": speaker, "words": entries}


class CleanTests(unittest.TestCase):
    def test_punctuation_is_fixed_and_the_words_stay_the_same_words(self):
        machine = from_machine_turns([machine_turn(1, ["Я", "пошла", ",", "а", "он", "остался.."])])
        cleaned = clean(machine)
        validate(cleaned)
        turn = cleaned.turns[0]
        self.assertEqual(turn.text, "Я пошла, а он остался…")
        self.assertEqual(machine.turns[0].text, "Я пошла , а он остался..")
        # The last word only changed its punctuation: same word, same time.
        last = turn.words[-1]
        self.assertEqual((last.id, last.start, last.confidence), (6, 5.0, 0.95))
        self.assertEqual(last.author, Author.SPEECH_MODEL)
        # The comma that stood alone is gone; the word before it took it and
        # is still the same word.
        self.assertEqual([word.id for word in turn.words], [1, 2, 4, 5, 6])
        self.assertEqual(turn.words[1].author, Author.SPEECH_MODEL)

    def test_clean_text_is_left_alone(self):
        machine = from_machine_turns([machine_turn(1, ["Я", "пошла,", "а", "он", "остался."])])
        self.assertIs(clean(machine), machine)
        cleaned = clean(from_machine_turns([machine_turn(1, ["Ну", ",", "да.."])]))
        self.assertIs(clean(cleaned), cleaned)


class CheckTests(unittest.TestCase):
    def transcript(self):
        return from_machine_turns(
            [
                machine_turn(1, ["Где", "вы", "были?"]),
                machine_turn(2, ["Я", "ходила", "в", ("магазин,", 0.2), "потом", "домой."], start=10.0),
            ]
        )

    def test_a_flag_is_on_the_whole_word(self):
        transcript = self.transcript()
        flags = check(transcript)["flags"]
        self.assertEqual([flag["kind"] for flag in flags], ["hearing"])
        flag = flags[0]
        self.assertEqual(flag["span"], [7, 7])
        self.assertEqual([word.text for word in span_words(transcript, tuple(flag["span"]))], ["магазин,"])
        self.assertEqual(set(flag), {"id", "kind", "span", "text", "message", "suggestion"})
        self.assertEqual(flag["text"], "магазин")

    def test_a_flag_is_recognised_again_until_its_words_change(self):
        transcript = self.transcript()
        first = check(transcript)["flags"][0]["id"]
        elsewhere, _ = retext(transcript, 1, "А где вы были вчера?")
        nearby, _ = retext(transcript, 2, "Я потом ходила в магазин, потом домой.")
        self.assertEqual(check(elsewhere)["flags"][0]["id"], first)
        self.assertEqual(check(nearby)["flags"][0]["id"], first)

    def test_a_flag_follows_its_word_when_the_turn_is_edited(self):
        edited, _ = retext(self.transcript(), 2, "Я потом ходила в магазин, потом домой.")
        self.assertEqual(check(edited)["flags"][0]["span"], [7, 7])

    def test_a_retyped_word_is_no_longer_doubted(self):
        edited, _ = retext(self.transcript(), 2, "Я ходила в магазинчик, потом домой.")
        self.assertEqual(check(edited)["flags"], [])

    def test_the_checks_read_what_a_person_typed_as_it_is(self):
        # Cleanup would change this text. The checks must not, or their
        # character positions would be those of another text.
        typed, _ = retext(self.transcript(), 1, "Ну ,  где вы были??")
        found = analyze_proofreading(
            [{"speaker": turn.speaker, "start": turn.start, "end": turn.end, "text": turn.text}
             for turn in typed.turns]
        )
        self.assertEqual([turn["text"] for turn in found["turns"]], [turn.text for turn in typed.turns])

    def test_topics_are_turns(self):
        topics = check(self.transcript())["topics"]
        self.assertLessEqual(set(topics), {1, 2})


class CorpusTests(unittest.TestCase):
    """On the recordings of the test corpus, when there are any."""

    def test_every_finding_of_the_checks_becomes_a_flag_on_its_words(self):
        sys.path.insert(0, str(PACKAGE_DIR.parents[1] / "tools"))
        import corpus

        cases = [case for case in corpus.discover() if case.transcript_path.is_file()]
        if not cases:
            self.skipTest("no transcribed recordings in tests/corpus")
        for case in cases:
            with self.subTest(case=case.name):
                transcript = clean(from_machine_turns(corpus.load_transcript(case)["turns"]))
                validate(transcript)
                found = analyze_proofreading(
                    [
                        {"speaker": turn.speaker, "start": turn.start, "end": turn.end,
                         "text": turn.text,
                         "words": [{"text": word.text, "confidence": word.confidence}
                                   for word in turn.words]}
                        for turn in transcript.turns
                    ]
                )
                flags = check(transcript)["flags"]
                self.assertEqual(len(flags), len(found["issues"]))
                self.assertTrue(flags)
                for flag, issue in zip(flags, found["issues"]):
                    words = span_words(transcript, tuple(flag["span"]))
                    text = " ".join(word.text for word in words)
                    self.assertIn(issue["text"], text)
                    if issue["kind"] == "hearing":
                        self.assertEqual(len(words), 1)


if __name__ == "__main__":
    unittest.main()
