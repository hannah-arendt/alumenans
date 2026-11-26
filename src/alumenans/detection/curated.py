"""The curated list of public names: found, but not masked by default."""

from functools import lru_cache

from alumenans.detection.morphology import lemma_of
from alumenans.detection.patterns import WORD_RE, caseless_spans

# There are no names in the dictionary longer than three words, and
# trying every combination gets more expensive with each extra word.
CURATED_MAX_WORDS = 3


CURATED_KINDS = {"person": "person", "organization": "org"}


# Generic words that are put in front of a name and that are not a name in
# their own right.
GENERIC_WORDS = {
    "фонд", "организация", "проект", "издание", "центр", "движение",
    "инициатива", "ассоциация", "объединение", "группа", "сообщество",
    "телеканал", "канал", "газета", "журнал", "медиа", "нко",
}


@lru_cache(maxsize=1)
def curated_index():
    """The dictionary of names and organisations, reduced to dictionary forms.

    A Russian name in a text is almost never in the nominative case:
    «поддерживаю Земфиру» ("I support Zemfira", accusative), «подписан на
    Марем» ("subscribed to Marem"). The dictionary, however, is written in
    the nominative, and a literal comparison misses such places —
    «Земфира» was in the dictionary from the very start, yet in the
    transcript it went unnoticed. So both the dictionary and the text are
    reduced to the dictionary form, and it is those that are compared."""

    from alumenans.proofreading import curated_entries

    index = {}
    for entry in curated_entries():
        kind = CURATED_KINDS.get(str(entry.get("kind") or ""))
        if kind is None:
            continue
        for form in (entry.get("canonical"), *(entry.get("aliases") or [])):
            words = WORD_RE.findall(str(form or ""))
            if not words or len(words) > CURATED_MAX_WORDS:
                continue
            key = tuple(lemma_of(word) for word in words)
            index.setdefault(key, kind)
    return index


def is_public_name(fragment):
    """A publicly known name or title — from the study dictionary.

    It is checked separately from the search: the finding may have come from
    Natasha, and then it carries no dictionary mark. But what matters for the
    decision is not who found it but what exactly was found: Greenpeace stays
    Greenpeace, no matter who spotted it."""

    words = WORD_RE.findall(str(fragment or ""))
    if not words:
        return False
    index = curated_index()
    # The generic word in front of a name is written in different ways:
    # «фонд „Нужна помощь“» ("the 'Нужна помощь' foundation"), «издание
    # „Дождь“» ("the outlet 'Дождь'"), just «Нужна помощь». For the decision
    # it is all the same, so we try both with it and without it.
    variants = [words]
    if len(words) > 1 and lemma_of(words[0]) in GENERIC_WORDS:
        variants.append(words[1:])
    if len(words) > 1 and lemma_of(words[-1]) in GENERIC_WORDS:
        variants.append(words[:-1])
    return any(
        len(variant) <= CURATED_MAX_WORDS
        and tuple(lemma_of(word) for word in variant) in index
        for variant in variants
    )


def curated_spans(text):
    """Finds publicly known names and titles in the text.

    The "names-and-organizations" dictionary was not compiled in order to
    anonymise these, but exactly the opposite. Greenpeace, «Нужна помощь»,
    ОВД-Инфо, Земфира — public organisations and well-known people whom
    the respondent talks about as the subject of conversation. There is no
    point replacing them with pseudonyms: they give nobody away, and the
    transcript becomes unreadable as a result.

    Checked on two anonymisations: of the seven replacements made by a
    person, not one is in the dictionary, while of the fifty or so
    superfluous suggestions the dictionary covers half. So a finding from
    the dictionary is marked as public — it is visible, but by default it
    stays as it is."""

    index = curated_index()
    if not index:
        return []
    positions = [(m.group(0), m.start(), m.end()) for m in WORD_RE.finditer(text)]
    lemmas = [lemma_of(word) for word, _, _ in positions]

    found = []
    taken = set()
    for length in range(CURATED_MAX_WORDS, 0, -1):
        for start in range(0, len(positions) - length + 1):
            if any(word_index in taken for word_index in range(start, start + length)):
                continue
            kind = index.get(tuple(lemmas[start : start + length]))
            if kind is None:
                continue
            # A name in lower case is most likely not a name: «нужна помощь»
            # ("help needed") and «тебе поверят» ("you will be believed") are
            # real foundations, but the same words very often mean exactly
            # what they say. A capital letter or quotation marks tell one
            # from the other.
            word = positions[start][0]
            before_text = text[max(0, positions[start][1] - 1) : positions[start][1]]
            if not word[:1].isupper() and before_text not in "«\"„":
                continue
            found.append(
                {
                    "start": positions[start][1],
                    "stop": positions[start + length - 1][2],
                    "cat": kind,
                    "conf": "strong",
                    "by": "словарь",
                    "public": True,
                }
            )
            taken.update(range(start, start + length))
    return found


def dict_spans(text):
    """Findings that are more reliably found with a dictionary than with a general NER model."""
    return caseless_spans(text) + curated_spans(text)
