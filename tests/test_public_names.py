#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The names dictionary is a protective list, not a list for replacement.

Checked against two real anonymizations. The person replaced seven
things: their own name, their own city and four organizations they
themselves take part in. Everything else — Greenpeace, «Нужна помощь»
("Help Needed"), ОВД-Инфо (OVD-Info), Земфира (Zemfira), Казань (Kazan) —
they left as is: it's the subject of the conversation, not something that
gives anyone away.

None of the seven replacements appears in the dictionary, while of the
fifty or so unnecessary suggestions the dictionary covers half. So a
match from the dictionary means "keep", not "replace".
"""

import unittest


from alumenans import app as A


class PublicNameTests(unittest.TestCase):
    def setUp(self):
        A.MODEL_STATE["gliner"] = "off"

    def test_a_well_known_organisation_is_public(self):
        for title in ("Greenpeace", "Нужна помощь", "ОВД-Инфо"):
            with self.subTest(title=title):
                self.assertTrue(A.is_public_name(title))

    def test_a_well_known_person_is_public(self):
        self.assertTrue(A.is_public_name("Земфира"))

    def test_case_forms_count_too(self):
        # «Анохину» and «Анохина» are one and the same public person.
        self.assertEqual(A.is_public_name("Анохину"), A.is_public_name("Анохина"))

    def test_a_long_phrase_is_not_looked_up(self):
        self.assertFalse(A.is_public_name("я перевела туда деньги в прошлом году"))

    def test_nothing_is_not_public(self):
        self.assertFalse(A.is_public_name(""))
        self.assertFalse(A.is_public_name("2024"))


class MarkingTests(unittest.TestCase):
    """The mark goes on the match, whoever brought it in."""

    def setUp(self):
        A.MODEL_STATE["gliner"] = "off"

    def find(self, text):
        return {
            text[span["start"] : span["stop"]]: bool(span.get("public"))
            for span in A.find_spans(text)
        }

    def test_a_dictionary_name_found_by_the_model_is_still_public(self):
        # Natasha finds «Земфиру» before the dictionary and displaces its match.
        # What matters for the decision is not who found it, but what exactly.
        found = self.find("Я слушаю Земфиру каждый день.")
        public = [piece for piece, is_public in found.items() if is_public]
        self.assertTrue(any("емфир" in piece for piece in public))

    def test_an_unknown_name_is_not_public(self):
        found = self.find("Меня зовут Екатерина Волкова.")
        self.assertTrue(found)
        self.assertFalse(any(found.values()))

    def test_the_whole_entity_is_public_if_one_mention_is(self):
        text = "Фонд «Нужна помощь» помогает. В Нужна помощь работают волонтёры."
        entities = A.entity_groups(text, A.find_spans(text))
        help_entities = [w for w in entities if "омощь" in w["canonical"]]
        self.assertTrue(help_entities)
        self.assertTrue(help_entities[0]["public"])


if __name__ == "__main__":
    unittest.main()
