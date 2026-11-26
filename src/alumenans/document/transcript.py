"""The transcript: an immutable value made of turns, which are made of words.

A word is a run of non-space characters, punctuation included ("Hello,"),
with the time at which it is heard. A turn is a run of words by one speaker.
A turn's text is its words joined by single spaces, so the text and the
words are the same information and neither can drift from the other.

Nothing here changes a transcript. `alumenans.document.editing` builds new
ones; unchanged turns are shared between the old value and the new.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import Iterable, Mapping, Sequence

WordId = int
TurnId = int

# A run of consecutive words, given by the first and the last (inclusive).
# This is where an annotation applies.
Span = tuple[WordId, WordId]


class Author(Enum):
    """Who last changed a word's letters."""

    SPEECH_MODEL = "speech_model"
    CLEANUP = "cleanup"
    HUMAN = "human"


@dataclass(frozen=True, slots=True)
class Word:
    id: WordId
    text: str
    start: float
    end: float
    # The speech model's own estimate; None once the letters were retyped.
    confidence: float | None = None
    author: Author = Author.SPEECH_MODEL
    # The machine words this one descends from; empty if typed from nothing.
    origin: tuple[WordId, ...] = ()


@dataclass(frozen=True, slots=True)
class Turn:
    id: TurnId
    speaker: int
    words: tuple[Word, ...]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return max(word.end for word in self.words)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


@dataclass(frozen=True)
class Transcript:
    """Turns in order of start time.

    Identifiers are issued from the two counters and never reused, so an
    identifier seen once always means the same word or turn."""

    turns: tuple[Turn, ...] = ()
    revision: int = 0
    next_word_id: WordId = 1
    next_turn_id: TurnId = 1

    @cached_property
    def positions(self) -> dict[WordId, tuple[int, int]]:
        """Where each word is: (index of its turn, index inside the turn)."""

        return {
            word.id: (turn_index, word_index)
            for turn_index, turn in enumerate(self.turns)
            for word_index, word in enumerate(turn.words)
        }

    @cached_property
    def turn_indexes(self) -> dict[TurnId, int]:
        return {turn.id: index for index, turn in enumerate(self.turns)}

    def turn(self, turn_id: TurnId) -> Turn:
        return self.turns[self.turn_indexes[turn_id]]

    def word(self, word_id: WordId) -> Word:
        turn_index, word_index = self.positions[word_id]
        return self.turns[turn_index].words[word_index]

    def words(self) -> Iterable[Word]:
        for turn in self.turns:
            yield from turn.words


# ---------------------------------------------------------------------------
# Words as text
# ---------------------------------------------------------------------------


def letters(text: str) -> str:
    """The letters and digits of a word, lowercased: what is heard.

    "Hello," and "hello" have the same letters. Used to tell a change of
    case or punctuation from a change of the word itself, and to weigh
    words when time is divided among them. Works by Unicode category, so
    it is the same for every alphabet."""

    return "".join(character for character in text if character.isalnum()).casefold()


def bare(text: str) -> str:
    """The word without the punctuation at its edges: "Hello," → "Hello"."""

    first = 0
    last = len(text)
    while first < last and not text[first].isalnum():
        first += 1
    while last > first and not text[last - 1].isalnum():
        last -= 1
    return text[first:last]


def word_ranges(turn: Turn) -> tuple[tuple[int, int], ...]:
    """Each word's character range in the turn's text."""

    ranges = []
    position = 0
    for word in turn.words:
        ranges.append((position, position + len(word.text)))
        position += len(word.text) + 1
    return tuple(ranges)


def words_in_range(turn: Turn, start: int, stop: int) -> tuple[int, int]:
    """The words that a character range of the turn's text touches.

    Returns (index of the first word, number of words). The range is
    widened to whole words. A range that touches no word, such as a caret
    sitting on a space, gives a count of 0 and the index of the word that
    follows it."""

    ranges = word_ranges(turn)
    if start >= stop:
        # A caret: it touches a word only when it is strictly inside it.
        for index, (left, right) in enumerate(ranges):
            if left < start < right:
                return index, 1
            if left >= start:
                return index, 0
        return len(ranges), 0
    first = sum(1 for _, right in ranges if right <= start)
    last = sum(1 for left, _ in ranges if left < stop)
    return first, max(0, last - first)


# ---------------------------------------------------------------------------
# Spans and time
# ---------------------------------------------------------------------------


def span_words(transcript: Transcript, span: Span) -> tuple[Word, ...] | None:
    """The words of a span, in order; None if either end no longer exists
    or the ends are the wrong way round.

    A span normally lies inside one turn. After a turn was split inside it,
    its words continue into the following turn."""

    first = transcript.positions.get(span[0])
    last = transcript.positions.get(span[1])
    if first is None or last is None or last < first:
        return None
    found: list[Word] = []
    for turn_index in range(first[0], last[0] + 1):
        words = transcript.turns[turn_index].words
        low = first[1] if turn_index == first[0] else 0
        high = last[1] + 1 if turn_index == last[0] else len(words)
        found.extend(words[low:high])
    return tuple(found)


def span_time(transcript: Transcript, span: Span) -> tuple[float, float] | None:
    """From when to when a span is heard."""

    words = span_words(transcript, span)
    if not words:
        return None
    return min(word.start for word in words), max(word.end for word in words)


def word_at(transcript: Transcript, seconds: float) -> Word | None:
    """The word being spoken at a moment, or the last one begun before it.

    When turns overlap, the turn that started last wins."""

    starts = [turn.start for turn in transcript.turns]
    latest = bisect_right(starts, seconds) - 1
    if latest < 0:
        return None
    chosen = latest
    for index in range(latest, -1, -1):
        if transcript.turns[index].end >= seconds:
            chosen = index
            break
    words = transcript.turns[chosen].words
    position = bisect_right([word.start for word in words], seconds) - 1
    return words[max(0, position)]


def occurrences(transcript: Transcript, form: str) -> list[WordId]:
    """Every word with the same letters as `form`, whatever its case and
    punctuation."""

    wanted = letters(form)
    return [word.id for word in transcript.words() if letters(word.text) == wanted]


def changed_turns(old: Transcript, new: Transcript) -> tuple[list[TurnId], list[TurnId]]:
    """What a view has to redraw after an edit: the turns of `new` that are
    new or different, and the turns of `old` that are gone.

    Unchanged turns are the very same objects in both, so this compares
    identity and does not look at the words."""

    before = {turn.id: turn for turn in old.turns}
    changed = [turn.id for turn in new.turns if before.get(turn.id) is not turn]
    removed = [turn.id for turn in old.turns if turn.id not in new.turn_indexes]
    return changed, removed


def time_is_measured(word: Word, machine: Transcript) -> bool:
    """Whether the word still has the time the speech model gave it, as
    opposed to one computed after an edit. `machine` is the transcript as
    it came from transcription."""

    if len(word.origin) != 1 or word.origin[0] not in machine.positions:
        return False
    heard = machine.word(word.origin[0])
    return heard.start == word.start and heard.end == word.end


# ---------------------------------------------------------------------------
# Building and checking
# ---------------------------------------------------------------------------


def in_start_order(turns: Iterable[Turn]) -> tuple[Turn, ...]:
    """Turns by start time; those that start together keep their order."""

    return tuple(sorted(turns, key=lambda turn: turn.start))


def from_machine_turns(turns: Sequence[Mapping]) -> Transcript:
    """The transcript as transcription returned it.

    Takes the turns of the transcription result: each with `speaker` and
    `words`, each word with `text`, `start`, `end` and optionally
    `confidence`. Turns without words are dropped. A word entry that holds
    several words is split, and its time divided by letter count."""

    built: list[Turn] = []
    word_id = 1
    for raw_turn in turns:
        words: list[Word] = []
        floor = 0.0
        for raw in raw_turn.get("words") or ():
            texts = str(raw.get("text") or "").split()
            if not texts:
                continue
            start = max(floor, _seconds(raw.get("start")))
            end = max(start, _seconds(raw.get("end")))
            confidence = raw.get("confidence")
            confidence = None if confidence is None else float(confidence)
            for text, (left, right) in zip(texts, divide_time(start, end, texts)):
                words.append(
                    Word(word_id, text, left, right, confidence, Author.SPEECH_MODEL, (word_id,))
                )
                word_id += 1
                floor = left
        if words:
            built.append(Turn(len(built) + 1, int(raw_turn.get("speaker") or 1), tuple(words)))
    return Transcript(
        turns=in_start_order(built),
        next_word_id=word_id,
        next_turn_id=len(built) + 1,
    )


def divide_time(start: float, end: float, texts: Sequence[str]) -> list[tuple[float, float]]:
    """Shares an interval among words in proportion to their letter count.

    A word without letters or digits, such as a dash, is not heard: it gets
    no duration and sits where the word before it ended."""

    weights = [len(letters(text)) for text in texts]
    total = sum(weights)
    shares = []
    position = start
    spent = 0
    for weight in weights:
        spent += weight
        if not total:
            right = start
        elif spent == total:
            right = end  # exactly, not to within rounding
        else:
            right = start + (end - start) * spent / total
        shares.append((position, right))
        position = right
    return shares


def _seconds(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


class InvalidTranscript(ValueError):
    """A transcript breaks one of the rules it is built on. Always a bug."""


def validate(transcript: Transcript) -> None:
    """Raises InvalidTranscript unless every rule of the structure holds."""

    seen_words: set[WordId] = set()
    seen_turns: set[TurnId] = set()
    previous_start = -math.inf
    for turn in transcript.turns:
        if turn.id in seen_turns:
            raise InvalidTranscript(f"turn {turn.id} appears twice")
        if not 0 < turn.id < transcript.next_turn_id:
            raise InvalidTranscript(f"turn {turn.id} was not issued by this transcript")
        seen_turns.add(turn.id)
        if not turn.words:
            raise InvalidTranscript(f"turn {turn.id} has no words")
        word_start = -math.inf
        for word in turn.words:
            if word.id in seen_words:
                raise InvalidTranscript(f"word {word.id} appears twice")
            if not 0 < word.id < transcript.next_word_id:
                raise InvalidTranscript(f"word {word.id} was not issued by this transcript")
            seen_words.add(word.id)
            if not word.text or word.text.split() != [word.text]:
                raise InvalidTranscript(f"word {word.id} has text {word.text!r}")
            if not (math.isfinite(word.start) and math.isfinite(word.end)):
                raise InvalidTranscript(f"word {word.id} has no time")
            if word.end < word.start:
                raise InvalidTranscript(f"word {word.id} ends before it starts")
            if word.start < word_start:
                raise InvalidTranscript(f"word {word.id} starts before the word in front of it")
            word_start = word.start
        if turn.start < previous_start:
            raise InvalidTranscript(f"turn {turn.id} starts before the turn in front of it")
        previous_start = turn.start
