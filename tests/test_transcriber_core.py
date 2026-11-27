#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path

from alumenans.transcription.core import (
    AudioChunk,
    DiarizedSpan,
    TimedWord,
    TranscriptTurn,
    TranscriptionError,
    frames_to_spans,
    mark_topic_starts,
    merge_speaker_spans,
    merge_transcript_turns,
    plan_asr_chunks,
    renumber_speakers,
    render_transcript,
    resolve_overlaps,
    split_joined_utterance_words,
    timed_words_from_tokens,
    validate_media_path,
    validate_speaker_count,
    viterbi_path,
    words_to_transcript_turns,
)


class MediaValidationTests(unittest.TestCase):
    def test_accepts_every_agreed_format_case_insensitively(self):
        with tempfile.TemporaryDirectory() as folder:
            for extension in ("mp3", "WAV", "wma", "MP4", "m4a", "OGG"):
                path = Path(folder, f"интервью.{extension}")
                path.touch()
                self.assertEqual(validate_media_path(path), path.resolve())

    def test_rejects_an_unlisted_format(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "интервью.txt")
            path.touch()
            with self.assertRaises(TranscriptionError):
                validate_media_path(path)

    def test_accepts_only_agreed_speaker_counts(self):
        self.assertEqual(validate_speaker_count("2"), 2)
        self.assertEqual(validate_speaker_count(10), 10)
        # A single voice is a legitimate case: dictation, a monologue with no questions.
        self.assertEqual(validate_speaker_count(1), 1)
        for value in (0, 11, "много", True):
            with self.assertRaises(TranscriptionError):
                validate_speaker_count(value)


class ViterbiTests(unittest.TestCase):
    """One principle instead of a stack of rules.

    The dilemma it was all for: a long monologue must not be split in the
    middle, while a short «угу» ("uh-huh") in another voice must get
    through. A single penalty for changing voice satisfies both
    requirements.
    """

    @staticmethod
    def _switches(path):
        return sum(1 for left, right in zip(path, path[1:]) if left != right)

    def test_single_blurred_frame_does_not_break_a_monologue(self):
        scores = [[2.0, 0.0]] * 8 + [[0.0, 1.2]] + [[2.0, 0.0]] * 8
        self.assertEqual(set(viterbi_path(scores)), {0})

    def test_short_interjection_breaks_through(self):
        scores = [[2.0, 0.0]] * 8 + [[0.0, 2.5]] * 3 + [[2.0, 0.0]] * 8
        path = viterbi_path(scores)
        self.assertEqual(path[8:11], [1, 1, 1])
        self.assertEqual(self._switches(path), 2)

    def test_penalty_controls_sensitivity(self):
        scores = [[2.0, 0.0]] * 6 + [[0.0, 2.0]] * 2 + [[2.0, 0.0]] * 6
        self.assertEqual(self._switches(viterbi_path(scores, switch_penalty=0.2)), 2)
        self.assertEqual(self._switches(viterbi_path(scores, switch_penalty=9.0)), 0)

    def test_clean_alternation_is_kept(self):
        scores = [[3.0, 0.0]] * 5 + [[0.0, 3.0]] * 5 + [[3.0, 0.0]] * 5
        self.assertEqual(self._switches(viterbi_path(scores)), 2)

    def test_single_voice_needs_no_decisions(self):
        self.assertEqual(viterbi_path([[1.0], [1.0], [1.0]]), [0, 0, 0])

    def test_empty_input(self):
        self.assertEqual(viterbi_path([]), [])

    def test_path_length_matches_the_frames(self):
        self.assertEqual(len(viterbi_path([[1.0, 0.5]] * 17)), 17)


class FramesToSpansTests(unittest.TestCase):
    @staticmethod
    def _frames(count, hop=0.5, window=1.5):
        return [(index * hop, index * hop + window) for index in range(count)]

    def test_neighbouring_frames_of_one_voice_become_one_span(self):
        spans = frames_to_spans(self._frames(6), [0, 0, 0, 1, 1, 1])
        self.assertEqual(len(spans), 2)
        self.assertEqual([span.speaker for span in spans], [0, 1])

    def test_boundary_sits_between_the_frames(self):
        spans = frames_to_spans(self._frames(4), [0, 0, 1, 1])
        self.assertAlmostEqual(spans[0].end, spans[1].start)
        self.assertGreater(spans[0].end, 1.0)
        self.assertLess(spans[0].end, 2.5)

    def test_gap_in_speech_splits_the_span(self):
        frames = [(0.0, 1.5), (0.5, 2.0), (30.0, 31.5), (30.5, 32.0)]
        self.assertEqual(len(frames_to_spans(frames, [0, 0, 0, 0])), 2)

    def test_one_frame_gives_one_span(self):
        spans = frames_to_spans([(1.0, 2.5)], [1])
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].speaker, 1)

    def test_mismatched_input_is_refused(self):
        self.assertEqual(frames_to_spans([(0.0, 1.5)], [0, 1]), [])
        self.assertEqual(frames_to_spans([], []), [])


class DiarizationShapeTests(unittest.TestCase):
    def test_speakers_are_numbered_by_first_appearance(self):
        spans = [
            DiarizedSpan(0, 2, 8),
            DiarizedSpan(2, 4, 3),
            DiarizedSpan(4, 6, 8),
        ]
        self.assertEqual([span.speaker for span in renumber_speakers(spans)], [1, 2, 1])

    def test_overlap_becomes_one_non_overlapping_timeline(self):
        result = resolve_overlaps(
            [
                DiarizedSpan(0, 4, 0),
                DiarizedSpan(3, 6, 1),
            ]
        )
        self.assertTrue(result)
        for previous, current in zip(result, result[1:]):
            self.assertLessEqual(previous.end, current.start)

    def test_neighbouring_fragments_of_one_speaker_are_merged(self):
        result = merge_speaker_spans(
            [
                DiarizedSpan(0, 2, 1),
                DiarizedSpan(3.2, 4, 1),
                DiarizedSpan(4.2, 5, 2),
            ]
        )
        self.assertEqual(result, [DiarizedSpan(0, 4, 1), DiarizedSpan(4.2, 5, 2)])


class RecognitionWindowTests(unittest.TestCase):
    def test_uses_a_pause_near_twenty_seconds(self):
        chunks = plan_asr_chunks(
            41,
            [(0, 18), (18.8, 41)],
        )

        self.assertEqual(len(chunks), 2)
        self.assertGreaterEqual(chunks[0].end, 18)
        self.assertLessEqual(chunks[0].end, 18.8)
        self.assertGreaterEqual(chunks[1].start, 18)
        self.assertLessEqual(chunks[1].start, 18.8)
        self.assertTrue(all(chunk.end - chunk.start <= 24 for chunk in chunks))

    def test_ignores_an_early_pause_and_uses_overlap_without_a_late_pause(self):
        paused = plan_asr_chunks(
            35,
            [(0, 1), (2, 19), (19.8, 35)],
        )
        continuous = plan_asr_chunks(50, [(0, 50)])

        self.assertGreater(paused[0].end, 18)
        self.assertEqual(continuous[0], AudioChunk(0, 20))
        self.assertLess(continuous[1].start, continuous[0].end)
        self.assertTrue(
            all(chunk.end - chunk.start <= 24 for chunk in continuous)
        )

    def test_tokens_become_timestamped_words(self):
        words = timed_words_from_tokens(
            [" Пр", "и", "вет", ",", " ", "мир", "!"],
            [0.0, 0.1, 0.2, 0.3, 0.7, 0.8, 1.1],
            chunk_start=10,
            chunk_end=12,
        )

        self.assertEqual([word.text for word in words], ["Привет,", "мир!"])
        self.assertEqual(words[0].start, 10)
        self.assertGreaterEqual(words[1].start, 10.8)

    def test_low_token_score_is_preserved_as_word_confidence(self):
        words = timed_words_from_tokens(
            [" Сло", "во", "."],
            [0.0, 0.2, 0.4],
            chunk_start=0,
            chunk_end=1,
            logprobs=[-0.01, -0.6, -0.02],
        )

        self.assertEqual(len(words), 1)
        self.assertLess(words[0].confidence, 0.56)

    def test_words_are_assigned_after_recognition(self):
        turns = words_to_transcript_turns(
            [
                TimedWord(1, 2, "Первый"),
                TimedWord(2.1, 3, "вопрос."),
                TimedWord(10, 11, "Ответ."),
            ],
            [
                DiarizedSpan(0, 5, 1),
                DiarizedSpan(5, 15, 2),
            ],
        )

        self.assertEqual(
            [(turn.speaker, turn.text) for turn in turns],
            [(1, "Первый вопрос."), (2, "Ответ.")],
        )

    def test_an_opening_dash_is_dropped_from_the_words_as_well_as_the_text(self):
        turns = words_to_transcript_turns(
            [
                TimedWord(1, 2, "Вопрос?"),
                TimedWord(2.1, 2.2, "—"),
                TimedWord(2.2, 3, "Ответ"),
                TimedWord(3.1, 3.2, "—"),
                TimedWord(3.2, 4, "это"),
                TimedWord(4.1, 5, "да."),
                TimedWord(5.2, 5.3, "—"),
                TimedWord(5.3, 6, "Угу."),
                TimedWord(20, 21, "—Ясно."),
            ],
            [
                DiarizedSpan(0, 2.05, 1),
                DiarizedSpan(2.05, 15, 2),
                DiarizedSpan(15, 25, 1),
            ],
        )

        self.assertEqual(
            [(turn.speaker, turn.text) for turn in turns],
            [(1, "Вопрос?"), (2, "Ответ — это да. Угу."), (1, "Ясно.")],
        )
        for turn in turns:
            self.assertEqual(turn.text, " ".join(word.text for word in turn.words))
        self.assertEqual(turns[1].start, 2.2)

    def test_speaker_boundary_splits_a_phrase_at_the_matching_word(self):
        turns = words_to_transcript_turns(
            [
                TimedWord(0.2, 1.0, "Предыдущий ответ."),
                TimedWord(1.2, 2.0, "Извини,"),
                TimedWord(2.1, 3.0, "повтори"),
                TimedWord(3.1, 4.5, "пожалуйста."),
                TimedWord(6.1, 6.6, "Угу."),
            ],
            [
                DiarizedSpan(0, 3.5, 1),
                DiarizedSpan(3.5, 5.5, 2),
                DiarizedSpan(6, 7, 1),
            ],
        )

        self.assertEqual(
            [(turn.speaker, turn.text) for turn in turns],
            [
                (1, "Предыдущий ответ. Извини, повтори"),
                (2, "пожалуйста."),
                (1, "Угу."),
            ],
        )

    def test_joined_utterances_split_at_a_confirmed_voice_change(self):
        words = split_joined_utterance_words(
            [
                TimedWord(48.16, 53.36, "лет?га"),
                TimedWord(65.67, 71.71, "чертах.Угу.."),
            ],
            [
                DiarizedSpan(40.0, 51.65, 1),
                DiarizedSpan(52.26, 63.28, 2),
                DiarizedSpan(63.28, 65.50, 1),
                DiarizedSpan(65.50, 72.0, 2),
            ],
        )

        self.assertEqual(
            [word.text for word in words],
            ["лет?", "га", "чертах.", "Угу.."],
        )


class TranscriptLogicTests(unittest.TestCase):
    def setUp(self):
        self.turns = [
            TranscriptTurn(0, 8, 1, "Расскажите, пожалуйста, где вы работаете?"),
            TranscriptTurn(8, 75, 2, "Я работаю в городской больнице."),
            TranscriptTurn(180, 190, 1, "А теперь давайте поговорим про образование."),
            TranscriptTurn(190, 260, 2, "Я окончила медицинский университет."),
            TranscriptTurn(480, 490, 1, "Перейдём к вашему опыту переезда."),
            TranscriptTurn(490, 560, 2, "Я переехала сюда десять лет назад."),
        ]

    def test_topic_markers_use_whole_minutes(self):
        marked = mark_topic_starts(self.turns)
        starts = [(turn.start, turn.topic_start) for turn in marked]
        self.assertIn((180, True), starts)
        self.assertIn((480, True), starts)
        text = render_transcript(marked)
        self.assertIn("[3 мин]", text)
        self.assertIn("[8 мин]", text)
        self.assertNotIn("[0 мин]", text)

    def test_topic_change_can_be_started_by_any_speaker(self):
        turns = [
            TranscriptTurn(0, 20, 1, "Начало разговора."),
            TranscriptTurn(130, 140, 2, "А теперь поговорим о работе."),
            TranscriptTurn(190, 200, 1, "Следующий вопрос будет про переезд."),
        ]
        marked = mark_topic_starts(turns)
        self.assertEqual(
            [turn.start for turn in marked if turn.topic_start],
            [130, 190],
        )

    def test_adjacent_text_chunks_form_one_turn(self):
        result = merge_transcript_turns(
            [
                TranscriptTurn(1, 2, 3, "Первая часть"),
                TranscriptTurn(3.2, 4, 3, "вторая часть."),
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "Первая часть вторая часть.")

    def test_repeated_speaker_label_is_suppressed_until_speaker_changes(self):
        text = render_transcript(
            [
                TranscriptTurn(0, 5, 2, "Первый абзац."),
                TranscriptTurn(10, 15, 2, "Второй абзац."),
                TranscriptTurn(20, 25, 1, "Ответ другого человека."),
            ]
        )
        self.assertEqual(text.count("Спикер 2:"), 1)
        self.assertIn("Первый абзац.\n\nВторой абзац.", text)


if __name__ == "__main__":
    unittest.main()
