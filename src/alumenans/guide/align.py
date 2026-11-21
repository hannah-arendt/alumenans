#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aligning the guide onto the transcript.

The program used to ask about each turn: "does it resemble some question
from the guide?" That question is easy to fool: the turn «а почему?» ("and
why?") resembles half the guide at once. An interview mostly follows the
guide in order, and that knowledge is worth using: what should be aligned
is not a turn onto a question but the whole guide sequence onto the whole
sequence of turns.

This immediately gives three things we didn't have.

Who the interviewer is. Not from one lucky match, but from the accumulated
score of the whole alignment: for the real interviewer it is many times
higher.

Which branch happened. A fork in the guide means exactly one branch is
followed in the interview; aligning each of them and comparing the scores
shows which one.

Anchors. Turns about which we are almost certain that the interviewer is
reading the guide. They can be handed to diarisation as labelled samples.

A separate caveat about order: monotonic alignment, by construction,
cannot find reorderings; it simply doesn't consider them. So for the
questions that found no place in the main thread a second pass is made
over the whole recording. If such a question is found, but earlier or
later than its place, it is not a miss but a reordering, and it has to
be talked about differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from alumenans.guide.parse import Guide, GuideItem, content_words, parse_guide, similarity


# Below this a match doesn't count as a match: almost any pair of turns
# has a chance overlap of two or three words.
MATCH_FLOOR = 0.30
# A confident match: such turns are fit to be anchors for diarisation.
ANCHOR_LEVEL = 0.50
# An unplanned question: a long questioning turn by the interviewer that
# didn't fit any item of the guide.
UNPLANNED_WORDS = 6
# The place in the conversation is a strong argument, but not the only one.
# A turn that has nothing at all in common with the question must not be
# slotted in on position alone: between two questions the interviewer also
# says a lot of unrelated things.
SOFT_FLOOR = 0.08


@dataclass(frozen=True)
class ItemMatch:
    key: str
    item: GuideItem
    turn_index: int | None = None
    time: float | None = None
    score: float = 0.0
    out_of_order: bool = False
    likely: bool = False
    merged_with: str = ""

    @property
    def covered(self) -> bool:
        return self.turn_index is not None

    @property
    def state(self) -> str:
        if not self.covered:
            return "не прозвучал"
        if self.merged_with:
            return "вместе с соседним"
        if self.out_of_order:
            return "не по порядку"
        return "вероятно" if self.likely else "прозвучал"


@dataclass
class Alignment:
    moderator: int | None = None
    branch: str = ""
    branch_title: str = ""
    matches: tuple[ItemMatch, ...] = ()
    unplanned: tuple[dict, ...] = ()
    anchors: tuple[dict, ...] = ()
    score: float = 0.0
    confidence: float = 0.0

    @property
    def covered(self) -> list[ItemMatch]:
        return [match for match in self.matches if match.covered]

    @property
    def missing(self) -> list[ItemMatch]:
        return [match for match in self.matches if not match.covered]


def _speaker_turns(turns: Sequence[dict], speaker: int) -> list[tuple[int, dict]]:
    return [
        (index, turn)
        for index, turn in enumerate(turns)
        if int(turn.get("speaker") or 0) == speaker
    ]


def _align_sequences(
    items: Sequence[GuideItem],
    spoken: Sequence[tuple[int, dict]],
) -> tuple[list[tuple[int, int, float]], float]:
    """Needleman-Wunsch: the largest sum of matches that preserves order.

    Gaps are not penalised. The interviewer says a lot that isn't in the
    guide, and part of the guide is never said at all; penalising that
    would force the alignment to invent matches where there are none.
    """

    rows, columns = len(items), len(spoken)
    if not rows or not columns:
        return [], 0.0

    weights = [
        [
            max(0.0, similarity(item.text, str(turn.get("text") or "")) - MATCH_FLOOR)
            for _, turn in spoken
        ]
        for item in items
    ]

    table = [[0.0] * (columns + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        weight_row = weights[row - 1]
        current, previous = table[row], table[row - 1]
        for column in range(1, columns + 1):
            gain = weight_row[column - 1]
            diagonal = previous[column - 1] + gain if gain > 0 else -1.0
            current[column] = max(previous[column], current[column - 1], diagonal)

    pairs: list[tuple[int, int, float]] = []
    row, column = rows, columns
    while row > 0 and column > 0:
        gain = weights[row - 1][column - 1]
        if gain > 0 and abs(table[row][column] - (table[row - 1][column - 1] + gain)) < 1e-9:
            pairs.append((row - 1, column - 1, gain + MATCH_FLOOR))
            row -= 1
            column -= 1
        elif abs(table[row][column] - table[row - 1][column]) < 1e-9:
            row -= 1
        else:
            column -= 1
    pairs.reverse()
    return pairs, table[rows][columns]


def _rescue_out_of_order(
    item: GuideItem,
    spoken: Sequence[tuple[int, dict]],
    taken: set[int],
) -> tuple[int, float] | None:
    """Searches the whole recording for a question that didn't fit the main
    thread."""

    best = None
    for position, (_, turn) in enumerate(spoken):
        if position in taken:
            continue
        score = similarity(item.text, str(turn.get("text") or ""))
        if score < MATCH_FLOOR + 0.12:
            continue
        if best is None or score > best[1]:
            best = (position, score)
    return best


def _fill_gaps(
    matches: list[ItemMatch],
    items: Sequence[GuideItem],
    spoken: Sequence[tuple[int, dict]],
    taken: set[int],
) -> list[ItemMatch]:
    """Places the questions the interviewer paraphrased in their own words.

    «Как вы про них узнали?» ("How did you learn about them?") comes out as
    «хочется узнать, как ты узнаёшь о тех фондах» ("I'd like to know how
    you find out about those foundations"): there are almost no shared
    words, and such a question can never be caught by similarity. But the
    alignment knows its place: if the neighbouring questions were found at
    turns 5 and 25, this one can only have been said between them. If the
    gap contains unassigned interviewer turns, the question is most likely
    there; if the gap is empty, it really wasn't asked.

    Such findings are flagged separately and never passed off as confident.
    """

    positions = {
        match.key: position
        for position, (turn_index, _) in enumerate(spoken)
        for match in matches
        if match.turn_index == turn_index
    }
    result = list(matches)

    index = 0
    while index < len(result):
        if result[index].covered:
            index += 1
            continue
        run_start = index
        while index < len(result) and not result[index].covered:
            index += 1
        run_end = index  # the first covered one after the gap

        left = next(
            (
                positions.get(result[step].key, -1)
                for step in range(run_start - 1, -1, -1)
                if result[step].covered
            ),
            -1,
        )
        right = next(
            (
                positions.get(result[step].key, len(spoken))
                for step in range(run_end, len(result))
                if result[step].covered
            ),
            len(spoken),
        )
        window = [
            position
            for position in range(left + 1, min(right, len(spoken)))
            if position not in taken
        ]
        if not window:
            continue

        for step in range(run_start, run_end):
            item = result[step]
            if not item.item.is_question or not window:
                continue
            best_position, best_score = None, SOFT_FLOOR
            for position in window:
                _, turn = spoken[position]
                score = similarity(item.item.text, str(turn.get("text") or ""))
                if score > best_score:
                    best_position, best_score = position, score
            if best_position is None:
                continue
            turn_index, turn = spoken[best_position]
            result[step] = ItemMatch(
                key=item.key,
                item=item.item,
                turn_index=turn_index,
                time=float(turn.get("start") or 0.0),
                score=best_score,
                likely=True,
            )
            taken.add(best_position)
            window = [value for value in window if value > best_position]
    return result


def _align_variant(
    items: Sequence[GuideItem],
    spoken: Sequence[tuple[int, dict]],
) -> tuple[list[ItemMatch], float, set[int]]:
    pairs, score = _align_sequences(items, spoken)
    by_item = {item_index: (position, gain) for item_index, position, gain in pairs}
    taken = {position for _, position, _ in pairs}

    matches: list[ItemMatch] = []
    for index, item in enumerate(items):
        found = by_item.get(index)
        out_of_order = False
        if found is None and item.is_question:
            rescued = _rescue_out_of_order(item, spoken, taken)
            if rescued is not None:
                found = rescued
                taken.add(rescued[0])
                out_of_order = True
        if found is None:
            matches.append(ItemMatch(key=item.key, item=item))
            continue
        position, gain = found
        turn_index, turn = spoken[position]
        matches.append(
            ItemMatch(
                key=item.key,
                item=item,
                turn_index=turn_index,
                time=float(turn.get("start") or 0.0),
                score=gain,
                out_of_order=out_of_order,
            )
        )
    matches = _fill_gaps(matches, items, spoken, taken)
    matches = _merge_neighbours(matches, spoken)
    return matches, score, taken


# A match too weak to be a finding on its own, but enough to accept that
# the question was asked inside a neighbouring turn.
MERGED_FLOOR = 0.17


def _merge_neighbours(
    matches: list[ItemMatch],
    spoken: Sequence[tuple[int, dict]],
) -> list[ItemMatch]:
    """Several guide questions asked in a single turn.

    A real interviewer often glues neighbouring questions together:
    «приведи примеры… и на что ты обращаешь внимание, что тебя цепляет?»
    ("give some examples… and what do you pay attention to, what hooks
    you?") is three guide items at once. Counting two of them as missed is
    wrong."""

    by_turn = {
        match.turn_index: match
        for match in matches
        if match.covered and not match.likely
    }
    result = list(matches)
    for index, match in enumerate(result):
        if match.covered or not match.item.is_question:
            continue
        neighbours = []
        for step in (index - 1, index + 1):
            if 0 <= step < len(result) and result[step].covered:
                neighbours.append(result[step])
        best = None
        for neighbour in neighbours:
            turn = next(
                (
                    candidate
                    for turn_index, candidate in spoken
                    if turn_index == neighbour.turn_index
                ),
                None,
            )
            if turn is None:
                continue
            score = similarity(match.item.text, str(turn.get("text") or ""))
            if score >= MERGED_FLOOR and (best is None or score > best[1]):
                best = (neighbour, score)
        if best is None:
            continue
        neighbour, score = best
        result[index] = ItemMatch(
            key=match.key,
            item=match.item,
            turn_index=neighbour.turn_index,
            time=neighbour.time,
            score=score,
            merged_with=neighbour.key,
        )
    return result


def align_guide(
    guide: Guide | str,
    turns: Sequence[dict],
    *,
    moderator: int | None = None,
) -> Alignment:
    """Determines the interviewer, the branch, and where each guide item
    falls in the recording."""

    if isinstance(guide, str):
        guide = parse_guide(guide)
    turns = [turn for turn in turns if isinstance(turn, dict)]
    if not turns or not guide.everything:
        return Alignment()

    speakers = (
        [moderator]
        if moderator
        else sorted({int(turn.get("speaker") or 1) for turn in turns})
    )
    branch_keys = [branch.key for branch in guide.branches] or [""]

    results = []
    for speaker in speakers:
        spoken = _speaker_turns(turns, speaker)
        if not spoken:
            continue
        for branch in branch_keys:
            items = guide.items_for(branch)
            matches, score, taken = _align_variant(items, spoken)
            results.append((score, speaker, branch, matches, spoken, taken))

    if not results:
        return Alignment()

    results.sort(key=lambda item: item[0], reverse=True)
    score, speaker, branch, matches, spoken, taken = results[0]

    # How confidently the interviewer was chosen: how many times their score
    # exceeds that of the best of the other voices.
    other = max(
        (item[0] for item in results if item[1] != speaker),
        default=0.0,
    )
    confidence = 1.0 if not other else max(0.0, 1.0 - other / score) if score else 0.0

    unplanned = []
    for position, (turn_index, turn) in enumerate(spoken):
        if position in taken:
            continue
        text = str(turn.get("text") or "")
        if "?" not in text or len(content_words(text)) < UNPLANNED_WORDS:
            continue
        unplanned.append(
            {
                "turn_index": turn_index,
                "time": float(turn.get("start") or 0.0),
                "text": text,
            }
        )

    anchors = [
        {
            "turn_index": match.turn_index,
            "start": float(turns[match.turn_index].get("start") or 0.0),
            "end": float(turns[match.turn_index].get("end") or 0.0),
            "score": match.score,
        }
        for match in matches
        if match.covered and match.score >= ANCHOR_LEVEL
    ]

    branch_title = next(
        (item.title for item in guide.branches if item.key == branch), ""
    )
    return Alignment(
        moderator=speaker,
        branch=branch,
        branch_title=branch_title,
        matches=tuple(matches),
        unplanned=tuple(unplanned),
        anchors=tuple(anchors),
        score=score,
        confidence=confidence,
    )
