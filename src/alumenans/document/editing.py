"""Changing a transcript: four edits, and free retyping of a turn's text.

Every function here returns a new transcript and leaves the old one as it
was. With it comes a `Mapping` that says what happened to the words, so that
anything attached to them (a decision, a code) can follow the edit.

The rules for words, when a run of n old words becomes m new ones:

* n = m: the words keep their identity and their time. A word whose letters
  changed loses the speech model's confidence; a change of case or
  punctuation alone does not.
* n ≠ m, both at least 1: the new words are new. They share the time from
  the start of the first old word to the end of the last, in proportion to
  their letter count.
* m = 0: the words are removed and their time becomes a gap.
* n = 0: the new words take the silence between their neighbours if there is
  enough of it; otherwise one neighbour shares its time with them.

So an edit never moves the time of a word outside the run it replaced, with
the one exception of that neighbour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Sequence, assert_never

from alumenans.document.transcript import (
    Author,
    Span,
    Transcript,
    Turn,
    TurnId,
    Word,
    WordId,
    divide_time,
    in_start_order,
    letters,
    span_words,
)

# Silence between two words that is long enough to hold a word typed in
# between them, in seconds.
MINIMUM_SILENCE = 0.15


class EditError(ValueError):
    """The edit does not fit the transcript it was applied to."""


@dataclass(frozen=True, slots=True)
class ReplaceWords:
    """Puts `texts` in place of `count` words of a turn, from index `first`.

    The only edit that changes text. `count` may be 0 (insert before the
    word at `first`) and `texts` may be empty (delete). A turn left without
    words is removed."""

    turn: TurnId
    first: int
    count: int
    texts: tuple[str, ...]
    author: Author = Author.HUMAN


@dataclass(frozen=True, slots=True)
class SplitTurn:
    """Cuts a turn in two before the word at index `at`. The first part
    keeps the turn's identifier; the second gets a new one."""

    turn: TurnId
    at: int


@dataclass(frozen=True, slots=True)
class MergeTurns:
    """Joins a turn with the one after it, which must be by the same
    speaker. The result keeps the first turn's identifier."""

    turn: TurnId


@dataclass(frozen=True, slots=True)
class SetSpeaker:
    turn: TurnId
    speaker: int


Edit = ReplaceWords | SplitTurn | MergeTurns | SetSpeaker


@dataclass(frozen=True)
class Mapping:
    """What an edit did to the words that were there before it.

    `replaced`: each word that no longer exists, with the words that took
    its place (none if it was deleted). `retyped`: words that kept their
    identity but whose letters changed. A word in neither is untouched."""

    replaced: dict[WordId, tuple[WordId, ...]] = field(default_factory=dict)
    retyped: frozenset[WordId] = frozenset()


def follow_span(old: Transcript, mapping: Mapping, span: Span) -> Span | None:
    """Where a span of the transcript before an edit is after it.

    Replaced words are followed to the words that took their place. None
    if every word of the span was deleted, or the span was not valid."""

    words = span_words(old, span)
    if not words:
        return None
    after: list[WordId] = []
    for word in words:
        after.extend(mapping.replaced.get(word.id, (word.id,)))
    return (after[0], after[-1]) if after else None


# ---------------------------------------------------------------------------
# The four edits
# ---------------------------------------------------------------------------


def apply(transcript: Transcript, edit: Edit) -> tuple[Transcript, Mapping]:
    """Applies one edit. An edit that changes nothing returns the same
    transcript, with the same revision."""

    index = transcript.turn_indexes.get(edit.turn)
    if index is None:
        raise EditError(f"no turn {edit.turn}")
    turn = transcript.turns[index]
    turns = list(transcript.turns)
    next_word_id = transcript.next_word_id
    next_turn_id = transcript.next_turn_id
    mapping = Mapping()

    match edit:
        case ReplaceWords(first=first, count=count, texts=texts, author=author):
            change = _Rewrite(turn.words, next_word_id, author)
            change.replace(first, count, texts)
            if change.unchanged:
                return transcript, mapping
            turns[index : index + 1] = change.turns_like(turn)
            next_word_id = change.next_word_id
            mapping = change.mapping()
        case SplitTurn(at=at):
            if not 0 < at < len(turn.words):
                raise EditError(f"turn {turn.id} cannot be split at word {at}")
            turns[index : index + 1] = [
                replace(turn, words=turn.words[:at]),
                Turn(next_turn_id, turn.speaker, turn.words[at:]),
            ]
            next_turn_id += 1
        case MergeTurns():
            if index + 1 >= len(turns):
                raise EditError(f"turn {turn.id} is the last one")
            following = turns[index + 1]
            if following.speaker != turn.speaker:
                raise EditError(f"turns {turn.id} and {following.id} have different speakers")
            if following.words[0].start < turn.words[-1].start:
                # The next turn began while this one was still going: its
                # words cannot simply be put after this one's.
                raise EditError(f"turns {turn.id} and {following.id} overlap")
            turns[index : index + 2] = [replace(turn, words=turn.words + following.words)]
        case SetSpeaker(speaker=speaker):
            if speaker == turn.speaker:
                return transcript, mapping
            turns[index] = replace(turn, speaker=speaker)
        case _:
            assert_never(edit)

    return (
        Transcript(
            turns=in_start_order(turns),
            revision=transcript.revision + 1,
            next_word_id=next_word_id,
            next_turn_id=next_turn_id,
        ),
        mapping,
    )


# ---------------------------------------------------------------------------
# Free retyping
# ---------------------------------------------------------------------------


def retext(
    transcript: Transcript,
    turn_id: TurnId,
    text: str,
    author: Author = Author.HUMAN,
) -> tuple[Transcript, Mapping]:
    """Makes a turn read `text`, keeping as many words as it can.

    This is what free editing comes down to: the person changed the turn's
    text in any way they liked, and the words are brought in line with it.
    Words that are still there keep their identity and time; the rest
    follow the rules at the top of this module. Empty text removes the
    turn. Text equal to the turn's own changes nothing."""

    index = transcript.turn_indexes.get(turn_id)
    if index is None:
        raise EditError(f"no turn {turn_id}")
    turn = transcript.turns[index]
    change = _Rewrite(turn.words, transcript.next_word_id, author)
    runs = align([word.text for word in turn.words], text.split())
    # From the right, so that the indexes of the runs still to do stay valid.
    for first, count, texts in reversed(runs):
        change.replace(first, count, texts)
    if change.unchanged:
        return transcript, Mapping()
    turns = list(transcript.turns)
    turns[index : index + 1] = change.turns_like(turn)
    return (
        Transcript(
            turns=in_start_order(turns),
            revision=transcript.revision + 1,
            next_word_id=change.next_word_id,
            next_turn_id=transcript.next_turn_id,
        ),
        change.mapping(),
    )


def align(old: Sequence[str], new: Sequence[str]) -> list[tuple[int, int, tuple[str, ...]]]:
    """The smallest replacements that turn the words `old` into `new`.

    Returns runs (first, count, texts): `count` old words from index
    `first` become `texts`. Runs are in order and do not touch each other's
    words; everything outside them is the same in both.

    Words are compared whole first. Where a stretch differs, it is cut
    further by looking at letters, so that "alot in to" → "a lot
    into" comes out as one word becoming two and two becoming one, not
    as three words becoming three others."""

    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(old), len(new)) - prefix
        and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]
    ):
        suffix += 1
    old_middle = old[prefix : len(old) - suffix]
    new_middle = new[prefix : len(new) - suffix]

    runs = []
    matcher = SequenceMatcher(None, old_middle, new_middle, autojunk=False)
    for tag, old_from, old_to, new_from, new_to in matcher.get_opcodes():
        if tag == "equal":
            continue
        pieces = _cut_by_letters(old_middle[old_from:old_to], new_middle[new_from:new_to])
        for piece_old_from, piece_old_to, piece_new_from, piece_new_to in pieces:
            runs.append(
                (
                    prefix + old_from + piece_old_from,
                    piece_old_to - piece_old_from,
                    tuple(new_middle[new_from + piece_new_from : new_from + piece_new_to]),
                )
            )
    return runs


def _cut_by_letters(old: Sequence[str], new: Sequence[str]) -> list[tuple[int, int, int, int]]:
    """Cuts "these old words became these new words" into smaller such
    statements, as (old from, old to, new from, new to).

    An old and a new word belong together when one is a retyping of the
    other (they share at least half of the longer one's letters, in the
    same order) or a part of it (all of the shorter one's letters). Words
    that belong together, directly or through others, form one piece; what
    lies between two pieces forms a piece of its own."""

    if not old or not new or (len(old) == 1 and len(new) == 1):
        return [(0, len(old), 0, len(new))]

    old_letters, old_owner = _letter_stream(old)
    new_letters, new_owner = _letter_stream(new)
    shared: dict[tuple[int, int], int] = {}
    matcher = SequenceMatcher(None, old_letters, new_letters, autojunk=False)
    for old_at, new_at, size in matcher.get_matching_blocks():
        for offset in range(size):
            pair = (old_owner[old_at + offset], new_owner[new_at + offset])
            if pair[0] >= 0 and pair[1] >= 0:
                shared[pair] = shared.get(pair, 0) + 1

    # [old first, old last, new first, new last], each growing as links arrive.
    blocks: list[list[int]] = []
    for (old_index, new_index), count in sorted(shared.items()):
        sizes = (len(letters(old[old_index])), len(letters(new[new_index])))
        if count < min(sizes) and 2 * count < max(sizes):
            continue
        if blocks and (old_index <= blocks[-1][1] or new_index <= blocks[-1][3]):
            blocks[-1][1] = max(blocks[-1][1], old_index)
            blocks[-1][3] = max(blocks[-1][3], new_index)
        else:
            blocks.append([old_index, old_index, new_index, new_index])

    pieces = []
    old_done = new_done = 0
    for old_first, old_last, new_first, new_last in blocks:
        if old_first > old_done or new_first > new_done:
            pieces.append((old_done, old_first, new_done, new_first))
        pieces.append((old_first, old_last + 1, new_first, new_last + 1))
        old_done, new_done = old_last + 1, new_last + 1
    if old_done < len(old) or new_done < len(new):
        pieces.append((old_done, len(old), new_done, len(new)))
    return pieces


def _letter_stream(words: Sequence[str]) -> tuple[list[str], list[int]]:
    """The letters of the words one after another, with a separator between
    words, and for each position the index of the word it came from (-1 for
    a separator)."""

    stream: list[str] = []
    owner: list[int] = []
    for index, word in enumerate(words):
        if index:
            stream.append(" ")
            owner.append(-1)
        for letter in letters(word):
            stream.append(letter)
            owner.append(index)
    return stream, owner


# ---------------------------------------------------------------------------
# Replacing words inside one turn
# ---------------------------------------------------------------------------


class _Rewrite:
    """The words of one turn while they are being replaced, with a record of
    what happened to them."""

    def __init__(self, words: Sequence[Word], next_word_id: WordId, author: Author) -> None:
        self.original = tuple(words)
        self.words = list(words)
        self.next_word_id = next_word_id
        self.author = author
        self.replaced: dict[WordId, tuple[WordId, ...]] = {}
        self.retyped: set[WordId] = set()

    @property
    def unchanged(self) -> bool:
        return len(self.words) == len(self.original) and all(
            now is before for now, before in zip(self.words, self.original)
        )

    def mapping(self) -> Mapping:
        return Mapping(dict(self.replaced), frozenset(self.retyped))

    def turns_like(self, turn: Turn) -> list[Turn]:
        """The turn with the new words, or nothing if none are left."""

        return [replace(turn, words=tuple(self.words))] if self.words else []

    def replace(self, first: int, count: int, texts: Sequence[str]) -> None:
        if count < 0 or first < 0 or first + count > len(self.words):
            raise EditError(f"no words {first}..{first + count} in a turn of {len(self.words)}")
        for text in texts:
            if not text or text.split() != [text]:
                raise EditError(f"not a single word: {text!r}")

        old = self.words[first : first + count]
        if len(texts) == count:
            new = [self._retyped(word, text) for word, text in zip(old, texts)]
        elif not texts:
            new = []
            for word in old:
                self.replaced[word.id] = ()
        elif old:
            new = self._instead_of(old, texts, self._ceiling(first + count))
        else:
            new = self._inserted(first, texts)
        self.words[first : first + count] = new

    def _retyped(self, word: Word, text: str) -> Word:
        if text == word.text:
            return word
        if letters(text) == letters(word.text):
            return replace(word, text=text)
        self.retyped.add(word.id)
        return replace(word, text=text, confidence=None, author=self.author)

    def _instead_of(self, old: Sequence[Word], texts: Sequence[str], ceiling: float) -> list[Word]:
        start = old[0].start
        end = max(start, min(max(word.end for word in old), ceiling))
        origin = tuple(dict.fromkeys(source for word in old for source in word.origin))
        new = self._new_words(texts, divide_time(start, end, texts), origin)
        for word in old:
            self.replaced[word.id] = tuple(created.id for created in new)
        return new

    def _inserted(self, at: int, texts: Sequence[str]) -> list[Word]:
        previous = self.words[at - 1] if at > 0 else None
        following = self.words[at] if at < len(self.words) else None
        if previous is None and following is None:
            raise EditError("a turn without words cannot be typed into")

        if not any(letters(text) for text in texts):
            # Nothing that is heard, such as a dash: it takes no time.
            if previous is not None:
                moment = min(previous.end, self._ceiling(at))
            else:
                moment = self._ceiling(at)
            return self._new_words(texts, [(moment, moment)] * len(texts), ())

        if previous and following and following.start - previous.end >= MINIMUM_SILENCE:
            shares = divide_time(previous.end, following.start, texts)
            return self._new_words(texts, shares, ())

        if previous and (following is None or previous.end > previous.start):
            # No silence to use: the word before gives up part of its time.
            end = max(previous.start, min(previous.end, self._ceiling(at)))
            shares = divide_time(previous.start, end, [previous.text, *texts])
            self.words[at - 1] = replace(previous, end=shares[0][1])
            return self._new_words(texts, shares[1:], ())

        # At the start of a turn the word after does the same.
        following = self.words[at]
        end = max(following.start, min(following.end, self._ceiling(at + 1)))
        shares = divide_time(following.start, end, [*texts, following.text])
        self.words[at] = replace(following, start=shares[-1][0], end=max(following.end, shares[-1][0]))
        return self._new_words(texts, shares[:-1], ())

    def _ceiling(self, index: int) -> float:
        """The start of the word at `index`: no word placed before it may
        start later than that."""

        return self.words[index].start if index < len(self.words) else math.inf

    def _new_words(
        self,
        texts: Sequence[str],
        times: Sequence[tuple[float, float]],
        origin: tuple[WordId, ...],
    ) -> list[Word]:
        created = []
        for text, (start, end) in zip(texts, times):
            created.append(Word(self.next_word_id, text, start, end, None, self.author, origin))
            self.next_word_id += 1
        return created
