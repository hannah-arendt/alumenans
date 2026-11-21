#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The interview guide as a structure, not a bag of words.

The guide used to be used in two ways: names were pulled from it for the
proofreading dictionary, and it was used to try to guess who the
interviewer was. Yet the guide knows much more: it knows the order of the
conversation, the planned time of each block, and that the interview may
go down one of several branches.

The parsing is built around three observations about how guides are
actually written.

First: question numbers are unreliable. In a real guide it is easy to
find two questions «9.» in a row: they were edited and not renumbered. So
the number is kept here for reference only, and a question's identity is
given by a separate key.

Second: not everything between the questions is said out loud. Under a
question there may be a list of prompts for the interviewer themselves,
and at the start there is text read out to the respondent. These are
different things, and their coverage cannot be measured the same way.

Third: the fork. If the guide has branches, exactly one of them is
followed in a given interview. A report announcing "missed" questions
from the two branches not taken is noise that devalues the whole report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from alumenans.i18n import t


# A number, a dot or parenthesis, a space: that is what a question looks
# like.
QUESTION_LINE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+(\S.*)$")

# A block heading almost always carries the planned time:
# «Вступление (2-3 мин)» ("Introduction (2-3 min)").
SECTION_TIME = re.compile(
    r"^(?P<title>.*?)\s*\(\s*(?P<from>\d{1,3})\s*(?:[-–—]\s*(?P<to>\d{1,3}))?\s*"
    r"мин[а-яё]*\.?\s*\)\s*$"
)

# A branch: one letter, a dot, a title. Latin and Cyrillic are mixed: in
# the same guide «A» is Latin, «В» is Russian, «C» is Latin again.
BRANCH_LINE = re.compile(
    r"^\s*([A-ZА-ЯЁ])\s*[.)]\s+(\S.{0,80})$"
)

FORK_LINE = re.compile(r"^\s*развилк[а-яё]*\s*:?\s*$", re.IGNORECASE)

WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-'’][0-9A-Za-zА-Яа-яЁё]+)*")

# Function words: a question always contains them, and they say nothing
# about a match.
STOPWORDS = {
    "а", "бы", "быть", "в", "вам", "вас", "ваш", "ваша", "ваше", "ваши",
    "вот", "вы", "где", "да", "для", "до", "его", "ее", "её", "если",
    "есть", "ещё", "еще", "же", "за", "и", "из", "или", "им", "их", "как",
    "какой", "какая", "какие", "когда", "кто", "ли", "мне", "может",
    "можно", "мы", "на", "не", "нет", "но", "ну", "о", "об", "он", "она",
    "они", "от", "по", "под", "потому", "при", "про", "с", "себя",
    "сейчас", "со", "так", "такой", "там", "то", "тоже", "только", "ты",
    "у", "уже", "чем", "что", "чтобы", "это", "этот", "я",
}


@dataclass(frozen=True)
class GuideItem:
    """A piece of the guide that may be said in the interview.

    ``kind`` distinguishes things measured differently: ``question`` is a
    question, ``script`` is text read out loud (consent, introduction).
    """

    key: str
    kind: str
    text: str
    section: str
    branch: str = ""
    number: int | None = None
    prompts: tuple[str, ...] = ()

    @property
    def is_question(self) -> bool:
        return self.kind == "question"


@dataclass(frozen=True)
class GuideSection:
    title: str
    branch: str = ""
    planned_from: float | None = None
    planned_to: float | None = None

    @property
    def planned_seconds(self) -> tuple[float, float] | None:
        if self.planned_from is None:
            return None
        upper = self.planned_to if self.planned_to is not None else self.planned_from
        return (self.planned_from * 60.0, upper * 60.0)


@dataclass(frozen=True)
class GuideBranch:
    key: str
    title: str
    items: tuple[GuideItem, ...] = ()


@dataclass
class Guide:
    """A parsed guide: the common part plus, possibly, several branches."""

    sections: tuple[GuideSection, ...] = ()
    common: tuple[GuideItem, ...] = ()
    branches: tuple[GuideBranch, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def has_fork(self) -> bool:
        return len(self.branches) > 1

    def items_for(self, branch_key: str = "") -> list[GuideItem]:
        """Everything that should be said if the interview took this branch."""

        items = list(self.common)
        for branch in self.branches:
            if branch.key == branch_key:
                items.extend(branch.items)
        return items

    def section(self, title: str) -> GuideSection | None:
        return next((item for item in self.sections if item.title == title), None)

    @property
    def questions(self) -> list[GuideItem]:
        return [item for item in self.everything if item.is_question]

    @property
    def everything(self) -> list[GuideItem]:
        items = list(self.common)
        for branch in self.branches:
            items.extend(branch.items)
        return items


def content_words(text: str) -> list[str]:
    """Words worth comparing by. Case and ё are not distinguished."""

    words = []
    for match in WORD.finditer(str(text or "")):
        word = match.group(0).lower().replace("ё", "е")
        if len(word) < 3 or word in STOPWORDS:
            continue
        words.append(word)
    return words


STEM_LENGTH = 4

# Parentheses in the guide are almost always a note for the interviewer
# themselves: «(Если человеку некомфортно…)» ("If the person is
# uncomfortable…"), «(Далее напоминалка для нас…)» ("Next, a reminder for
# us…"). This is not said out loud, and there are more words in it than in
# the question itself.
ASIDE = re.compile(r"\([^)]*\)")


def _stem(word: str) -> str:
    """The start of the word instead of an attempt to cut off the ending.

    A real interviewer doesn't read the question but paraphrases it: «как
    вы про них узнали» ("how did you learn about them") turns into «как ты
    узнаёшь» ("how do you find out"). No ending-stripping will bring these
    forms together, but the first four letters do. The coarseness is
    deliberate: falsely matching words with similar roots is cheaper here
    than a miss."""

    return word[:STEM_LENGTH]


def _weights(words: Iterable[str]) -> dict[str, float]:
    """Word weight: the longer and rarer it is, the more a match means."""

    result: dict[str, float] = {}
    for word in words:
        stem = _stem(word)
        # Short words are almost always function words, even if not listed.
        result[stem] = max(result.get(stem, 0.0), 1.0 if len(word) >= 6 else 0.6)
    return result


def matchable_text(text: str) -> str:
    """Guide text without the notes in parentheses."""

    return ASIDE.sub(" ", str(text or ""))


def similarity(left: str, right: str) -> float:
    """To what extent the guide question was said in the turn.

    The measure is asymmetric: it counts the share of the question found in
    the turn, not the similarity of two texts. The interviewer adds caveats
    and explanations to the question, and the match shouldn't drop because
    of that.
    """

    guide_weights = _weights(content_words(matchable_text(left)))
    spoken = {_stem(word) for word in content_words(right)}
    if not guide_weights or not spoken:
        return 0.0
    total = sum(guide_weights.values())
    shared = sum(
        weight for stem, weight in guide_weights.items() if stem in spoken
    )
    if not shared:
        return 0.0
    covered = shared / total
    # A small correction for turn length: in a very long monologue a couple
    # of matching words will turn up by chance.
    density = min(1.0, shared / max(3.0, len(spoken) * 0.35))
    return 0.8 * covered + 0.2 * density


def _looks_like_prompt(line: str) -> bool:
    """A reminder line: short, no addressing verb, not a question."""

    stripped = line.strip().strip("-–—•*").strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return False
    if len(stripped) > 110:
        return False
    return True


def parse_guide(text: str) -> Guide:
    """Parses the guide into blocks, questions and branches."""

    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    sections: list[GuideSection] = []
    common: list[GuideItem] = []
    branches: list[GuideBranch] = []

    current_section = ""
    current_branch = ""
    branch_titles: dict[str, str] = {}
    branch_items: dict[str, list[GuideItem]] = {}
    after_fork = False

    pending_question: dict | None = None
    script_lines: list[str] = []
    counter = 0

    def target() -> list[GuideItem]:
        if current_branch:
            return branch_items.setdefault(current_branch, [])
        return common

    def flush_question() -> None:
        nonlocal pending_question
        if pending_question is None:
            return
        target().append(
            GuideItem(
                key=pending_question["key"],
                kind="question",
                text=pending_question["text"].strip(),
                section=pending_question["section"],
                branch=pending_question["branch"],
                number=pending_question["number"],
                prompts=tuple(pending_question["prompts"]),
            )
        )
        pending_question = None

    def flush_script() -> None:
        nonlocal script_lines
        body = " ".join(part.strip() for part in script_lines if part.strip())
        script_lines = []
        if len(body) < 40:
            return
        nonlocal counter
        counter += 1
        target().append(
            GuideItem(
                key=f"script-{counter}",
                kind="script",
                text=body,
                section=current_section,
                branch=current_branch,
            )
        )

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if FORK_LINE.match(line):
            flush_question()
            flush_script()
            after_fork = True
            continue

        branch_match = BRANCH_LINE.match(line) if after_fork else None
        if branch_match and not QUESTION_LINE.match(line):
            flush_question()
            flush_script()
            key = branch_match.group(1)
            title_part = branch_match.group(2)
            timed = SECTION_TIME.match(title_part)
            title = (timed.group("title") if timed else title_part).strip()
            current_branch = key
            current_section = title
            branch_titles[key] = title
            branch_items.setdefault(key, [])
            sections.append(
                GuideSection(
                    title=title,
                    branch=key,
                    planned_from=float(timed.group("from")) if timed else None,
                    planned_to=(
                        float(timed.group("to"))
                        if timed and timed.group("to")
                        else None
                    ),
                )
            )
            continue

        question_match = QUESTION_LINE.match(line)
        if question_match:
            flush_question()
            flush_script()
            counter += 1
            pending_question = {
                "key": f"q{counter}",
                "number": int(question_match.group(1)),
                "text": question_match.group(2),
                "section": current_section,
                "branch": current_branch,
                "prompts": [],
            }
            continue

        timed = SECTION_TIME.match(line)
        if timed and not pending_question:
            flush_script()
            current_section = timed.group("title").strip() or current_section
            sections.append(
                GuideSection(
                    title=current_section,
                    branch=current_branch,
                    planned_from=float(timed.group("from")),
                    planned_to=(
                        float(timed.group("to")) if timed.group("to") else None
                    ),
                )
            )
            continue
        if timed and pending_question:
            # The heading of the next block right after a question.
            flush_question()
            current_section = timed.group("title").strip() or current_section
            sections.append(
                GuideSection(
                    title=current_section,
                    branch=current_branch,
                    planned_from=float(timed.group("from")),
                    planned_to=(
                        float(timed.group("to")) if timed.group("to") else None
                    ),
                )
            )
            continue

        if pending_question is not None:
            # An unclosed parenthesis means the note simply wrapped onto the
            # next line. It belongs to the question, not to the prompt list.
            current = pending_question["text"]
            if current.count("(") > current.count(")"):
                pending_question["text"] += " " + line
                continue
            # Otherwise a line under a question is a reminder for the
            # interviewer.
            if _looks_like_prompt(line) or (len(line) < 110 and not line.endswith("?")):
                pending_question["prompts"].append(line.strip("-–—•* ").strip())
            else:
                pending_question["text"] += " " + line
            continue

        script_lines.append(line)

    flush_question()
    flush_script()

    for key, items in branch_items.items():
        branches.append(
            GuideBranch(key=key, title=branch_titles.get(key, key), items=tuple(items))
        )

    result = Guide(
        sections=tuple(sections),
        common=tuple(common),
        branches=tuple(sorted(branches, key=lambda item: item.key)),
    )
    return Guide(
        sections=result.sections,
        common=result.common,
        branches=result.branches,
        problems=tuple(_guide_problems(result)),
    )


def _guide_problems(guide: Guide) -> list[str]:
    """Remarks about the guide itself.

    The program doesn't fix them and they don't affect parsing: the
    question number here is for reference anyway. But it is useful for
    the person editing the guide before the next series of interviews to
    know about them."""

    problems = []
    scopes: dict[str, list[GuideItem]] = {"": list(guide.common)}
    for branch in guide.branches:
        scopes[branch.key] = list(branch.items)

    for key, items in scopes.items():
        seen: dict[int, int] = {}
        for item in items:
            if item.number is None:
                continue
            seen[item.number] = seen.get(item.number, 0) + 1
        repeated = sorted(number for number, count in seen.items() if count > 1)
        if repeated:
            numbers = ", ".join(str(number) for number in repeated)
            problems.append(
                t("guide.repeated_numbers_branch", branch=key, numbers=numbers)
                if key
                else t("guide.repeated_numbers_common", numbers=numbers)
            )

    if not guide.questions:
        problems.append(t("guide.no_questions"))
    return problems
