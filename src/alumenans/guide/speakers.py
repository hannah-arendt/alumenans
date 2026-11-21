#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The guide as a way to check whom a turn is attributed to.

Telling voices apart relies on sound alone. It has a blind spot: a short
turn at the boundary between two speakers sounds smeared, and it is easy
to hand it to the neighbour. The guide knows nothing about sound, but it
knows the content: if a turn matches a question from the guide almost
word for word, the interviewer said it, whatever the voice analysis
decided.

The value of this sign lies precisely in its independence. Two errors
rarely coincide: where the acoustics get confused, the text is usually
confident, and vice versa.

The caution here is the same as everywhere in this program. The guide
alone corrects nothing: it only points out suspicious places, and a
decision is made only when the sound agrees with it too. If there is
nothing to check against by sound, the turn stays as it was and is
simply flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Sequence

from alumenans.guide.parse import Guide, parse_guide, similarity
from alumenans.guide.align import align_guide


# How confident a match with a guide question must be before we doubt the
# voice labelling at all. Weak matches won't do here: the question
# «а почему?» ("and why?") resembles half the guide.
ANCHOR_LEVEL = 0.45

# How much closer to the interviewer's voice a turn must be before it is
# reassigned. The threshold is high on purpose: silently spoiling correct
# labelling is worse than leaving one error in place.
VOICE_MARGIN = 0.08


@dataclass(frozen=True)
class SpeakerFix:
    """One correction or suspicion."""

    turn_index: int
    was: int
    became: int
    score: float
    applied: bool
    reason: str

    @property
    def text(self) -> str:
        return (
            f"Реплика {self.turn_index}: голос {self.was} → {self.became}"
            if self.applied
            else f"Реплика {self.turn_index}: голос {self.was} под вопросом"
        )


def _turn_dicts(turns: Sequence) -> list[dict]:
    result = []
    for turn in turns:
        result.append(
            {
                "start": float(getattr(turn, "start", 0.0)),
                "end": float(getattr(turn, "end", 0.0)),
                "speaker": int(getattr(turn, "speaker", 1)),
                "text": str(getattr(turn, "text", "")),
            }
        )
    return result


def find_speaker_doubts(
    guide: Guide | str,
    turns: Sequence,
) -> tuple[int | None, list[tuple[int, float]]]:
    """Who the interviewer is, and which of their turns are attributed to
    someone else.

    Returns the interviewer's voice number and a list of suspicious turns:
    those that confidently matched a guide question but are credited to
    someone else."""

    if isinstance(guide, str):
        guide = parse_guide(guide)
    rows = _turn_dicts(turns)
    if not rows or not guide.everything:
        return None, []

    alignment = align_guide(guide, rows)
    moderator = alignment.moderator
    if moderator is None:
        return None, []

    # Alignment works only with the interviewer's turns, so by construction
    # its findings contain no attribution errors. We have to look in the
    # rest: that is exactly where whatever diarisation gave to the wrong
    # voice lies.
    taken = {
        match.item.key
        for match in alignment.matches
        if match.covered and match.score >= ANCHOR_LEVEL
    }
    unused = [
        item
        for item in guide.items_for(alignment.branch)
        if item.is_question and item.key not in taken
    ]
    if not unused:
        return moderator, []

    doubts = []
    for index, row in enumerate(rows):
        if row["speaker"] == moderator or not row["text"].strip():
            continue
        best = max(
            (similarity(item.text, row["text"]) for item in unused),
            default=0.0,
        )
        if best >= ANCHOR_LEVEL:
            doubts.append((index, best))
    return moderator, doubts


def correct_speakers(
    guide: Guide | str,
    turns: Sequence,
    *,
    voice_check: Callable[[int, int, int], float | None] | None = None,
) -> tuple[list, int | None, list[SpeakerFix]]:
    """Corrects attribution where the guide and the sound agree.

    ``voice_check(turn_index, current_speaker, moderator)`` must return how
    much closer the turn is to the interviewer's voice than to the current
    one: above zero means closer. ``None`` means "nothing to check with";
    then no correction is made and the place stays flagged.
    """

    moderator, doubts = find_speaker_doubts(guide, turns)
    if moderator is None or not doubts:
        return list(turns), moderator, []

    updated = list(turns)
    fixes: list[SpeakerFix] = []
    for index, score in doubts:
        current = int(getattr(updated[index], "speaker", 1))
        margin = None if voice_check is None else voice_check(index, current, moderator)
        if margin is None:
            fixes.append(
                SpeakerFix(
                    turn_index=index,
                    was=current,
                    became=moderator,
                    score=score,
                    applied=False,
                    reason="совпало с гайдом, но проверить голосом нечем",
                )
            )
            continue
        if margin < VOICE_MARGIN:
            fixes.append(
                SpeakerFix(
                    turn_index=index,
                    was=current,
                    became=moderator,
                    score=score,
                    applied=False,
                    reason="совпало с гайдом, но голос против",
                )
            )
            continue
        updated[index] = replace(updated[index], speaker=moderator)
        fixes.append(
            SpeakerFix(
                turn_index=index,
                was=current,
                became=moderator,
                score=score,
                applied=True,
                reason="совпало с гайдом, и голос согласен",
            )
        )
    return updated, moderator, fixes
