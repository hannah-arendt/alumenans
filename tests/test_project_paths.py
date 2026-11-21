import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from alumenans.storage.cipher import StudySecret
from alumenans.storage.project_store import (
    ProjectStore,
    project_path_for,
    project_summaries,
)
from alumenans.storage.study_store import STUDY_FILE_NAME


def a_secret():
    return StudySecret(os.urandom(16), os.urandom(32))


def save_interview(path, secret, source_name="interview-1.mp3"):
    store = ProjectStore(path, secret)
    try:
        store.save_transcript(
            {
                "source_name": source_name,
                "duration": 1.0,
                "turns": [{"id": "t1", "start": 0, "end": 1, "speaker": 1, "text": "Да."}],
            }
        )
    finally:
        store.close()


class ProjectPathTests(unittest.TestCase):
    def test_project_is_named_after_the_recording_and_lives_in_the_study(self):
        path = project_path_for("/audio/interview-3.mp3", "/study")

        self.assertEqual(path, Path("/study/interview-3.interview.alumenans"))

    def test_there_is_no_place_for_a_project_without_a_study(self):
        with self.assertRaises(TypeError):
            project_path_for("/audio/interview-3.mp3")

    def test_no_recording_name_turns_into_the_study_file(self):
        for recording in ("study.mp3", "Study.m4a", "STUDY.wav"):
            with self.subTest(recording=recording):
                path = project_path_for(f"/audio/{recording}", "/study")

                self.assertNotEqual(path.name.casefold(), STUDY_FILE_NAME.casefold())


class ProjectListingTests(unittest.TestCase):
    def test_listing_finds_projects_and_skips_the_study_file(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            secret = a_secret()
            (home / STUDY_FILE_NAME).write_bytes(b"study")
            save_interview(project_path_for(home / "interview-1.mp3", home), secret)

            summaries, unreadable = project_summaries(home, secret)

            self.assertEqual([item["source_name"] for item in summaries], ["interview-1.mp3"])
            self.assertEqual(unreadable, [])
            # Reading the list leaves no "-wal"/"-shm" files behind.
            self.assertEqual(
                sorted(item.name for item in home.iterdir()),
                sorted(["interview-1.interview.alumenans", STUDY_FILE_NAME]),
            )

    def test_files_the_password_does_not_open_are_reported_not_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            secret = a_secret()
            save_interview(project_path_for(home / "mine.mp3", home), secret)
            save_interview(project_path_for(home / "theirs.mp3", home), a_secret(), "theirs.mp3")
            plain = project_path_for(home / "old.mp3", home)
            sqlite3.connect(plain).execute("CREATE TABLE t (x)").connection.close()

            summaries, unreadable = project_summaries(home, secret)

            self.assertEqual([item["source_name"] for item in summaries], ["interview-1.mp3"])
            self.assertEqual(
                sorted(item["name"] for item in unreadable),
                [plain.name, "theirs.interview.alumenans"],
            )
            self.assertTrue(all(item["error"] for item in unreadable))


if __name__ == "__main__":
    unittest.main()
