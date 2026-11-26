"""Weeding out false findings: speech noise, function words, generic organisations."""

import re

from alumenans.detection.morphology import morph_tools
from alumenans.detection.patterns import GIVEN_NAMES
from alumenans.detection.subject import is_subject_fragment

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
