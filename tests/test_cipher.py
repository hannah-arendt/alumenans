"""The study's files are encrypted on disk: every page, the WAL included."""

import os
import shutil
import sqlite3
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from alumenans import i18n
from alumenans.storage import cipher
from alumenans.storage.cipher import StudySecret
from alumenans.storage.project_store import ProjectError, ProjectStore
from alumenans.storage.study_store import STUDY_FILE_NAME, StudyStore

NAME = "Мария Ивановна Соколова"


def a_secret():
    return StudySecret(os.urandom(16), os.urandom(32))


def transcript(text=NAME):
    return {
        "source_name": "interview-1.mp3",
        "duration": 2.0,
        "turns": [
            {"id": "t1", "start": 0, "end": 1, "speaker": 1, "text": "Как вас зовут?"},
            {"id": "t2", "start": 1, "end": 2, "speaker": 2, "text": f"Меня зовут {text}."},
        ],
    }


def on_disk(path):
    """Every byte of the database: the main file and its WAL."""

    data = b""
    for item in (path, path.with_name(path.name + "-wal")):
        if item.is_file():
            data += item.read_bytes()
    return data


def setUpModule():
    previous = i18n.get_language()
    i18n.set_language("ru", persist=False)
    globals()["_previous_language"] = previous


def tearDownModule():
    i18n.set_language(_previous_language, persist=False)


class KeyDerivationTests(unittest.TestCase):
    @patch("alumenans.storage.cipher.KDF_ITERATIONS", 10)
    def test_the_same_password_and_salt_give_the_same_key(self):
        salt = os.urandom(16)

        self.assertEqual(
            StudySecret.derive("пароль", salt).key, StudySecret.derive("пароль", salt).key
        )
        self.assertNotEqual(
            StudySecret.derive("пароль", salt).key, StudySecret.derive("пароли", salt).key
        )

    @patch("alumenans.storage.cipher.KDF_ITERATIONS", 10)
    def test_composed_and_decomposed_letters_are_the_same_password(self):
        salt = os.urandom(16)
        composed = unicodedata.normalize("NFC", "йод")
        decomposed = unicodedata.normalize("NFD", "йод")
        self.assertNotEqual(composed, decomposed)

        self.assertEqual(
            StudySecret.derive(composed, salt).key, StudySecret.derive(decomposed, salt).key
        )

    def test_the_key_never_shows_in_its_representation(self):
        secret = a_secret()

        self.assertNotIn(secret.key.hex(), repr(secret))

    def test_a_secret_of_the_wrong_size_is_refused(self):
        with self.assertRaises(ValueError):
            StudySecret(os.urandom(15), os.urandom(32))
        with self.assertRaises(ValueError):
            StudySecret(os.urandom(16), os.urandom(31))


class EncryptedFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.path = self.folder / "interview-1.interview.alumenans"
        self.secret = a_secret()

    def tearDown(self):
        self.temporary.cleanup()

    def test_a_transcript_reads_back(self):
        store = ProjectStore(self.path, self.secret)
        interview_id = store.save_transcript(transcript())
        store.close()

        again = ProjectStore(self.path, self.secret)
        try:
            state = again.load(interview_id)
        finally:
            again.close()
        self.assertIn(NAME, state["turns"][1]["text"])

    def test_nothing_readable_is_on_disk_even_in_the_wal(self):
        store = ProjectStore(self.path, self.secret)
        try:
            store.save_transcript(transcript())
            # Still open, as after a crash: the newest pages are in the WAL.
            self.assertTrue(self.path.with_name(self.path.name + "-wal").is_file())
            data = on_disk(self.path)
        finally:
            store.close()

        self.assertNotIn(NAME.encode("utf-8"), data)
        self.assertNotIn(b"SQLite format 3", data)
        self.assertEqual(self.path.read_bytes()[:16], self.secret.salt)

    def test_a_copy_left_by_a_crash_opens_with_the_key(self):
        store = ProjectStore(self.path, self.secret)
        try:
            interview_id = store.save_transcript(transcript())
            copy = self.folder / "copy.interview.alumenans"
            for end in ("", "-wal", "-shm"):
                shutil.copyfile(f"{self.path}{end}", f"{copy}{end}")
        finally:
            store.close()

        again = ProjectStore(copy, self.secret)
        try:
            self.assertIn(NAME, again.load(interview_id)["turns"][1]["text"])
        finally:
            again.close()

    def test_a_wrong_key_does_not_open_the_file(self):
        with StudyStore(self.path, self.secret) as store:
            store.ensure_study()
        wrong = StudySecret(self.secret.salt, os.urandom(32))

        with self.assertRaises(cipher.WrongPassword):
            cipher.connect(self.path, wrong)
        with self.assertRaises(ProjectError) as raised:
            ProjectStore(self.path, wrong).connect()
        self.assertEqual(str(raised.exception), "Неверный пароль.")

    def test_a_file_of_another_study_is_told_apart(self):
        store = ProjectStore(self.path, a_secret())
        store.save_transcript(transcript())
        store.close()

        with self.assertRaises(cipher.OtherStudy):
            cipher.connect(self.path, self.secret)

    def test_a_plaintext_file_from_an_earlier_version_is_refused(self):
        sqlite3.connect(self.path).execute("CREATE TABLE t (x)").connection.close()

        with self.assertRaises(cipher.NotEncrypted):
            cipher.connect(self.path, self.secret)
        with self.assertRaises(cipher.NotEncrypted):
            StudySecret.for_file(self.path, "пароль")

    def test_a_file_too_short_to_be_a_study_file(self):
        self.path.write_bytes(b"short")

        with self.assertRaises(cipher.NotAStudyFile):
            StudySecret.for_file(self.path, "пароль")

    def test_forgetting_the_original_keeps_the_file_encrypted(self):
        store = ProjectStore(self.path, self.secret)
        try:
            interview_id = store.save_transcript(transcript())
            store.save_anonymized_turns(
                interview_id,
                [
                    {"id": "t1", "text": "Как вас зовут?"},
                    {"id": "t2", "text": "Меня зовут [ИМЯ]."},
                ],
            )
            store.forget_original_text(interview_id)
        finally:
            store.close()

        self.assertNotIn(NAME.encode("utf-8"), on_disk(self.path))
        self.assertEqual(self.path.read_bytes()[:16], self.secret.salt)
        again = ProjectStore(self.path, self.secret)
        try:
            self.assertIn("[ИМЯ]", again.load(interview_id)["turns"][1]["text"])
        finally:
            again.close()

    def test_the_study_file_is_encrypted_with_the_same_secret(self):
        path = self.folder / STUDY_FILE_NAME
        store = StudyStore(path, self.secret)
        try:
            study_id = store.ensure_study()
            store.save_study(study_id, title=NAME)
            self.assertNotIn(NAME.encode("utf-8"), on_disk(path))
        finally:
            store.close()

        again = StudyStore(path, self.secret)
        try:
            self.assertEqual(again.load_study()["title"], NAME)
        finally:
            again.close()


if __name__ == "__main__":
    unittest.main()
