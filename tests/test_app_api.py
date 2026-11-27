#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from alumenans.api import AppApi
from alumenans.detection.filters import keep_entity_span
from alumenans.launch import (
    is_incomplete_build,
    missing_packages,
    prepare,
    prepare_launch,
    transcriber_models_ready,
)
from alumenans.dependencies import required_modules
from alumenans.paths import PACKAGE_DIR
from alumenans import i18n
from alumenans.storage.cipher import StudySecret
from alumenans.storage.study_store import study_path_for


def setUpModule():
    # These tests check Russian interface messages.
    previous = i18n.get_language()
    i18n.set_language("ru", persist=False)
    globals()["_previous_language"] = previous


def tearDownModule():
    i18n.set_language(_previous_language, persist=False)


class FakeTranscriber:
    def __init__(self):
        self.started = None

    def status(self):
        return {"status": "idle"}

    def reset(self):
        return {"ok": True}

    def start(self, path, speaker_count, guide=""):
        self.started = (path, speaker_count, guide)
        return {"ok": True}


class AppApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.media = Path(self.temporary.name, "интервью.m4a")
        self.media.touch()
        self.transcriber = FakeTranscriber()
        self.api = AppApi(self.transcriber)
        # An open, unlocked study: nothing about a recording works without one.
        self.api._study_folder = Path(self.temporary.name)
        self.api._study_secret = StudySecret(os.urandom(16), os.urandom(32))
        self.api.selected_media = self.media

    def tearDown(self):
        self.temporary.cleanup()

    def test_transcription_needs_an_unlocked_study(self):
        self.api._study_secret = None

        response = self.api.start_transcription(2)

        self.assertFalse(response["ok"])
        self.assertIn("паролем", response["error"])
        self.assertIsNone(self.transcriber.started)

    def test_passes_known_speaker_count_to_manager(self):
        response = self.api.start_transcription(4)

        self.assertEqual(response, {"ok": True})
        self.assertEqual(self.transcriber.started, ([self.media], 4, ""))

    def test_proofreading_is_saved_when_each_call_comes_on_its_own_thread(self):
        # The window runs every call to the application on a new thread.
        # The threads here outlive their calls, so that no two calls can be
        # given the same thread identifier.
        finished = threading.Event()
        self.addCleanup(finished.set)

        def call(method, *arguments):
            replies = queue.Queue()

            def run():
                replies.put(method(*arguments))
                finished.wait()

            threading.Thread(target=run, daemon=True).start()
            return replies.get(timeout=10)

        turn = {"id": "t1", "start": 0.0, "end": 1.0, "speaker": 1, "text": "Привет", "words": []}
        result = {"source_name": self.media.name, "duration": 1.0, "turns": [turn]}
        self.assertTrue(call(self.api.project_save_transcript, result)["ok"])

        saved = call(self.api.project_save_turns, [{**turn, "text": "Привет!"}])

        self.assertEqual(saved.get("error"), None)
        loaded = call(self.api.project_load)
        self.addCleanup(self.api._close_project)
        self.assertEqual(loaded["state"]["turns"][0]["text"], "Привет!")

    def test_interface_is_shown_only_once_when_bridge_is_ready(self):
        shown = []
        ready = []

        class Window:
            @staticmethod
            def show():
                shown.append(True)

        api = AppApi(self.transcriber, ready_callback=lambda: ready.append(True))
        api.bind_window(Window())

        self.assertEqual(api.mark_interface_ready(), {"ok": True})
        self.assertEqual(api.mark_interface_ready(), {"ok": True})
        self.assertEqual(shown, [True])
        self.assertEqual(ready, [True])

    def test_passes_guide_to_manager(self):
        response = self.api.start_transcription(2, "1. Расскажите о себе?")

        self.assertEqual(response, {"ok": True})
        # A recording is always passed as a list of parts: an interrupted
        # interview consists of several files, but it's one conversation.
        self.assertEqual(
            self.transcriber.started,
            ([self.media], 2, "1. Расскажите о себе?"),
        )

    def test_rejects_invalid_speaker_count(self):
        response = self.api.start_transcription(12)

        self.assertFalse(response["ok"])
        self.assertIsNone(self.transcriber.started)

    @patch("alumenans.api._schedule_new_words_email")
    def test_new_words_are_saved_before_email_draft_opens(self, schedule):
        response = self.api.open_new_words_email(
            ["донатят", "Донатят", "краудфандинг"]
        )

        destination = self.media.parent / "Новые слова.txt"
        self.assertTrue(response["ok"])
        self.assertEqual(
            destination.read_text(encoding="utf-8"),
            "донатят\nкраудфандинг\n",
        )
        schedule.assert_called_once_with(
            destination,
            ["донатят", "краудфандинг"],
        )


class EntityFilterTests(unittest.TestCase):
    @staticmethod
    def keep(text, category):
        return keep_entity_span(
            text,
            {
                "start": 0,
                "stop": len(text),
                "cat": category,
                "conf": "strong",
                "by": "test",
            },
        )

    def test_speech_fillers_and_function_words_are_not_people(self):
        for text in (
            "Угу",
            "А-а",
            "Да Ннормально",
            "Это",
            "Э-э-э",
            "Э-э",
            "Дда-да-да-да-да",
        ):
            with self.subTest(text=text):
                self.assertFalse(self.keep(text, "person"))

    def test_professions_are_never_personal_data(self):
        self.assertFalse(self.keep("чиновники", "job"))
        self.assertFalse(self.keep("художница", "job"))

    def test_generic_institutions_and_public_platforms_are_not_organizations(self):
        for text in (
            "хоспис",
            "фонд паллиативной помощи",
            "Инстаграм",
            "WhatsApp",
            "Storis",
        ):
            with self.subTest(text=text):
                self.assertFalse(self.keep(text, "org"))

    def test_capitalized_verb_is_not_a_place(self):
        self.assertFalse(self.keep("Выровняется", "place"))

    def test_real_names_and_places_are_retained(self):
        self.assertTrue(self.keep("Германия", "place"))
        self.assertTrue(self.keep("фонд «Подари жизнь»", "org"))


class BundledDependencyTests(unittest.TestCase):
    """Packages ship inside the build; the program only checks them.

    Previously, on a missing package or a version mismatch the program
    called pip — on first launch, needing internet, sometimes for half an
    hour. Now something missing means one thing: the build is damaged.
    Installing anything at launch is not allowed."""

    @staticmethod
    def present_except(*absent):
        return lambda module: None if module in absent else object()

    def test_a_missing_module_is_reported_by_name(self):
        with patch(
            "alumenans.dependencies.importlib.util.find_spec",
            side_effect=self.present_except("gliner", "onnx_asr"),
        ):
            self.assertEqual(missing_packages(), ["gliner", "onnx_asr"])

    def test_a_complete_build_needs_nothing(self):
        with patch(
            "alumenans.dependencies.importlib.util.find_spec",
            side_effect=self.present_except(),
        ):
            self.assertEqual(missing_packages(), [])

    def test_pyobjc_is_required_on_mac_only(self):
        self.assertIn("WebKit", required_modules("darwin"))
        self.assertNotIn("WebKit", required_modules("win32"))

    @patch("alumenans.launch.prepare_transcriber_models", return_value=None)
    @patch("alumenans.launch.subprocess.run")
    @patch("alumenans.launch.missing_packages", return_value=["gliner"])
    def test_prepare_never_installs_anything(self, _missing, run, models):
        answer = prepare(prepare_anonymizer=True)
        self.assertTrue(is_incomplete_build(answer), answer)
        self.assertIn("gliner", answer)
        run.assert_not_called()
        models.assert_not_called()

    @patch("alumenans.launch.prepare", side_effect=AssertionError("prepare is not needed"))
    @patch("alumenans.launch.missing_packages", return_value=["torch"])
    def test_an_incomplete_build_stops_the_launch_at_once(self, _missing, _prepare):
        answer = prepare_launch()
        self.assertTrue(is_incomplete_build(answer), answer)

    def test_no_pip_is_left_in_the_program(self):
        source_text = "".join(
            (PACKAGE_DIR / name).read_text(encoding="utf-8")
            for name in ("app.py", "launch.py", "api.py")
        )
        self.assertNotIn('"-m", "pip"', source_text)
        self.assertNotIn("ALUMENANS_PACKAGES", source_text)

    def test_model_check_requires_voxceleb_embedding_not_old_chinese_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in (
                "asr/v3_e2e_ctc.int8.onnx",
                "vad/silero_vad.onnx",
                "embedding/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with patch("alumenans.launch.TRANSCRIBER_MODELS", root):
                self.assertFalse(transcriber_models_ready())
                (
                    root
                    / "embedding"
                    / "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
                ).touch()
                self.assertTrue(transcriber_models_ready())


# A fast key derivation: the real one takes a noticeable fraction of a second.
@patch("alumenans.storage.cipher.KDF_ITERATIONS", 10)
class StudyLockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name, "study")
        self.folder.mkdir()
        self.media = self.folder / "интервью.m4a"
        self.media.touch()
        self.transcriber = FakeTranscriber()
        self.api = AppApi(self.transcriber)
        # Opening a study remembers it; never in the real install folder.
        remembering = patch.object(
            AppApi, "LAST_FOLDER", Path(self.temporary.name, ".last-folder")
        )
        remembering.start()
        self.addCleanup(remembering.stop)

    def tearDown(self):
        self.api._close_project()
        self.api._close_study()
        self.temporary.cleanup()

    def test_without_a_study_nothing_about_a_recording_works(self):
        for reply in (
            self.api.choose_media(str(self.media)),
            self.api.start_transcription(2),
            self.api.project_open(str(self.folder / "x.interview.alumenans")),
            self.api.study_add_word("отписка"),
        ):
            with self.subTest(reply=reply):
                self.assertFalse(reply["ok"])
        self.assertEqual(list(self.folder.iterdir()), [self.media])

    def test_a_chosen_folder_is_locked_and_nothing_is_written(self):
        reply = self.api.study_open(str(self.folder))

        self.assertTrue(reply["ok"])
        self.assertFalse(reply["exists"])
        self.assertTrue(reply["locked"])
        self.assertFalse(self.api.choose_media(str(self.media))["ok"])
        self.assertEqual(list(self.folder.iterdir()), [self.media])

    def test_create_then_unlock(self):
        self.api.study_open(str(self.folder))
        created = self.api.study_create("правильный пароль")
        self.assertTrue(created["ok"], created)
        self.assertTrue(study_path_for(self.folder).is_file())
        self.assertTrue(self.api.choose_media(str(self.media))["ok"])

        reopened = AppApi(FakeTranscriber())
        try:
            reopened.study_open(str(self.folder))
            self.assertTrue(reopened.study_open(str(self.folder))["exists"])
            wrong = reopened.study_unlock("не тот пароль")
            self.assertFalse(wrong["ok"])
            self.assertTrue(wrong["wrong_password"])
            self.assertFalse(reopened.choose_media(str(self.media))["ok"])

            right = reopened.study_unlock("правильный пароль")
            self.assertTrue(right["ok"], right)
            self.assertTrue(reopened.choose_media(str(self.media))["ok"])
        finally:
            reopened._close_study()

    def test_short_password_and_second_create_are_refused(self):
        self.api.study_open(str(self.folder))
        self.assertFalse(self.api.study_create("кратко")["ok"])
        self.assertFalse(study_path_for(self.folder).exists())
        self.assertTrue(self.api.study_create("достаточно длинный")["ok"])
        self.assertFalse(self.api.study_create("достаточно длинный")["ok"])

    def test_choosing_another_folder_locks_again(self):
        self.api.study_open(str(self.folder))
        self.api.study_create("правильный пароль")
        self.api.choose_media(str(self.media))
        other = Path(self.temporary.name, "other")
        other.mkdir()

        self.api.study_open(str(other))

        self.assertIsNone(self.api._study_secret)
        self.assertIsNone(self.api.selected_media)
        self.assertFalse(self.api.start_transcription(2)["ok"])

    def test_the_last_study_is_offered_only_while_it_exists(self):
        self.assertEqual(self.api.last_study_folder()["folder"], "")

        self.api.study_open(str(self.folder))
        self.api.study_create("правильный пароль")

        offered = self.api.last_study_folder()
        self.assertEqual(offered["folder"], str(self.folder))
        self.assertEqual(offered["name"], "study")
        # A remembered folder that no longer holds a study isn't offered.
        AppApi.LAST_FOLDER.write_text(self.temporary.name, encoding="utf-8")
        self.assertEqual(self.api.last_study_folder()["folder"], "")

    def test_work_file_outside_the_study_is_refused(self):
        self.api.study_open(str(self.folder))
        self.api.study_create("правильный пароль")
        stray = Path(self.temporary.name, "elsewhere.interview.alumenans")
        stray.write_bytes(b"x" * 32)

        reply = self.api.project_open(str(stray))

        self.assertFalse(reply["ok"])
        self.assertIn("не в папке", reply["error"])


if __name__ == "__main__":
    unittest.main()
