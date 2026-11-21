#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The interface language (Russian / English).

Interface text lives in catalogs: `alumenans.locales.<code>` for Python,
`const I18N` in anonymizer.html for the screen. Each language must have the
same keys with the same placeholders, and everything the code asks for must
exist — otherwise a person sees a bare key or a half-translated window.

The interface language is independent of the recording language: the
pseudonyms, speaker labels and file names written for a Russian interview
stay Russian whatever the person reads the program in.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alumenans import i18n
from alumenans.i18n import screen_catalogs
from alumenans.locales import en as python_en
from alumenans.locales import ru as python_ru
from alumenans.paths import PACKAGE_DIR, RESOURCES, child_environment

SCREEN = (RESOURCES / "anonymizer.html").read_text(encoding="utf-8")
I18N_SCRIPT = (RESOURCES / "i18n.js").read_text(encoding="utf-8")
CATALOGS = screen_catalogs(I18N_SCRIPT)
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def placeholders(text):
    return sorted(set(re.findall(r"\{(\w+)\}", text)))


class LanguageState(unittest.TestCase):
    """Restores the module's language after each test."""

    def setUp(self):
        self._current = i18n._current
        self._env = os.environ.get(i18n.ENV_VAR)

    def tearDown(self):
        i18n._current = self._current
        if self._env is None:
            os.environ.pop(i18n.ENV_VAR, None)
        else:
            os.environ[i18n.ENV_VAR] = self._env


class PythonCatalogTests(unittest.TestCase):
    def test_languages_have_the_same_keys(self):
        self.assertEqual(set(python_ru.MESSAGES), set(python_en.MESSAGES))

    def test_placeholders_match(self):
        for key, text in python_ru.MESSAGES.items():
            self.assertEqual(placeholders(text), placeholders(python_en.MESSAGES[key]), key)

    def test_english_has_no_russian_left(self):
        for key, text in python_en.MESSAGES.items():
            self.assertIsNone(CYRILLIC.search(text), key)

    def test_every_key_asked_for_exists(self):
        used = set()
        for path in PACKAGE_DIR.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            used.update(re.findall(r'\bt\(\s*"([a-z0-9_.]+)"', source))
            used.update(re.findall(r'^\s*"(proof\.similar_in_\w+)"', source, re.M))
        self.assertTrue(used)
        self.assertEqual(sorted(used - set(python_ru.MESSAGES)), [])

    def test_file_dialog_filters_are_valid_for_pywebview(self):
        # pywebview only accepts letters, digits and spaces before "(*.ext)":
        # a hyphen or comma in the description makes the dialog raise ValueError.
        try:
            from webview.util import parse_file_type
        except ImportError:
            self.skipTest("pywebview is not installed")
        for catalog in (python_ru.MESSAGES, python_en.MESSAGES):
            for key, text in catalog.items():
                if key.startswith("api.filetype_"):
                    with self.subTest(key=key, text=text):
                        parse_file_type(text)


class ScreenCatalogTests(unittest.TestCase):
    def test_both_languages_are_there(self):
        self.assertEqual(list(CATALOGS), ["ru", "en"])

    def test_languages_have_the_same_keys(self):
        self.assertEqual(set(CATALOGS["ru"]), set(CATALOGS["en"]))

    def test_placeholders_and_markup_match(self):
        russian, english = CATALOGS["ru"], CATALOGS["en"]
        for key, text in russian.items():
            self.assertEqual(placeholders(text), placeholders(english[key]), key)
            self.assertEqual(re.findall(r"</?\w+", text), re.findall(r"</?\w+", english[key]), key)

    def test_english_has_no_russian_left(self):
        for key, text in CATALOGS["en"].items():
            if key == "language.self":
                continue
            self.assertIsNone(CYRILLIC.search(text), key)

    def test_every_key_asked_for_exists(self):
        texts = CATALOGS["ru"]
        script = SCREEN[SCREEN.index("<script>"):] + I18N_SCRIPT
        markup = SCREEN[:SCREEN.index("<script>")]
        missing = []
        for key in re.findall(r'\btr\("([a-z0-9_.]+)"', script):
            if key not in texts:
                missing.append(key)
        for key in re.findall(r'\btrn\("([a-z0-9_.]+)"', script):
            if f"{key}.many" not in texts:
                missing.append(key)
        for key in re.findall(r'data-i18n(?:-title|-placeholder|-aria-label)?="([a-z0-9_.]+)"', markup):
            if key not in texts and f"{key}.many" not in texts:
                missing.append(key)
        self.assertEqual(missing, [])

    def test_the_markup_carries_keys_not_russian(self):
        body = SCREEN[SCREEN.index("<body"):SCREEN.index("<script>")]
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        texts = [text.strip() for text in re.findall(r">([^<>]*)<", body)]
        self.assertEqual([text for text in texts if CYRILLIC.search(text)], [])
        attributes = re.findall(r'\s(?:title|placeholder|aria-label)="([^"]*)"', body)
        self.assertEqual([value for value in attributes if CYRILLIC.search(value)], [])

    def test_the_page_takes_its_language_from_the_address(self):
        self.assertIn('new URLSearchParams(location.search).get("lang")', I18N_SCRIPT)
        app_source = (PACKAGE_DIR / "app.py").read_text(encoding="utf-8")
        self.assertIn('/?lang={i18n.get_language()}', app_source)
        self.assertIn('self.path.split("?", 1)[0] in ("/", "/index.html")', app_source)

    def test_the_catalog_loads_before_the_page_script(self):
        # A classic script, so I18N, UI_LANG and tr() are globals the page shares.
        self.assertIn('<script src="/i18n.js"></script>\n<script>', SCREEN)
        self.assertNotIn("const I18N", SCREEN)

    def test_the_switch_is_remembered_by_python(self):
        self.assertIn('appCall("set_ui_language", code)', SCREEN)
        self.assertIn('id="uiLanguage"', SCREEN)

    def test_pseudonyms_stay_in_the_recording_language(self):
        # What goes into the transcript is language data, not interface text.
        self.assertIn('stub:"Персона"', SCREEN)
        self.assertIn('return value || `Спикер ${number}`;', SCREEN)


class PluralTests(unittest.TestCase):
    def test_russian_forms(self):
        expected = {1: "one", 2: "few", 4: "few", 5: "many", 11: "many", 12: "many",
                    14: "many", 21: "one", 22: "few", 25: "many", 111: "many", 0: "many"}
        for n, form in expected.items():
            self.assertEqual(i18n.plural_form(n, "ru"), form, n)

    def test_english_forms(self):
        self.assertEqual(i18n.plural_form(1, "en"), "one")
        for n in (0, 2, 5, 21):
            self.assertEqual(i18n.plural_form(n, "en"), "many", n)


class TranslationTests(LanguageState):
    def test_the_text_follows_the_language(self):
        i18n.set_language("ru", persist=False)
        self.assertEqual(i18n.t("key.wrong_password"), "Пароль не подходит.")
        i18n.set_language("en", persist=False)
        self.assertEqual(i18n.t("key.wrong_password"), "Wrong password.")

    def test_parameters_are_filled_in(self):
        i18n.set_language("en", persist=False)
        self.assertEqual(i18n.t("project.unresolved", count=3), "Unresolved anonymization findings left: 3.")

    def test_a_missing_translation_falls_back_to_russian_then_the_key(self):
        i18n.set_language("en", persist=False)
        with patch.dict(python_en.MESSAGES, clear=True):
            i18n._catalogs.pop("en", None)
            try:
                self.assertEqual(i18n.t("key.wrong_password"), "Пароль не подходит.")
            finally:
                i18n._catalogs.pop("en", None)
        self.assertEqual(i18n.t("no.such.key"), "no.such.key")

    def test_unknown_language_is_refused(self):
        with self.assertRaises(ValueError):
            i18n.set_language("de", persist=False)


class ChoiceTests(LanguageState):
    def setUp(self):
        super().setUp()
        folder = Path(tempfile.mkdtemp())
        self.file = folder / ".ui-language"
        self.installed = folder / ".installed"
        patcher = patch("alumenans.i18n.language_file", return_value=self.file)
        patcher.start()
        self.addCleanup(patcher.stop)
        state = patch(
            "alumenans.i18n.state_file",
            side_effect=lambda name, legacy=None: folder / name,
        )
        state.start()
        self.addCleanup(state.stop)
        i18n._current = None
        os.environ.pop(i18n.ENV_VAR, None)

    def test_the_choice_is_remembered(self):
        i18n.set_language("en")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "en")
        i18n._current = None
        os.environ.pop(i18n.ENV_VAR, None)
        self.assertEqual(i18n.get_language(), "en")

    def test_a_child_process_follows_its_parent(self):
        self.file.write_text("ru", encoding="utf-8")
        os.environ[i18n.ENV_VAR] = "en"
        self.assertEqual(i18n.get_language(), "en")

    def test_an_existing_installation_stays_russian(self):
        self.installed.write_text("done", encoding="utf-8")
        with patch("alumenans.i18n.system_language", return_value="en"):
            self.assertEqual(i18n.get_language(), "ru")

    def test_a_new_installation_follows_the_system(self):
        with patch("alumenans.i18n.system_language", return_value="en"):
            self.assertEqual(i18n.get_language(), "en")

    def test_the_system_language_is_reduced_to_a_supported_one(self):
        with patch.dict(os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "ru_RU.UTF-8"}), \
                patch("alumenans.i18n.sys.platform", "linux"):
            self.assertEqual(i18n.system_language(), "ru")
        with patch.dict(os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "de_DE.UTF-8"}), \
                patch("alumenans.i18n.sys.platform", "linux"):
            self.assertEqual(i18n.system_language(), "en")

    def test_child_environment_carries_the_language(self):
        i18n.set_language("en", persist=False)
        self.assertEqual(child_environment({})[i18n.ENV_VAR], "en")


class ApiTests(LanguageState):
    def test_the_window_can_read_and_switch_the_language(self):
        from alumenans.app import AppApi

        api = AppApi(transcriber=None)
        with patch("alumenans.i18n.language_file", return_value=Path(tempfile.mkdtemp()) / ".ui-language"):
            answer = api.set_ui_language("en")
            self.assertEqual(answer, {"ok": True, "language": "en"})
            self.assertEqual(api.get_ui_language()["language"], "en")
            self.assertEqual(api.get_ui_language()["languages"], ["ru", "en"])
            self.assertFalse(api.set_ui_language("xx")["ok"])


if __name__ == "__main__":
    unittest.main()
