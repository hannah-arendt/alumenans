"""End-to-end checks on real recordings, and tests of the harness that runs them.

The recordings live in tests/corpus (see the README there). A machine that
has none of them, such as CI today, skips the end-to-end part.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from alumenans.paths import PACKAGE_DIR

REPO = PACKAGE_DIR.parents[1]

_spec = importlib.util.spec_from_file_location("corpus_tool", REPO / "tools" / "corpus.py")
corpus = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = corpus
_spec.loader.exec_module(corpus)


def make_case(root: Path, name: str = "sample", **manifest) -> "corpus.Case":
    folder = root / name
    folder.mkdir(parents=True)
    lines = [f"{key} = {json.dumps(value)}" for key, value in manifest.items()]
    (folder / corpus.MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return corpus.load_case(folder)


def give_transcript(case, turns, *, audio: bytes | None = b"sound") -> None:
    """Puts a transcript into the case's cache, as `transcribe` would."""

    case.transcript_path.parent.mkdir(parents=True, exist_ok=True)
    case.transcript_path.write_text(json.dumps({"turns": turns}), encoding="utf-8")
    meta = {"speakers": case.speakers}
    if audio is not None and case.audio:
        case.audio.write_bytes(audio)
        meta["audio_sha256"] = corpus.file_digest(case.audio)
    case.transcript_meta_path.write_text(json.dumps(meta), encoding="utf-8")


TURNS = [
    {"speaker": 1, "start": 0.0, "end": 4.0, "text": "Расскажите, пожалуйста, о себе."},
    {"speaker": 2, "start": 4.0, "end": 9.0, "text": "Меня зовут Мария."},
    {"speaker": 2, "start": 9.0, "end": 14.0, "text": "Я живу в Казани."},
]


class TemporaryCorpus(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)


class DiscoveryTests(TemporaryCorpus):
    def test_a_folder_with_a_manifest_is_a_case(self):
        make_case(self.root, "one", audio="a.m4a", speakers=3)
        (self.root / "not-a-case").mkdir()
        cases = corpus.discover([self.root, self.root / "absent"])
        self.assertEqual([case.folder.name for case in cases], ["one"])
        self.assertEqual(cases[0].speakers, 3)
        self.assertEqual(cases[0].audio, self.root / "one" / "a.m4a")

    def test_a_named_guide_that_is_missing_is_an_error(self):
        case = make_case(self.root, guide="guide.txt")
        with self.assertRaises(corpus.CorpusError):
            case.guide

    def test_the_private_part_of_the_corpus_is_not_committed(self):
        ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("tests/corpus/private/", ignored)


class CacheTests(TemporaryCorpus):
    def test_no_transcript_means_the_stage_is_skipped(self):
        case = make_case(self.root, audio="a.m4a")
        self.assertEqual(corpus.cache_status(case), "missing")
        with self.assertRaises(corpus.StageUnavailable):
            corpus.load_transcript(case)

    def test_a_changed_recording_makes_the_transcript_stale(self):
        case = make_case(self.root, audio="a.m4a")
        give_transcript(case, TURNS)
        self.assertEqual(corpus.cache_status(case), "ok")
        case.audio.write_bytes(b"another sound")
        self.assertEqual(corpus.cache_status(case), "stale")
        with self.assertRaises(corpus.CorpusError) as caught:
            corpus.load_transcript(case)
        self.assertNotIsInstance(caught.exception, corpus.StageUnavailable)

    def test_a_changed_speaker_count_makes_the_transcript_stale(self):
        case = make_case(self.root, audio="a.m4a", speakers=2)
        give_transcript(case, TURNS)
        changed = corpus.Case(case.folder, dict(case.manifest, speakers=3))
        self.assertEqual(corpus.cache_status(changed), "stale")

    def test_a_transcript_shared_without_its_recording_is_trusted(self):
        case = make_case(self.root, audio="a.m4a")
        give_transcript(case, TURNS, audio=None)
        self.assertEqual(corpus.cache_status(case), "ok")


class TranscriptTextTests(unittest.TestCase):
    def test_turns_of_one_speaker_are_joined_and_located(self):
        text, ranges = corpus.transcript_text(TURNS)
        self.assertEqual(
            text,
            "Спикер 1: Расскажите, пожалуйста, о себе.\n\n"
            "Спикер 2: Меня зовут Мария. Я живу в Казани.",
        )
        for turn, (start, end) in zip(TURNS, ranges):
            self.assertEqual(text[start:end], turn["text"])

    def test_a_topic_start_gets_a_time_mark_and_breaks_the_block(self):
        turns = [dict(turn) for turn in TURNS]
        turns[2]["topic_start"] = True
        text, ranges = corpus.transcript_text(turns)
        self.assertIn("\n\n[00:09]\n\nСпикер 2: Я живу в Казани.", text)
        start, end = ranges[2]
        self.assertEqual(text[start:end], "Я живу в Казани.")

    def test_an_empty_turn_has_no_place(self):
        turns = [TURNS[0], {"speaker": 2, "text": "  "}, TURNS[1]]
        _, ranges = corpus.transcript_text(turns)
        self.assertEqual(ranges[1], (-1, -1))


class SnapshotTests(TemporaryCorpus):
    def test_canonical_text_does_not_depend_on_key_order_or_float_noise(self):
        first = corpus.canonical({"b": 0.1 + 0.2, "a": [1, {"y": 1, "x": 2}]})
        second = corpus.canonical({"a": [1, {"x": 2, "y": 1}], "b": 0.3})
        self.assertEqual(first, second)
        self.assertIn("Мария", corpus.canonical({"name": "Мария"}))

    def test_a_stage_without_an_accepted_snapshot_is_skipped_but_kept(self):
        case = make_case(self.root)
        with self.assertRaises(corpus.StageUnavailable):
            corpus.compare_snapshot(case, "transcript", "{}\n")
        self.assertEqual(case.output_path("transcript").read_text(encoding="utf-8"), "{}\n")

    def test_accepting_makes_the_output_the_expected_one(self):
        case = make_case(self.root)
        first = corpus.canonical({"issues": [1, 2]})
        self.assertTrue(corpus.accept_snapshot(case, "proofreading", first))
        self.assertFalse(corpus.accept_snapshot(case, "proofreading", first))
        self.assertIsNone(corpus.compare_snapshot(case, "proofreading", first))
        diff = corpus.compare_snapshot(
            case, "proofreading", corpus.canonical({"issues": [1, 3]})
        )
        self.assertIn("-  2", diff)
        self.assertIn("+  3", diff)

    def test_the_transcript_stage_runs_from_the_cache(self):
        case = make_case(self.root, audio="a.m4a")
        give_transcript(case, TURNS)
        output = json.loads(corpus.run_stage(case, "transcript"))
        self.assertEqual(len(output["turns"]), 3)
        self.assertEqual(output["turns"][1]["text"], "Меня зовут Мария.")

    def test_the_guide_stage_is_skipped_without_a_guide(self):
        case = make_case(self.root, audio="a.m4a")
        give_transcript(case, TURNS)
        with self.assertRaises(corpus.StageUnavailable):
            corpus.run_stage(case, "guide")


REFERENCE = """\
[00:00] Спикер 1: Расскажите, пожалуйста, о себе.
[00:04] Спикер 2: Меня зовут {{person:Мария}}. Я живу в {{place:Казани}},
как и {{person:Толстой}} когда-то.
[00:14]
"""


class ReferenceTests(TemporaryCorpus):
    def test_marks_are_removed_and_located(self):
        clean, marks = corpus.strip_markup("Меня зовут {{person:Мария}}, я из {{place:Казани}}.")
        self.assertEqual(clean, "Меня зовут Мария, я из Казани.")
        self.assertEqual([mark["cat"] for mark in marks], ["person", "place"])
        for mark in marks:
            self.assertEqual(clean[mark["start"]:mark["stop"]], mark["text"])

    def test_the_reference_is_read_without_its_marks(self):
        reference, marks = corpus.parse_marked_reference(REFERENCE)
        self.assertEqual(len(reference.turns), 2)
        self.assertEqual(reference.end, 14.0)
        self.assertEqual(
            reference.turns[1].text,
            "Меня зовут Мария. Я живу в Казани, как и Толстой когда-то.",
        )
        self.assertEqual(marks[0], [])
        self.assertEqual([mark["text"] for mark in marks[1]], ["Мария", "Казани", "Толстой"])

    def test_a_draft_reference_is_the_transcript_in_the_reference_format(self):
        case = make_case(self.root, audio="a.m4a")
        give_transcript(case, TURNS)
        draft = corpus.draft_reference(case, mark=False)
        self.assertEqual(
            draft.splitlines()[1], "[00:04.0] Спикер 2: Меня зовут Мария."
        )
        reference, marks = corpus.parse_marked_reference(draft)
        self.assertEqual([turn.text for turn in reference.turns], [t["text"] for t in TURNS])
        self.assertEqual(reference.end, 14.1)
        case.reference_path.write_text(draft, encoding="utf-8")
        self.assertEqual(corpus.metrics(case)["wer"], 0.0)

    def test_recognition_is_measured_against_the_reference(self):
        case = make_case(self.root, audio="a.m4a")
        heard = [dict(turn) for turn in TURNS]
        heard[2]["text"] = "Я живу в Рязани."
        give_transcript(case, heard)
        case.reference_path.write_text(
            "[00:00] Спикер 1: Расскажите, пожалуйста, о себе.\n"
            "[00:04] Спикер 2: Меня зовут Мария. Я живу в Казани.\n"
            "[00:14]\n",
            encoding="utf-8",
        )
        numbers = corpus.metrics(case)
        self.assertEqual(numbers["wer"], round(1 / 11, 4))
        self.assertLess(numbers["speaker_error_rate"], 0.01)
        self.assertNotIn("detection_recall", numbers)

    def test_a_case_without_a_reference_has_no_metrics(self):
        case = make_case(self.root)
        with self.assertRaises(corpus.StageUnavailable):
            corpus.metrics(case)

    def test_only_a_slip_beyond_the_margin_is_a_regression(self):
        case = make_case(self.root, margin=0.01)
        baseline = {"wer": 0.10, "detection_recall": 0.80, "category_accuracy": 0.9}
        fine = {"wer": 0.105, "detection_recall": 0.795, "category_accuracy": 0.9}
        self.assertEqual(corpus.regressions(case, fine, baseline), [])
        better = {"wer": 0.02, "detection_recall": 0.99, "category_accuracy": 1.0}
        self.assertEqual(corpus.regressions(case, better, baseline), [])
        worse = {"wer": 0.13, "detection_recall": 0.70, "category_accuracy": 0.5}
        self.assertEqual(len(corpus.regressions(case, worse, baseline)), 3)


# ---------------------------------------------------------------------------
# The corpus itself
# ---------------------------------------------------------------------------


class CorpusTests(unittest.TestCase):
    """Every case in tests/corpus, stage by stage.

    A failing snapshot is not necessarily a bug: it says the output changed.
    Look at the diff, and if the change is intended, accept it with
    `python tools/corpus.py accept`."""

    @classmethod
    def setUpClass(cls):
        cls.cases = corpus.discover()

    def setUp(self):
        if not self.cases:
            self.skipTest("no recordings in tests/corpus")

    def _check_stage(self, stage):
        for case in self.cases:
            with self.subTest(case=case.name):
                try:
                    actual = corpus.run_stage(case, stage)
                    diff = corpus.compare_snapshot(case, stage, actual)
                except corpus.StageUnavailable as reason:
                    self.skipTest(str(reason))
                if diff is not None:
                    self.fail(
                        f"«{stage}» output changed; full output in "
                        f"{case.output_path(stage)}\n{diff}"
                    )

    def test_transcript(self):
        self._check_stage("transcript")

    def test_proofreading(self):
        self._check_stage("proofreading")

    def test_detection(self):
        self._check_stage("detection")

    def test_guide(self):
        self._check_stage("guide")

    def test_metrics_are_no_worse_than_the_baseline(self):
        for case in self.cases:
            with self.subTest(case=case.name):
                try:
                    baseline = corpus.load_baseline(case)
                    current = corpus.metrics(case)
                except corpus.StageUnavailable as reason:
                    self.skipTest(str(reason))
                self.assertEqual(corpus.regressions(case, current, baseline), [])


if __name__ == "__main__":
    unittest.main()
