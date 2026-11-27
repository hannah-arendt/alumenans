#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The interface language: which language the person reads the program in.

This is only about text a person reads — labels, errors, progress. It is
deliberately independent of the recording language: the ASR model, NER,
morphology, word lists and pseudonyms written into a transcript never look
at it (see CLAUDE.md, "Recording language and UI language are independent").

The catalogs are plain dicts in `alumenans.locales.<code>`; every language
has the same keys (tests/test_i18n.py checks this). Strings use
`str.format` placeholders: ``t("study.open_failed", error=error)``.

The choice is kept in the `.ui-language` state file. A child process
(the transcription worker) gets it through ALUMENANS_UI_LANGUAGE, which
`paths.child_environment()` sets.
"""

from __future__ import annotations

import importlib
import json
import locale
import os
import re
import subprocess
import sys

from alumenans.paths import RESOURCES, state_file

LANGUAGES = ("ru", "en")
DEFAULT = "ru"
ENV_VAR = "ALUMENANS_UI_LANGUAGE"

_current: str | None = None
_catalogs: dict[str, dict[str, str]] = {}


def language_file():
    return state_file(".ui-language")


def normalize(code) -> str | None:
    value = str(code or "").strip().lower().replace("-", "_")
    base = value.split("_", 1)[0].split(".", 1)[0]
    return base if base in LANGUAGES else None


def system_language() -> str:
    """The operating system's language, reduced to one we support."""

    candidates = []
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        candidates.append(os.environ.get(name, ""))
    try:
        candidates.append(locale.getlocale()[0] or "")
    except Exception:
        pass
    if sys.platform == "darwin":
        # A Finder-launched app usually has no LANG; the system list is here.
        try:
            listed = subprocess.run(
                ["/usr/bin/defaults", "read", "-g", "AppleLanguages"],
                capture_output=True, text=True, timeout=3,
            ).stdout
            first = re.search(r"[A-Za-z]{2}", listed)
            if first:
                candidates.insert(0, first.group(0))
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes

            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            candidates.insert(0, "ru" if (lang_id & 0x3FF) == 0x19 else "en")
        except Exception:
            pass
    for candidate in candidates:
        if not candidate or candidate.split(".", 1)[0] in ("C", "POSIX"):
            continue
        return "ru" if normalize(candidate) == "ru" else "en"
    return DEFAULT


def default_language() -> str:
    """Language for a person who has not chosen one yet.

    An existing installation (the first-run mark is there) keeps Russian,
    which is all it ever showed. A fresh one follows the operating system."""

    if state_file(".installed", legacy=".установлено").exists():
        return DEFAULT
    return system_language()


def get_language() -> str:
    global _current
    if _current:
        return _current
    chosen = normalize(os.environ.get(ENV_VAR))
    if not chosen:
        try:
            chosen = normalize(language_file().read_text(encoding="utf-8"))
        except OSError:
            chosen = None
    _current = chosen or default_language()
    return _current


def set_language(code, *, persist: bool = True) -> str:
    """Switches the interface language; with `persist` also remembers it."""

    global _current
    chosen = normalize(code)
    if not chosen:
        raise ValueError(f"unsupported UI language: {code!r}")
    _current = chosen
    os.environ[ENV_VAR] = chosen
    if persist:
        try:
            language_file().write_text(chosen, encoding="utf-8")
        except OSError:
            pass
    return chosen


def catalog(code: str) -> dict[str, str]:
    if code not in _catalogs:
        module = importlib.import_module(f"alumenans.locales.{code}")
        _catalogs[code] = module.MESSAGES
    return _catalogs[code]


def t(key: str, **params) -> str:
    """The text for `key` in the interface language.

    Falls back to Russian (the complete catalog), then to the key itself, so
    a missing translation shows something rather than crashing."""

    for code in (get_language(), DEFAULT):
        text = catalog(code).get(key)
        if text is not None:
            break
    else:
        text = key
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def screen_catalogs(script: str | None = None) -> dict[str, dict[str, str]]:
    """The screen's catalogs (`const I18N` in resources/i18n.js), by language.

    The page keeps its own text so it can switch without a round trip;
    this reads it back for checks (tests, self-test)."""

    if script is None:
        script = (RESOURCES / "i18n.js").read_text(encoding="utf-8")
    block = re.search(r"^const I18N = \{\n(.*?)\n\};$", script, re.S | re.M)
    if block is None:
        raise ValueError("i18n.js has no I18N catalog")
    result = {}
    for code, body in re.findall(
        r"^  ([a-z]{2}): \{\n(.*?)\n  \}", block.group(1), re.S | re.M
    ):
        result[code] = json.loads("{" + body + "}")
    return result
