#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local, conservative proofreading of an interview transcript."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

from alumenans.paths import RESOURCES
from alumenans.phonetics import phonetic_similarity


LEXICON_PATH = RESOURCES / "names-and-organizations.txt"
CURATED_LEXICON_PATH = LEXICON_PATH  # former name, kept so existing callers don't break

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]*")
CYRILLIC_WORD_RE = re.compile(r"^[А-Яа-яЁё][А-Яа-яЁё'-]*$")
CAPITALIZED_RE = re.compile(r"^[А-ЯЁ][а-яё-]{2,}$")
FILLER_RE = re.compile(
    r"^(?:[аэоумх]-?){1,6}$|^(?:угу|ага|мг+|хм+|эм+|ээ+|мм+)$",
    re.IGNORECASE,
)
REPEAT_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё_-])"
    r"([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]{1,})"
    r"([ \t]+)\1"
    r"(?![0-9A-Za-zА-Яа-яЁё_-])",
    re.IGNORECASE,
)
INTERNAL_DIALOGUE_DASH_RE = re.compile(
    r"(?<=[.!?…])\s+[—–]\s+(?=[А-ЯЁA-Z])"
)

EXPLICIT_TOPIC_RE = re.compile(
    r"(?:"
    r"\b(?:а\s+)?(?:теперь|сейчас)\b[^.!?]{0,80}"
    r"(?:поговорим|обсудим|перейд[её]м)|"
    r"\b(?:давай|давайте)\b[^.!?]{0,60}"
    r"(?:поговорим|обсудим|перейд[её]м)|"
    r"\bперейд[её]м\b[^.!?]{0,40}\bк\b|"
    r"\bследующ(?:ая|ий|ее|ую)\s+(?:тема|вопрос|блок|часть)\b|"
    r"\b(?:ещ[её]\s+один|другой)\s+вопрос\b|"
    r"\bхочу\s+(?:теперь\s+)?(?:спросить|поговорить)\s+(?:о|об|про)\b|"
    r"\bчто\s+касается\b"
    r")",
    re.IGNORECASE,
)
QUESTION_START_RE = re.compile(
    r"^\s*(?:[—–-]\s*)?(?:"
    r"а\s+|и\s+)?(?:"
    r"как|что|где|когда|почему|зачем|кто|какой|какая|какие|"
    r"можешь|можете|расскажи|расскажите|бывало|есть\s+ли|"
    r"правильно\s+ли|верно\s+ли"
    r")\b",
    re.IGNORECASE,
)

NOTE_STOPWORDS = {
    "интервью",
    "заметка",
    "заметки",
    "респондент",
    "респондентка",
    "интервьюер",
    "тема",
    "вопрос",
    "ответ",
    "важно",
    "правильно",
    "название",
    "организация",
    "организации",
    "это",
    "для",
}

NAME_STOPWORDS = NOTE_STOPWORDS | {
    "давайте",
    "расскажите",
    "почему",
    "когда",
    "сейчас",
    "потом",
    "далее",
    "например",
    "вообще",
    "возможно",
    "конечно",
    "спикер",
    "через",
}

KNOWN_COLLOQUIAL = {
    "донат",
    "доната",
    "донате",
    "донатом",
    "донаты",
    "донатов",
    "донатить",
    "донатил",
    "донатила",
    "донатили",
    "задонатить",
}

COMMON_CORRECTION_WORDS = {
    "будет",
    "бывает",
    "важно",
    "вместе",
    "возможно",
    "вопрос",
    "время",
    "говорить",
    "дальше",
    "делать",
    "должен",
    "другая",
    "другой",
    "знаю",
    "именно",
    "интересно",
    "кажется",
    "когда",
    "который",
    "можно",
    "наверное",
    "например",
    "нужно",
    "обычно",
    "очень",
    "первый",
    "почему",
    "помню",
    "потому",
    "правильно",
    "просто",
    "работа",
    "рассказать",
    "сейчас",
    "сказать",
    "сначала",
    "такой",
    "тогда",
    "хорошо",
    "человек",
}
RUSSIAN_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"

from alumenans.transcription.core import UNFINISHED_TAIL  # noqa: E402  (one list for the whole program)
from alumenans.i18n import t

CONTENT_STOPWORDS = {
    "а",
    "без",
    "бы",
    "был",
    "была",
    "были",
    "было",
    "быть",
    "в",
    "вам",
    "вас",
    "весь",
    "вот",
    "вы",
    "где",
    "да",
    "даже",
    "для",
    "до",
    "его",
    "ее",
    "её",
    "если",
    "есть",
    "ещё",
    "же",
    "за",
    "здесь",
    "и",
    "из",
    "или",
    "им",
    "их",
    "как",
    "когда",
    "который",
    "кто",
    "ли",
    "мне",
    "может",
    "мы",
    "на",
    "над",
    "не",
    "нет",
    "но",
    "ну",
    "о",
    "об",
    "один",
    "она",
    "они",
    "оно",
    "от",
    "по",
    "под",
    "потом",
    "потому",
    "при",
    "про",
    "просто",
    "с",
    "себя",
    "сейчас",
    "со",
    "так",
    "такой",
    "там",
    "то",
    "тоже",
    "только",
    "у",
    "уже",
    "хорошо",
    "что",
    "чтобы",
    "это",
    "этот",
    "я",
}

# The study's vocabulary: field words like «задонатить» ("to donate").
# The program fills it in when a study is opened; here it is empty by
# default so that proofreading can be tested without a study open.
STUDY_WORDS: set[str] = set()


def set_study_words(words) -> set[str]:
    """Study words that should no longer be underlined."""

    STUDY_WORDS.clear()
    STUDY_WORDS.update(
        str(word).strip().lower().replace("ё", "е")
        for word in words or ()
        if str(word).strip()
    )
    return STUDY_WORDS


_morph = None
_morph_failed = False


def _subn(pattern: str | re.Pattern, replacement, text: str, flags: int = 0):
    return re.subn(pattern, replacement, text, flags=flags)


def normalize_transcript_text(text: str) -> tuple[str, int]:
    """Fix only mechanical errors, without changing the words or meaning."""

    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    source = source.replace("\u00a0", " ").replace("\u202f", " ")
    source = source.replace("\n", " ")
    fixes = 0

    source, count = _subn(r"[ \t]{2,}", " ", source)
    fixes += count
    source, count = _subn(r"^\s*[—–]\s*", "", source)
    fixes += count
    source, count = _subn(r"(?<=\S)[—–](?=\s)", r" —", source)
    fixes += count
    source, count = _subn(r"(?<=\s)[—–](?=\S)", r"— ", source)
    fixes += count
    source, count = _subn(r"\s+([,.;:!?…])", r"\1", source)
    fixes += count
    source, count = _subn(r",\s*[-—–]\s*,", ",", source)
    fixes += count
    source, count = _subn(r",{2,}", ",", source)
    fixes += count
    source, count = _subn(r";{2,}", ";", source)
    fixes += count
    source, count = _subn(r":{2,}", ":", source)
    fixes += count
    source, count = _subn(r"\.{2,}", "…", source)
    fixes += count
    source, count = _subn(r"!{2,}", "!", source)
    fixes += count
    source, count = _subn(r"\?{2,}", "?", source)
    fixes += count

    def write_filler(match: re.Match) -> str:
        raw = match.group(0)
        lower = raw.lower()
        if lower.startswith("х"):
            result = "х" + "-".join(lower[1:])
        else:
            result = "-".join(lower)
        if raw[:1].isupper():
            result = result[:1].upper() + result[1:]
        return result

    source, count = _subn(r"(?<![А-Яа-яЁё])[Ээ]{2,5}(?![А-Яа-яЁё])", write_filler, source)
    fixes += count
    source, count = _subn(r"(?<![А-Яа-яЁё])[Мм]{2,5}(?![А-Яа-яЁё])", write_filler, source)
    fixes += count
    source, count = _subn(r"(?<![А-Яа-яЁё])[Хх][Мм]{2,5}(?![А-Яа-яЁё])", write_filler, source)
    fixes += count

    def space_after_mark(match: re.Match) -> str:
        mark = match.group(1)
        before = source[match.start() - 1 : match.start()]
        after = source[match.end() : match.end() + 1]
        if mark == "," and before.isdigit() and after.isdigit():
            return mark
        return mark + " "

    source, count = _subn(
        r"([,;:!?…])(?=[0-9A-Za-zА-Яа-яЁё«\"])",
        space_after_mark,
        source,
    )
    fixes += count

    source, count = _subn(
        r"([A-Za-zА-Яа-яЁё]{2,})\.(?=[A-Za-zА-Яа-яЁё«\"])",
        r"\1. ",
        source,
    )
    fixes += count

    def uppercase_after_boundary(match: re.Match) -> str:
        return match.group(1) + match.group(2).upper()

    source, count = _subn(
        r"(^|[.!?…]\s+[«\"(]*)([а-яё])",
        uppercase_after_boundary,
        source,
    )
    fixes += count

    source, count = close_the_sentence(source)
    fixes += count
    return source.strip(), fixes


def close_the_sentence(text: str) -> tuple[str, int]:
    """Put a mark at the end of a turn if speech recognition didn't.

    Every fourth turn arrives without a final punctuation mark, and it
    reads as if cut off mid-word. A full stop goes where the thought is
    finished, an ellipsis where the speech was interrupted: a sentence
    doesn't end on a preposition or a conjunction."""

    source = str(text or "").rstrip()
    if not source or re.search(r"[.!?…»)\]]$", source):
        return source, 0
    words = WORD_RE.findall(source)
    if not words:
        return source, 0
    last_word = words[-1].lower().replace("ё", "е")
    # A dangling comma where the end should be is the same cut-off
    # thought as a preposition: the person didn't finish speaking.
    is_cut_off = last_word in UNFINISHED_TAIL or bool(re.search(r"[,;:—–-]$", source))
    source = re.sub(r"[,;:\s—–-]+$", "", source)
    return source + ("…" if is_cut_off else "."), 1


def _normalized_letters(value: str) -> str:
    return re.sub(r"[^a-zа-яё]", "", value.lower().replace("ё", "е"))


def _similarity(left: str, right: str) -> float:
    left_key = _normalized_letters(left)
    right_key = _normalized_letters(right)
    if not left_key or not right_key:
        return 0.0
    length_ratio = min(len(left_key), len(right_key)) / max(
        len(left_key),
        len(right_key),
    )
    if length_ratio < 0.48:
        return 0.0
    return SequenceMatcher(None, left_key, right_key).ratio()


# Sound similarity runs systematically higher than letter similarity,
# so it gets its own threshold rather than sharing the letters' one.
SOUND_THRESHOLD = 0.78


def _word_similarity(left: str, right: str) -> float:
    """Similarity of two words, with the ear having the final say.

    Recognition makes mistakes by ear: «Морен» instead of «Марем» is a
    single sound substitution, but three letters out of five, and a
    letter-based measure simply doesn't see such a pair. Letters remain
    the fallback: they catch what a phonetic transcription coarsens —
    abbreviations, Latin mixed in with Cyrillic, hyphenated spellings."""

    letters = _similarity(left, right)
    sound = phonetic_similarity(left, right)
    if sound < SOUND_THRESHOLD:
        return letters
    # «Мария» (Maria) sounds almost like «Марем», but it's a name, not a
    # mishearing. A word that is a known name in its own right isn't
    # left to the ear: otherwise the program would offer to replace the
    # respondent with the subject of the study.
    if (_is_known_name(left) or _is_known_name(right)) and not _same_lemma(
        left, right
    ):
        return letters
    return max(sound, letters)


def _starts_sentence(text: str, start: int) -> bool:
    before = str(text or "")[: max(0, start)]
    return not before.strip() or bool(
        re.search(r'(?:^|[.!?…\r\n])\s*[«"(\[]*\s*$', before)
    )


def curated_entries() -> tuple[dict, ...]:
    """The dictionary of names and organizations — the same one proofreading sees.

    It is exposed so that the personal-data search looks through the same
    list. Otherwise proofreading knows about «Земфира» (Zemfira) while the
    anonymizer doesn't, and the real name stays in the transcript."""

    return _load_lexicon()


@lru_cache(maxsize=1)
def _load_lexicon() -> tuple[dict, ...]:
    """One dictionary, one format: a line per person or organization.

    There used to be a json file next to it with some thirty entries, and
    almost all of them repeated lines from this file. Two lists of the same
    thing mean the question "which one do I edit", answered anew every
    time; the entries that weren't in the text file were moved into it."""

    entries = []
    try:
        # A section is recognized by the word's stem, not by the whole line:
        # the file is edited by hand, and the heading sometimes goes missing,
        # sometimes gets a shorter name. Until a heading has appeared, we
        # treat entries as persons — in a list like this, organizations
        # come second.
        section = "person"
        for raw_line in CURATED_LEXICON_PATH.read_text(
            encoding="utf-8"
        ).splitlines():
            line = raw_line.strip()
            if line.startswith("==="):
                inside = line.strip("= ").upper()
                if "ОРГАНИЗАЦ" in inside:
                    section = "organization"
                elif "ПЕРСОН" in inside:
                    section = "person"
                continue
            if not line or line.startswith("---"):
                continue
            variants = [
                re.sub(r"\s+", " ", value).strip()
                for value in line.split(",")
                if value.strip()
            ]
            if not variants:
                continue
            entries.append(
                {
                    "canonical": variants[0],
                    "aliases": variants[1:],
                    "kind": section,
                }
            )
    except OSError:
        pass

    cleaned_by_key = {}
    for entry in entries:
        canonical = str(entry.get("canonical") or "").strip()
        if not canonical:
            continue
        aliases = [
            str(alias).strip()
            for alias in entry.get("aliases") or []
            if str(alias).strip()
        ]
        kind = str(entry.get("kind") or "term")
        key = (_normalized_letters(canonical), kind)
        current = cleaned_by_key.get(key)
        if current is None:
            cleaned_by_key[key] = {
                "canonical": canonical,
                "aliases": list(dict.fromkeys([canonical, *aliases])),
                "kind": kind,
                "source": "reference",
                "threshold": 0.78 if kind == "organization" else 0.72,
            }
            continue
        current["aliases"] = list(
            dict.fromkeys([*current["aliases"], *aliases])
        )

    return tuple(
        {
            **entry,
            "aliases": tuple(entry["aliases"]),
        }
        for entry in cleaned_by_key.values()
    )


def _note_terms(notes: str) -> tuple[dict, ...]:
    notes = str(notes or "").strip()
    if not notes:
        return ()

    candidates: list[tuple[str, str]] = []
    for quoted in re.findall(r"[«\"]([^»\"\n]{2,80})[»\"]", notes):
        candidates.append((quoted.strip(), "quoted"))

    proper_name = re.compile(
        r"(?<![0-9A-Za-zА-Яа-яЁё_])"
        r"([А-ЯЁ][а-яё-]{2,}(?:\s+[А-ЯЁ][а-яё-]{2,}){0,2})"
    )
    for match in proper_name.finditer(notes):
        candidate = match.group(1)
        words = WORD_RE.findall(candidate)
        if len(words) == 1:
            lower = words[0].lower().replace("ё", "е")
            if lower in (NAME_STOPWORDS | CONTENT_STOPWORDS):
                continue
            if _starts_sentence(notes, match.start(1)):
                continue
        candidates.append((candidate, "inline"))

    for source_line in notes.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", source_line).strip()
        words = WORD_RE.findall(line)
        looks_like_term = words and all(
            word[:1].isupper() or word.lower() in {"и", "имени"}
            for word in words
        )
        if 1 <= len(words) <= 4 and len(line) <= 70 and looks_like_term:
            if len(words) == 1:
                lower = words[0].lower().replace("ё", "е")
                if (
                    lower in (NAME_STOPWORDS | CONTENT_STOPWORDS)
                    or _known_russian_word(lower)
                ):
                    continue
            candidates.append((line, "line"))

    result = []
    seen = set()
    for raw, source in candidates:
        canonical = re.sub(r"\s+", " ", raw).strip(" \t.,;:!?—–-")
        words = WORD_RE.findall(canonical)
        if not words:
            continue
        if (
            len(words) == 1
            and source != "quoted"
            and words[0].lower().replace("ё", "е")
            in (NAME_STOPWORDS | CONTENT_STOPWORDS)
        ):
            continue
        if len(words) > 4:
            continue
        key = _normalized_letters(canonical)
        if len(key) < 4 or key in seen:
            continue
        seen.add(key)
        aliases = [canonical]
        if len(words) >= 2:
            aliases.append(words[-1])
        result.append(
            {
                "canonical": canonical,
                "aliases": tuple(dict.fromkeys(aliases)),
                "kind": "note",
                "source": "notes",
                "threshold": 0.58,
            }
        )
    return tuple(result)


def _guide_material(guide: str) -> tuple[tuple[dict, ...], tuple[str, ...]]:
    guide = str(guide or "").strip()
    if not guide:
        return (), ()

    terms = []
    for term in _note_terms(guide):
        terms.append(
            {
                **term,
                "source": "guide",
                "threshold": max(0.64, float(term.get("threshold") or 0.64)),
            }
        )

    questions = []
    seen = set()
    for source_line in guide.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", source_line).strip()
        if not line or len(line) < 12 or len(line) > 260:
            continue
        if "?" not in line and not QUESTION_START_RE.search(line):
            continue
        normalized = _normalized_letters(line)
        if len(normalized) < 10 or normalized in seen:
            continue
        seen.add(normalized)
        questions.append(line)
    return tuple(terms), tuple(questions)


def _turn_id(turn: dict, index: int) -> str:
    return f"{float(turn.get('start') or 0):.3f}:{int(turn.get('speaker') or 1)}:{index}"


def _coerce_turns(turns: Iterable[dict]) -> tuple[list[dict], int]:
    result = []
    total_fixes = 0
    for index, raw in enumerate(turns or []):
        if not isinstance(raw, dict):
            continue
        text, fixes = normalize_transcript_text(raw.get("text") or "")
        if not text:
            continue
        total_fixes += fixes
        start = max(0.0, float(raw.get("start") or 0.0))
        end = max(start, float(raw.get("end") or start))
        try:
            speaker = max(1, int(raw.get("speaker") or 1))
        except (TypeError, ValueError):
            speaker = 1
        words = raw.get("words") if isinstance(raw.get("words"), list) else []
        result.append(
            {
                "id": str(raw.get("id") or _turn_id(raw, index)),
                "start": start,
                "end": end,
                "speaker": speaker,
                "text": text,
                "topic_start": False,
                "topic_reason": None,
                "words": words,
            }
        )
    return result, total_fixes


def _issue_id(kind: str, turn: dict, start: int, text: str) -> str:
    raw = f"{kind}|{turn['id']}|{start}|{text}".encode("utf-8")
    return f"{kind}-{hashlib.sha1(raw).hexdigest()[:12]}"


def _make_issue(
    kind: str,
    turn: dict,
    turn_index: int,
    start: int,
    end: int,
    *,
    message: str,
    suggestion: str | None = None,
    confidence: float | None = None,
) -> dict:
    text = turn["text"][start:end]
    issue = {
        "id": _issue_id(kind, turn, start, text),
        "kind": kind,
        "turn_id": turn["id"],
        "turn_index": turn_index,
        "start": start,
        "end": end,
        "text": text,
        "message": message,
        "suggestion": suggestion,
        "time": turn["start"],
    }
    if confidence is not None and math.isfinite(confidence):
        issue["confidence"] = max(0.0, min(1.0, float(confidence)))
    return issue


def _word_positions(text: str) -> list[re.Match]:
    return list(WORD_RE.finditer(text))


def _evidence_positions(turn: dict) -> list[tuple[dict, re.Match]]:
    matches = _word_positions(turn["text"])
    if not matches:
        return []
    cursor = 0
    result = []
    for raw in turn.get("words") or []:
        if not isinstance(raw, dict):
            continue
        evidence_text = str(raw.get("text") or "")
        evidence_words = WORD_RE.findall(evidence_text)
        if not evidence_words:
            continue
        target = _normalized_letters(evidence_words[0])
        best_index = None
        for index in range(cursor, min(len(matches), cursor + 4)):
            candidate = _normalized_letters(matches[index].group(0))
            if candidate == target or _word_similarity(candidate, target) >= 0.72:
                best_index = index
                break
        if best_index is None:
            if cursor >= len(matches):
                break
            best_index = cursor
        result.append((raw, matches[best_index]))
        cursor = best_index + 1
    return result


def _acoustic_issues(turn: dict, turn_index: int) -> list[dict]:
    issues = []
    for evidence, match in _evidence_positions(turn):
        try:
            confidence = float(evidence.get("confidence"))
        except (TypeError, ValueError):
            confidence = math.nan
        boundary_risk = bool(evidence.get("boundary_risk"))
        word = match.group(0)
        if FILLER_RE.fullmatch(word):
            continue
        normalized = word.lower().replace("ё", "е")
        known = _known_russian_word(normalized)
        suggestion = None if known else _spelling_suggestion(word)
        very_low = math.isfinite(confidence) and confidence < 0.42
        linguistically_suspicious = not known or suggestion is not None
        combined_risk = (
            math.isfinite(confidence)
            and confidence < 0.72
            and linguistically_suspicious
        )
        risky_boundary = (
            boundary_risk
            and math.isfinite(confidence)
            and confidence < 0.62
            and linguistically_suspicious
        )
        if not very_low and not combined_risk and not risky_boundary:
            continue
        if len(_normalized_letters(word)) < 4 and not very_low:
            continue
        message = (
            t("proof.boundary_low_confidence")
            if risky_boundary
            else t("proof.low_confidence")
        )
        issues.append(
            _make_issue(
                "hearing",
                turn,
                turn_index,
                match.start(),
                match.end(),
                message=message,
                suggestion=suggestion,
                confidence=confidence if math.isfinite(confidence) else None,
            )
        )
    return issues


def _morph_analyzer():
    global _morph, _morph_failed
    if _morph is not None or _morph_failed:
        return _morph
    try:
        import pymorphy3

        _morph = pymorphy3.MorphAnalyzer()
    except Exception:
        _morph_failed = True
    return _morph


@lru_cache(maxsize=8192)
def _known_russian_word(word: str) -> bool:
    analyzer = _morph_analyzer()
    if analyzer is None:
        return True
    try:
        return bool(analyzer.word_is_known(word))
    except Exception:
        return True


def is_known_russian_word(word: str) -> bool:
    """Whether such a word exists in the Russian-language dictionary.

    Needed for replacing throughout the text: «Морем» and «морем» ("by the
    sea") are spelled almost the same, but one is a name and the other an
    ordinary word, and that one must not be touched.
    """

    normalized = re.sub(r"[^а-яёa-z-]", "", str(word or "").lower().replace("ё", "е"))
    if len(normalized) < 2:
        return False
    return _known_russian_word(normalized)


def _match_case(candidate: str, example: str) -> str:
    if example.isupper():
        return candidate.upper()
    if example[:1].isupper():
        return candidate[:1].upper() + candidate[1:]
    return candidate


@lru_cache(maxsize=4096)
def _spelling_suggestion(word: str) -> str | None:
    lower = word.lower().replace("ё", "е")
    if (
        len(lower) < 4
        or len(lower) > 18
        or not lower.isalpha()
        or _known_russian_word(lower)
    ):
        return None

    phases: list[set[str]] = []
    phases.append(
        {
            lower[:index] + lower[index + 1 :]
            for index in range(len(lower))
        }
        | {
            lower[:index]
            + lower[index + 1]
            + lower[index]
            + lower[index + 2 :]
            for index in range(len(lower) - 1)
            if lower[index] != lower[index + 1]
        }
    )
    phases.append(
        {
            lower[:index] + letter + lower[index + 1 :]
            for index in range(len(lower))
            for letter in RUSSIAN_ALPHABET
            if letter != lower[index]
        }
        | {
            lower[:index] + letter + lower[index:]
            for index in range(len(lower) + 1)
            for letter in RUSSIAN_ALPHABET
        }
    )

    candidates: set[str] = set()
    for phase in phases:
        candidates = {
            candidate
            for candidate in phase
            if len(candidate) >= 3 and _known_russian_word(candidate)
        }
        if candidates:
            break
    if not candidates:
        return None

    def rank(candidate: str) -> tuple[int, int, int, int]:
        return (
            int(candidate in COMMON_CORRECTION_WORDS),
            int(candidate[:1] == lower[:1]),
            int(candidate[-1:] == lower[-1:]),
            -abs(len(candidate) - len(lower)),
        )

    ordered = sorted(
        candidates,
        key=lambda candidate: (rank(candidate), candidate),
        reverse=True,
    )
    if len(ordered) > 1 and rank(ordered[0]) == rank(ordered[1]):
        return None
    return _match_case(ordered[0], word)


@lru_cache(maxsize=8192)
def _lemma(word: str) -> str:
    lower = word.lower().replace("ё", "е")
    analyzer = _morph_analyzer()
    if analyzer is None:
        return lower
    try:
        parsed = analyzer.parse(lower)
        if parsed and parsed[0].is_known:
            return parsed[0].normal_form.replace("ё", "е")
    except Exception:
        pass
    return lower


NAME_TAGS = {"Name", "Surn", "Patr"}


def _is_known_name(word: str) -> bool:
    """The word on its own is a known personal name."""

    analyzer = _morph_analyzer()
    if analyzer is None:
        return False
    try:
        parsed = analyzer.parse(str(word or "").lower().replace("ё", "е"))
    except Exception:
        return False
    return any(
        variant.is_known and NAME_TAGS & set(variant.tag.grammemes)
        for variant in parsed[:3]
    )


def _same_lemma(left: str, right: str) -> bool:
    """One word in different forms, not two recognition guesses.

    The ear can't tell «Казань» (Kazan) from «Казани»: they sound almost
    the same, and without morphological analysis proofreading would offer
    a choice between grammatical cases of one and the same city."""

    analyzer = _morph_analyzer()
    if analyzer is None:
        return False
    left_lower, right_lower = left.lower().replace("ё", "е"), right.lower().replace("ё", "е")
    if left_lower == right_lower:
        return True
    try:
        parsed_left = analyzer.parse(left_lower)
        parsed_right = analyzer.parse(right_lower)
    except Exception:
        return False
    if not parsed_left or not parsed_right:
        return False
    # An unknown word is never a form of a known one: that is exactly what
    # the mishearing looks like that this whole thing was built for.
    if not parsed_left[0].is_known or not parsed_right[0].is_known:
        return False
    left_forms = {
        variant.normal_form.replace("ё", "е") for variant in parsed_left
    }
    right_forms = {
        variant.normal_form.replace("ё", "е") for variant in parsed_right
    }
    return bool(left_forms & right_forms)


def _term_lemma_key(value: str) -> str:
    return "".join(_lemma(word) for word in WORD_RE.findall(value))


def _inflect_like(canonical: str, example: str) -> str:
    canonical_words = WORD_RE.findall(canonical)
    example_words = WORD_RE.findall(example)
    analyzer = _morph_analyzer()
    if analyzer is None or len(canonical_words) != len(example_words):
        return canonical

    inflected = []
    for target, source in zip(canonical_words, example_words, strict=True):
        try:
            source_parse = analyzer.parse(source)[0]
            target_parse = analyzer.parse(target)[0]
            grammemes = {
                value
                for value in (source_parse.tag.case, source_parse.tag.number)
                if value
            }
            changed = target_parse.inflect(grammemes) if grammemes else None
            word = changed.word if changed is not None else target
        except Exception:
            word = target
        if target[:1].isupper():
            word = word[:1].upper() + word[1:]
        inflected.append(word)
    return " ".join(inflected)


def _language_issues(
    turn: dict,
    turn_index: int,
    occupied: Sequence[tuple[int, int]],
) -> list[dict]:
    issues = []
    for match in _word_positions(turn["text"]):
        word = match.group(0)
        lower = word.lower().replace("ё", "е")
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        if len(lower) < 4 or not CYRILLIC_WORD_RE.fullmatch(word):
            continue
        if lower in KNOWN_COLLOQUIAL or lower in STUDY_WORDS:
            continue
        if word[:1].isupper() or FILLER_RE.fullmatch(word):
            continue
        if _known_russian_word(lower):
            continue
        issues.append(
            _make_issue(
                "language",
                turn,
                turn_index,
                match.start(),
                match.end(),
                message=t("proof.unknown_word"),
                suggestion=_spelling_suggestion(word),
            )
        )
    return issues


def _repeat_issues(turn: dict, turn_index: int) -> list[dict]:
    result = []
    for match in REPEAT_RE.finditer(turn["text"]):
        result.append(
            _make_issue(
                "repeat",
                turn,
                turn_index,
                match.start(),
                match.end(),
                message=t("proof.repeated_word"),
                suggestion=match.group(1),
            )
        )
    return result


def _dialogue_dash_issues(turn: dict, turn_index: int) -> list[dict]:
    result = []
    for match in INTERNAL_DIALOGUE_DASH_RE.finditer(turn["text"]):
        start = max(0, match.start() - 18)
        end = min(len(turn["text"]), match.end() + 30)
        result.append(
            _make_issue(
                "speaker",
                turn,
                turn_index,
                start,
                end,
                message=t("proof.inner_dash"),
            )
        )
    return result


def _unquoted_name_issues(turn: dict, turn_index: int) -> list[dict]:
    """An organization name without quotes and in lower case.

    «Нужна помощь» ("Help Needed"), «Тебе поверят» ("You'll Be Believed")
    are names, but they consist of ordinary words, and recognition writes
    them as ordinary words. The transcript ends up with «перевела в нужна
    помощь» ("transferred to help needed"), and the reader stumbles. We
    suggest quotes only where the name is already in the study's
    vocabulary: guessing from the look of the words is not allowed here —
    «нужна помощь» more often means exactly what it says."""

    from alumenans.codebook import normalize_name  # noqa: F401  (shared whitespace normalization)

    text = str(turn.get("text") or "")
    if not text:
        return []
    try:
        from alumenans import app

        findings = app.curated_spans(text)
    except Exception:
        return []

    result = []
    for finding in findings:
        if finding.get("cat") != "org":
            continue
        start, end = int(finding["start"]), int(finding["stop"])
        piece = text[start:end]
        if len(piece.split()) < 2:
            continue
        before_text = text[max(0, start - 1) : start]
        after_text = text[end : end + 1]
        if before_text in "«\"„" or after_text in "»\"“":
            continue
        result.append(
            _make_issue(
                "term",
                turn,
                turn_index,
                start,
                end,
                message=t("proof.org_quotes"),
                suggestion="«" + piece[:1].upper() + piece[1:] + "»",
            )
        )
    return result


def _broken_phrase_issues(turns: Sequence[dict]) -> list[dict]:
    """A turn is cut off mid-word, and then someone else speaks.

    The program joins adjacent turns of the same voice by itself. What's
    left is what can't be joined: the phrase ended on a preposition, and
    the next line was attributed to another speaker. Either there is an
    interjected «угу» ("uh-huh") that was heard as someone else's speech,
    or the voices got mixed up at that point. Only a person can sort it
    out — they heard this recording."""

    from alumenans.transcription.core import unfinished_phrase

    result = []
    for index, turn in enumerate(turns[:-1]):
        text = str(turn.get("text") or "")
        if not unfinished_phrase(text):
            continue
        next_turn = turns[index + 1]
        if next_turn.get("speaker") == turn.get("speaker"):
            continue
        start = max(0, len(text) - 40)
        result.append(
            _make_issue(
                "speaker",
                turn,
                index,
                start,
                len(text),
                message=t("proof.cut_turn"),
            )
        )
    return result


def _candidate_name_spans(turn: dict) -> list[tuple[int, int, str]]:
    tokens = _word_positions(turn["text"])
    result = []
    for index, token in enumerate(tokens):
        if not CAPITALIZED_RE.fullmatch(token.group(0)):
            continue
        if token.group(0).lower().replace("ё", "е") in (
            NAME_STOPWORDS | CONTENT_STOPWORDS
        ):
            continue
        for length in (3, 2, 1):
            selected = tokens[index : index + length]
            if len(selected) != length:
                continue
            between = turn["text"][selected[0].start() : selected[-1].end()]
            if "\n" in between or len(between) > 70:
                continue
            if any(
                not re.fullmatch(r"\s+", turn["text"][left.end() : right.start()])
                for left, right in zip(selected, selected[1:])
            ):
                continue
            if length > 1 and any(
                not CAPITALIZED_RE.fullmatch(item.group(0)) for item in selected
            ):
                continue
            if length == 1 and _starts_sentence(turn["text"], selected[0].start()):
                continue
            result.append((selected[0].start(), selected[-1].end(), between))
    return result


def _reference_issues(
    turns: Sequence[dict],
    references: Sequence[dict],
) -> list[dict]:
    proposals = []
    for turn_index, turn in enumerate(turns):
        for start, end, candidate in _candidate_name_spans(turn):
            candidate_key = _normalized_letters(candidate)
            if any(
                candidate_key == _normalized_letters(alias)
                or _term_lemma_key(candidate) == _term_lemma_key(alias)
                for reference in references
                for alias in reference["aliases"]
            ):
                continue
            # An ordinary Russian word was heard correctly by the program,
            # and it must not be swapped for a similar entry from the
            # reference list: that's how «Наталья» (Natalya) turned into
            # «Навальной» (Navalnaya), and «Грузии» (Georgia) into «Грозевым».
            #
            # This doesn't apply to the notes and the guide. Names there
            # were written by a person specifically for this interview: if
            # they wrote «Марем», then «Морен» is almost certainly just that.
            is_ordinary = len(WORD_RE.findall(candidate)) == 1 and _known_russian_word(
                candidate.lower().replace("ё", "е")
            )
            best = None
            for reference in references:
                if is_ordinary and reference["source"] == "reference":
                    continue
                for alias in reference["aliases"]:
                    if " " not in candidate.strip() and " " in alias.strip():
                        continue
                    score = _word_similarity(candidate, alias)
                    if score < reference["threshold"]:
                        continue
                    ranked = (
                        score,
                        int(reference["source"] == "notes"),
                        len(alias),
                    )
                    if best is None or ranked > best[0]:
                        best = (ranked, reference, alias)
            if best is None:
                continue
            score, reference, matched_alias = best[0][0], best[1], best[2]
            source = (
                "proof.similar_in_notes"
                if reference["source"] == "notes"
                else "proof.similar_in_guide"
                if reference["source"] == "guide"
                else "proof.similar_in_reference"
            )
            candidate_words = WORD_RE.findall(candidate)
            alias_words = WORD_RE.findall(matched_alias)
            if (
                len(candidate_words) == 1
                and len(alias_words) == 1
                and len(_normalized_letters(candidate)) < 10
            ):
                suggestion = matched_alias
            else:
                suggestion = _inflect_like(reference["canonical"], candidate)
            proposals.append(
                (
                    turn_index,
                    start,
                    end,
                    score,
                    _make_issue(
                        "term",
                        turn,
                        turn_index,
                        start,
                        end,
                        message=t(source),
                        suggestion=suggestion,
                    ),
                )
            )

    # A long phrase takes precedence over a single word nested inside it.
    proposals.sort(key=lambda item: (-item[3], -(item[2] - item[1]), item[0], item[1]))
    accepted = []
    occupied: dict[int, list[tuple[int, int]]] = {}
    for turn_index, start, end, _, issue in proposals:
        ranges = occupied.setdefault(turn_index, [])
        if any(start < right and end > left for left, right in ranges):
            continue
        ranges.append((start, end))
        accepted.append(issue)
    return accepted


def _guide_question_issues(
    turns: Sequence[dict],
    questions: Sequence[str],
    acoustic_issues: Sequence[dict],
) -> list[dict]:
    if not questions:
        return []
    acoustic_turns = {issue["turn_index"] for issue in acoustic_issues}
    result = []
    for turn_index, turn in enumerate(turns):
        text = str(turn.get("text") or "").strip()
        if turn_index not in acoustic_turns or len(text) < 12:
            continue
        best = max(
            ((_similarity(text, question), question) for question in questions),
            default=(0.0, ""),
        )
        score, question = best
        length_ratio = min(len(text), len(question)) / max(len(text), len(question))
        if score < 0.84 or length_ratio < 0.68:
            continue
        if _normalized_letters(text) == _normalized_letters(question):
            continue
        result.append(
            _make_issue(
                "guide",
                turn,
                turn_index,
                0,
                len(turn["text"]),
                message=t("proof.guide_question"),
                suggestion=question,
            )
        )
    return result


def _inconsistent_name_issues(
    turns: Sequence[dict],
    occupied: Sequence[dict],
) -> list[dict]:
    names = []
    for turn_index, turn in enumerate(turns):
        for match in _word_positions(turn["text"]):
            word = match.group(0)
            if not CAPITALIZED_RE.fullmatch(word):
                continue
            if word.lower().replace("ё", "е") in NAME_STOPWORDS:
                continue
            # «Э-э-э» and «М-м» ("uh", "mm") are capitalized and look like
            # names, but they never become names: there's nothing to merge
            # their variants into.
            if FILLER_RE.fullmatch(word.replace("-", "")):
                continue
            before = turn["text"][: match.start()]
            if not before.strip() or re.search(r"[.!?…]\s*$", before):
                continue
            names.append((word, turn_index, match.start(), match.end()))

    frequencies = Counter(
        word.lower().replace("ё", "е")
        for word, _, _, _ in names
    )
    result = []
    shown = set()
    for index, left in enumerate(names):
        variants = [left]
        for right in names[index + 1 :]:
            if _normalized_letters(left[0]) == _normalized_letters(right[0]):
                continue
            if left[0][0].lower() != right[0][0].lower():
                continue
            # «Казань» and «Казани» (Kazan) sound almost the same, but they
            # aren't two recognition guesses — they're one word in different
            # grammatical cases.
            if _same_lemma(left[0], right[0]):
                continue
            if _word_similarity(left[0], right[0]) >= 0.72:
                variants.append(right)
        distinct = {item[0].lower().replace("ё", "е") for item in variants}
        if len(distinct) < 2:
            continue
        # One name, one note
        if distinct & shown:
            continue
        shown |= distinct
        forms = ", ".join(dict.fromkeys(item[0] for item in variants))
        ranked_forms = sorted(
            dict.fromkeys(item[0] for item in variants),
            key=lambda word: (
                frequencies[word.lower().replace("ё", "е")],
                len(word),
            ),
            reverse=True,
        )
        preferred = ranked_forms[0]
        has_preferred = (
            len(ranked_forms) == 1
            or frequencies[preferred.lower().replace("ё", "е")]
            > frequencies[ranked_forms[1].lower().replace("ё", "е")]
        )
        for word, turn_index, start, end in variants:
            issue = _make_issue(
                "inconsistent",
                turns[turn_index],
                turn_index,
                start,
                end,
                message=t("proof.variants", forms=forms),
                suggestion=(
                    _match_case(preferred, word)
                    if has_preferred
                    and word.lower().replace("ё", "е")
                    != preferred.lower().replace("ё", "е")
                    else None
                ),
            )
            overlaps_reference = any(
                existing["turn_index"] == turn_index
                and start < existing["end"]
                and end > existing["start"]
                for existing in occupied
            )
            if not overlaps_reference:
                result.append(issue)
    return result


def _content_terms(text: str) -> Counter:
    terms = []
    for match in WORD_RE.finditer(text):
        word = match.group(0)
        lower = word.lower().replace("ё", "е")
        if len(lower) < 4 or lower in CONTENT_STOPWORDS or FILLER_RE.fullmatch(word):
            continue
        terms.append(_lemma(lower))
    return Counter(terms)


def _cosine(left: Counter, right: Counter) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0) for key, value in left.items())
    left_length = math.sqrt(sum(value * value for value in left.values()))
    right_length = math.sqrt(sum(value * value for value in right.values()))
    if not left_length or not right_length:
        return 0.0
    return dot / (left_length * right_length)


def _window_text(
    turns: Sequence[dict],
    index: int,
    *,
    before: bool,
    seconds: float = 100.0,
) -> str:
    anchor = turns[index]["start"]
    selected = []
    if before:
        for turn in turns[:index]:
            if anchor - turn["end"] <= seconds:
                selected.append(turn["text"])
    else:
        for turn in turns[index:]:
            if turn["start"] - anchor <= seconds:
                selected.append(turn["text"])
    return " ".join(selected)


def _detect_topics(turns: list[dict], note_terms: Sequence[dict]) -> None:
    if len(turns) < 3:
        return
    last_topic_time = turns[0]["start"]
    note_keys = {
        _normalized_letters(alias)
        for term in note_terms
        for alias in term["aliases"]
        if len(_normalized_letters(alias)) >= 5
    }

    for index in range(1, len(turns)):
        turn = turns[index]
        since_last = turn["start"] - last_topic_time
        if turn["start"] < 60 or since_last < 60:
            continue

        explicit = bool(EXPLICIT_TOPIC_RE.search(turn["text"]))
        question = "?" in turn["text"] or bool(QUESTION_START_RE.search(turn["text"]))
        previous_text = _window_text(turns, index, before=True)
        next_text = _window_text(turns, index, before=False)
        similarity = _cosine(
            _content_terms(previous_text),
            _content_terms(next_text),
        )
        current_terms = _content_terms(turn["text"])
        previous_terms = _content_terms(previous_text)
        novelty = (
            sum(value for key, value in current_terms.items() if key not in previous_terms)
            / max(1, sum(current_terms.values()))
        )
        normalized_current = _normalized_letters(" " + next_text[:500])
        normalized_previous = _normalized_letters(previous_text)
        new_note_cue = any(
            key in normalized_current and key not in normalized_previous
            for key in note_keys
        )

        reason = None
        if explicit:
            reason = t("proof.topic_explicit")
        elif new_note_cue and question and since_last >= 90 and similarity < 0.22:
            reason = t("proof.topic_notes")
        elif (
            question
            and since_last >= 150
            and similarity < 0.12
            and novelty >= 0.62
            and len(current_terms) >= 3
        ):
            reason = t("proof.topic_shift")

        if reason is None:
            continue
        turn["topic_start"] = True
        turn["topic_reason"] = reason
        last_topic_time = turn["start"]


def format_timecode(seconds: float) -> str:
    value = max(0, int(round(float(seconds))))
    hours, remainder = divmod(value, 3600)
    minutes, rest = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{rest:02d}"
    return f"{minutes:02d}:{rest:02d}"


def render_reviewed_transcript(turns: Sequence[dict]) -> str:
    blocks = []
    current_speaker = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_speaker, current_parts
        if current_speaker is None or not current_parts:
            return
        blocks.append(f"Спикер {current_speaker}: {' '.join(current_parts)}")
        current_speaker = None
        current_parts = []

    for turn in turns:
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        if turn.get("topic_start"):
            flush()
            blocks.append(f"[{format_timecode(turn.get('start') or 0)}]")
        speaker = max(1, int(turn.get("speaker") or 1))
        if current_speaker != speaker:
            flush()
            current_speaker = speaker
        current_parts.append(text)
    flush()
    return "\n\n".join(blocks)


def analyze_proofreading(
    turns: Iterable[dict],
    notes: str = "",
    guide: str = "",
) -> dict:
    cleaned_turns, _ = _coerce_turns(turns)
    note_terms = _note_terms(notes)
    guide_terms, guide_questions = _guide_material(guide)
    references = (*note_terms, *guide_terms, *_load_lexicon())

    issues: list[dict] = []
    all_acoustic = []
    for turn_index, turn in enumerate(cleaned_turns):
        acoustic = _acoustic_issues(turn, turn_index)
        all_acoustic.extend(acoustic)
        issues.extend(acoustic)
        occupied = [(item["start"], item["end"]) for item in acoustic]
        issues.extend(_language_issues(turn, turn_index, occupied))
        issues.extend(_repeat_issues(turn, turn_index))
        issues.extend(_dialogue_dash_issues(turn, turn_index))
        issues.extend(_unquoted_name_issues(turn, turn_index))

    reference_issues = _reference_issues(cleaned_turns, references)
    issues.extend(_broken_phrase_issues(cleaned_turns))
    issues.extend(reference_issues)
    issues.extend(
        _guide_question_issues(
            cleaned_turns,
            guide_questions,
            all_acoustic,
        )
    )
    issues.extend(
        _inconsistent_name_issues(
            cleaned_turns,
            reference_issues,
        )
    )

    # We don't turn proofreading into a wall of warnings: the most
    # verifiable signals are shown first, keeping the order of the
    # conversation.
    priority = {
        "term": 0,
        "guide": 0,
        "hearing": 1,
        "speaker": 2,
        "repeat": 3,
        "inconsistent": 4,
        "language": 5,
    }
    issues.sort(
        key=lambda item: (
            item["turn_index"],
            item["start"],
            priority.get(item["kind"], 9),
        )
    )

    _detect_topics(cleaned_turns, (*note_terms, *guide_terms))
    topics = [
        {
            "turn_id": turn["id"],
            "turn_index": index,
            "time": turn["start"],
            "reason": turn["topic_reason"],
            "text": turn["text"],
        }
        for index, turn in enumerate(cleaned_turns)
        if turn["topic_start"]
    ]

    return {
        "schema": 1,
        "turns": cleaned_turns,
        "issues": issues,
        "topics": topics,
        "notes_terms": [term["canonical"] for term in note_terms],
        "guide_terms": [term["canonical"] for term in guide_terms],
        "guide_questions": list(guide_questions),
        "stats": {
            "issues": len(issues),
            "topics": len(topics),
        },
        "text": render_reviewed_transcript(cleaned_turns),
    }
