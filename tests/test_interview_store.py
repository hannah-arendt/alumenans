"""The interview file: the transcript goes in and comes out unchanged."""

import random
import tempfile
import unittest
from pathlib import Path

from alumenans import i18n
from alumenans.document.editing import MergeTurns, SetSpeaker, SplitTurn, apply, retext
from alumenans.document.transcript import from_machine_turns, validate
from alumenans.storage.cipher import StudySecret
from alumenans.storage.interview_store import InterviewFileError, InterviewStore
from alumenans.storage.project_store import ProjectStore


def setUpModule():
    i18n.set_language("en", persist=False)


def machine_transcript():
    def turn(speaker, text, start):
        return {
            "speaker": speaker,
            "words": [
                {"text": word, "start": start + index, "end": start + index + 0.8, "confidence": 0.9}
                for index, word in enumerate(text.split())
            ],
        }

    return from_machine_turns(
        [
            turn(1, "Привет, как дела?", 0.0),
            turn(2, "Хорошо. А у тебя?", 10.0),
            turn(1, "Тоже неплохо, спасибо.", 20.0),
        ]
    )


class InterviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name, "inna.interview.alumenans")
        self.secret = StudySecret(b"s" * 16, b"k" * 32)
        self.store = InterviewStore(self.path, self.secret)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def reopened(self):
        self.store.close()
        self.store = InterviewStore(self.path, self.secret)
        return self.store

    def test_a_new_file_holds_no_transcript(self):
        self.assertIsNone(self.store.load())
        self.assertIsNone(self.store.machine())

    def test_a_transcription_reads_back_as_it_was(self):
        machine = machine_transcript()
        self.store.create(machine)
        store = self.reopened()
        self.assertEqual(store.load(), machine)
        self.assertEqual(store.machine(), machine)

    def test_an_edit_is_saved_and_the_machine_transcript_is_not_touched(self):
        machine = machine_transcript()
        self.store.create(machine)
        edited, _ = retext(machine, 2, "Хорошо. А как у тебя дела?")
        self.store.save(edited, machine)
        store = self.reopened()
        self.assertEqual(store.load(), edited)
        self.assertEqual(store.machine(), machine)

    def test_only_the_changed_turns_are_rewritten(self):
        machine = machine_transcript()
        self.store.create(machine)
        connection = self.store.connect()
        edited, _ = retext(machine, 2, "Хорошо.")
        before = connection.total_changes
        self.store.save(edited, machine)
        # One turn deleted with its 4 words and written back with 1, the
        # order of 3 turns, the counters.
        self.assertEqual(connection.total_changes - before, 1 + 4 + 1 + 1 + 3 + 1)

    def test_going_back_to_an_earlier_version_is_a_save_too(self):
        machine = machine_transcript()
        self.store.create(machine)
        edited, _ = apply(machine, MergeTurns(2))
        self.store.save(edited, machine)
        self.store.save(machine, edited)
        self.assertEqual(self.reopened().load(), machine)

    def test_any_run_of_edits_reads_back(self):
        texts = ["да", "нет, не так", "ну — вот", "Қазақстанда были", ""]
        for seed in range(10):
            dice = random.Random(seed)
            current = machine_transcript()
            self.store.create(current)
            for _ in range(40):
                turn = dice.choice(current.turns)
                edit = dice.choice(
                    [
                        lambda: retext(current, turn.id, turn.text + " " + dice.choice(texts)),
                        lambda: retext(current, turn.id, dice.choice(texts)),
                        lambda: apply(current, SplitTurn(turn.id, dice.randrange(len(turn.words) + 1))),
                        lambda: apply(current, MergeTurns(turn.id)),
                        lambda: apply(current, SetSpeaker(turn.id, dice.randint(1, 3))),
                    ]
                )
                try:
                    after, _ = edit()
                except Exception:
                    continue  # an edit the transcript refuses
                if not after.turns:
                    continue
                self.store.save(after, current)
                current = after
            validate(current)
            self.assertEqual(self.reopened().load(), current, seed)

    def test_a_fresh_transcription_replaces_the_previous_one(self):
        machine = machine_transcript()
        self.store.create(machine)
        edited, _ = retext(machine, 1, "Здравствуйте.")
        self.store.save(edited, machine)
        self.store.create(machine)
        self.assertEqual(self.reopened().load(), machine)

    def test_a_file_made_by_an_earlier_version_is_refused(self):
        self.store.close()
        old = ProjectStore(self.path, self.secret)
        old.connect()
        old.close()
        with self.assertRaisesRegex(InterviewFileError, "another version"):
            InterviewStore(self.path, self.secret).connect()

    def test_another_password_is_refused(self):
        self.store.create(machine_transcript())
        self.store.close()
        other = InterviewStore(self.path, StudySecret(b"s" * 16, b"x" * 32))
        with self.assertRaises(InterviewFileError):
            other.connect()


if __name__ == "__main__":
    unittest.main()
