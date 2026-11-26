"""Dictionary forms, grammar and inflection (Natasha morphology + pymorphy3)."""

import re
import threading
from functools import lru_cache


@lru_cache(maxsize=100_000)
def lemma_of(word: str) -> str:
    """The dictionary form of a word. An unknown word stays as it is."""

    lowered = str(word or "").lower().replace("ё", "е")
    try:
        _, analyzer = morph_tools()
    except Exception:
        return lowered
    if analyzer is None:
        return lowered
    try:
        parsed = analyzer.parse(lowered)
    except Exception:
        return lowered
    if parsed and parsed[0].is_known:
        return parsed[0].normal_form.replace("ё", "е")
    return lowered


# Separate locks: a long load of one model must not hold up the work of
# the other one or of the whole program.
_morph = None


_pymorphy = None


_morph_lock = threading.Lock()


UD_CASE = {"Nom": "nomn", "Gen": "gent", "Dat": "datv",
           "Acc": "accs", "Ins": "ablt", "Loc": "loct"}


def morph_tools():
    """Sentence parsing (Natasha) and word inflection (pymorphy3).

    Natasha resolves ambiguity from context: «позвонила Марии» ("called
    Maria") is dative, while «позвонила Мария» ("Maria called") is
    nominative, even though the word on its own can't tell them apart.
    pymorphy3 then puts the substitute into the same case."""
    global _morph, _pymorphy
    with _morph_lock:
        if _morph is None:
            from natasha import Segmenter, NewsEmbedding, NewsMorphTagger
            import pymorphy3
            _morph = (Segmenter(), NewsMorphTagger(NewsEmbedding()))
            _pymorphy = pymorphy3.MorphAnalyzer()
    return _morph, _pymorphy


def grammar_of(text, spans):
    """For each finding, determine its case, number and gender in context."""
    from natasha import Doc

    (segmenter, morph), _ = morph_tools()
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_morph(morph)

    tokens = [t for t in doc.tokens if t.feats]
    for span in spans:
        # Take the last content word of the finding: in «Мария Петровна»
        # ("Maria Petrovna") the whole group carries the case, but the tail
        # sets the form.
        inside = [t for t in tokens if t.start >= span["start"] and t.stop <= span["stop"]]
        chosen = None
        for token in inside:
            if token.pos in ("PROPN", "NOUN", "ADJ"):
                chosen = token
        if chosen is None and inside:
            chosen = inside[-1]
        if chosen is not None:
            span["gram"] = {
                "case": UD_CASE.get(chosen.feats.get("Case"), "nomn"),
                "number": "plur" if chosen.feats.get("Number") == "Plur" else "sing",
                "gender": (chosen.feats.get("Gender") or "").lower(),
            }
    return spans


def inflect_word(phrase, gram):
    """Put the substitute into the required form. The number in brackets is left alone."""
    _, pymorphy = morph_tools()
    tail = re.search(r"\s+(\d+)$", phrase)
    stem = phrase[:tail.start()] if tail else phrase
    words = stem.split()
    if not words:
        return phrase

    target = {gram.get("case", "nomn")}
    if gram.get("number") == "plur":
        target.add("plur")

    out = []
    for word in words:
        parsed = pymorphy.parse(word)
        form = parsed[0].inflect(target) if parsed else None
        result = form.word if form else word
        if word[:1].isupper():
            result = result[:1].upper() + result[1:]
        out.append(result)
    return " ".join(out) + (tail.group(0) if tail else "")
