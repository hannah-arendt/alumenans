"""The interview that is open in the window: its transcript, and everything
a person can do to it.

Each action applies edits to the transcript, writes the result to the
interview file, and reports what the page has to redraw. The transcript in
memory and the one in the file are therefore always the same.

Undo and redo last as long as the interview is open.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Callable, Sequence

from alumenans.document import editing
from alumenans.document.editing import (
    EditError,
    MergeTurns,
    ReplaceWords,
    SetSpeaker,
    SplitTurn,
    apply,
)
from alumenans.document.transcript import (
    Span,
    Transcript,
    Turn,
    TurnId,
    changed_turns,
    letters,
)
from alumenans.storage.interview_store import InterviewStore


class StaleRevision(Exception):
    """The page asked for an edit to a transcript it no longer has. It
    should take the current one and let the person try again."""


class OpenInterview:
    """The window calls from a new thread each time, so every method lets
    one caller in at a time.

    The editing methods take the revision the page was showing when the
    person acted, and raise StaleRevision if the transcript has moved on.
    They raise EditError for an edit that does not fit the transcript. In
    both cases nothing changes."""

    def __init__(self, store: InterviewStore, transcript: Transcript) -> None:
        self._store = store
        self._transcript = transcript
        self._undo: list[Transcript] = []
        self._redo: list[Transcript] = []
        # The turn the person is typing in, if typing was the last action.
        self._typing: TurnId | None = None
        self._lock = threading.Lock()

    @property
    def transcript(self) -> Transcript:
        return self._transcript

    def view(self) -> dict:
        """The whole transcript, in the form the actions report changes."""

        with self._lock:
            return self._report(Transcript())

    # -- what a person can do ------------------------------------------------

    def retext(self, revision: int, turn_id: TurnId, text: str, continues: bool = False) -> dict:
        """The person typed in a turn; `text` is what it reads now.

        The page saves a turn several times while the person is in it.
        `continues` says that this is one more save of the same visit: for
        undo, a visit to a turn is one step."""

        return self._act(
            revision,
            lambda transcript: editing.retext(transcript, turn_id, text)[0],
            typing=turn_id,
            same_step=continues and self._typing == turn_id,
        )

    def replace(self, revision: int, replacements: Sequence[tuple[Span, str]]) -> dict:
        """Puts new text in place of each span, as one step: accepting a
        suggestion, or the same correction in many places."""

        def edit(transcript: Transcript) -> Transcript:
            for span, text in replacements:
                turn, first, count = _span_in_turn(transcript, span)
                texts = text.split()
                if texts and not any(letters(word) for word in texts):
                    # Only punctuation is left of the words, a comma say. It
                    # goes to the word in front; at the start of a turn it
                    # has nothing to belong to.
                    if first == 0:
                        texts = []
                    else:
                        first -= 1
                        count += 1
                        texts = [turn.words[first].text + "".join(texts)]
                change = ReplaceWords(turn.id, first, count, tuple(texts))
                transcript, _ = apply(transcript, change)
            return transcript

        return self._act(revision, edit)

    def split(self, revision: int, turn_id: TurnId, before: str, after: str) -> dict:
        """Cuts a turn in two where the caret is: `before` and `after` are
        its text on either side. The caret may be inside a word, and the
        text may hold typing that was not saved yet."""

        def edit(transcript: Transcript) -> Transcript:
            at = len(before.split())
            transcript, _ = editing.retext(transcript, turn_id, f"{before} {after}")
            return apply(transcript, SplitTurn(turn_id, at))[0]

        return self._act(revision, edit)

    def merge(self, revision: int, turn_id: TurnId) -> dict:
        """Joins a turn to the one before it, which must be by the same
        speaker."""

        def edit(transcript: Transcript) -> Transcript:
            index = transcript.turn_indexes.get(turn_id)
            if not index:
                raise EditError(f"no turn before turn {turn_id}")
            return apply(transcript, MergeTurns(transcript.turns[index - 1].id))[0]

        return self._act(revision, edit)

    def set_speaker(self, revision: int, turn_id: TurnId, speaker: int) -> dict:
        """Gives a whole turn to another speaker."""

        return self._act(
            revision, lambda transcript: apply(transcript, SetSpeaker(turn_id, speaker))[0]
        )

    def undo(self) -> dict:
        with self._lock:
            return self._restore(self._undo, self._redo)

    def redo(self) -> dict:
        with self._lock:
            return self._restore(self._redo, self._undo)

    # -- how ------------------------------------------------------------------

    def _act(
        self,
        revision: int,
        edit: Callable[[Transcript], Transcript],
        typing: TurnId | None = None,
        same_step: bool = False,
    ) -> dict:
        with self._lock:
            before = self._transcript
            if revision != before.revision:
                raise StaleRevision()
            after = edit(before)
            if after is not before:
                self._store.save(after, before)
                if not same_step:
                    self._undo.append(before)
                self._redo.clear()
                self._transcript = after
                self._typing = typing
            elif typing != self._typing:
                self._typing = None
            return self._report(before)

    def _restore(self, source: list[Transcript], other: list[Transcript]) -> dict:
        before = self._transcript
        self._typing = None
        if source:
            # A new revision, so that the page can tell this transcript from
            # the one that had the same number before.
            after = replace(source[-1], revision=before.revision + 1)
            self._store.save(after, before)
            source.pop()
            other.append(before)
            self._transcript = after
        return self._report(before)

    def _report(self, before: Transcript) -> dict:
        """What differs from `before`, for the page: the turns to draw
        again, and the order of all turns, which also says which are gone."""

        transcript = self._transcript
        changed, _ = changed_turns(before, transcript)
        return {
            "revision": transcript.revision,
            "order": [turn.id for turn in transcript.turns],
            "changed": [_for_page(transcript.turn(turn_id)) for turn_id in changed],
            "can_undo": bool(self._undo),
            "can_redo": bool(self._redo),
        }


def _for_page(turn: Turn) -> dict:
    return {
        "id": turn.id,
        "speaker": turn.speaker,
        "start": turn.start,
        "end": turn.end,
        "words": [
            {"id": word.id, "text": word.text, "start": word.start, "end": word.end}
            for word in turn.words
        ],
    }


def _span_in_turn(transcript: Transcript, span: Span) -> tuple[Turn, int, int]:
    """The turn a span lies in, the index of its first word there, and the
    number of words."""

    first = transcript.positions.get(span[0])
    last = transcript.positions.get(span[1])
    if first is None or last is None or first[0] != last[0] or last[1] < first[1]:
        raise EditError(f"words {span[0]}..{span[1]} are not a run inside one turn")
    return transcript.turns[first[0]], first[1], last[1] - first[1] + 1

