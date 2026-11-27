"""The open interview: each action edits, saves and reports."""

import random
import tempfile
import threading
import unittest
from pathlib import Path

from alumenans.document.editing import EditError
from alumenans.document.transcript import from_machine_turns, validate
from alumenans.open_interview import OpenInterview, StaleRevision
from alumenans.storage.cipher import StudySecret
from alumenans.storage.interview_store import Details, InterviewStore


def machine_turn(speaker, text, start, step=1.0):
    return {
        "speaker": speaker,
        "words": [
            {"text": word, "start": start + step * index, "end": start + step * index + 0.8}
            for index, word in enumerate(text.split())
        ],
    }


class OpenInterviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = InterviewStore(
            Path(self.temporary.name, "recording.interview.alumenans"), StudySecret(b"s" * 16, b"k" * 32)
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def open(self, *turns):
        """An interview of three turns (words 1-3, 4-7, 8-10) unless given."""

        turns = turns or (
            machine_turn(1, "Привет, как дела?", 0.0),
            machine_turn(2, "Хорошо. А у тебя?", 10.0),
            machine_turn(1, "Тоже неплохо, спасибо.", 20.0),
        )
        machine = from_machine_turns(turns)
        self.store.create(machine, Details())
        self.interview = OpenInterview(self.store, machine)
        return self.interview

    def texts(self):
        return [(turn.speaker, turn.text) for turn in self.interview.transcript.turns]

    def assert_saved(self):
        validate(self.interview.transcript)
        self.assertEqual(self.store.load(), self.interview.transcript)

    # -- reports -------------------------------------------------------------

    def test_the_view_is_the_whole_transcript(self):
        view = self.open().view()
        self.assertEqual(view["revision"], 0)
        self.assertEqual(view["order"], [1, 2, 3])
        self.assertEqual([turn["id"] for turn in view["changed"]], [1, 2, 3])
        self.assertEqual(
            view["changed"][0],
            {
                "id": 1,
                "speaker": 1,
                "start": 0.0,
                "end": 2.8,
                "words": [
                    {"id": 1, "text": "Привет,", "start": 0.0, "end": 0.8},
                    {"id": 2, "text": "как", "start": 1.0, "end": 1.8},
                    {"id": 3, "text": "дела?", "start": 2.0, "end": 2.8},
                ],
            },
        )
        self.assertFalse(view["can_undo"] or view["can_redo"])

    def test_typing_reports_the_one_turn_and_is_saved(self):
        change = self.open().retext(0, 2, "Хорошо. А у вас?")
        self.assertEqual(change["revision"], 1)
        self.assertEqual(change["order"], [1, 2, 3])
        self.assertEqual([turn["id"] for turn in change["changed"]], [2])
        self.assertEqual(change["changed"][0]["words"][3]["text"], "вас?")
        self.assertTrue(change["can_undo"])
        self.assert_saved()

    def test_deleting_a_turns_text_takes_it_out_of_the_order(self):
        change = self.open().retext(0, 2, "")
        self.assertEqual(change["order"], [1, 3])
        self.assertEqual(change["changed"], [])
        self.assert_saved()

    # -- refusals --------------------------------------------------------------

    def test_an_edit_to_an_old_revision_is_refused(self):
        interview = self.open()
        interview.retext(0, 2, "Хорошо.")
        before = interview.transcript
        with self.assertRaises(StaleRevision):
            interview.retext(0, 2, "Плохо.")
        self.assertIs(interview.transcript, before)
        self.assert_saved()

    def test_an_edit_that_does_not_fit_changes_nothing(self):
        interview = self.open()
        for action in (
            lambda: interview.retext(0, 99, "Да."),
            lambda: interview.split(0, 1, "", "Привет, как дела?"),  # at the start of a turn
            lambda: interview.split(0, 1, "Привет, как дела?", " "),  # at its end
            lambda: interview.merge(0, 1),  # no turn before the first
            # The second replacement names a word the first one removed.
            lambda: interview.replace(0, [((2, 3), "что"), ((3, 3), "дело?")]),
        ):
            with self.assertRaises(EditError):
                action()
        self.assertEqual(interview.transcript.revision, 0)
        self.assertFalse(interview.view()["can_undo"])
        self.assert_saved()

    def test_an_edit_that_changes_nothing_is_not_a_step(self):
        interview = self.open()
        for change in (
            interview.retext(0, 2, "Хорошо.  А у тебя?"),
            interview.set_speaker(0, 2, 2),
            interview.undo(),
            interview.redo(),
        ):
            self.assertEqual((change["revision"], change["changed"]), (0, []))
            self.assertFalse(change["can_undo"])

    # -- turns -----------------------------------------------------------------

    def test_a_turn_is_split_at_the_caret_and_joined_back(self):
        interview = self.open()
        change = interview.split(0, 2, "Хорошо. А ", "у тебя?")
        self.assertEqual(change["order"], [1, 2, 4, 3])
        self.assertEqual([turn["id"] for turn in change["changed"]], [2, 4])
        self.assertEqual(self.texts()[1:3], [(2, "Хорошо. А"), (2, "у тебя?")])
        self.assertEqual([word.id for word in interview.transcript.turn(4).words], [6, 7])
        self.assert_saved()
        change = interview.merge(change["revision"], 4)
        self.assertEqual(change["order"], [1, 2, 3])
        self.assertEqual(self.texts()[1], (2, "Хорошо. А у тебя?"))
        self.assert_saved()

    def test_a_split_inside_a_word_cuts_the_word(self):
        interview = self.open()
        interview.split(0, 2, "Хоро", "шо. А у тебя?")
        self.assertEqual(self.texts()[1:3], [(2, "Хоро"), (2, "шо. А у тебя?")])
        first, second = interview.transcript.turn(2).words[0], interview.transcript.turn(4).words[0]
        self.assertEqual((first.start, second.end), (10.0, 10.8))  # they share the word's time
        self.assert_saved()
        interview.undo()  # one step
        self.assertEqual(self.texts()[1], (2, "Хорошо. А у тебя?"))

    def test_a_split_takes_typing_that_was_not_saved_yet(self):
        interview = self.open()
        interview.split(0, 2, "Хорошо, очень. ", "А у вас?")
        self.assertEqual(self.texts()[1:3], [(2, "Хорошо, очень."), (2, "А у вас?")])
        self.assert_saved()

    def test_a_turn_given_to_another_speaker_stays_a_turn(self):
        interview = self.open()
        change = interview.set_speaker(0, 2, 1)
        self.assertEqual(change["order"], [1, 2, 3])
        self.assertEqual([turn["id"] for turn in change["changed"]], [2])
        self.assertEqual([speaker for speaker, _ in self.texts()], [1, 1, 1])
        self.assert_saved()

    def test_only_turns_by_one_speaker_are_joined(self):
        interview = self.open()
        with self.assertRaises(EditError):
            interview.merge(0, 2)
        interview.set_speaker(0, 2, 1)
        interview.merge(1, 2)
        self.assertEqual(self.texts()[0], (1, "Привет, как дела? Хорошо. А у тебя?"))
        self.assert_saved()

    # -- replacing -------------------------------------------------------------

    def test_replacements_in_many_places_are_one_step(self):
        interview = self.open()
        change = interview.replace(0, [((2, 3), "что нового, друг?"), ((9, 9), "хорошо,")])
        self.assertEqual([turn["id"] for turn in change["changed"]], [1, 3])
        self.assertEqual(self.texts()[0], (1, "Привет, что нового, друг?"))
        self.assertEqual(self.texts()[2], (1, "Тоже хорошо, спасибо."))
        self.assert_saved()
        interview.undo()
        self.assertEqual(self.texts()[0], (1, "Привет, как дела?"))
        self.assertEqual(self.texts()[2], (1, "Тоже неплохо, спасибо."))

    def test_a_replacement_that_leaves_only_punctuation_gives_it_to_the_word_before(self):
        interview = self.open()
        interview.replace(0, [((2, 2), ","), ((8, 8), "…"), ((5, 6), "")])
        self.assertEqual(
            self.texts(), [(1, "Привет,, дела?"), (2, "Хорошо. тебя?"), (1, "неплохо, спасибо.")]
        )
        self.assert_saved()
        interview.undo()
        self.assertEqual(self.texts()[0], (1, "Привет, как дела?"))

    # -- undo and redo -----------------------------------------------------------

    def test_undo_and_redo_restore_the_text_and_save_it(self):
        interview = self.open()
        interview.retext(0, 2, "Хорошо.")
        interview.retext(1, 2, "Плохо.")
        change = interview.undo()
        self.assertEqual(self.texts()[1], (2, "Хорошо."))
        self.assertEqual([turn["id"] for turn in change["changed"]], [2])
        self.assertTrue(change["can_undo"] and change["can_redo"])
        self.assert_saved()
        interview.undo()
        self.assertEqual(self.texts()[1], (2, "Хорошо. А у тебя?"))
        self.assertFalse(interview.view()["can_undo"])
        self.assert_saved()
        interview.redo()
        interview.redo()
        self.assertEqual(self.texts()[1], (2, "Плохо."))
        self.assertFalse(interview.view()["can_redo"])
        self.assert_saved()

    def test_a_visit_to_a_turn_is_one_step_however_often_it_was_saved(self):
        interview = self.open()
        interview.retext(0, 2, "Хорошо. А у")
        interview.retext(1, 2, "Хорошо. А у вас", continues=True)
        interview.retext(2, 2, "Хорошо. А у вас всё?", continues=True)
        interview.undo()
        self.assertEqual(self.texts()[1], (2, "Хорошо. А у тебя?"))
        self.assertFalse(interview.view()["can_undo"])
        self.assert_saved()

    def test_a_new_visit_or_another_action_starts_a_new_step(self):
        interview = self.open()
        interview.retext(0, 2, "Хорошо.")
        interview.retext(1, 2, "Плохо.")  # came back to the turn later
        interview.retext(2, 1, "Привет!", continues=True)  # the page is wrong: another turn
        interview.set_speaker(3, 1, 2)
        interview.retext(4, 1, "Привет.", continues=True)  # not typing just before
        for expected in ("Привет!", "Привет!", "Привет, как дела?"):
            interview.undo()
            self.assertEqual(self.texts()[0][1], expected)
        interview.undo()
        self.assertEqual(self.texts()[1], (2, "Хорошо."))

    def test_a_save_that_changes_nothing_does_not_join_a_visit_to_the_step_before(self):
        interview = self.open()
        interview.set_speaker(0, 1, 2)
        interview.retext(1, 2, "Хорошо. А у тебя?")  # nothing typed yet
        interview.retext(1, 2, "Хорошо.", continues=True)
        interview.undo()
        self.assertEqual(self.texts()[:2], [(2, "Привет, как дела?"), (2, "Хорошо. А у тебя?")])

    def test_the_revision_never_repeats(self):
        interview = self.open()
        interview.retext(0, 2, "Хорошо.")
        self.assertEqual(interview.undo()["revision"], 2)
        with self.assertRaises(StaleRevision):
            interview.retext(0, 2, "Плохо.")  # the page still shows the first version
        self.assertEqual(interview.redo()["revision"], 3)

    def test_a_new_edit_ends_redo(self):
        interview = self.open()
        interview.retext(0, 2, "Хорошо.")
        interview.undo()
        change = interview.retext(2, 1, "Привет!")
        self.assertFalse(change["can_redo"])
        self.assertEqual(interview.redo()["changed"], [])

    # -- everything together -------------------------------------------------------

    def test_the_window_may_call_from_any_thread(self):
        interview = self.open()
        failures = []

        def call():
            try:
                interview.retext(0, 2, "Хорошо.")
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=call)
        thread.start()
        thread.join()
        self.assertEqual(failures, [])
        self.assert_saved()

    def test_a_long_run_of_actions_keeps_memory_and_file_the_same(self):
        vocabulary = "да нет вот, это было. Потом мы — ну как-то так".split()
        done = 0
        for seed in range(10):
            generator = random.Random(seed)
            interview = self.open()
            for _ in range(40):
                transcript = interview.transcript
                turn = generator.choice(transcript.turns) if transcript.turns else None
                word = generator.choice(turn.words).id if turn else 0
                last = generator.choice(turn.words).id if turn else 0
                revision = transcript.revision
                text = " ".join(generator.choices(vocabulary, k=generator.randint(0, 5)))
                actions = [
                    lambda: interview.retext(
                        revision, turn.id if turn else 0, text, generator.random() < 0.5
                    ),
                    lambda: interview.replace(revision, [((word, last), text)]),
                    lambda: interview.split(
                        revision, turn.id if turn else 0, text, generator.choice(vocabulary)
                    ),
                    lambda: interview.merge(revision, turn.id if turn else 0),
                    lambda: interview.set_speaker(revision, turn.id if turn else 0, generator.randint(1, 3)),
                    interview.undo,
                    interview.redo,
                ]
                try:
                    change = generator.choice(actions)()
                except EditError:
                    self.assertIs(interview.transcript, transcript)
                    continue
                done += change["revision"] != revision
                self.assertEqual(change["order"], [turn.id for turn in interview.transcript.turns])
                self.assert_saved()
            self.store.close()
        self.assertGreater(done, 150)


if __name__ == "__main__":
    unittest.main()
