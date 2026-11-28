"""Editing a transcript: the four edits, free retyping, and what must hold
after any sequence of them."""

import importlib.util
import random
import sys
import unittest

from alumenans.document.editing import (
    EditError,
    Mapping,
    MergeTurns,
    ReplaceWords,
    SetSpeaker,
    SplitTurn,
    align,
    apply,
    follow_span,
    retext,
)
from alumenans.document.transcript import (
    Author,
    from_machine_turns,
    letters,
    validate,
)
from alumenans.paths import PACKAGE_DIR

REPO = PACKAGE_DIR.parents[1]


def machine_turn(speaker, text, start=0.0, step=1.0, length=0.8):
    """A turn as transcription returns it: one word per second, 0.2 s apart."""

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


def one_turn(text, **timing):
    return from_machine_turns([machine_turn(1, text, **timing)])


def described(transcript, turn_index=0):
    """A turn's words as (id, text, start, end), times rounded."""

    return [
        (word.id, word.text, round(word.start, 3), round(word.end, 3))
        for word in transcript.turns[turn_index].words
    ]


class ReplaceWordsTests(unittest.TestCase):
    def test_a_change_of_punctuation_or_case_keeps_everything(self):
        before = one_turn("привет как дела")
        after, mapping = apply(before, ReplaceWords(1, 0, 1, ("Привет,",)))
        validate(after)
        word = after.turns[0].words[0]
        self.assertEqual((word.id, word.text, word.start, word.end), (1, "Привет,", 0.0, 0.8))
        self.assertEqual(word.confidence, 0.9)
        self.assertEqual(word.author, Author.SPEECH_MODEL)
        self.assertEqual(mapping, Mapping())
        self.assertEqual(after.revision, before.revision + 1)

    def test_retyped_letters_keep_the_word_and_its_time_but_not_the_confidence(self):
        before = one_turn("по Морем гуляли")
        after, mapping = apply(before, ReplaceWords(1, 1, 1, ("Марем",)))
        validate(after)
        word = after.turns[0].words[1]
        self.assertEqual((word.id, word.text, word.start, word.end), (2, "Марем", 1.0, 1.8))
        self.assertIsNone(word.confidence)
        self.assertEqual(word.author, Author.HUMAN)
        self.assertEqual(word.origin, (2,))
        self.assertEqual(mapping, Mapping({}, frozenset({2})))

    def test_the_cleanup_pass_signs_its_own_changes(self):
        after, _ = apply(one_turn("ну ээээ да"), ReplaceWords(1, 1, 1, ("э-э",), Author.CLEANUP))
        self.assertEqual(after.turns[0].words[1].author, Author.CLEANUP)

    def test_one_word_split_in_two_shares_its_time_by_letters(self):
        before = one_turn("ну вобщем да", length=0.6)
        after, mapping = apply(before, ReplaceWords(1, 1, 1, ("в", "общем")))
        validate(after)
        self.assertEqual(
            described(after),
            [(1, "ну", 0.0, 0.6), (4, "в", 1.0, 1.1), (5, "общем", 1.1, 1.6), (3, "да", 2.0, 2.6)],
        )
        for word in after.turns[0].words[1:3]:
            self.assertEqual(word.origin, (2,))
            self.assertIsNone(word.confidence)
            self.assertEqual(word.author, Author.HUMAN)
        self.assertEqual(mapping, Mapping({2: (4, 5)}))

    def test_two_words_joined_take_the_time_of_both(self):
        after, mapping = apply(one_turn("а по этому да"), ReplaceWords(1, 1, 2, ("поэтому",)))
        validate(after)
        self.assertEqual(described(after)[1], (5, "поэтому", 1.0, 2.8))
        self.assertEqual(after.turns[0].words[1].origin, (2, 3))
        self.assertEqual(mapping, Mapping({2: (5,), 3: (5,)}))

    def test_two_words_become_three(self):
        after, mapping = apply(one_turn("раз двадва три"), ReplaceWords(1, 1, 2, ("а", "б", "в")))
        validate(after)
        self.assertEqual(
            described(after),
            [(1, "раз", 0.0, 0.8), (4, "а", 1.0, 1.6), (5, "б", 1.6, 2.2), (6, "в", 2.2, 2.8)],
        )
        self.assertEqual(mapping, Mapping({2: (4, 5, 6), 3: (4, 5, 6)}))

    def test_deleted_words_leave_a_gap_and_the_neighbours_alone(self):
        before = one_turn("раз два три четыре")
        after, mapping = apply(before, ReplaceWords(1, 1, 2, ()))
        validate(after)
        self.assertEqual(described(after), [(1, "раз", 0.0, 0.8), (4, "четыре", 3.0, 3.8)])
        self.assertIs(after.turns[0].words[0], before.turns[0].words[0])
        self.assertEqual(mapping, Mapping({2: (), 3: ()}))

    def test_deleting_every_word_removes_the_turn(self):
        before = from_machine_turns([machine_turn(1, "да"), machine_turn(2, "нет", start=5.0)])
        after, _ = apply(before, ReplaceWords(1, 0, 1, ()))
        validate(after)
        self.assertEqual([turn.id for turn in after.turns], [2])

    def test_a_typed_word_takes_the_silence_between_its_neighbours(self):
        after, mapping = apply(one_turn("раз три"), ReplaceWords(1, 1, 0, ("два",)))
        validate(after)
        self.assertEqual(
            described(after), [(1, "раз", 0.0, 0.8), (3, "два", 0.8, 1.0), (2, "три", 1.0, 1.8)]
        )
        word = after.turns[0].words[1]
        self.assertEqual((word.origin, word.confidence, word.author), ((), None, Author.HUMAN))
        self.assertEqual(mapping, Mapping())

    def test_without_silence_the_word_before_shares_its_time(self):
        before = one_turn("раз три", step=1.0, length=1.0)
        after, _ = apply(before, ReplaceWords(1, 1, 0, ("два",)))
        validate(after)
        self.assertEqual(
            described(after), [(1, "раз", 0.0, 0.5), (3, "два", 0.5, 1.0), (2, "три", 1.0, 2.0)]
        )
        self.assertIs(after.turns[0].words[2], before.turns[0].words[1])

    def test_at_the_start_of_a_turn_the_word_after_shares_its_time(self):
        after, _ = apply(one_turn("три четыре", start=4.0), ReplaceWords(1, 0, 0, ("два",)))
        validate(after)
        self.assertEqual(described(after)[:2], [(3, "два", 4.0, 4.4), (1, "три", 4.4, 4.8)])

    def test_at_the_end_of_a_turn_the_last_word_shares_its_time(self):
        after, _ = apply(one_turn("раз два"), ReplaceWords(1, 2, 0, ("три",)))
        validate(after)
        self.assertEqual(described(after)[1:], [(2, "два", 1.0, 1.4), (3, "три", 1.4, 1.8)])

    def test_a_typed_dash_takes_no_time(self):
        after, _ = apply(one_turn("да нет", length=1.0), ReplaceWords(1, 1, 0, ("—",)))
        validate(after)
        self.assertEqual(
            described(after), [(1, "да", 0.0, 1.0), (3, "—", 1.0, 1.0), (2, "нет", 1.0, 2.0)]
        )

    def test_new_words_never_start_after_the_word_that_follows(self):
        # The speech model sometimes lets a word end after the next began.
        before = from_machine_turns(
            [
                {
                    "speaker": 1,
                    "words": [
                        {"text": "поэтому", "start": 1.0, "end": 3.0},
                        {"text": "да", "start": 2.0, "end": 2.5},
                    ],
                }
            ]
        )
        after, _ = apply(before, ReplaceWords(1, 0, 1, ("по", "этому")))
        validate(after)
        self.assertLessEqual(after.turns[0].words[1].start, 2.0)

    def test_the_same_words_again_change_nothing(self):
        before = one_turn("раз два три")
        after, mapping = apply(before, ReplaceWords(1, 0, 3, ("раз", "два", "три")))
        self.assertIs(after, before)
        self.assertEqual(mapping, Mapping())

    def test_what_does_not_fit_is_refused(self):
        transcript = one_turn("раз два")
        for edit in (
            ReplaceWords(9, 0, 1, ("а",)),
            ReplaceWords(1, 1, 2, ("а",)),
            ReplaceWords(1, -1, 1, ("а",)),
            ReplaceWords(1, 0, 1, ("два слова",)),
            ReplaceWords(1, 0, 1, ("",)),
        ):
            with self.subTest(edit=edit):
                with self.assertRaises(EditError):
                    apply(transcript, edit)


class TurnEditTests(unittest.TestCase):
    def two_turns(self):
        return from_machine_turns(
            [machine_turn(1, "раз два три"), machine_turn(2, "четыре пять", start=10.0)]
        )

    def test_a_split_gives_the_second_part_a_new_identifier(self):
        before = self.two_turns()
        after, mapping = apply(before, SplitTurn(1, 1))
        validate(after)
        self.assertEqual(
            [(turn.id, turn.speaker, turn.text) for turn in after.turns],
            [(1, 1, "раз"), (3, 1, "два три"), (2, 2, "четыре пять")],
        )
        self.assertEqual(mapping, Mapping())
        self.assertIs(after.turns[2], before.turns[1])
        self.assertEqual(after.next_turn_id, 4)
        for word_before, word_after in zip(before.words(), after.words()):
            self.assertIs(word_after, word_before)

    def test_a_split_at_either_end_is_refused(self):
        for at in (0, 3, -1):
            with self.assertRaises(EditError):
                apply(self.two_turns(), SplitTurn(1, at))

    def test_a_merge_keeps_the_first_turn(self):
        one_speaker, _ = apply(self.two_turns(), SetSpeaker(2, 1))
        after, _ = apply(one_speaker, MergeTurns(1))
        validate(after)
        self.assertEqual(
            [(turn.id, turn.speaker, turn.text) for turn in after.turns],
            [(1, 1, "раз два три четыре пять")],
        )

    def test_turns_by_different_speakers_are_not_merged(self):
        with self.assertRaisesRegex(EditError, "different speakers"):
            apply(self.two_turns(), MergeTurns(1))

    def test_split_then_merge_gives_the_same_words_back(self):
        before = self.two_turns()
        split, _ = apply(before, SplitTurn(1, 2))
        merged, _ = apply(split, MergeTurns(1))
        self.assertEqual(merged.turns, before.turns)

    def test_the_last_turn_has_nothing_to_merge_with(self):
        with self.assertRaises(EditError):
            apply(self.two_turns(), MergeTurns(2))

    def test_the_speaker_of_a_turn_can_be_changed(self):
        before = self.two_turns()
        after, _ = apply(before, SetSpeaker(2, 1))
        validate(after)
        self.assertEqual([turn.speaker for turn in after.turns], [1, 1])
        self.assertIs(after.turns[1].words, before.turns[1].words)
        same, _ = apply(before, SetSpeaker(2, 2))
        self.assertIs(same, before)

    def test_a_selection_goes_to_the_other_speaker(self):
        # Split before it, split after it, change the speaker of the middle.
        transcript = self.two_turns()
        transcript, _ = apply(transcript, SplitTurn(1, 1))
        middle = transcript.turns[1].id
        transcript, _ = apply(transcript, SplitTurn(middle, 1))
        transcript, _ = apply(transcript, SetSpeaker(middle, 2))
        validate(transcript)
        self.assertEqual(
            [(turn.speaker, turn.text) for turn in transcript.turns],
            [(1, "раз"), (2, "два"), (1, "три"), (2, "четыре пять")],
        )

    def overlapping(self):
        # Speaker 2 puts in one word while speaker 1 is still talking.
        return from_machine_turns(
            [machine_turn(1, "раз два три четыре"), machine_turn(2, "мгм", start=1.5, length=0.3)]
        )

    def test_a_split_of_an_overlapped_turn_puts_the_parts_in_order_of_start(self):
        after, _ = apply(self.overlapping(), SplitTurn(1, 2))
        validate(after)
        self.assertEqual([turn.text for turn in after.turns], ["раз два", "мгм", "три четыре"])

    def test_deleting_the_start_of_an_overlapped_turn_can_move_it(self):
        after, _ = apply(self.overlapping(), ReplaceWords(1, 0, 2, ()))
        validate(after)
        self.assertEqual([turn.text for turn in after.turns], ["мгм", "три четыре"])

    def test_overlapping_turns_are_not_merged(self):
        with self.assertRaises(EditError):
            apply(self.overlapping(), MergeTurns(1))

    def test_an_unknown_turn_is_refused(self):
        for edit in (SplitTurn(9, 1), MergeTurns(9), SetSpeaker(9, 1)):
            with self.assertRaises(EditError):
                apply(self.two_turns(), edit)


class AlignTests(unittest.TestCase):
    def runs(self, old, new):
        return align(old.split(), new.split())

    def test_the_same_text_needs_nothing(self):
        self.assertEqual(self.runs("раз два три", "раз два три"), [])
        self.assertEqual(self.runs("", ""), [])

    def test_one_changed_word(self):
        self.assertEqual(self.runs("по Морем гуляли", "по Марем гуляли"), [(1, 1, ("Марем",))])

    def test_a_typed_and_a_deleted_word(self):
        self.assertEqual(self.runs("раз три", "раз два три"), [(1, 0, ("два",))])
        self.assertEqual(self.runs("раз два три", "раз три"), [(1, 1, ())])
        self.assertEqual(self.runs("раз", "раз два"), [(1, 0, ("два",))])
        self.assertEqual(self.runs("раз", ""), [(0, 1, ())])

    def test_a_repeated_word_loses_one_copy(self):
        self.assertEqual(self.runs("да да да", "да да"), [(2, 1, ())])

    def test_a_split_next_to_a_join_is_seen_as_two_changes(self):
        self.assertEqual(
            self.runs("Я ходил вобщем по этому поводу", "Я ходил, в общем, поэтому поводу"),
            [(1, 1, ("ходил,",)), (2, 1, ("в", "общем,")), (3, 2, ("поэтому",))],
        )

    def test_words_with_nothing_in_common_are_replaced_together(self):
        self.assertEqual(self.runs("раз кот пёс три", "раз жук три"), [(1, 2, ("жук",))])

    def test_changes_in_two_places_are_two_runs(self):
        self.assertEqual(
            self.runs("раз два три четыре пять", "раз две три четыре шесть семь"),
            [(1, 1, ("две",)), (4, 1, ("шесть", "семь"))],
        )

    def test_the_runs_always_rebuild_the_new_text(self):
        rng = random.Random(7)
        vocabulary = "да нет вот в общем по этому поэтому вобщем — ну, так. Марем".split()
        for _ in range(500):
            old = [rng.choice(vocabulary) for _ in range(rng.randint(0, 8))]
            new = [rng.choice(vocabulary) for _ in range(rng.randint(0, 8))]
            rebuilt = list(old)
            position = 0
            for first, count, texts in reversed(align(old, new)):
                rebuilt[first : first + count] = texts
            for first, count, _ in align(old, new):
                self.assertGreaterEqual(first, position)
                position = first + count
            self.assertEqual(rebuilt, new, (old, new))


class RetextTests(unittest.TestCase):
    def test_the_example_from_the_design(self):
        before = one_turn("Я ходил вобщем по этому поводу", length=0.6)
        after, mapping = retext(before, 1, "Я ходил, в общем, поэтому поводу")
        validate(after)
        words = after.turns[0].words
        self.assertEqual(after.turns[0].text, "Я ходил, в общем, поэтому поводу")
        # Untouched, and punctuation only: the same words.
        self.assertIs(words[0], before.turns[0].words[0])
        self.assertEqual((words[1].id, words[1].confidence), (2, 0.9))
        self.assertIs(words[5], before.turns[0].words[5])
        # One word became two.
        self.assertEqual([word.origin for word in words[2:4]], [(3,), (3,)])
        self.assertEqual((words[2].start, words[3].end), (2.0, 2.6))
        self.assertAlmostEqual(words[2].end, 2.1)
        # Two became one.
        self.assertEqual((words[4].origin, words[4].start, words[4].end), ((4, 5), 3.0, 4.6))
        self.assertEqual(mapping.replaced[3], (words[2].id, words[3].id))
        self.assertEqual(mapping.replaced[4], (words[4].id,))
        self.assertEqual(mapping.replaced[5], (words[4].id,))
        self.assertEqual(after.revision, before.revision + 1)

    def test_the_turns_own_text_changes_nothing(self):
        before = one_turn("Привет, как дела?")
        after, mapping = retext(before, 1, before.turns[0].text)
        self.assertIs(after, before)
        self.assertEqual(mapping, Mapping())

    def test_whitespace_is_not_content(self):
        before = one_turn("Привет, как дела?")
        after, _ = retext(before, 1, "  Привет,\n как\t дела?  ")
        self.assertIs(after, before)

    def test_empty_text_removes_the_turn(self):
        before = from_machine_turns([machine_turn(1, "да"), machine_turn(2, "нет", start=5.0)])
        after, mapping = retext(before, 2, "   ")
        validate(after)
        self.assertEqual([turn.id for turn in after.turns], [1])
        self.assertEqual(mapping, Mapping({2: ()}))

    def test_other_turns_are_the_same_objects(self):
        before = from_machine_turns([machine_turn(1, "да"), machine_turn(2, "нет", start=5.0)])
        after, _ = retext(before, 2, "нет, конечно")
        self.assertIs(after.turns[0], before.turns[0])

    def test_editing_a_computed_time_again_stays_inside_it(self):
        machine = one_turn("ну вобщем да", length=0.6)
        once, _ = retext(machine, 1, "ну в общем да")
        twice, _ = retext(once, 1, "ну в об щем да")
        validate(twice)
        words = twice.turns[0].words
        self.assertEqual((words[1].start, words[3].end), (1.0, 1.6))
        self.assertIs(words[0], machine.turns[0].words[0])
        self.assertIs(words[4], machine.turns[0].words[2])
        self.assertEqual({word.origin for word in words[1:4]}, {(2,)})

    def test_an_unknown_turn_is_refused(self):
        with self.assertRaises(EditError):
            retext(one_turn("да"), 9, "нет")


# ---------------------------------------------------------------------------
# Any sequence of edits
# ---------------------------------------------------------------------------

VOCABULARY = (
    "да нет вот ну, так. в общем, поэтому по этому Марем Морем — «тема» "
    "что-то Қазақстанда э-э-э привет, а? 2024 [смеётся]"
).split()


def random_transcript(rng):
    turns = []
    clock = 0.0
    for _ in range(rng.randint(2, 7)):
        words = []
        start = clock
        for _ in range(rng.randint(1, 10)):
            length = rng.choice((0.1, 0.3, 0.6))
            words.append(
                {
                    "text": rng.choice(VOCABULARY),
                    "start": clock,
                    "end": clock + length,
                    "confidence": rng.random(),
                }
            )
            # Words back to back, with a short gap, or after a real pause.
            clock += length + rng.choice((0.0, 0.0, 0.05, 0.4))
        turns.append({"speaker": rng.randint(1, 2), "words": words})
        if rng.random() < 0.2:
            # The next turn starts while this one is still going.
            clock = (start + clock) / 2
    return from_machine_turns(turns)


def scrambled(rng, text):
    """A turn's text after a person had their way with it."""

    words = text.split()
    for _ in range(rng.randint(1, 3)):
        action = rng.choice(("retype", "insert", "delete", "join", "split", "punctuate"))
        at = rng.randrange(len(words)) if words else 0
        if action == "insert" or not words:
            words.insert(at, rng.choice(VOCABULARY))
        elif action == "retype":
            words[at] = rng.choice(VOCABULARY)
        elif action == "delete":
            del words[at]
        elif action == "join" and at + 1 < len(words):
            words[at : at + 2] = [words[at] + words[at + 1]]
        elif action == "split" and len(words[at]) > 1:
            cut = rng.randrange(1, len(words[at]))
            words[at : at + 1] = [words[at][:cut], words[at][cut:]]
        elif action == "punctuate":
            words[at] = words[at] + rng.choice(",.!")
    return (" " * rng.randint(0, 2)).join([""] + words) if rng.random() < 0.1 else " ".join(words)


class AnySequenceOfEditsTests(unittest.TestCase):
    """Thousands of random edits. After each one the transcript must still
    be sound, and the edit must have been honest about what it touched."""

    def check_step(self, before, after, mapping):
        validate(after)
        if after is before:
            self.assertEqual(mapping, Mapping())
            return
        self.assertEqual(after.revision, before.revision + 1)
        old = {word.id: word for word in before.words()}
        new = {word.id: word for word in after.words()}

        # Identifiers are never reused, and every lost word is accounted for.
        for word_id in new.keys() - old.keys():
            self.assertGreaterEqual(word_id, before.next_word_id)
        self.assertEqual(old.keys() - new.keys(), mapping.replaced.keys())
        for successors in mapping.replaced.values():
            self.assertLessEqual(set(successors), new.keys() - old.keys())
        self.assertLessEqual(mapping.retyped, old.keys() & new.keys())

        # A word that stayed and was not retyped still says the same thing.
        for word_id in old.keys() & new.keys():
            if word_id in mapping.retyped:
                self.assertNotEqual(letters(new[word_id].text), letters(old[word_id].text))
                self.assertIsNone(new[word_id].confidence)
            else:
                self.assertEqual(letters(new[word_id].text), letters(old[word_id].text))
                self.assertEqual(new[word_id].confidence, old[word_id].confidence)
                self.assertEqual(new[word_id].origin, old[word_id].origin)

        # New words that replaced old ones are heard inside the old ones' time.
        for word_id, successors in mapping.replaced.items():
            lost = [other for other, same in mapping.replaced.items() if same == successors]
            earliest = min(old[other].start for other in lost)
            latest = max(old[other].end for other in lost)
            for successor in successors:
                self.assertGreaterEqual(new[successor].start, earliest)
                self.assertLessEqual(new[successor].end, latest)
                self.assertEqual(
                    set(new[successor].origin),
                    {source for other in lost for source in old[other].origin},
                )

    def check_only_one_turn_touched(self, before, after, turn_id):
        for turn in before.turns:
            if turn.id != turn_id:
                self.assertIs(after.turn(turn.id), turn)

    def check_replace_is_local(self, before, after, edit):
        """Words outside the replaced run are the same objects, except the
        one neighbour that shares its time with an insertion."""

        old_words = before.turn(edit.turn).words
        if edit.turn not in after.turn_indexes:
            self.assertEqual(edit.count, len(old_words))
            return
        new_words = after.turn(edit.turn).words
        head = old_words[: edit.first]
        tail = old_words[edit.first + edit.count :]
        new_head = new_words[: len(head)]
        new_tail = new_words[len(new_words) - len(tail) :] if tail else ()
        moved = 0
        for kept, now in list(zip(head, new_head)) + list(zip(tail, new_tail)):
            self.assertEqual((now.id, now.text), (kept.id, kept.text))
            if now is not kept:
                moved += 1
                self.assertGreaterEqual(now.start, kept.start)
                self.assertLessEqual(now.end, kept.end)
        self.assertLessEqual(moved, 1 if edit.count == 0 else 0)

    def test_random_edits_keep_the_transcript_sound(self):
        for seed in range(40):
            rng = random.Random(seed)
            transcript = random_transcript(rng)
            validate(transcript)
            for step in range(120):
                if not transcript.turns:
                    break
                turn = rng.choice(transcript.turns)
                kind = rng.choice(("replace", "replace", "retext", "retext", "split", "merge", "speaker"))
                with self.subTest(seed=seed, step=step, kind=kind):
                    before = transcript
                    if kind == "replace":
                        first = rng.randint(0, len(turn.words))
                        count = rng.randint(0, min(3, len(turn.words) - first))
                        texts = tuple(rng.choice(VOCABULARY) for _ in range(rng.randint(0, 3)))
                        edit = ReplaceWords(turn.id, first, count, texts)
                        transcript, mapping = apply(before, edit)
                        self.check_replace_is_local(before, transcript, edit)
                        self.check_only_one_turn_touched(before, transcript, turn.id)
                    elif kind == "retext":
                        text = scrambled(rng, turn.text)
                        transcript, mapping = retext(before, turn.id, text)
                        if text.split():
                            self.assertEqual(transcript.turn(turn.id).text, " ".join(text.split()))
                        else:
                            self.assertNotIn(turn.id, transcript.turn_indexes)
                        self.check_only_one_turn_touched(before, transcript, turn.id)
                    elif kind == "split":
                        if len(turn.words) < 2:
                            continue
                        at = rng.randint(1, len(turn.words) - 1)
                        transcript, mapping = apply(before, SplitTurn(turn.id, at))
                        self.assertEqual(len(transcript.turns), len(before.turns) + 1)
                    elif kind == "merge":
                        index = before.turn_indexes[turn.id]
                        if index + 1 == len(before.turns):
                            continue
                        following = before.turns[index + 1]
                        try:
                            transcript, mapping = apply(before, MergeTurns(turn.id))
                        except EditError:
                            self.assertTrue(
                                following.speaker != turn.speaker
                                or following.words[0].start < turn.words[-1].start
                            )
                            continue
                        self.assertEqual(
                            transcript.turn(turn.id).words, turn.words + following.words
                        )
                    else:
                        transcript, mapping = apply(before, SetSpeaker(turn.id, rng.randint(1, 3)))
                    if kind in ("split", "merge", "speaker"):
                        self.assertEqual(mapping, Mapping())
                        self.assertEqual(
                            {word.id: word for word in transcript.words()},
                            {word.id: word for word in before.words()},
                        )
                    self.check_step(before, transcript, mapping)
                    # Re-entering any turn's own text changes nothing.
                    if transcript.turns:
                        probe = rng.choice(transcript.turns)
                        self.assertIs(retext(transcript, probe.id, probe.text)[0], transcript)


class FollowSpanTests(unittest.TestCase):
    def test_an_untouched_span_stays(self):
        before = one_turn("раз два три четыре")
        _, mapping = retext(before, 1, "раз два три пять")
        self.assertEqual(follow_span(before, mapping, (1, 2)), (1, 2))

    def test_a_span_follows_its_words_into_what_replaced_them(self):
        before = one_turn("звонил Иван Петров вчера")
        after, mapping = retext(before, 1, "звонил Иван Пет ров вчера")
        span = follow_span(before, mapping, (2, 3))
        self.assertEqual(
            [after.word(word_id).text for word_id in span], ["Иван", "ров"]
        )

    def test_a_span_shrinks_when_one_end_is_deleted(self):
        before = one_turn("звонил Иван Петров вчера")
        _, mapping = retext(before, 1, "звонил Иван вчера")
        self.assertEqual(follow_span(before, mapping, (2, 3)), (2, 2))

    def test_a_span_whose_words_are_all_deleted_is_gone(self):
        before = one_turn("звонил Иван Петров вчера")
        _, mapping = retext(before, 1, "звонил вчера")
        self.assertIsNone(follow_span(before, mapping, (2, 3)))

    def test_retyped_words_are_reported_but_stay_in_place(self):
        before = one_turn("по Морем гуляли")
        _, mapping = retext(before, 1, "по Марем гуляли")
        self.assertEqual(mapping.retyped, {2})
        self.assertEqual(follow_span(before, mapping, (2, 2)), (2, 2))




# ---------------------------------------------------------------------------
# Real recordings
# ---------------------------------------------------------------------------


def corpus_tool():
    name = "corpus_tool"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / "corpus.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def proofread_whole(corpus, case):
    """The machine transcript of a case as one turn, and that turn retyped
    into the text a person wrote for the same recording.

    One turn, because the person's turns are not cut where the machine's
    are; the words and their times are what is being checked."""

    machine_turns = corpus.load_transcript(case)["turns"]
    words = [word for turn in machine_turns for word in turn.get("words") or ()]
    machine = from_machine_turns([{"speaker": 1, "words": words}])
    reference, _ = corpus.parse_marked_reference(case.reference_path.read_text(encoding="utf-8"))
    text = " ".join(turn.text for turn in reference.turns)
    proofread, mapping = retext(machine, 1, text)
    return machine, proofread, mapping, text


class RealRecordingTests(unittest.TestCase):
    """Proofreading a real machine transcript in one go, on the recordings
    in tests/corpus. Skipped where there are none, such as in CI today."""

    def test_proofreading_keeps_the_time_of_the_words_it_leaves_alone(self):
        corpus = corpus_tool()
        cases = [case for case in corpus.discover() if case.reference_path.is_file()]
        if not cases:
            self.skipTest("no recordings with a reference in tests/corpus")
        for case in cases:
            with self.subTest(case=case.name):
                try:
                    machine, proofread, mapping, text = proofread_whole(corpus, case)
                except corpus.StageUnavailable as reason:
                    self.skipTest(str(reason))
                validate(proofread)
                words = proofread.turns[0].words
                self.assertEqual(proofread.turns[0].text, " ".join(text.split()))

                # A word that is still there is heard where the machine heard
                # it. Its time can only have shrunk, to make room for a word
                # typed next to it.
                kept = [word for word in words if word.id in machine.positions]
                for word in kept:
                    heard = machine.word(word.id)
                    self.assertGreaterEqual(word.start, heard.start, word)
                    self.assertLessEqual(word.end, heard.end, word)
                # A proofread transcript is mostly the machine's own words,
                # with the machine's own times.
                measured = [
                    word
                    for word in kept
                    if (word.start, word.end)
                    == (machine.word(word.id).start, machine.word(word.id).end)
                ]
                self.assertGreater(len(measured), len(words) / 2)
                # Whatever was replaced is heard where its source was.
                for word in words:
                    if word.id in machine.positions or not word.origin:
                        continue
                    sources = [machine.word(source) for source in word.origin]
                    self.assertGreaterEqual(word.start, min(source.start for source in sources))
                    self.assertLessEqual(word.end, max(source.end for source in sources))


if __name__ == "__main__":
    unittest.main()
