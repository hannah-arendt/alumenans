#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What in this transcript is the subject of the study, not personal data.

The anonymiser doesn't know what the study is about, so it suggests
replacing everything indiscriminately.

The guide and the study programme know the answer: they name the subject
in plain words. Candidates are extracted from them here.

What this module does not do is important. It decides nothing. It returns
suggestions, each with a reference to where it came from; they are
enabled only after a human confirms them. The decision "this data need
not be hidden" is the only one in the program where a mistake is
discovered only after the transcript has been handed to someone.
"""

from __future__ import annotations

import re
from collections import Counter

from alumenans.guide.parse import Guide


CAPITALIZED = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё_])"
    # Only spaces are allowed inside a name: a line break means that one
    # sentence has ended and the next one has begun.
    r"([А-ЯЁA-Z][а-яёa-z-]{2,}(?:[ \t]+[А-ЯЁA-Z][а-яёa-z-]{2,}){0,2})"
)
QUOTED = re.compile(r"[«\"]([^»\"\n]{2,60})[»\"]")

# Words that start a sentence and therefore look like proper names.
SENTENCE_OPENERS = {
    "если", "интервью", "как", "многие", "мне", "представляемся", "расскажите",
    "сразу", "что", "далее", "лучше", "например", "вот", "это", "есть", "было",
    "зацепиться", "важен", "может", "почему", "медиадиета", "завершение",
    "развилка", "вступление", "переход", "нынешний", "бывший", "думает",
}

# Words that appear in the guide as subject-area terms but are never
# personal data.
DOMAIN_HINTS = {
    "нко", "фонд", "фонды", "донор", "доноры", "пожертвование", "пожертвования",
    "благотворительность", "фандрайзинг", "подписка", "донат", "донаты",
}


def _known_word(word: str) -> bool:
    """Whether the word is in the Russian dictionary. Without one, keep it."""

    try:
        from alumenans.proofreading import is_known_russian_word

        return is_known_russian_word(word)
    except Exception:
        return False


def _starts_sentence(text: str, start: int) -> bool:
    before = text[:start]
    return not before.strip() or bool(re.search(r"(?:^|[.!?…:;\n])\s*$", before))


def _candidates_from(text: str, source: str) -> list[dict]:
    text = str(text or "")
    found: list[dict] = []

    for match in QUOTED.finditer(text):
        value = match.group(1).strip()
        # In the guide, quotes often frame a sample wording of a question
        # rather than a name. A name is short and doesn't end with a question
        # mark.
        if len(value) < 2 or "?" in value or len(value.split()) > 4:
            continue
        found.append({"term": value, "kind": "org", "source": source})

    for match in CAPITALIZED.finditer(text):
        value = match.group(1).strip()
        words = value.split()
        first = words[0].lower().replace("ё", "е")
        if first in SENTENCE_OPENERS:
            continue
        if _starts_sentence(text, match.start(1)) and len(words) == 1:
            continue
        # A single word that is in the Russian dictionary is almost always
        # an ordinary word at the start of a phrase, not a name.
        if len(words) == 1 and _known_word(words[0]):
            continue
        found.append({"term": value, "kind": "", "source": source})

    lowered = text.lower().replace("ё", "е")
    for hint in DOMAIN_HINTS:
        if re.search(rf"(?<![а-яa-z]){re.escape(hint)}(?![а-яa-z])", lowered):
            found.append({"term": hint, "kind": "domain", "source": source})
    return found


def suggest_from_documents(
    guide: Guide | str = "",
    programme: str = "",
    *,
    minimum_mentions: int = 1,
) -> list[dict]:
    """Candidates for the study subject, taken from the guide and programme.

    The study programme weighs more than the guide: it says directly what
    is being studied, whereas in the guide the subject's name may flash by
    only once."""

    if isinstance(guide, Guide):
        guide_text = "\n".join(
            [item.text for item in guide.everything]
            + [prompt for item in guide.everything for prompt in item.prompts]
            + [section.title for section in guide.sections]
        )
    else:
        guide_text = str(guide or "")

    candidates = _candidates_from(guide_text, "гайд")
    candidates += _candidates_from(str(programme or ""), "программа")

    grouped: dict[str, dict] = {}
    counts: Counter = Counter()
    for item in candidates:
        key = re.sub(r"\s+", " ", item["term"]).strip().lower().replace("ё", "е")
        counts[key] += 1
        current = grouped.get(key)
        if current is None:
            grouped[key] = {**item, "mentions": 1}
            continue
        current["mentions"] += 1
        # The study programme is a weightier source than the guide.
        if item["source"] == "программа" and current["source"] != "программа":
            current["source"] = "программа"
        if item["kind"] and not current["kind"]:
            current["kind"] = item["kind"]

    # One candidate may be entirely contained in another.
    # Frequency decides, not length:
    # whatever is mentioned more often is the study subject. On a tie we
    # take the longer one, since the name is replaced as a whole.
    keys = list(grouped)
    swallowed: set[str] = set()
    for short in keys:
        for long in keys:
            if short == long or len(short) >= len(long):
                continue
            if not re.search(rf"(?<![а-яa-z]){re.escape(short)}\b", long):
                continue
            if counts[short] > counts[long]:
                swallowed.add(long)
            else:
                swallowed.add(short)

    result = []
    for key, item in grouped.items():
        if key in swallowed:
            continue
        item["mentions"] = counts[key]
        if item["kind"] == "domain" or item["mentions"] >= minimum_mentions:
            result.append(item)

    # What is mentioned more often is more likely the study subject and not
    # a coincidence.
    result.sort(
        key=lambda item: (
            item["source"] != "программа",
            -item["mentions"],
            item["term"].lower(),
        )
    )
    return result

