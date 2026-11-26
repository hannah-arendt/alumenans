#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Returning to proofreading does not wipe out the anonymisation.

The order "proofread first, then anonymise" is right, but a person
notices a proofreading mistake after already replacing half the names.
Previously the anonymisation tab was rebuilt from the proofreading text
every time it was opened, and that half was lost.

Now the replacements already made are carried into the proofreading text
itself — as if they had been typed there by hand. The key is checked
separately: the replaced names are no longer in the text, but the
"name → pseudonym" mapping must not be lost.
"""

import re
import unittest

from alumenans.i18n import plural_form, screen_catalogs
from alumenans.paths import PACKAGE_DIR


def catalog(language="ru"):
    return screen_catalogs()[language]


def languages():
    return list(screen_catalogs())



SCREEN = (PACKAGE_DIR / "resources" / "anonymizer.html").read_text(
    encoding="utf-8"
)



def handler(element_id):
    """The whole top-level `$("…").onclick = …` handler, up to its closing `};`.

    Not a fixed number of characters: comments inside the handler may grow."""
    start = SCREEN.find(f'$("{element_id}").onclick')
    if start < 0:
        raise AssertionError(f"no onclick handler for {element_id}")
    return SCREEN[start:SCREEN.index("\n};", start) + 3]


def body(name):
    """The function's text from its header to the next one at zero indentation."""

    start = SCREEN.find(f"function {name}(")
    if start < 0:
        raise AssertionError(f"no function {name}")
    end = SCREEN.find("\nfunction ", start + 1)
    return SCREEN[start:end if end > 0 else len(SCREEN)]


class CarryTests(unittest.TestCase):
    def test_the_carry_runs_when_leaving_for_proofreading(self):
        click = handler("tabProofreading")
        self.assertIn("carryAnonymizationIntoProof();", click)

    def test_replacements_land_in_the_turns_themselves(self):
        code = body("carryAnonymizationIntoProof")
        # The replacement is written into the turn's text and inflected the same
        # way as in the finished document: otherwise proofreading would show
        # «Персона 1» where the case calls for «Персону 1» (accusative).
        self.assertIn("inflect(g.pseudonym, e)", code)
        self.assertIn("turnItem.text = assembled", code)

    def test_the_edited_turn_counts_as_edited_by_hand(self):
        # Otherwise the proofreading check will offer to restore the original word.
        self.assertIn("PROOF.manuallyEdited.add(id)", body("carryAnonymizationIntoProof"))

    def test_the_carry_uses_the_map_taken_when_the_tab_opened(self):
        """Turn boundaries are taken once, when the tab is opened.

        Recomputing them later isn't possible: by then the turns have
        already been rewritten by replacements, and the findings' offsets
        were taken from the original text. There used to be a check here
        that the texts matched, and it silently cancelled the carry — along
        with all the work."""

        code = body("carryAnonymizationIntoProof")
        self.assertIn("!state.turns.length", code)
        self.assertNotIn("proofTranscriptWithMap()", code)

    def test_opening_the_tab_again_keeps_the_key(self):
        code = body("openAnonymizer")
        self.assertIn("keepKey:true", code)

    def test_a_new_file_drops_the_key(self):
        # Someone else's replacements in the key are worse than none at all.
        code = body("load")
        self.assertIn("if (!options || !options.keepKey) state.carried = []", code)


class KeyTests(unittest.TestCase):
    def test_the_saved_key_includes_the_carried_replacements(self):
        code = handler("btnSaveKey")
        self.assertIn("state.carried", code)
        self.assertIn("save_key_archive", code)

    def test_the_key_on_screen_includes_them_too(self):
        self.assertIn("state.carried", body("renderKey"))

    def test_the_key_button_wakes_up_for_carried_replacements(self):
        # All replacements may have gone into proofreading; the button must stay enabled.
        code = body("renderKey")
        self.assertIn('$("btnSaveKey").disabled = !done.length', code)
        self.assertIn("const done = state.carried.map(", code)


class SpeakerNameTests(unittest.TestCase):
    """Speaker labels for coding programs.

    MAXQDA and ATLAS.ti, when importing a transcript, create a code for each
    speaker — by the name before the colon. «Спикер 1» ("Speaker 1") in twenty
    interviews means twenty different people."""

    def test_the_respondent_gets_the_pseudonym_from_the_file_name(self):
        code = body("nameSpeakers")
        self.assertIn("labelSpeaker(respondentNumber, state.pseudonym)", code)
        self.assertIn('"Интервьюер"', code)

    def test_a_hand_written_label_is_never_overwritten(self):
        code = body("nameSpeakers")
        self.assertIn('if ((state.speakerRoles.get(String(number)) || "").trim()) return', code)

    def test_a_single_voice_is_the_respondent(self):
        self.assertIn("if (state.speakers.length === 1)", body("nameSpeakers"))

    def test_the_program_tells_the_screen_who_the_respondent_is(self):
        app_source = (PACKAGE_DIR / "server.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"respondent": respondent_speaker(text)', app_source)
        self.assertIn("nameSpeakers(data.respondent)", SCREEN)


class SingleSpeakerTests(unittest.TestCase):
    def test_one_voice_can_be_chosen(self):
        self.assertIn(
            '<option value="1" data-i18n="speakers.people" data-i18n-n="1">', SCREEN
        )
        self.assertEqual(catalog("ru")["speakers.people.one"], "{n} человек")

    def test_two_stays_the_default(self):
        """The selected attribute in the markup is not enough.

        The browser remembers the previous choice by the row number in the
        list. When «1 человек» ("1 person") appeared at the top, the old number
        came to mean a different count, and the program started with one
        voice without asking anyone."""

        self.assertIn('$("speakerCount").value = "2";', SCREEN)

    def test_the_label_fits_the_box(self):
        # The field is 180 pixels wide: a long label gets cut off in the middle.
        counts = re.findall(r'data-i18n="speakers.people" data-i18n-n="(\d+)"', SCREEN)
        self.assertEqual(counts, [str(n) for n in range(1, 11)])
        for language in languages():
            texts = catalog(language)
            labels = [
                texts[f"speakers.people.{plural_form(int(n), language)}"
                      if f"speakers.people.{plural_form(int(n), language)}" in texts
                      else "speakers.people.many"].format(n=n)
                for n in counts
            ]
            longest = max(labels, key=len)
            self.assertLessEqual(len(longest), len("10 человек"), longest)

    def test_diarization_is_skipped_for_one_voice(self):
        worker_source = (PACKAGE_DIR / "transcription" / "worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("if speaker_count == 1:", worker_source)
        # Voice fingerprints are the slowest part of the work; with one voice
        # they aren't needed at all.
        start = worker_source.find("if speaker_count == 1:")
        end = worker_source.find("extractor = _speaker_embedding_extractor", start)
        self.assertGreater(end, start)
        self.assertIn("return [DiarizedSpan(", worker_source[start:end])


if __name__ == "__main__":
    unittest.main()
