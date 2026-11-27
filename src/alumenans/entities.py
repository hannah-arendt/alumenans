#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One person, one pseudonym.

In a transcript the same person is called in different ways.
While each finding got a pseudonym on its
own, one person turned into five «Персона» ("Person") entries, and the
transcript became unreadable after anonymisation: it was no longer
possible to tell that Person 2 and Person 7 are the same individual.

Here mentions are gathered into entities by three signs:

    forms of one word    «Земфире» and «Земфиру» are one Zemfira;
    sound                A mishearing, not two people;
    containment          Parts of one name.

The last sign is the most useful and the most dangerous. If there are
two versions of Natalya in a conversation, then a lone «Наталья» could
be either of them, and then they must not be merged; it has to be left
to a human.

An entity is a suggestion, not a decision. A human confirms the
pseudonym anyway, and that same human sees all the forms at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Sequence

from alumenans.phonetics import phonetic_key, phonetic_similarity

WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]*")

# Sound is applied more strictly here than in proofreading. There, a
# similar word is a hint to a human, and a mistake costs one extra remark.
# Here a merge means a shared pseudonym: get it wrong and two different
# people become one, which is no longer anonymisation but substitution.

# Kinds of findings where a name can be given in part. Ages and dates
# don't come in parts.
COMPOUND_KINDS = {"person", "org", "place"}


@dataclass
class Entity:
    """One person, one organisation or one place."""

    key: str
    category: str
    mentions: list[int] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)
    canonical: str = ""
    merged_from: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "cat": self.category,
            "mentions": list(self.mentions),
            "forms": list(self.forms),
            "canonical": self.canonical,
            "merged_from": list(self.merged_from),
        }


def _words(text: str) -> list[str]:
    return WORD.findall(str(text or ""))


def group_mentions(
    mentions: Sequence[dict],
    *,
    lemma: Callable[[str], str] | None = None,
) -> list[Entity]:
    """Gathers mentions into entities.

    Input: findings, ``{"id", "cat", "text"}``. Output: entities, in order
    of first appearance in the text."""

    normalise = lemma or (lambda word: word.lower().replace("ё", "е"))

    entities: list[Entity] = []
    by_key: dict[str, Entity] = {}
    for finding in mentions:
        kind = str(finding.get("cat") or "")
        text = str(finding.get("text") or "").strip()
        if not text:
            continue
        key = kind + "|" + " ".join(normalise(w) for w in _words(text))
        entity = by_key.get(key)
        if entity is None:
            entity = Entity(key=key, category=kind, canonical=text)
            by_key[key] = entity
            entities.append(entity)
        entity.mentions.append(finding.get("id"))
        if text not in entity.forms:
            entity.forms.append(text)

    entities = _merge_by_sound(entities)
    entities = _merge_by_containment(entities, normalise)
    for entity in entities:
        entity.canonical = _pick_canonical(entity.forms, normalise)
    return entities


def _pick_canonical(forms: Sequence[str], normalise) -> str:
    """What to call the entity when showing it to a human.

    A full name is clearer than a part, and the nominative clearer than an
    oblique case."""

    def weight(form: str):
        words = _words(form)
        is_base_form = all(normalise(w) == w.lower().replace("ё", "е") for w in words)
        return (len(words), is_base_form, len(form))

    return max(forms, key=weight)


def _merge_into(target: Entity, source_entity: Entity) -> None:
    target.mentions.extend(source_entity.mentions)
    for form in source_entity.forms:
        if form not in target.forms:
            target.forms.append(form)
    target.merged_from.append(source_entity.key)
    target.merged_from.extend(source_entity.merged_from)


def _sounding(text: str) -> tuple[str, ...]:
    return tuple(phonetic_key(word) for word in _words(text))


def _same_name(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Whether two names sound like one and the same."""

    if left == right:
        return True
    if len(left) != len(right) or len(left) < 2:
        return False
    differing = [
        (left_part, right_part) for left_part, right_part in zip(left, right) if left_part != right_part
    ]
    if len(differing) != 1:
        return False
    left_part, right_part = differing[0]
    return phonetic_similarity(left_part, right_part) >= 0.78


def _merge_by_sound(entities: list[Entity]) -> list[Entity]:
    """A recognition mishearing, not two people."""

    result: list[Entity] = []
    for entity in entities:
        target = None
        sounds_like = _sounding(entity.canonical)
        for ready_entity in result:
            if ready_entity.category != entity.category:
                continue
            if sounds_like and _same_name(_sounding(ready_entity.canonical), sounds_like):
                target = ready_entity
                break
        if target is None:
            result.append(entity)
        else:
            _merge_into(target, entity)
    return result


def _merge_by_containment(entities: list[Entity], normalise) -> list[Entity]:
    """Parts of one name.

    A part is joined to the whole only if there is exactly one whole. Two
    «Натальи» (Natalyas) in one conversation are a reason to ask a human,
    not to guess."""

    full = [w for w in entities if len(_words(w.canonical)) > 1]
    if not full:
        return entities

    def word_set(entity: Entity) -> set[str]:
        # Compare by base forms: «Наталью» (accusative) is «Наталья».
        return {normalise(word) for form in entity.forms for word in _words(form)}

    absorbed: set[str] = set()
    for relative in entities:
        if len(_words(relative.canonical)) != 1 or relative.category not in COMPOUND_KINDS:
            continue
        word = word_set(relative)
        # It is enough for at least one form of the part to be found in the
        # full name
        owners = [
            whole
            for whole in full
            if whole.category == relative.category
            and word & word_set(whole)
            and not word >= word_set(whole)
        ]
        if len(owners) != 1:
            # None: the part stands on its own. More than one: it is unknown
            # whose it is, and that is for a human to decide, not the program.
            continue
        _merge_into(owners[0], relative)
        absorbed.add(relative.key)

    return [w for w in entities if w.key not in absorbed]

