#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The application shell.

Brings up a server on a local address, serves the editor screen and opens
it in its own window. To the person it looks like an ordinary program:
start it, drag a file in, get to work. No command line at all.

The network is used only inside the machine (127.0.0.1). Nothing goes
out: the server listens on the loopback interface and can do nothing else.

The heavy transcription stages run in a separate process. That way, once
the transcript is ready, the memory is freed before the anonymisation
models are loaded.
"""

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from alumenans.proofreading import analyze_proofreading, set_study_words
from alumenans.storage.project_store import (
    ProjectError,
    interview_stem,
    ProjectStore,
    project_path_for,
    project_summaries,
)
from alumenans.interview_name import (
    group_parts,
    numbering_problems,
    parse_interview_name,
    study_title_from,
)
from alumenans.cleanup import unanonymised_files
from alumenans.echoes import echo_spans
from alumenans.storage.exchange import (
    STUDY_FILE_NAME,
    ExchangeError,
    build_export,
    export_name,
    read_study_file,
)
from alumenans.introduction import respondent_speaker, self_introduction_spans
from alumenans.places import KINDS as PLACE_KINDS, place_kind
from alumenans.study_documents import classify_documents, document_kind, read_document
from alumenans.storage import cipher
from alumenans.storage.cipher import StudySecret
from alumenans.storage.study_store import StudyError, StudyStore, matches_subject_term, study_path_for
from alumenans.speech_noise import restore_span, smooth
from alumenans.subject_terms import suggest_from_documents
from alumenans.vocative import morphology_checks as vocative_checks, vocative_spans
from alumenans.transcription.core import (
    TranscriptionError,
    existing_directory,
    probe_media_duration,
    validate_media_path,
    validate_speaker_count,
)
from alumenans.transcription.service import (
    WORKER_MODULE,
    TranscriptionManager,
    worker_python,
)
from alumenans.dependencies import missing_modules, required_modules
from alumenans.paths import (
    INSTALL_ROOT,
    RESOURCES,
    child_environment,
    models_home,
    state_file,
)
from alumenans import i18n
from alumenans.i18n import t

# The program is started through pythonw — as a window, with no console.
# There sys.stdout and sys.stderr are None, and any attempt to print
# something dies with an error inside someone else's library, often in its
# own thread. From the outside this looks like "the window didn't open and
# said nothing". We substitute a dummy: there is nowhere to print, but
# nothing to crash on either.
for _stream in ("stdout", "stderr"):
    if getattr(sys, _stream, None) is None:
        setattr(sys, _stream, open(os.devnull, "w", encoding="utf-8"))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FIRST_RUN_MARK = state_file(".installed", legacy=".установлено")
RESEARCHER_FILE = state_file(".researcher", legacy=".исследователь")
NL = chr(10)
NL2 = chr(10) * 2
SCREEN = RESOURCES / "anonymizer.html"
I18N_SCRIPT = RESOURCES / "i18n.js"
LOGO = RESOURCES / "ALumenans_logo.png"
ICON = RESOURCES / "ALumenans_logo.ico"
WINDOWS_APP_ID = "ALumenans.InterviewProcessing"
LIMIT = 32 * 1024 * 1024  # a sensible ceiling on the size of a transcript
TRANSCRIBER_MODELS = models_home()
ANONYMIZER_SETUP_ARG = "--prepare-anonymizer-models"
SETUP_WINDOW_ARG = "--setup-window"
SELF_TEST_ARG = "--self-test"
MEDIA_SUFFIXES = {".mp3", ".wav", ".wma", ".mp4", ".m4a", ".ogg"}
REQUIRED_MODULES = required_modules()

# ---------------------------------------------------------------------------
# Case-insensitive layers.
#
# Natasha and the rules rely on the capital letter and go completely blind
# on lower-case text. A transcript from a voice recorder does not respect
# case, so everything below is matched case-insensitively and works even on
# text that is entirely lower-case.
# ---------------------------------------------------------------------------

# Patronymics: the suffix almost never occurs in other words, so the signal
# stays reliable even without a capital letter.
PATRONYMIC = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё_])[а-яёa-z]{3,}(?:ович|евич|ьевич|овна|евна|ьевна|инична)"
    r"(?:[ауые]|ем|ой|ны|ем)?(?![0-9A-Za-zА-Яа-яЁё_])",
    re.IGNORECASE,
)

# Given names, including diminutives: what is heard most often in interviews.
GIVEN_NAMES = """
александр саша сашка сашенька шура шурик алексей алёша алеша лёша леша лёха
анатолий толя толик андрей андрюша андрюха антон антоша аркадий арсений сеня
артём артем тёма тема артур борис боря вадим валентин валера валерий василий вася
виктор витя витёк владимир вова володя вовка владислав влад вячеслав слава
геннадий гена георгий гоша жора григорий гриша данил даниил данила денис дима
дмитрий димон евгений женя егор ефим захар иван ваня ванька игорь илья илюша
кирилл коля константин костя лев лёва лева леонид лёня леня макар максим макс
марк матвей михаил миша мишка никита коля николай олег павел паша пётр петр петя
роман рома ростислав руслан рустам савелий семён семен сёма сема сергей серёжа
сережа серёга серега станислав стас степан стёпа степа тарас тимофей тимур
фёдор федор федя филипп эдуард эдик юрий юра яков яша ярослав
александра алла алина алёна алена анастасия настя настенька анна аня анюта
антонина тоня валентина валя валерия лера варвара варя вера вероника виктория вика
галина галя дарья даша диана евгения женя екатерина катя катюша елена лена леночка
елизавета лиза зинаида зина зоя инна ирина ира ирочка карина кристина ксения ксюша
лариса лидия лида лилия любовь люба людмила люда мила маргарита рита марина марья
мария маша машенька надежда надя наталья наташа нина оксана олеся ольга оля
полина раиса регина римма светлана света софья соня таисия тамара татьяна таня
танюшка ульяна фаина эльвира юлия юля яна ярослава
""".split()

NAME_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё_])(?:"
    + "|".join(sorted((re.escape(n) for n in GIVEN_NAMES), key=len, reverse=True))
    + r")(?:[аеиоуыюя]|ой|ей|ем|ом|ку|ке|ки|шу|ше|ши)?(?![0-9A-Za-zА-Яа-яЁё_])",
    re.IGNORECASE,
)

# Age. Separate from dates: in live speech it sounds like «ей 22» ("she's
# 22"), «мне под сорок» ("I'm nearly forty"), «лет в двадцать» ("at about
# twenty") — without the word «год» ("year") half the time.
AGE_RES = [
    re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_])(?:мне|тебе|ему|ей|нам|им|вам)\s+"
               r"(?:было\s+|уже\s+|тогда\s+|сейчас\s+|около\s+|почти\s+|под\s+)*"
               r"\d{1,3}(?:\s*(?:год[ауе]?|года|лет))?(?![0-9A-Za-zА-Яа-яЁё_])",
               re.IGNORECASE),
    re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_])\d{1,3}\s*(?:год[ауе]?|года|лет)"
               r"(?![0-9A-Za-zА-Яа-яЁё_])", re.IGNORECASE),
    re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_])(?:в\s+возрасте|возраст)\s+\d{1,3}"
               r"(?![0-9A-Za-zА-Яа-яЁё_])", re.IGNORECASE),
    re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_])\d{1,3}[-\s]?летн[а-яё]{1,4}"
               r"(?![0-9A-Za-zА-Яа-яЁё_])", re.IGNORECASE),
    re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_])(?:лет|года)\s+(?:в|до|после|около)\s+"
               r"(?:двадцат|тридцат|сорок|пятидесят|шестидесят)[а-яё]*"
               r"(?![0-9A-Za-zА-Яа-яЁё_])", re.IGNORECASE),
]


# Date of birth. Ordinary mentions of years are left alone — in an
# interview they are content, not an identifying feature.
BIRTH_RES = [
    re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_])(?:родил[ася]{2,3}|рождени[яе]|день\s+рождения|"
               r"дата\s+рождения|р\.)\s*(?:в\s+)?[^.,;\n]{0,28}?"
               r"\d{1,2}[.\/]\d{1,2}[.\/]\d{2,4}", re.IGNORECASE),
    re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_])(?:родил[ася]{2,3}|рождени[яе])\s*"
               r"(?:в\s+)?\d{1,2}\s+(?:январ|феврал|март|апрел|ма|июн|июл|август|"
               r"сентябр|октябр|ноябр|декабр)[а-яё]{0,3}(?:\s+\d{4})?", re.IGNORECASE),
    re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_])\d{1,2}[.\/]\d{1,2}[.\/](?:19|20)\d{2}"
               r"(?![0-9A-Za-zА-Яа-яЁё_])"),
]

CASELESS = [
    (PATRONYMIC, "person", "strong"),
    (NAME_RE, "person", "strong"),
] + [(r, "age", "strong") for r in AGE_RES] + [(r, "birth", "strong") for r in BIRTH_RES]


def caseless_spans(text):
    """Findings that don't care about case."""
    out = []
    for pattern, cat, conf in CASELESS:
        for m in pattern.finditer(text):
            start, stop = m.start(), m.end()
            while stop > start and text[stop-1] in ".,-+&":
                stop -= 1
            out.append({"start": start, "stop": stop, "cat": cat,
                        "conf": conf, "by": "словарь"})
    return out


WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]*")


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


TYPE_MAP = {"PER": "person", "ORG": "org", "LOC": "place"}

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


# ---------------------------------------------------------------------------
# The subject of the study.
#
# The study is about some group; if the anonymiser replaces the group with
# «Организация 1» ("Organisation 1"), the transcript becomes useless. The
# models don't know this — to them it is an organisation no different from
# the respondent's place of work.
#
# The list of confirmed terms comes from the study file and lives here,
# because the search for findings runs outside the window. An empty list is
# the normal state: until the person has confirmed anything, nothing is
# protected.
# ---------------------------------------------------------------------------

SUBJECT_STATE = {"terms": ()}


def _refresh_study_words(words):
    """The study dictionary — into proofreading. The one place where it is read."""

    return set_study_words(words or ())


def set_subject_terms(terms):
    """Remembers the confirmed terms for the subject of the study."""

    cleaned = tuple(
        str(term).strip()
        for term in (terms or ())
        if str(term or "").strip()
    )
    SUBJECT_STATE["terms"] = cleaned
    return cleaned


def is_subject_fragment(fragment):
    return any(
        matches_subject_term(fragment, term) for term in SUBJECT_STATE["terms"]
    )

_tagger = None
_gliner = None
# Separate locks: a long load of one model must not hold up the work of
# the other one or of the whole program.
_morph = None
_pymorphy = None
_tagger_lock = threading.Lock()
_morph_lock = threading.Lock()
_gliner_lock = threading.Lock()

GLINER_LABELS = {
    "имя человека": "person",
    "фамилия": "person",
    "название организации": "org",
    "учебное заведение": "org",
    "город или населённый пункт": "place",
    "адрес": "place",
    "возраст": "age",
    "дата рождения": "birth",
}

_ENTITY_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*")
_SPEECH_NOISE = re.compile(
    r"^(?:"
    r"[аэоуюя]+(?:-[аэоуюя]+)+|"
    r"[мх]+(?:-[мх]+)*|"
    r"у+гу+|а+га+|"
    r"д+а(?:-д+а)+|н+ет(?:-н+ет)+"
    r")$",
    re.IGNORECASE,
)
_FUNCTION_WORDS = {
    "а", "без", "бы", "в", "во", "вот", "вы", "где", "да", "для", "до",
    "если", "же", "за", "и", "из", "или", "к", "как", "когда", "ли", "мы",
    "на", "не", "нет", "но", "ну", "о", "об", "он", "она", "они", "от",
    "по", "под", "потому", "при", "про", "с", "со", "так", "то", "ты",
    "у", "угу", "что", "это", "я",
}
_PUBLIC_ORGANIZATION = re.compile(
    r"^(?:"
    r"instagram|инстаграм(?:а|е|ом|у)?|"
    r"whatsapp|ватсап(?:а|е|ом|у)?|"
    r"telegram|телеграм(?:а|е|ом|у)?|"
    r"facebook|фейсбук(?:а|е|ом|у)?|"
    r"youtube|ютуб(?:а|е|ом|у)?|"
    r"tiktok|тикток(?:а|е|ом|у)?|"
    r"google|гугл(?:а|е|ом|у)?|"
    r"яндекс(?:а|е|ом|у)?|"
    r"stories|story|storis|сторис(?:а|е|ом|у)?|"
    r"вконтакте|vk"
    r")$",
    re.IGNORECASE,
)
_GENERIC_ORGANIZATION = re.compile(
    r"^(?:"
    r"фонд|хоспис|больниц[а-яё]*|поликлиник[а-яё]*|клиник[а-яё]*|"
    r"школ[а-яё]*|университет[а-яё]*|институт[а-яё]*|"
    r"организаци[а-яё]*|учреждени[а-яё]*|компани[а-яё]*|"
    r"министерств[а-яё]*|ведомств[а-яё]*"
    r")(?:\s+[а-яё-]+){0,4}$",
    re.IGNORECASE,
)
_FALSE_PLACE_WORDS = {"мир", "миру", "мире", "морем"}
_NON_ENTITY_POS = {
    "ADVB", "CONJ", "GRND", "INFN", "INTJ", "NPRO", "PRCL", "PREP", "VERB",
}


def _entity_words(fragment):
    return _ENTITY_WORD.findall(fragment)


def _looks_like_speech_noise(fragment):
    words = _entity_words(fragment)
    if not words:
        return True
    lowered = [word.lower().replace("ё", "е") for word in words]
    compact = " ".join(lowered)
    if _SPEECH_NOISE.fullmatch(compact):
        return True
    if all(word in _FUNCTION_WORDS for word in lowered):
        return True
    if lowered[0] in {"да", "нет", "ну", "вот", "а", "и", "это"}:
        meaningful = [
            word for word in words[1:]
            if word.lower().replace("ё", "е") not in _FUNCTION_WORDS
        ]
        if not meaningful:
            return True
        try:
            _, pymorphy = morph_tools()
            return all(
                pymorphy.parse(word)[0].tag.POS in {"ADVB", "ADJF", "ADJS", "PRCL"}
                for word in meaningful
            )
        except Exception:
            return False
    return False


def _primary_pos(fragment):
    words = _entity_words(fragment)
    if len(words) != 1:
        return None
    try:
        _, pymorphy = morph_tools()
        parsed = pymorphy.parse(words[0])
        return parsed[0].tag.POS if parsed else None
    except Exception:
        return None


def keep_entity_span(text, span):
    """Discards the classes of errors that the models consistently mistake for personal data."""

    fragment = text[span["start"]:span["stop"]].strip()
    category = span.get("cat")
    if not fragment or category == "job":
        return False

    # The subject of the study is not personal data. This check comes first:
    # whatever the models decide, what the person has confirmed takes
    # precedence.
    if is_subject_fragment(fragment):
        return False

    normalized = " ".join(
        word.lower().replace("ё", "е") for word in _entity_words(fragment)
    )
    if _PUBLIC_ORGANIZATION.fullmatch(normalized):
        return False

    if category == "person":
        if _looks_like_speech_noise(fragment):
            return False
        if (
            len(_entity_words(fragment)) == 1
            and normalized not in GIVEN_NAMES
            and _primary_pos(fragment) in _NON_ENTITY_POS
        ):
            return False

    if category == "place":
        if _looks_like_speech_noise(fragment) or normalized in _FALSE_PLACE_WORDS:
            return False
        if _primary_pos(fragment) in _NON_ENTITY_POS:
            return False

    if category == "org" and _GENERIC_ORGANIZATION.fullmatch(fragment):
        internal_capital = re.search(r"\s[А-ЯЁ]", fragment)
        if not internal_capital and not re.search(r"[«\"]", fragment):
            return False

    return True


def gliner_model(local_files_only=False):
    global _gliner
    with _gliner_lock:
        if _gliner is None:
            from gliner import GLiNER
            _gliner = GLiNER.from_pretrained(
                "urchade/gliner_multi-v2.1",
                local_files_only=local_files_only,
                low_cpu_mem_usage=True,
            )
    return _gliner


def gliner_spans(text):
    """GLiNER findings go into the doubtful layer: the model grabs too much,
    but it sees what is in neither the dictionary nor Natasha."""
    model = gliner_model()
    labels = list(GLINER_LABELS)
    out = []
    # Cut into pieces: the model can't digest a long text in one go.
    step = 1200
    for start in range(0, len(text), step):
        piece = text[start:start + step]
        for found in model.predict_entities(piece, labels, threshold=0.5):
            cat = GLINER_LABELS.get(found["label"])
            if not cat:
                continue
            out.append({"start": start + found["start"],
                        "stop": start + found["end"],
                        "cat": cat, "conf": "weak", "by": "gliner"})
    return out


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


def tagger():
    """Natasha loads on first use, so that the window opens straight away."""
    global _tagger
    with _tagger_lock:
        if _tagger is None:
            from natasha import Segmenter, NewsEmbedding, NewsNERTagger
            _tagger = (Segmenter(), NewsNERTagger(NewsEmbedding()))
    return _tagger


def natasha_spans(text, by="natasha"):
    """What the sentence parser considers a name, a title, a place."""

    from natasha import Doc

    segmenter, ner = tagger()
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(ner)

    found = []
    for span in doc.spans:
        category = TYPE_MAP.get(span.type)
        if not category:
            continue
        if len(text[span.start:span.stop].strip()) < 2:
            continue
        found.append(
            {
                "start": span.start,
                "stop": span.stop,
                "cat": category,
                "conf": "strong",
                "by": by,
            }
        )
    return found


def false_start_spans(text):
    """Names lost to a slip of the tongue — a second pass over a clean copy.

    «Работать на с <имя>» ("to work on with <name>") — the person started
    «над» ("on"), changed their mind and said «с» ("with"). The parser loses
    the name on this, even though in the intact phrase it finds it
    confidently. The clean copy exists only for the sake of this parse; the
    transcript text does not change."""

    clean_text, offsets = smooth(text)
    if clean_text == text:
        return []
    restored = []
    for span in natasha_spans(clean_text, by="false-start"):
        piece = clean_text[span["start"]:span["stop"]]
        place = restore_span(span["start"], span["stop"], offsets)
        if place is None:
            continue
        # The finding may have taken in the spot the preposition was dropped
        # from. Then in the original text something else sits at these
        # boundaries, and it must not be taken: the wrong thing would be
        # replaced.
        if text[place[0]:place[1]] != piece:
            continue
        span["start"], span["stop"] = place
        restored.append(span)
    return restored


def find_spans(text):
    spans = [
        span for span in natasha_spans(text) if keep_entity_span(text, span)
    ]
    spans.extend(false_start_spans(text))
    spans.extend(dict_spans(text))
    spans.extend(vocative_name_spans(text))
    if MODEL_STATE.get("gliner") == "ready":
        try:
            spans.extend(gliner_spans(text))
        except Exception:
            MODEL_STATE["gliner"] = "off"
    spans = [span for span in spans if keep_entity_span(text, span)]
    # The parser does not recognise the same word in every sentence. A
    # repeat of a name that has already been recognised is personal data
    # too.
    spans.extend(
        span for span in echo_spans(text, spans) if keep_entity_span(text, span)
    )

    # Remove overlaps: a long finding matters more than a short one.
    spans.sort(key=lambda s: (s.get("conf") == "weak", s["start"] - s["stop"], s["start"]))
    kept = []
    for span in spans:
        if any(span["start"] < k["stop"] and span["stop"] > k["start"] for k in kept):
            continue
        kept.append(span)
    kept.sort(key=lambda s: s["start"])
    # Publicness is set after selection: overlapping findings are weeded
    # out, and the dictionary one may not have survived to this point.
    for span in kept:
        if not span.get("public"):
            span["public"] = is_public_name(text[span["start"]:span["stop"]])
    # What kind of place this is: country, region, town, address. Otherwise
    # the anonymiser suggests «Город 1» ("Town 1") both for Georgia and for
    # a street.
    for span in kept:
        if span.get("cat") == "place":
            span["place_kind"] = place_kind(
                text[span["start"]:span["stop"]],
                before=text[max(0, span["start"] - 40):span["start"]],
                lemma=lemma_of,
            )
    # The name the respondent introduced themselves by: its replacement is
    # known in advance — it is in the file name.
    try:
        introductions = self_introduction_spans(text)
    except Exception:
        introductions = []
    for span in kept:
        span["self"] = any(
            span["start"] < end and span["stop"] > start
            for start, end in introductions
        )
    try:
        grammar_of(text, kept)
    except Exception:
        pass
    return kept


# ---------------------------------------------------------------------------
# Audio for proofreading.
#
# In the proofreading tab the person is asked to correct a word they cannot
# hear. Yet we already have both the file and the timestamps of every word.
# So the server serves the chosen recording with support for Range requests:
# without them WebView downloads the whole file and cannot seek.
#
# None of this goes outside, as before: the server listens only on 127.0.0.1
# and serves exactly one file — the one the person chose themselves in this
# window.
# ---------------------------------------------------------------------------

# An interrupted interview lies on disk as several files. Each is served to
# the player separately, while proofreading knows them as one continuous
# sound: the time of the second part continues the first.
AUDIO_STATE = {"paths": [], "token": ""}
AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".wma": "audio/x-ms-wma",
}
AUDIO_BLOCK = 256 * 1024


def set_current_audio(path):
    """Remembers the recording that can be listened to in the proofreading window."""

    if path is None:
        AUDIO_STATE["paths"] = []
        AUDIO_STATE["token"] = ""
        return ""
    paths = [path] if isinstance(path, (str, Path)) else list(path)
    AUDIO_STATE["paths"] = [Path(item) for item in paths]
    AUDIO_STATE["token"] = f"{int(time.time() * 1000):x}"
    return AUDIO_STATE["token"]


def audio_parts(paths):
    """The parts of the recording with their start in the overall interview time."""

    file_parts = []
    start = 0.0
    for index, path in enumerate(paths):
        length = probe_media_duration(path) or 0.0
        file_parts.append(
            {
                "index": index,
                "name": Path(path).name,
                "start": round(start, 3),
                "duration": round(length, 3),
            }
        )
        start += length
    return file_parts


def parse_byte_range(header, size):
    """Parses the Range header. Returns None if it is not applicable."""

    if not header:
        return None
    match = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", str(header))
    if not match or size <= 0:
        return None
    first, last = match.group(1), match.group(2)
    if not first and not last:
        return None
    if not first:
        length = min(int(last), size)
        if length <= 0:
            return None
        return size - length, size - 1
    start = int(first)
    if start >= size:
        return "unsatisfiable"
    stop = min(int(last), size - 1) if last else size - 1
    if stop < start:
        return None
    return start, stop


# The local server is reachable by every program on the machine, and by any
# web page through DNS rebinding (a site pointing its own name at 127.0.0.1).
# It serves the open interview's recording, so every request must name this
# server exactly in its Host header, and everything past the bare page must
# carry this launch's secret. The window gets the secret in its own URL;
# fetch() sends it as a header, the <audio> element in the query.
SESSION_SECRET = secrets.token_urlsafe(32)
SESSION_HEADER = "X-ALumenans-Session"
SESSION_PARAMETER = "session"
# What the window needs before it knows the secret: the page and its files.
PUBLIC_PATHS = frozenset({"/", "/index.html", "/i18n.js", "/ALumenans_logo.png", "/logo.png"})


class Handler(BaseHTTPRequestHandler):
    # The player needs HTTP/1.1: without a persistent connection, seeking
    # through a long recording turns into reopening the socket on every
    # move of the slider.
    protocol_version = "HTTP/1.1"
    # But persistent connections have a downside: the browser engine opens
    # sockets in reserve and often sends nothing over them. Without a
    # timeout, every such socket ties up a server thread forever.
    timeout = 30

    def log_message(self, *args):
        pass  # don't make noise in the console

    def _allowed(self):
        """Refuses the request (and returns False) unless it comes from the window."""

        port = self.server.server_address[1]
        address = urllib.parse.urlparse(self.path)
        given = self.headers.get(SESSION_HEADER) or (
            urllib.parse.parse_qs(address.query).get(SESSION_PARAMETER, [""])[0]
        )
        if self.headers.get("Host", "") == f"127.0.0.1:{port}" and (
            address.path in PUBLIC_PATHS
            or secrets.compare_digest(given.encode("utf-8"), SESSION_SECRET.encode("utf-8"))
        ):
            return True
        # The body of a refused request is never read, so the connection
        # can't be reused for another request.
        self.close_connection = True
        self._send(403, json.dumps({"error": "forbidden"}))
        return False

    def _send_audio(self, head_only=False):
        paths = AUDIO_STATE.get("paths") or []
        query = urllib.parse.urlparse(self.path).query
        try:
            relative = int(urllib.parse.parse_qs(query).get("part", ["0"])[0])
        except ValueError:
            relative = 0
        if not 0 <= relative < len(paths):
            relative = 0
        media = paths[relative] if paths else None
        if media is None or not Path(media).is_file():
            self._send(404, json.dumps({"error": t("http.no_recording")}))
            return
        media = Path(media)
        size = media.stat().st_size
        mime = AUDIO_MIME.get(media.suffix.lower(), "application/octet-stream")
        span = parse_byte_range(self.headers.get("Range"), size)

        if span == "unsatisfiable":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        start, stop = span if span else (0, size - 1)
        length = stop - start + 1
        self.send_response(206 if span else 200)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if span:
            self.send_header("Content-Range", f"bytes {start}-{stop}/{size}")
        self.end_headers()
        if head_only:
            return

        try:
            with media.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining > 0:
                    block = source.read(min(AUDIO_BLOCK, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Seeking cuts off the previous request — this is the player working normally.
            pass

    def do_HEAD(self):
        if not self._allowed():
            return
        if self.path.split("?", 1)[0] == "/audio":
            self._send_audio(head_only=True)
            return
        self._send(404, b"", "text/plain; charset=utf-8")

    def _send(self, code, body, mime="application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if not self._allowed():
            return
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            try:
                self._send(200, SCREEN.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, t("http.no_screen"), "text/plain; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/i18n.js":
            try:
                self._send(200, I18N_SCRIPT.read_bytes(), "text/javascript; charset=utf-8")
            except OSError:
                self._send(404, b"", "text/javascript; charset=utf-8")
        elif self.path in ("/ALumenans_logo.png", "/logo.png"):
            try:
                self._send(200, LOGO.read_bytes(), "image/png")
            except OSError:
                self._send(404, b"", "image/png")
        elif self.path.split("?", 1)[0] == "/audio":
            self._send_audio()
        elif self.path == "/health":
            self._send(200, json.dumps({"model": dict(MODEL_STATE)}))
        else:
            self._send(404, json.dumps({"error": t("http.not_found")}))

    def do_POST(self):
        if not self._allowed():
            return
        if self.path == "/warmup":
            request_anonymizer_warmup()
            self._send(202, json.dumps({"model": dict(MODEL_STATE)}))
            return

        if self.path == "/inflect":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                word = inflect_word(request.get("phrase") or "", request.get("gram") or {})
            except Exception:
                word = (request.get("phrase") if isinstance(request, dict) else "") or ""
            self._send(200, json.dumps({"phrase": word}, ensure_ascii=False))
            return

        if self.path == "/known-words":
            # Replacing throughout the text has to tell a proper name from
            # an ordinary word: «Морем» is a name, «морем» is the
            # instrumental case of «море» ("sea"). The Russian dictionary is
            # already installed alongside.
            length = int(self.headers.get("Content-Length") or 0)
            try:
                from alumenans.proofreading import is_known_russian_word

                request = json.loads(self.rfile.read(length).decode("utf-8"))
                words = request.get("words") or []
                known = {
                    str(word): is_known_russian_word(str(word))
                    for word in words[:400]
                    if isinstance(word, (str, int))
                }
            except Exception:
                known = {}
            self._send(200, json.dumps({"known": known}, ensure_ascii=False))
            return

        if self.path == "/guide-report":
            # The guide knows the order of the conversation. Alignment turns
            # that knowledge into an answer to the question "which of the
            # planned topics actually came up".
            length = int(self.headers.get("Content-Length") or 0)
            if length > LIMIT:
                self._send(413, json.dumps({"error": t("http.transcript_too_large")}))
                return
            try:
                from alumenans.guide.report import build_report

                request = json.loads(self.rfile.read(length).decode("utf-8"))
                guide_text = str(request.get("guide") or "").strip()
                if not guide_text:
                    self._send(200, json.dumps({"empty": True}, ensure_ascii=False))
                    return
                report = build_report(guide_text, request.get("turns") or [])
            except Exception as error:
                self._send(
                    500,
                    json.dumps(
                        {"error": str(error)[:600] or type(error).__name__},
                        ensure_ascii=False,
                    ),
                )
                return
            self._send(200, json.dumps(report, ensure_ascii=False))
            return

        if self.path == "/proofread":
            length = int(self.headers.get("Content-Length") or 0)
            if length > LIMIT:
                self._send(413, json.dumps({"error": t("http.transcript_too_large")}))
                return
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                result = analyze_proofreading(
                    request.get("turns") or [],
                    request.get("notes") or "",
                    request.get("guide") or "",
                )
            except Exception as error:
                self._send(
                    500,
                    json.dumps(
                        {"error": str(error)[:600] or type(error).__name__},
                        ensure_ascii=False,
                    ),
                )
                return
            self._send(200, json.dumps(result, ensure_ascii=False))
            return

        if self.path != "/analyze":
            self._send(404, json.dumps({"error": t("http.not_found")}))
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > LIMIT:
            self._send(413, json.dumps({"error": t("http.file_too_large")}))
            return

        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            text = request.get("text") or ""
        except Exception:
            self._send(400, json.dumps({"error": t("http.bad_request")}))
            return

        try:
            spans = find_spans(text)
        except ImportError:
            # No Natasha — serve at least the dictionary findings.
            spans = dict_spans(text)
        except Exception as error:
            self._send(500, json.dumps({"error": str(error)}))
            return

        self._send(
            200,
            json.dumps(
                {
                    "spans": spans,
                    "entities": entity_groups(text, spans),
                    "model": dict(MODEL_STATE),
                    # The pseudonym from the file name: the respondent is
                    # labelled with it everywhere they are talked about —
                    # and in the transcript itself it belongs wherever the
                    # respondent names themselves.
                    "pseudonym": current_pseudonym(),
                    # Which of the speakers is the respondent. Needed to
                    # label the turns with names rather than «Спикер 1»
                    # ("Speaker 1"): on import MAXQDA and ATLAS.ti create a
                    # code for every speaker, and the code «Спикер 1» across
                    # twenty interviews means twenty different people.
                    "respondent": respondent_speaker(text),
                    "place_kinds": PLACE_KINDS,
                },
                ensure_ascii=False,
            ),
        )


def vocative_name_spans(text):
    """Names used in address: «Миш», «Саш», «Кать» (truncated vocatives).

    The name parser knows «Мишу» (accusative of «Миша»), but not «Мишь». In
    interviews this is exactly how people call each other, and these names
    were remaining in the text after anonymisation."""

    try:
        _, pymorphy = morph_tools()
        checks = vocative_checks(pymorphy)
        return vocative_spans(
            text,
            is_vocative_form=checks[0],
            is_personal_name=checks[1],
        )
    except Exception:
        # The morphology dictionary is not required for the rest of the parse.
        return []


def current_pseudonym():
    """The respondent's pseudonym — the one in the audio file's name.
    The trailing digit belongs to the recording number, not the name, and is not included here."""

    path = AUDIO_STATE.get("paths") or []
    if not path:
        return ""
    parsed_name = parse_interview_name(path[0])
    return parsed_name.respondent if parsed_name.recognised else ""


def entity_groups(text, spans):
    """Groups findings into entities: one person — one pseudonym.

    Each finding gets an entity number; everything else — which pseudonym
    to give it, and whether to give one at all — is still up to the person."""

    try:
        from alumenans.entities import group_mentions
    except Exception:
        return []
    # A mention's key is its place in the text: the interface discards
    # overlapping findings and numbers the remaining ones its own way.
    mentions = [
        {
            "id": f"{span['start']}:{span['stop']}",
            "cat": span.get("cat"),
            "text": text[span["start"]:span["stop"]],
            "public": bool(span.get("public")),
            "self": bool(span.get("self")),
            "place_kind": span.get("place_kind") or "",
        }
        for span in spans
    ]
    try:
        entities = group_mentions(mentions, lemma=lemma_of)
    except Exception:
        return []
    public = {
        mention["id"] for mention in mentions if mention["public"]
    }
    introduced = {
        mention["id"] for mention in mentions if mention["self"]
    }
    place_kinds = {
        mention["id"]: mention["place_kind"]
        for mention in mentions
        if mention["place_kind"]
    }
    ready_items = []
    for entity in entities:
        entry = entity.as_dict()
        # One public mention is enough: if «Нужна помощь» was recognised by
        # the dictionary even once, the whole foundation is public.
        entry["public"] = any(place in public for place in entity.mentions)
        # The respondent names themselves once, but later they are referred
        # to by surname and in other cases: the whole entity needs the mark.
        entry["self"] = any(place in introduced for place in entity.mentions)
        kinds = [place_kinds[place] for place in entity.mentions if place_kinds.get(place)]
        entry["place_kind"] = kinds[0] if kinds else ""
        ready_items.append(entry)
    return ready_items


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


MODEL_STATE = {"setup": "idle", "natasha": "idle", "gliner": "idle"}
_warmup_start_lock = threading.Lock()
_warmup_thread = None


def transcriber_models_ready():
    root = TRANSCRIBER_MODELS
    return all(
        (
            (root / "asr" / "v3_e2e_ctc.int8.onnx").is_file(),
            (root / "vad" / "silero_vad.onnx").is_file(),
            (
                root
                / "embedding"
                / "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
            ).is_file(),
        )
    )


def prepare_transcriber_models(progress):
    if transcriber_models_ready():
        return None

    progress(t("launch.loading_transcription_models"))
    environment = child_environment()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    try:
        process = subprocess.Popen(
            [str(worker_python()), "-m", WORKER_MODULE, "prepare"],
            cwd=existing_directory(INSTALL_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert process.stdout is not None
        last_error = None
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "progress" and event.get("message"):
                progress(event["message"])
            elif event.get("type") == "error":
                last_error = event.get("message")
        process.stdout.close()
        return_code = process.wait()
        if return_code:
            return str(last_error or t("launch.transcription_models_failed"))[:400]
    except Exception as error:
        return str(error)[:400]

    if not transcriber_models_ready():
        return t("launch.transcription_models_incomplete")
    return None


def prepare_anonymizer_models():
    try:
        process = subprocess.run(
            [str(worker_python()), "-m", "alumenans", ANONYMIZER_SETUP_ARG],
            cwd=existing_directory(INSTALL_ROOT),
            env=child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as error:
        return str(error)[:400]
    if process.returncode:
        output = (process.stdout or "").strip()
        return output[-400:] or t("launch.anonymizer_models_failed")
    return None


def is_incomplete_build(message) -> bool:
    return str(message or "").startswith(t("launch.incomplete_build"))


def incomplete_build_message(missing):
    """What to say if packages are missing from the build.

    The program cannot and should not fetch them: everything it needs ships
    together with it. So the installation is damaged, and the only fix is to
    reinstall."""

    return (
        t("launch.incomplete_build") + ", ".join(missing) + "."
        + NL2 + t("launch.reinstall")
    )[:400]


def prepare(progress=lambda text: None, *, prepare_anonymizer=False):
    """Loads the models once, in child processes.

    No packages are installed here: they are already in the build."""
    missing = missing_packages()
    if missing:
        return incomplete_build_message(missing)

    if prepare_anonymizer:
        progress(t("launch.preparing_anonymizer"))
        error = prepare_anonymizer_models()
        if error:
            return error

    return prepare_transcriber_models(progress)


def stabilize_wallpaper():
    """Keeps the cached Windows wallpaper if the original file has been deleted."""
    if sys.platform != "win32":
        return

    try:
        import ctypes
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
            source, _ = winreg.QueryValueEx(key, "WallPaper")

        source_path = Path(os.path.expandvars(str(source).strip().strip('"')))
        if source_path.is_file():
            return

        appdata = os.environ.get("APPDATA")
        if not appdata:
            return

        cached = Path(appdata) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper"
        if not cached.is_file() or cached.stat().st_size == 0:
            return

        # SPIF_SENDWININICHANGE refreshes this session; no profile setting is written.
        ctypes.windll.user32.SystemParametersInfoW(20, 0, str(cached), 2)
    except Exception:
        pass


STARTUP_LOG = state_file(".startup.log", legacy=".запуск.log")


def _write_startup_log(lines):
    """Leaves a trace of the launch: without it, the cause of a long wait is invisible."""
    try:
        STARTUP_LOG.write_text(
            time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def prepare_launch(progress=lambda text: None):
    """Checks the installation in the launch window without importing the heavy models."""
    started = time.monotonic()
    log = []

    def note(text):
        log.append(f"{time.monotonic() - started:6.1f} s  {text}")

    progress(t("launch.checking_packages"))
    missing = missing_packages()
    note(
        "packages checked: "
        + ("MISSING " + ", ".join(missing) if missing else "all present")
    )
    if missing:
        _write_startup_log(log)
        return incomplete_build_message(missing)

    first_run = not FIRST_RUN_MARK.exists()
    models_ready = transcriber_models_ready()
    note(f"first run: {'yes' if first_run else 'no'}")
    note(f"transcription models: {'present' if models_ready else 'not found'}")

    needs_anonymizer = first_run
    note(
        "anonymization models: "
        + ("being prepared" if needs_anonymizer else "need no preparation")
    )

    if first_run or not models_ready:
        note("STARTING THE LONG PREPARATION")
        error = prepare(progress, prepare_anonymizer=needs_anonymizer)
        note("preparation finished" + (f" with an error: {error}" if error else ""))
        if error:
            _write_startup_log(log)
            return error
        try:
            FIRST_RUN_MARK.write_text("done", encoding="utf-8")
        except Exception:
            pass
    else:
        note("no long preparation needed, skipping")

    progress(t("launch.opening_window"))
    stabilize_wallpaper()
    note("ready, opening the window")
    _write_startup_log(log)
    return None


def configure_windows_identity():
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_ID
        )
    except Exception:
        pass


def set_tk_icon(window):
    if sys.platform != "win32" or not ICON.exists():
        return
    try:
        window.iconbitmap(default=str(ICON))
    except Exception:
        pass


def setup_window(status_path=None, close_path=None):
    """The preparation window, in tkinter — it ships with Python, so the
    window appears before anything is installed: the person never sees a
    console or a browser. If the window can't be created, we prepare silently."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return prepare_launch()

    try:
        root = tk.Tk()
    except Exception:
        return prepare_launch()

    root.title("ALumenans")
    set_tk_icon(root)
    root.configure(bg="#EDEEE9")
    root.resizable(False, False)
    width, height = 470, 215
    root.geometry(f"{width}x{height}"
                  f"+{(root.winfo_screenwidth()-width)//2}"
                  f"+{(root.winfo_screenheight()-height)//2}")

    tk.Label(root, text=t("launch.title"), bg="#EDEEE9", fg="#1B1E1A",
             font=("Segoe UI", 14)).pack(pady=(30, 6))
    tk.Label(root, text=t("launch.subtitle"),
             bg="#EDEEE9", fg="#5E645C", font=("Segoe UI", 9)).pack()
    step = tk.Label(root, text="", bg="#EDEEE9", fg="#1B1E1A",
                    font=("Segoe UI", 10), wraplength=410, justify="center")
    step.pack(pady=(22, 0))
    bar = ttk.Progressbar(root, mode="indeterminate", length=340)
    bar.pack(pady=(16, 0))
    bar.start(14)

    outcome = {}
    progress_state = {"text": t("launch.checking")}
    finished = threading.Event()
    shown_at = time.monotonic()

    if status_path is not None and close_path is not None:
        status_path = Path(status_path)
        close_path = Path(close_path)
        last_text = None

        def poll_external():
            nonlocal last_text
            if close_path.is_file():
                remaining = max(
                    0,
                    500 - int((time.monotonic() - shown_at) * 1000),
                )
                root.after(remaining, root.destroy)
                return
            try:
                current = status_path.read_text(encoding="utf-8").strip()
            except OSError:
                current = ""
            if current and current != last_text:
                last_text = current
                step.config(text=current)
            root.after(80, poll_external)

        root.after(50, poll_external)
        root.mainloop()
        return None

    def job():
        try:
            outcome["error"] = prepare_launch(
                lambda text: progress_state.__setitem__("text", text)
            )
        except Exception as error:
            outcome["error"] = str(error)[:400] or type(error).__name__
        finally:
            finished.set()

    def poll():
        step.config(text=progress_state["text"])
        if finished.is_set():
            remaining = max(0, 500 - int((time.monotonic() - shown_at) * 1000))
            root.after(remaining, root.destroy)
            return
        root.after(50, poll)

    root.after(50, lambda: threading.Thread(target=job, daemon=True).start())
    root.after(50, poll)
    root.mainloop()
    return outcome.get("error")


def _write_setup_status(handle, text):
    if not handle:
        return
    try:
        handle["status"].write_text(str(text), encoding="utf-8")
    except OSError:
        pass


def start_setup_window_process():
    try:
        folder = Path(tempfile.mkdtemp(prefix="alumenans-launch-"))
        status = folder / "status.txt"
        close = folder / "close"
        status.write_text(t("launch.checking"), encoding="utf-8")
        process = subprocess.Popen(
            [
                str(sys.executable),
                "-m",
                "alumenans",
                SETUP_WINDOW_ARG,
                str(status),
                str(close),
            ],
            cwd=existing_directory(INSTALL_ROOT),
            env=child_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "folder": folder,
            "status": status,
            "close": close,
            "process": process,
        }
    except Exception:
        return None


def signal_setup_window_close(handle):
    if not handle:
        return
    try:
        handle["close"].touch(exist_ok=True)
    except OSError:
        pass


def cleanup_setup_window_process(handle):
    if not handle:
        return
    signal_setup_window_close(handle)
    process = handle.get("process")
    if process is not None:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
    for key in ("status", "close"):
        try:
            handle[key].unlink(missing_ok=True)
        except OSError:
            pass
    try:
        handle["folder"].rmdir()
    except OSError:
        pass


def missing_packages():
    """Modules from REQUIRED_MODULES that are missing from this build."""
    return missing_modules()


def everything_ready():
    return not missing_packages() and transcriber_models_ready()


def warmup():
    """Loads the installed models from disk without touching the network."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    MODEL_STATE["setup"] = "models"
    try:
        tagger()
        MODEL_STATE["natasha"] = "ready"
    except Exception:
        MODEL_STATE["natasha"] = "off"
    try:
        gliner_model(local_files_only=True)
        MODEL_STATE["gliner"] = "ready"
    except Exception as error:
        MODEL_STATE["gliner"] = "off"
        MODEL_STATE["error"] = str(error)[:300]
    MODEL_STATE["setup"] = "done"


def request_anonymizer_warmup():
    global _warmup_thread
    with _warmup_start_lock:
        if MODEL_STATE.get("setup") in {"models", "done"}:
            return
        MODEL_STATE["setup"] = "models"
        MODEL_STATE["natasha"] = "loading"
        MODEL_STATE["gliner"] = "loading"
        _warmup_thread = threading.Thread(
            target=warmup,
            daemon=True,
            name="anonymizer-warmup",
        )
        _warmup_thread.start()


def prepare_anonymizer_models_cli():
    try:
        morph_tools()
        tagger()
        gliner_model()
        return 0
    except Exception as error:
        print(str(error)[:400] or type(error).__name__, flush=True)
        return 1


def _open_new_words_email_window(path, words):
    recipient = "nelepelniza@gmail.com"
    subject = "Новые слова для словаря ALumenans"
    body = (
        "Здравствуйте!\n\n"
        "Прошу добавить эти словоформы в словарь ALumenans:\n\n"
        + "\n".join(words)
    )

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MapiRecipDescW(ctypes.Structure):
                _fields_ = [
                    ("ulReserved", wintypes.ULONG),
                    ("ulRecipClass", wintypes.ULONG),
                    ("lpszName", wintypes.LPWSTR),
                    ("lpszAddress", wintypes.LPWSTR),
                    ("ulEIDSize", wintypes.ULONG),
                    ("lpEntryID", wintypes.LPVOID),
                ]

            class MapiFileDescW(ctypes.Structure):
                _fields_ = [
                    ("ulReserved", wintypes.ULONG),
                    ("flFlags", wintypes.ULONG),
                    ("nPosition", wintypes.ULONG),
                    ("lpszPathName", wintypes.LPWSTR),
                    ("lpszFileName", wintypes.LPWSTR),
                    ("lpFileType", wintypes.LPVOID),
                ]

            class MapiMessageW(ctypes.Structure):
                _fields_ = [
                    ("ulReserved", wintypes.ULONG),
                    ("lpszSubject", wintypes.LPWSTR),
                    ("lpszNoteText", wintypes.LPWSTR),
                    ("lpszMessageType", wintypes.LPWSTR),
                    ("lpszDateReceived", wintypes.LPWSTR),
                    ("lpszConversationID", wintypes.LPWSTR),
                    ("flFlags", wintypes.ULONG),
                    ("lpOriginator", wintypes.LPVOID),
                    ("nRecipCount", wintypes.ULONG),
                    ("lpRecips", ctypes.POINTER(MapiRecipDescW)),
                    ("nFileCount", wintypes.ULONG),
                    ("lpFiles", ctypes.POINTER(MapiFileDescW)),
                ]

            recipient_data = MapiRecipDescW(
                0, 1, recipient, f"SMTP:{recipient}", 0, None
            )
            file_data = MapiFileDescW(
                0, 0, 0xFFFFFFFF, str(path), path.name, None
            )
            message = MapiMessageW(
                0,
                subject,
                body,
                None,
                None,
                None,
                0,
                None,
                1,
                ctypes.pointer(recipient_data),
                1,
                ctypes.pointer(file_data),
            )
            result = ctypes.windll.mapi32.MAPISendMailW(
                0,
                0,
                ctypes.byref(message),
                0x00000008 | 0x00000001,
                0,
            )
            if result in {0, 1}:
                return
        except Exception:
            pass

        query = urllib.parse.urlencode(
            {"subject": subject, "body": body},
            quote_via=urllib.parse.quote,
        )
        os.startfile(f"mailto:{recipient}?{query}")


def _schedule_new_words_email(path, words):
    threading.Thread(
        target=_open_new_words_email_window,
        args=(path, words),
        daemon=True,
        name="new-words-email",
    ).start()


class AppApi:
    def __init__(self, transcriber, ready_callback=None):
        self.transcriber = transcriber
        self.window = None
        self.selected_media = None
        self._ready_callback = ready_callback
        self._interface_ready = False
        self._ready_lock = threading.Lock()
        self._project = None
        self._interview_id = ""
        self._project_pinned = False
        self.media_parts = []
        # Re-entrant locks: the coding methods call each other — the
        # codebook is needed both when merging and when deleting — and an
        # ordinary lock would deadlock here on its own nested call.
        self._project_lock = threading.RLock()
        self._study = None
        self._study_id = ""
        self._study_folder = None
        # The key of the open study, in memory only. None means locked.
        self._study_secret = None
        self._unreadable_projects = []
        self._study_lock = threading.RLock()

    # -- project file ----------------------------------------------------

    def _study_gate(self):
        """An error reply until a study folder is chosen and unlocked, else None.

        Every interview file is encrypted with the study's password, so
        nothing can be read or written before the study is unlocked, and
        nothing is ever written next to the recording."""

        if self._study_folder is None:
            return {"ok": False, "error": t("api.choose_study_first")}
        if self._study_secret is None:
            return {"ok": False, "error": t("api.study_locked")}
        return None

    def _project_home(self):
        """The folder where the project files live: the unlocked study's.

        Not the recording's folder: the audio file gets deleted, the work
        doesn't."""

        if self._study_folder is None or self._study_secret is None:
            return None
        return Path(self._study_folder)

    def _project_store(self, *, create=True):
        """The project file is opened on demand.

        Switching recordings must close the previous project: otherwise the
        edits to one interview would quietly end up in another one's file."""

        if self.selected_media is None:
            # The work may have been opened without an audio file: it has
            # been deleted, but proofreading goes on. There is no reason to
            # close such a project.
            if self._project is not None and self._project_pinned:
                return self._project
            self._close_project()
            return None
        home = self._project_home()
        if home is None:
            self._close_project()
            return None
        target = project_path_for(self.selected_media, home)
        if self._project is not None and self._project.path == target:
            return self._project
        self._close_project()
        if not create and not target.is_file():
            return None
        self._project = ProjectStore(target, self._study_secret)
        self._project.connect()
        return self._project

    def _close_project(self):
        if self._project is not None:
            try:
                self._project.close()
            except Exception:
                pass
        self._project = None
        self._interview_id = ""
        self._project_pinned = False

    def project_save_transcript(self, result, guide="", notes="", speaker_count=None):
        """Puts a fresh transcript into the project file next to the recording."""

        with self._project_lock:
            gate = self._study_gate()
            if gate is not None:
                return gate
            store = self._project_store()
            if store is None:
                return {"ok": False, "error": t("api.no_source_audio")}
            try:
                self._interview_id = store.save_transcript(
                    result if isinstance(result, dict) else {},
                    source_path=self.selected_media,
                    guide=str(guide or ""),
                    notes=str(notes or ""),
                    speaker_count=speaker_count,
                )
            except (ProjectError, Exception) as error:
                return {"ok": False, "error": str(error)[:400]}
            return {
                "ok": True,
                "interview_id": self._interview_id,
                "path": str(store.path),
            }

    def project_save_turns(self, turns, guide=None, notes=None):
        """Autosave for proofreading: turns, topics and context."""

        with self._project_lock:
            store = self._project_store()
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.no_project")}
            try:
                saved = store.save_turns(
                    self._interview_id,
                    turns if isinstance(turns, list) else [],
                )
                if guide is not None or notes is not None:
                    store.save_context(
                        self._interview_id,
                        guide=None if guide is None else str(guide),
                        notes=None if notes is None else str(notes),
                    )
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "turns": saved, "path": str(store.path)}

    def project_record_decision(self, decision):
        with self._project_lock:
            store = self._project_store()
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.no_project")}
            payload = decision if isinstance(decision, dict) else {}
            try:
                store.record_decision(
                    self._interview_id,
                    turn_id=str(payload.get("turn_id") or ""),
                    kind=str(payload.get("kind") or ""),
                    fragment=str(payload.get("fragment") or ""),
                    action=str(payload.get("action") or ""),
                    replacement=str(payload.get("replacement") or ""),
                )
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True}

    def _keep_key_archive(self, archive, entries):
        """Puts the encrypted key into the project — the very same archive.

        The key never goes into the project file in plain text at all: such a
        file gets passed to a colleague, and a key inside it would undo the
        anonymisation entirely."""

        with self._project_lock:
            store = self._project_store()
            if store is None or not self._interview_id:
                return False
            try:
                store.save_anonymization_key(
                    self._interview_id, archive, entries=int(entries or 0)
                )
            except Exception:
                return False
            return True

    # -- study -----------------------------------------------------------

    def _study_store(self, *, create=True):
        """The study file lives in the folder the person chose, and opens
        only once the study is unlocked."""

        folder = self._study_folder
        if folder is None or self._study_secret is None:
            return None
        target = study_path_for(folder)
        if self._study is not None and self._study.path == target:
            return self._study
        self._close_study()
        if not create and not target.is_file():
            return None
        self._study = StudyStore(target, self._study_secret)
        self._study.connect()
        self._study_id = self._study.ensure_study()
        self._refresh_subject_terms()
        return self._study

    def _close_study(self):
        if self._study is not None:
            try:
                self._study.close()
            except Exception:
                pass
        self._study = None
        self._study_id = ""
        set_subject_terms(())

    def _refresh_subject_terms(self):
        """Moves the confirmed terms to where the search for findings can see them."""

        if self._study is None or not self._study_id:
            set_subject_terms(())
            return []
        terms = self._study.subject_terms(self._study_id, confirmed_only=True)
        set_subject_terms([item["term"] for item in terms])
        return terms

    def _study_interviews(self, found):
        """The list of the series' interviews: first the work, then the recordings.

        The list used to be built from the folder's contents, and that was a
        mistake. A recording is a consumable: it is deleted once the interview
        has been processed, and the interview would vanish from the first
        screen along with the audio file, even though all the work on it lay
        intact on disk. So the basis of the list is the project files, and the
        recordings found only supplement it with the interviews that have not
        been started yet."""

        entries = {}
        order = []

        def slot(key):
            if key not in entries:
                entries[key] = {
                    "key": key,
                    "paths": [],
                    "name": "",
                    "label": "",
                    "interviewer": "",
                    "number": "",
                    "respondent": "",
                    "parts": 0,
                    "audio": False,
                    "project": "",
                    "turns": 0,
                    "edited": 0,
                    "anonymized": 0,
                    "duration": 0.0,
                }
                order.append(key)
            return entries[key]

        def describe(item, source):
            parsed = parse_interview_name(source)
            item["name"] = item["name"] or Path(str(source)).name
            item["label"] = item["label"] or parsed.label
            item["interviewer"] = item["interviewer"] or parsed.interviewer
            item["number"] = item["number"] or parsed.number
            item["respondent"] = item["respondent"] or parsed.respondent

        summaries, unreadable = project_summaries(self._study_folder, self._study_secret)
        self._unreadable_projects = [
            t("api.unreadable_interview", name=item["name"], error=item["error"])
            for item in unreadable
        ]
        for summary in summaries:
            source = summary["source_name"] or Path(summary["path"]).stem
            item = slot(parse_interview_name(source).key)
            describe(item, source)
            item["project"] = summary["path"]
            item["turns"] = summary["turns"]
            item["edited"] = summary["edited"]
            item["anonymized"] = summary["anonymized"]
            item["duration"] = summary["duration"]

        for group in group_parts(found):
            item = slot(parse_interview_name(group[0]).key)
            describe(item, group[0])
            item["paths"] = [str(path) for path in group]
            item["parts"] = len(group)
            item["audio"] = True

        def rank(key):
            item = entries[key]
            digits = re.sub(r"\D", "", item["number"])
            return (0, int(digits)) if digits else (1, 0)

        return [entries[key] for key in sorted(order, key=lambda key: (rank(key), entries[key]["label"]))]

    def study_open(self, folder=""):
        """Chooses the study folder. Without a path, opens the folder picker.

        Nothing is read or written yet: the study opens with its password,
        through study_unlock, or is started with a new one, through
        study_create. The reply says which of the two the folder needs."""

        if not folder:
            if self.window is None:
                return {"ok": False, "error": t("api.window_not_ready")}
            try:
                import webview

                selected = self.window.create_file_dialog(webview.FileDialog.FOLDER)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            if not selected:
                return {"ok": True, "cancelled": True}
            folder = self._chosen_path(selected)
        chosen = Path(folder).expanduser()
        if not chosen.is_dir():
            return {"ok": False, "error": t("api.folder_open_failed")}
        if self.transcriber.status()["status"] in {"running", "cancelling"}:
            return {"ok": False, "error": t("api.stop_transcription_first")}
        # Project first, then study: the order export_interview takes them in.
        with self._project_lock:
            self._close_project()
            self.selected_media = None
            self.media_parts = []
            set_current_audio(None)
        with self._study_lock:
            self._close_study()
            self._study_secret = None
            self._study_folder = chosen
        return {
            "ok": True,
            "cancelled": False,
            "folder": str(chosen),
            "exists": study_path_for(chosen).is_file(),
            "locked": True,
        }

    def study_unlock(self, password=""):
        """Opens the chosen study with its password."""

        with self._study_lock:
            if self._study_folder is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            target = study_path_for(self._study_folder)
            if not target.is_file():
                return {"ok": False, "error": t("study.no_study_file")}
            try:
                secret = StudySecret.for_file(target, str(password or ""))
                # A trial open: a wrong password must not leave the study
                # half-unlocked.
                cipher.connect(target, secret).close()
            except cipher.WrongPassword as error:
                return {"ok": False, "wrong_password": True, "error": str(error)}
            except cipher.CipherError as error:
                return {"ok": False, "error": str(error)}
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            self._study_secret = secret
            return self._study_payload()

    def study_create(self, password=""):
        """Starts a new study in the chosen folder, protected by a new password."""

        with self._study_lock:
            if self._study_folder is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            if study_path_for(self._study_folder).is_file():
                return {"ok": False, "error": t("study.already_exists")}
            password = str(password or "")
            if len(password) < cipher.MIN_PASSWORD_LENGTH:
                return {
                    "ok": False,
                    "error": t("study.password_too_short", n=cipher.MIN_PASSWORD_LENGTH),
                }
            self._study_secret = StudySecret.create(password)
            reply = self._study_payload()
            if not reply.get("ok"):
                self._close_study()
                self._study_secret = None
            return reply

    def _study_payload(self):
        """Everything the window shows about the unlocked study."""

        with self._study_lock:
            try:
                store = self._study_store()
                if store is None:
                    return {"ok": False, "error": t("api.folder_open_failed")}
                state = store.load_study() or {}
                found = sorted(
                    item
                    for item in self._study_folder.iterdir()
                    if item.suffix.lower() in MEDIA_SUFFIXES
                )
                recordings = self._study_interviews(found)
                # The file name already contains the series designation —
                # there is no point asking for it separately.
                if not state.get("title"):
                    guessed = study_title_from(
                        [item["name"] for item in recordings if item["name"]]
                    )
                    if guessed:
                        store.save_study(self._study_id, title=guessed)
                        state["title"] = guessed
                picked_up = self._pick_up_documents(store, state, recordings)
                # The file put together by the lead is read automatically:
                # the person has no reason to remember that it needs
                # "updating".
                if (self._study_folder / STUDY_FILE_NAME).is_file():
                    self._import_study_file(store, state)
                self._remember_folder(self._study_folder)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {
                "ok": True,
                "cancelled": False,
                "folder": str(self._study_folder),
                "path": str(store.path),
                "recordings": recordings,
                # Gaps in the numbering are not an error: a sociologist's
                # device holds only their own interviews. Interview files
                # the password doesn't open are: they may be whole
                # interviews, and must not vanish from the list silently.
                "problems": list(self._unreadable_projects),
                "title": state.get("title", ""),
                "guide": state.get("guide", ""),
                "programme": state.get("programme", ""),
                "interviews": state.get("interviews", []),
                "terms": state.get("subject_terms", []),
                "documents": store.documents(self._study_id),
                "picked_up": picked_up,
                "version": store.version(self._study_id),
                "words": store.words(self._study_id),
                # The team buttons are needed only by those working from
                # the lead's file. A solo transcription does without them.
                "team": bool(store.version(self._study_id).get("version")),
            }

    def _pick_up_documents(self, store, state, recordings):
        """Picks up from the folder what the person doesn't need to be shown again.

        The guide and the programme are shared across the study; notes are
        tied to an interview by the audio file's name. A guide that has
        already been entered is not overwritten: the person may have corrected
        it in the window, and the file in the folder does not take precedence
        here."""

        if self._study_folder is None:
            return []
        stems = [
            Path(entry["name"]).stem
            for entry in recordings
            if entry.get("name")
        ]
        try:
            names = [
                item.name
                for item in self._study_folder.iterdir()
                if item.is_file()
            ]
        except OSError:
            return []

        found = classify_documents(names, stems)
        picked_up = []

        for field in ("guide", "programme"):
            name = found[field]
            if not name or state.get(field):
                continue
            text = self._read_study_file(name)
            if not text:
                continue
            store.save_study(self._study_id, **{field: text})
            state[field] = text
            picked_up.append({"name": name, "kind": field})

        for stem, name in found["notes"].items():
            text = self._read_study_file(name)
            if not text:
                continue
            store.save_document(
                self._study_id,
                filename=name,
                text=text,
                kind="notes",
                title=stem,
                interview_id=stem,
            )
            picked_up.append({"name": name, "kind": "notes", "interview": stem})

        for document in found["documents"]:
            text = self._read_study_file(document["name"])
            if not text:
                continue
            store.save_document(
                self._study_id,
                filename=document["name"],
                text=text,
                kind=document["kind"],
            )
            picked_up.append(document)
        return picked_up

    def _read_study_file(self, name):
        try:
            return read_document(self._study_folder / name).strip()
        except Exception:
            return ""

    def study_add_document(self, kind="document"):
        """Loads a study document that lives outside the series folder."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            if self.window is None:
                return {"ok": False, "error": t("api.window_not_ready")}
            try:
                import webview

                selected = self.window.create_file_dialog(
                    webview.FileDialog.OPEN,
                    allow_multiple=True,
                    file_types=(
                        t("api.filetype_documents"),
                        t("api.filetype_all"),
                    ),
                )
                if not selected:
                    return {"ok": True, "cancelled": True}
                added = []
                for target in self._chosen_paths(selected):
                    text = read_document(target).strip()
                    if not text:
                        continue
                    store.save_document(
                        self._study_id,
                        filename=target.name,
                        text=text,
                        kind=str(kind or "document") if kind != "auto"
                        else document_kind(target.name),
                    )
                    added.append(target.name)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {
                "ok": True,
                "cancelled": False,
                "added": added,
                "documents": store.documents(self._study_id),
            }

    def study_documents(self, interview=None):
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": True, "documents": []}
            return {
                "ok": True,
                "documents": store.documents(self._study_id, interview),
            }

    def study_remove_document(self, document_id):
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": False, "error": t("api.study_not_open")}
            store.remove_document(str(document_id))
            return {"ok": True, "documents": store.documents(self._study_id)}

    # -- study dictionary ------------------------------------------------

    def study_add_word(self, word):
        """A word from the field — «задонатить» ("to donate", slang),
        «отписка» ("unsubscribing") — into the study dictionary.

        This is a recognition dictionary, not a dictionary of names: it
        silences a proofreading remark and gives the recogniser a hint, but
        it has nothing to do with anonymisation. Names and titles live in a
        different list."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            try:
                added = store.add_word(self._study_id, word)
                words = store.words(self._study_id)
            except StudyError as error:
                return {"ok": False, "error": str(error)}
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
        _refresh_study_words(words)
        return {"ok": True, "word": added, "words": words}

    def study_words(self):
        with self._study_lock:
            store = self._study_store(create=False)
            words = store.words(self._study_id) if store else []
        _refresh_study_words(words)
        return {"ok": True, "words": words}

    def study_remove_word(self, word):
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": False, "error": t("api.study_not_open")}
            store.remove_word(self._study_id, word)
            words = store.words(self._study_id)
        _refresh_study_words(words)
        return {"ok": True, "words": words}

    # -- study file from the lead --------------------------------------------

    def _import_study_file(self, store, state):
        """Reads the file put together by the lead and puts everything in its place."""

        destination = self._study_folder / STUDY_FILE_NAME
        data = read_study_file(destination)
        store.save_study(
            self._study_id,
            title=data["title"],
            guide=data["guide"],
            programme=data["programme"],
        )
        store.save_version(
            self._study_id,
            version=data["version"],
            issued_at=data["issued_at"],
            issued_by=data["issued_by"],
            inbox=data["inbox"],
        )
        for word in data["words"]:
            store.add_word(self._study_id, word, source="исследование")
        for term in data["terms"]:
            store.set_subject_term(self._study_id, term, confirmed=True)
        for document in data["documents"]:
            if document["text"]:
                store.save_document(
                    self._study_id,
                    filename=document["name"],
                    text=document["text"],
                    kind=document["kind"],
                )
        state.update(
            {
                "title": data["title"],
                "guide": data["guide"] or state.get("guide", ""),
                "programme": data["programme"] or state.get("programme", ""),
            }
        )
        self._refresh_subject_terms()
        _refresh_study_words(store.words(self._study_id))
        return data

    def study_version(self):
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": True, "version": {}}
            return {"ok": True, "version": store.version(self._study_id)}

    # -- interview export ----------------------------------------------------

    def export_interview(self):
        """Puts the finished interview into the study folder as a single file.

        The program only writes the file. It is sent by a separate script
        that the lead put in the same folder: the network is none of the
        program's business."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.interview_not_open")}
            try:
                state = store.anonymization_state(self._interview_id)
                if not state["done"]:
                    return {"ok": False, "error": state["reason"]}
                turns = store.anonymized_turns(self._interview_id)
                interview = store.load(self._interview_id) or {}
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}

        with self._study_lock:
            study_store = self._study_store(create=False)
            version = study_store.version(self._study_id) if study_store else {}
            header = (study_store.load_study() or {}) if study_store else {}
        folder = self._study_folder
        if folder is None:
            return {"ok": False, "error": t("api.no_study_folder")}

        parsed_name = parse_interview_name(
            interview.get("source_name") or (self.selected_media or "")
        )
        try:
            export = build_export(
                study={
                    "title": header.get("title", ""),
                    "version": version.get("version", ""),
                    "issued_at": version.get("issued_at", ""),
                },
                interview={
                    "key": interview_stem(
                        interview.get("source_name") or (self.selected_media or "")
                    ),
                    "study": parsed_name.study,
                    "interviewer": parsed_name.interviewer,
                    "number": parsed_name.number,
                    "respondent": parsed_name.respondent,
                    "duration": interview.get("duration") or 0.0,
                    "moderator": interview.get("moderator_speaker"),
                    "anonymized_at": state.get("anonymized_at"),
                },
                turns=turns,
                notes=interview.get("notes") or "",
                coder=self.researcher_name(),
            )
            destination = folder / export_name(export["interview"]["key"])
            destination.write_text(
                json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except ExchangeError as error:
            return {"ok": False, "error": str(error)}
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}
        return {
            "ok": True,
            "path": str(destination),
            "name": destination.name,
            "turns": len(export["turns"]),
            "inbox": version.get("inbox", ""),
        }

    def study_save_documents(self, title="", guide="", programme=""):
        """The guide and the programme belong to the study, not to an individual recording."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            try:
                store.save_study(
                    self._study_id,
                    title=str(title or ""),
                    guide=str(guide or ""),
                    programme=str(programme or ""),
                )
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "path": str(store.path)}

    def study_register(self, branch="", branch_title="", attributes=None):
        """Registers the interview in the series together with its branch and case card."""

        with self._study_lock:
            store = self._study_store()
            if store is None or self.selected_media is None:
                return {"ok": False, "error": t("api.no_recording_or_study")}
            if not self._interview_id:
                return {"ok": False, "error": t("api.no_interview_project")}
            try:
                parsed = parse_interview_name(self.selected_media)
                store.register_interview(
                    self._study_id,
                    interview_id=self._interview_id,
                    label=parsed.label,
                    source_name=Path(self.selected_media).name,
                    source_path=str(self.selected_media),
                    project_path=str(
                        project_path_for(self.selected_media, self._project_home())
                    ),
                    duration=probe_media_duration(self.selected_media) or 0.0,
                )
                if branch:
                    store.save_branch(self._interview_id, branch, branch_title)
                if attributes:
                    store.save_case_attributes(self._interview_id, attributes)
                interviews = store.interviews(self._study_id)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "interviews": interviews}

    LAST_FOLDER = state_file(".last-folder", legacy=".последняя-папка")

    def _remember_folder(self, folder):
        """The study folder is chosen once, not on every launch."""

        try:
            self.LAST_FOLDER.write_text(str(folder), encoding="utf-8")
        except OSError:
            pass

    def last_study_folder(self):
        """The study opened last time, offered again at the next launch.

        Only a folder that still holds a study: an empty or vanished one is
        nothing to reopen."""

        try:
            path = self.LAST_FOLDER.read_text(encoding="utf-8").strip()
        except OSError:
            path = ""
        if not path or not study_path_for(path).is_file():
            return {"ok": True, "folder": ""}
        return {"ok": True, "folder": path, "name": Path(path).name}

    def researcher_name(self):
        path = RESEARCHER_FILE
        try:
            return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        except OSError:
            return ""

    def settings(self, researcher=None):
        """The researcher's name: future codes and memos will be signed with it."""

        path = RESEARCHER_FILE
        try:
            if researcher is not None:
                path.write_text(str(researcher).strip()[:80], encoding="utf-8")
            current = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        except OSError as error:
            return {"ok": False, "error": str(error)[:200]}
        return {"ok": True, "researcher": current}

    def study_terms(self):
        """What has been suggested and what has been confirmed as the subject of the study."""

        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": True, "terms": [], "study": ""}
            try:
                terms = store.subject_terms(self._study_id)
                self._refresh_subject_terms()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "terms": terms, "study": str(store.path)}

    def study_suggest_terms(self, guide="", programme=""):
        """Collects candidates from the guide and the programme. Confirms nothing."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_recording_first")}
            try:
                store.save_study(
                    self._study_id,
                    guide=str(guide or ""),
                    programme=str(programme or ""),
                )
                store.suggest_subject_terms(
                    self._study_id,
                    suggest_from_documents(str(guide or ""), str(programme or "")),
                )
                terms = store.subject_terms(self._study_id)
                self._refresh_subject_terms()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "terms": terms}

    def study_set_term(self, term, confirmed):
        """Confirms protection for a single term, or removes it."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_recording_first")}
            try:
                store.set_subject_term(
                    self._study_id, str(term or ""), confirmed=bool(confirmed)
                )
                terms = self._refresh_subject_terms()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "protected": [item["term"] for item in terms]}

    def study_remove_term(self, term):
        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_recording_first")}
            try:
                store.remove_subject_term(self._study_id, str(term or ""))
                self._refresh_subject_terms()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True}

    def project_load(self):
        """Returns unfinished work on the selected recording, if there is any."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None:
                return {"ok": True, "state": None}
            try:
                state = store.load()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            if state:
                self._interview_id = state["interview_id"]
            return {"ok": True, "state": state, "path": str(store.path)}

    def project_mark_anonymized(self, left=0, turns=None):
        """Marks the anonymisation as finished.

        Along with the mark comes the anonymised text, turn by turn: that is
        what gets coded, not the original."""

        with self._project_lock:
            store = self._project_store()
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.no_project")}
            try:
                if isinstance(turns, list) and turns:
                    store.save_anonymized_turns(self._interview_id, turns)
                store.mark_anonymized(self._interview_id, left=left)
                state = store.anonymization_state(self._interview_id)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "state": state}

    def coding_state(self):
        """Whether this interview may be coded.

        Coding a non-anonymised transcript spreads real names across tables,
        reports and exchange files — and from there they can no longer be
        gathered back. So the ban comes before coding, not after."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {
                    "ok": True,
                    "allowed": False,
                    "reason": t("api.interview_not_open"),
                }
            try:
                state = store.anonymization_state(self._interview_id)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {
                "ok": True,
                "allowed": bool(state["done"]),
                "reason": state["reason"],
                "state": state,
            }

    # -- coding ----------------------------------------------------------

    def coding_open(self):
        """Everything the coding screen needs, in one request.

        The anonymised text is used: that is what gets coded, quotes go off
        into reports and tables, and a real name can no longer be pulled back
        out of there. The codebook is shared across the series — codes are
        created once per study, not afresh in every interview."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {"ok": True, "allowed": False, "reason": t("api.interview_not_open")}
            try:
                state = store.anonymization_state(self._interview_id)
                turns = store.anonymized_turns(self._interview_id)
                codings = store.codings(self._interview_id)
                tally = store.coding_counts(self._interview_id)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}

        codes = self._codes(tally)
        reason = state["reason"]
        allowed = bool(state["done"]) and bool(turns)
        if state["done"] and not turns:
            reason = t("api.no_coding_text")
        return {
            "ok": True,
            "allowed": allowed,
            "reason": reason,
            "turns": turns,
            "moderator": self._moderator_speaker(),
            "codings": codings,
            "codes": codes,
            "counts": tally,
        }

    def cleanup_preview(self):
        """What would be left of the non-anonymised text if it were removed.

        Shown as a list before deletion: deletion is irreversible, and our
        own rule "we never change anything silently" matters most of all
        here — this is the most silent change the program could possibly
        make."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {"ok": True, "ready": False, "files": [], "inside": {}}
            try:
                state = store.anonymization_state(self._interview_id)
                inside = store.original_text_traces(self._interview_id)
                ready = bool(state["done"]) and bool(
                    store.anonymized_turns(self._interview_id)
                )
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}

        files = [str(path) for path in self._own_leftovers()]
        # Words don't count: they are not deleted but transplanted onto the
        # anonymised text, and after the cleanup they stay where they are.
        remaining = bool(files) or bool(inside["turns"]) or bool(inside["decisions"])
        return {
            "ok": True,
            "ready": ready,
            "left": remaining,
            "files": files,
            "inside": inside,
        }

    def _own_leftovers(self):
        """Files of this interview that the program saved itself."""

        folder = self._study_folder or (
            Path(self.selected_media).parent if self.selected_media else None
        )
        bases = [Path(path).stem for path in (AUDIO_STATE.get("paths") or [])]
        if self.selected_media:
            bases.append(Path(self.selected_media).stem)
        return unanonymised_files(folder, dict.fromkeys(bases))

    def cleanup_run(self):
        """Removes the non-anonymised text. Leaves the audio file alone."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.interview_not_open")}
            try:
                state = store.anonymization_state(self._interview_id)
                if not state["done"]:
                    return {"ok": False, "error": state["reason"]}
                if not store.anonymized_turns(self._interview_id):
                    return {
                        "ok": False,
                        "error": t("api.cannot_forget_original"),
                    }
                removed = store.forget_original_text(self._interview_id)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}

        deleted = []
        for path in self._own_leftovers():
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError:
                continue
        return {"ok": True, "removed": deleted, "inside": removed}

    def _moderator_speaker(self):
        """Who asks the questions. Their turns are visible, but cannot be coded."""

        store = self._project_store(create=False)
        if store is None or not self._interview_id:
            return None
        try:
            state = store.load(self._interview_id) or {}
        except Exception:
            return None
        value = state.get("moderator_speaker")
        return int(value) if value is not None else None

    def _codes(self, counts=None):
        """The codebook in the form the window expects it.

        Only what fits into json goes through the bridge to the window — so
        the codes become dictionaries here instead of staying objects."""

        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return []
            try:
                codes = store.codes(self._study_id)
            except Exception:
                return []
        count = counts or {}
        return [code.as_dict(uses=int(count.get(code.id, 0))) for code in codes]

    def coding_add(self, marking):
        """Marks a piece of a turn with a code.

        A refusal comes with a reason rather than silently: the interviewer's
        question or non-anonymised text cannot be coded, and the person
        must understand why the button didn't work."""

        data = marking if isinstance(marking, dict) else {}
        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.interview_not_open")}
            try:
                state = store.anonymization_state(self._interview_id)
                if not state["done"]:
                    return {"ok": False, "error": state["reason"]}
                turns = {
                    turn["id"]: turn
                    for turn in store.anonymized_turns(self._interview_id)
                }
                turn = turns.get(str(data.get("turn_id") or ""))
                if turn is None:
                    return {
                        "ok": False,
                        "error": t("api.turn_not_codable"),
                    }
                moderator = self._moderator_speaker()
                if moderator is not None and turn["speaker"] == moderator:
                    return {
                        "ok": False,
                        "error": t("api.interviewer_turn"),
                    }
                identifier = store.save_coding(
                    self._interview_id,
                    turn_id=turn["id"],
                    start=int(data.get("start") or 0),
                    end=int(data.get("end") or 0),
                    code_id=str(data.get("code_id") or ""),
                    memo=str(data.get("memo") or ""),
                    coder=str(data.get("coder") or ""),
                    coding_id=str(data.get("id") or ""),
                )
                return {
                    "ok": True,
                    "id": identifier,
                    "codings": store.codings(self._interview_id),
                    "counts": store.coding_counts(self._interview_id),
                }
            except ProjectError as error:
                return {"ok": False, "error": str(error)}
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}

    def coding_remove(self, coding_id):
        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.interview_not_open")}
            try:
                store.remove_coding(str(coding_id))
                return {
                    "ok": True,
                    "codings": store.codings(self._interview_id),
                    "counts": store.coding_counts(self._interview_id),
                }
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}

    def code_save(self, name, definition="", code_id=""):
        """Creates a code in the study's codebook.

        Nesting is expressed with a dot in the name — «деньги.нехватка»
        ("money.shortage"): no separate tree had to be set up, and a whole
        branch can be renamed in a single action."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            try:
                identifier = store.save_code(
                    self._study_id,
                    str(name or ""),
                    definition=str(definition or ""),
                    origin="рука",
                    code_id=str(code_id or ""),
                )
            except StudyError as error:
                return {"ok": False, "error": str(error)}
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "id": identifier, "codes": self._codes()}

    def code_rename(self, code_id, name):
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": False, "error": t("api.study_not_open")}
            try:
                store.rename_code(self._study_id, str(code_id), str(name or ""))
            except StudyError as error:
                return {"ok": False, "error": str(error)}
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
        return {"ok": True, "codes": self._codes()}

    def code_merge(self, source_id, target_id):
        """Merges two codes and carries over the markup already made."""

        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": False, "error": t("api.study_not_open")}
            try:
                store.merge_codes(self._study_id, str(source_id), str(target_id))
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
        with self._project_lock:
            project = self._project_store(create=False)
            moved = 0
            if project is not None and self._interview_id:
                try:
                    moved = project.move_codings(
                        self._interview_id, str(source_id), str(target_id)
                    )
                except Exception:
                    moved = 0
            return {
                "ok": True,
                "codes": self._codes(),
                "moved": moved,
                "codings": project.codings(self._interview_id) if project else [],
                "counts": project.coding_counts(self._interview_id) if project else {},
            }

    def code_remove(self, code_id):
        """Removes a code. The markup made with it has to be removed too."""

        with self._project_lock:
            project = self._project_store(create=False)
            if project is not None and self._interview_id:
                try:
                    for coding in project.codings(
                        self._interview_id, code_id=str(code_id)
                    ):
                        project.remove_coding(coding["id"])
                except Exception:
                    pass
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": False, "error": t("api.study_not_open")}
            try:
                store.remove_code(self._study_id, str(code_id))
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
        with self._project_lock:
            project = self._project_store(create=False)
            return {
                "ok": True,
                "codes": self._codes(),
                "codings": project.codings(self._interview_id) if project else [],
                "counts": project.coding_counts(self._interview_id) if project else {},
            }

    def project_open(self, path=""):
        """Opens the work on an interview whose recording no longer exists.

        The team has no intention of keeping a corpus of audio: a recording
        is deleted as soon as the interview has been processed. The
        transcript, the proofreading decisions and the anonymisation key
        survive the deletion, and the person must be able to get to them
        without the audio file — otherwise there is no point in keeping them."""

        target = Path(str(path or "")).expanduser()
        gate = self._study_gate()
        if gate is not None:
            return gate
        with self._project_lock:
            if not target.is_file():
                return {"ok": False, "error": t("api.work_file_not_found")}
            # The key belongs to the study: a work file from elsewhere won't
            # open with it, and would be saved outside the study.
            if target.parent.resolve() != Path(self._study_folder).resolve():
                return {"ok": False, "error": t("api.work_outside_study")}
            if self.transcriber.status()["status"] in {"running", "cancelling"}:
                return {"ok": False, "error": t("api.stop_transcription_first")}
            self.transcriber.reset()
            self._close_project()
            self.selected_media = None
            self.media_parts = []
            set_current_audio(None)
            try:
                self._project = ProjectStore(target, self._study_secret)
                self._project.connect()
                state = self._project.load()
            except Exception as error:
                self._close_project()
                return {"ok": False, "error": str(error)[:400]}
            self._project_pinned = True
            if state:
                self._interview_id = state["interview_id"]
            return {"ok": True, "state": state, "path": str(target), "audio": False}

    def bind_window(self, window):
        self.window = window

    def get_ui_language(self):
        """The interface language, and the choices, for the language switch."""

        return {"ok": True, "language": i18n.get_language(), "languages": list(i18n.LANGUAGES)}

    def set_ui_language(self, code):
        """Switches the interface language and remembers it.

        Only the interface: the recording language, the models and the
        pseudonyms written into transcripts do not depend on it."""

        try:
            return {"ok": True, "language": i18n.set_language(code)}
        except ValueError as error:
            return {"ok": False, "error": str(error)[:400]}

    def mark_interface_ready(self):
        with self._ready_lock:
            if self._interface_ready:
                return {"ok": True}
            self._interface_ready = True
        try:
            if self.window is not None:
                self.window.show()
            if self._ready_callback is not None:
                self._ready_callback()
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}

    def choose_media(self, path="", parts=None):
        """Chooses a recording. With a path — directly; without one — via a dialog.

        The path comes from the series list: hunting for the file in a dialog
        when it is already shown on screen is needless work."""

        gate = self._study_gate()
        if gate is not None:
            return gate
        if self.transcriber.status()["status"] in {"running", "cancelling"}:
            return {"ok": False, "error": t("api.stop_current_transcription_first")}
        if path:
            try:
                chosen = validate_media_path(path)
                file_parts = [validate_media_path(item) for item in (parts or [])]
            except TranscriptionError as error:
                return {"ok": False, "error": str(error)}
            return self._use_media(chosen, file_parts or self._siblings(chosen))

        if self.window is None:
            return {"ok": False, "error": t("api.window_not_ready")}
        try:
            import webview

            selected = self.window.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=str(self._study_folder),
                allow_multiple=False,
                file_types=(
                    t("api.filetype_media"),
                    t("api.filetype_all"),
                ),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            chosen = validate_media_path(self._chosen_path(selected))
        except TranscriptionError as error:
            return {"ok": False, "error": str(error)}
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}
        return self._use_media(chosen, self._siblings(chosen))

    def _siblings(self, path):
        """Other parts of the same interview lying alongside.

        The person picks a file, not a recording: they click «Татьяна1»
        ("Tatiana1") meaning the whole conversation. The program finds the
        second part itself — that is exactly why the file name contains a
        number."""

        neighbours = [
            item
            for item in sorted(Path(path).parent.glob("*"))
            if item.suffix.lower() in MEDIA_SUFFIXES
        ]
        key = parse_interview_name(path).key
        for group in group_parts(neighbours):
            if parse_interview_name(group[0]).key == key:
                return group
        return [Path(path)]

    def _use_media(self, path, parts=None):
        gate = self._study_gate()
        if gate is not None:
            return gate
        self.transcriber.reset()
        with self._project_lock:
            self._close_project()
        # The parts of an interrupted interview are all taken at once: it is
        # one conversation, and transcribing it in halves means getting two
        # different «Спикеров 1» ("Speaker 1"s) and turn numbering starting
        # over.
        paths = [Path(item) for item in (parts or [path])]
        if path not in paths:
            paths.insert(0, path)
        self.selected_media = paths[0]
        self.media_parts = paths
        file_parts = audio_parts(paths)
        parsed = parse_interview_name(paths[0])
        return {
            "ok": True,
            "cancelled": False,
            "name": paths[0].name,
            "label": parsed.label,
            "interviewer": parsed.interviewer,
            "respondent": parsed.respondent,
            "path": str(paths[0]),
            "size": sum(item.stat().st_size for item in paths),
            "extension": paths[0].suffix.lower()[1:].upper(),
            "duration": sum(item["duration"] for item in file_parts),
            "audio_token": set_current_audio(paths),
            "parts": file_parts,
            "project": str(project_path_for(paths[0], self._project_home())),
        }

    def start_transcription(self, speaker_count=2, guide=""):
        gate = self._study_gate()
        if gate is not None:
            return gate
        if self.selected_media is None:
            return {"ok": False, "error": t("api.choose_media_first")}
        file_parts = self.media_parts or [self.selected_media]
        try:
            requested_speakers = validate_speaker_count(speaker_count)
        except TranscriptionError as error:
            return {"ok": False, "error": str(error)}
        return self.transcriber.start(
            file_parts,
            requested_speakers,
            str(guide or ""),
        )

    def get_transcription_status(self):
        return self.transcriber.status()

    def cancel_transcription(self):
        return self.transcriber.cancel()

    def reset_transcription(self):
        result = self.transcriber.reset()
        if result.get("ok"):
            self.selected_media = None
            set_current_audio(None)
            with self._project_lock:
                self._close_project()
        return result

    @staticmethod
    def _chosen_paths(selected):
        """Paths from the system file-picker window.

        pywebview answers in different ways: the save dialog returns a single
        string, the file or folder picker returns a sequence. A string is
        easily mistaken for a sequence, and then the first element turns out
        to be the first character of the path. On the Mac that is "/", and
        the program tries to write to the root of the disk."""

        if not selected:
            return []
        if isinstance(selected, (str, bytes, os.PathLike)):
            return [Path(os.fsdecode(selected))]
        return [Path(os.fsdecode(path)) for path in selected]

    @classmethod
    def _chosen_path(cls, selected):
        paths = cls._chosen_paths(selected)
        return paths[0] if paths else None

    @staticmethod
    def _safe_text_name(suggested_name):
        safe_name = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]',
            "_",
            Path(str(suggested_name)).name,
        )
        safe_name = safe_name.strip(" .") or "расшифровка.txt"
        if not safe_name.lower().endswith(".txt"):
            safe_name += ".txt"
        return safe_name

    def save_text(self, suggested_name, content):
        if self.window is None:
            return {"ok": False, "error": t("api.window_not_ready")}
        safe_name = self._safe_text_name(suggested_name)

        try:
            import webview

            selected = self.window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=safe_name,
                file_types=(t("api.filetype_text"), t("api.filetype_all")),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            destination = self._chosen_path(selected)
            destination.write_text(str(content), encoding="utf-8")
            return {
                "ok": True,
                "cancelled": False,
                "name": destination.name,
                "path": str(destination),
            }
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}

    def save_docx(self, suggested_name, content, title=""):
        """Saves the finished transcript as a document.

        Built with the standard library: a .docx is a zip with XML, and it is
        not worth pulling in yet another package for it."""

        if self.window is None:
            return {"ok": False, "error": t("api.window_not_ready")}
        safe_name = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]', "_", Path(str(suggested_name)).name
        ).strip(" .") or "расшифровка"
        if not safe_name.lower().endswith(".docx"):
            safe_name = re.sub(r"\.txt$", "", safe_name, flags=re.IGNORECASE) + ".docx"

        try:
            import webview

            from alumenans.docx_export import save_transcript

            selected = self.window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=safe_name,
                file_types=(t("api.filetype_docx"), t("api.filetype_all")),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            destination = self._chosen_path(selected)
            if destination.suffix.lower() != ".docx":
                destination = destination.with_suffix(".docx")
            save_transcript(destination, str(content), str(title or ""))
            return {
                "ok": True,
                "cancelled": False,
                "name": destination.name,
                "path": str(destination),
            }
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}

    def save_key_archive(self, suggested_name, content, password, entries=0):
        """The anonymisation key exists only under a password.

        The archive is an ordinary zip: 7-Zip, WinRAR or the program itself
        will open it. The very same encrypted copy goes into the project
        file — not as a backup, but because project files are passed from
        hand to hand, and an unprotected key has no place in them. The
        password is stored nowhere: forget it, and the key cannot be opened."""

        if self.window is None:
            return {"ok": False, "error": t("api.window_not_ready")}
        if not str(password or ""):
            return {"ok": False, "error": t("api.key_needs_password")}

        safe_name = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]', "_", Path(str(suggested_name)).name
        ).strip(" .") or "КЛЮЧ"
        inner_name = safe_name if safe_name.lower().endswith(".txt") else safe_name + ".txt"
        archive_name = re.sub(r"\.txt$", "", inner_name, flags=re.IGNORECASE) + ".zip"

        try:
            import webview

            from alumenans.storage.secret_zip import make_secret_zip

            selected = self.window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=archive_name,
                file_types=(t("api.filetype_zip"), t("api.filetype_all")),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            destination = self._chosen_path(selected)
            if destination.suffix.lower() != ".zip":
                destination = destination.with_suffix(".zip")
            archive = make_secret_zip(inner_name, str(content), str(password))
            destination.write_bytes(archive)
            self._keep_key_archive(archive, entries)
            return {
                "ok": True,
                "cancelled": False,
                "name": destination.name,
                "path": str(destination),
            }
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}

    def open_new_words_email(self, words):
        if self.selected_media is None:
            return {
                "ok": False,
                "error": t("api.no_audio"),
            }
        cleaned = []
        seen = set()
        for value in words if isinstance(words, list) else []:
            word = re.sub(r"\s+", " ", str(value)).strip()
            key = word.casefold()
            if not word or key in seen:
                continue
            seen.add(key)
            cleaned.append(word[:160])
        if not cleaned:
            return {"ok": False, "error": t("api.no_new_words")}

        destination = Path(self.selected_media).parent / "Новые слова.txt"
        try:
            destination.write_text(
                "\n".join(sorted(cleaned, key=str.casefold)) + "\n",
                encoding="utf-8",
            )
            _schedule_new_words_email(destination, cleaned)
            return {"ok": True, "path": str(destination)}
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}


def open_window(url, api, *, hidden=False):
    """Opens the interface only in the program's own window."""
    try:
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            "ALumenans",
            url,
            js_api=api,
            width=1360,
            height=900,
            min_size=(900, 600),
            hidden=hidden,
            # Without this the window forbids selecting text: that is
            # pywebview's default. In proofreading it goes unnoticed — the
            # fields there are editable — but in anonymisation the text is
            # read-only, and manual markup via the right mouse button didn't
            # work at all: there was nothing to select.
            text_select=True,
        )
        api.bind_window(window)
        webview.start(icon=str(ICON) if ICON.exists() else None)
        return None
    except Exception as error:
        return str(error)[:400] or type(error).__name__


def show_error(message):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        set_tk_icon(root)
        messagebox.showerror("ALumenans", message)
        root.destroy()
    except Exception:
        pass


def main():
    # Before anything that opens a window or downloads models: CI runs this
    # on the installed program (see self_test.py).
    if len(sys.argv) > 1 and sys.argv[1] == SELF_TEST_ARG:
        from alumenans.self_test import run as run_self_test

        return run_self_test()
    if len(sys.argv) > 1 and sys.argv[1] == ANONYMIZER_SETUP_ARG:
        return prepare_anonymizer_models_cli()
    if len(sys.argv) > 3 and sys.argv[1] == SETUP_WINDOW_ARG:
        setup_window(Path(sys.argv[2]), Path(sys.argv[3]))
        return 0

    configure_windows_identity()

    if not SCREEN.exists():
        show_error(t("launch.no_screen"))
        return 1

    # The preparation window lives separately and stays visible until the
    # hidden main window confirms that the Python bridge is ready.
    setup_handle = start_setup_window_process()
    if setup_handle is None:
        error = setup_window()
    else:
        error = prepare_launch(
            lambda text: _write_setup_status(setup_handle, text)
        )
    if error:
        cleanup_setup_window_process(setup_handle)
        # Only the models need the internet; it won't fix an incomplete build.
        hint = (
            ""
            if is_incomplete_build(error)
            else NL2 + t("launch.check_internet")
        )
        show_error(t("launch.prepare_failed") + NL2 + error + hint)
        return 1

    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    transcriber = TranscriptionManager()
    api = AppApi(
        transcriber,
        ready_callback=lambda: signal_setup_window_close(setup_handle),
    )
    window_error = open_window(
        f"http://127.0.0.1:{port}/?lang={i18n.get_language()}&{SESSION_PARAMETER}={SESSION_SECRET}",
        api,
        hidden=setup_handle is not None,
    )
    cleanup_setup_window_process(setup_handle)
    transcriber.shutdown()
    server.shutdown()
    server.server_close()
    if window_error:
        show_error(t("launch.window_failed") + NL2 + window_error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
