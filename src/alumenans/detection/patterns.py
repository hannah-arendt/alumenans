"""Findings that need no model: patronymics, given names, ages, dates of birth."""

import re

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
