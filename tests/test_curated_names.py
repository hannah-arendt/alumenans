#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The dictionary of names and organizations in the personal-data search.

The dictionary is written in the nominative case, but in speech a name is
almost never in it: «поддерживаю Земфиру» ("I support Zemfira"),
«подписан на Марем» ("subscribed to Marem"). While the comparison was
literal, «Земфира» was in the dictionary from the very start and still
stayed un-anonymized in the transcript.
"""

import unittest
from pathlib import Path


from alumenans.detection import curated, models, morphology
from alumenans import proofreading as P


class DictionaryFileTests(unittest.TestCase):
    """A person edits the dictionary file, and its headers live a life of their own.

    The parser relied on two lines written letter for letter. Shortening
    «=== ОРГАНИЗАЦИИ, МЕДИА, ИНИЦИАТИВЫ ===» ("ORGANIZATIONS, MEDIA,
    INITIATIVES") to «=== ОРГАНИЗАЦИИ ===» was enough for the whole section
    to stop being read. Silently: the dictionary just got shorter, and
    nobody would have noticed.
    """

    def read(self, text):
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", encoding="utf-8", delete=False
        ) as file:
            file.write(text)
            path = Path(file.name)
        previous = P.CURATED_LEXICON_PATH
        try:
            P.CURATED_LEXICON_PATH = path
            P._load_lexicon.cache_clear()
            return {
                entry["canonical"]: entry["kind"] for entry in P.curated_entries()
            }
        finally:
            P.CURATED_LEXICON_PATH = previous
            P._load_lexicon.cache_clear()
            path.unlink()

    def test_a_shortened_heading_is_still_understood(self):
        read_result = self.read(
            "=== ПЕРСОНЫ ===\nЗемфира\n\n=== ОРГАНИЗАЦИИ ===\nGreenpeace\n"
        )
        self.assertEqual(read_result, {"Земфира": "person", "Greenpeace": "organization"})

    def test_a_missing_first_heading_does_not_swallow_the_list(self):
        # Without a header, lines count as persons rather than being lost.
        read_result = self.read("Земфира\n\n=== ОРГАНИЗАЦИИ ===\nGreenpeace\n")
        self.assertEqual(read_result.get("Земфира"), "person")
        self.assertEqual(read_result.get("Greenpeace"), "organization")

    def test_section_comments_are_skipped(self):
        read_result = self.read(
            "=== ПЕРСОНЫ ===\n--- Музыканты ---\nЗемфира, Рамазанова\n"
        )
        self.assertEqual(list(read_result), ["Земфира"])

    def test_the_shipped_dictionary_has_both_kinds(self):
        # If parsing breaks again, the count for one of the kinds drops to zero.
        kinds = {entry["kind"] for entry in P.curated_entries()}
        self.assertEqual(kinds, {"person", "organization"})


class CuratedIndexTests(unittest.TestCase):
    def setUp(self):
        models.MODEL_STATE["gliner"] = "off"

    def find(self, text):
        return [
            (text[span["start"] : span["stop"]], span["cat"])
            for span in curated.curated_spans(text)
        ]

    def test_a_name_is_found_in_any_case_form(self):
        # This is what it was all for.
        for phrase in (
            "Я слушаю Земфиру каждый день.",
            "Мы говорили о Земфире.",
            "Земфира выступала тогда.",
        ):
            with self.subTest(phrase=phrase):
                found = [text for text, _ in self.find(phrase)]
                self.assertTrue(
                    any("емфир" in word.lower() for word in found),
                    f"not found in «{phrase}»: {found}",
                )

    def test_a_two_word_name_is_found_whole(self):
        # The name comes from the dictionary, and a person edits it: we need
        # someone here who won't be removed from it in the course of a study.
        found = self.find("Читаю Екатерину Шульман.")
        self.assertTrue(any(len(text.split()) == 2 for text, _ in found))

    def test_an_organisation_in_lower_case_is_not_a_name(self):
        # «Нужна помощь» ("Help Needed") is a real foundation, but these same
        # words all the time mean exactly what they say.
        self.assertEqual(self.find("Здесь нужна помощь, срочно."), [])

    def test_the_same_organisation_with_a_capital_is_a_name(self):
        found = [text for text, _ in self.find("Перевела в Нужна помощь.")]
        self.assertTrue(found, "a capitalised name must be found")

    def test_quotes_replace_the_capital_letter(self):
        found = [text for text, _ in self.find('Фонд «нужна помощь» собрал.')]
        self.assertTrue(found)

    def test_a_common_word_that_is_also_a_name_needs_a_capital(self):
        self.assertEqual(self.find("мне нужна вера в людей"), [])

    def test_the_longest_name_wins(self):
        # «Шульман» and «Екатерина Шульман» are both in the dictionary; the
        # longer one wins.
        found = self.find("Пишет Екатерина Шульман.")
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0][0].split()), 2)


class LemmaTests(unittest.TestCase):
    def test_a_known_word_is_reduced_to_its_first_form(self):
        self.assertEqual(morphology.lemma_of("Земфиру"), morphology.lemma_of("Земфира"))
        self.assertEqual(morphology.lemma_of("городе"), "город")

    def test_an_unknown_word_stays_itself(self):
        # A recognition mishearing isn't in the language dictionary, and there's
        # no need to invent a base form for it.
        self.assertEqual(morphology.lemma_of("Морэм"), "морэм")

    def test_case_and_yo_do_not_matter(self):
        self.assertEqual(morphology.lemma_of("КЁЛЬН"), morphology.lemma_of("кельн"))


class DictionaryReachesAnonymiserTests(unittest.TestCase):
    """Proofreading and the anonymizer look at one and the same dictionary."""

    def test_both_layers_share_the_curated_list(self):
        from alumenans.proofreading import curated_entries

        entries = curated_entries()
        self.assertTrue(entries)
        kinds = {str(item.get("kind")) for item in entries}
        self.assertTrue({"person", "organization"} & kinds)
        self.assertTrue(curated.curated_index())


if __name__ == "__main__":
    unittest.main()
