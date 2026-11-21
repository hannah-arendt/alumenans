#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measuring quality: three numbers instead of eyeballing one fragment.

Sixty-six unit tests check that the code does what it says. Not one of
them answers the question of whether things are heard better. So here
three quantities are computed over a hand-annotated stretch of recording:

* WER: the share of misrecognised words;
* the share of misattributed speech: how many seconds went to the wrong voice;
* precision and recall of anonymization: how much personal data was found,
  and how much was needlessly hidden along the way.

After that, any change either improves the number or it doesn't, and the
argument is over. The module deliberately depends on nothing but the
standard library.
"""

from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path
from typing import Iterable, Sequence


REFERENCE_LINE = re.compile(
    r"^\s*\[?\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2}(?:[.,]\d+)?)\s*\]?\s*"
    r"(?:Спикер|СПИКЕР|Speaker)\s*(\d{1,2})\s*[:.]\s*(.*)$"
)
CLOSING_LINE = re.compile(
    r"^\s*\[?\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2}(?:[.,]\d+)?)\s*\]?\s*"
    r"(?:конец|КОНЕЦ|end)?\s*$"
)
PLAIN_SPEAKER_LINE = re.compile(
    r"^\s*(?:Спикер|СПИКЕР|Speaker)\s*(\d{1,2})\s*[:.]\s*(.*)$"
)
WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-'’][0-9A-Za-zА-Яа-яЁё]+)*")
FILLER = re.compile(
    r"^(?:[аэоумх](?:-[аэоумх])*|угу|ага|мг+|хм+|эм+|ээ+|мм+|ну|вот)$"
)


@dataclass(frozen=True)
class ReferenceTurn:
    start: float
    speaker: int
    text: str


@dataclass(frozen=True)
class Reference:
    """The whole annotated stretch: the turns and the point where annotation ends.

    The closing timecode is mandatory. Without it the last turn has no end,
    and then WER is computed over one stretch and attribution errors over
    another, shorter one. The difference is noticeable, and the cause is
    not obvious."""

    turns: tuple[ReferenceTurn, ...]
    end: float

    def __iter__(self):
        return iter(self.turns)

    def __len__(self) -> int:
        return len(self.turns)

    def __getitem__(self, index):
        return self.turns[index]


@dataclass
class ErrorCounts:
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0
    reference_words: int = 0

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def rate(self) -> float:
        return self.errors / self.reference_words if self.reference_words else 0.0

    def as_dict(self) -> dict:
        return {
            "wer": round(self.rate, 4),
            "errors": self.errors,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "reference_words": self.reference_words,
        }


@dataclass(frozen=True)
class SpeakerBlock:
    """A reference turn without a timecode: all that matters is who says what."""

    speaker: int
    text: str


@dataclass
class SpeakerWordScore:
    """Attribution errors counted in words rather than seconds."""

    misattributed: int = 0
    scored: int = 0
    reference_words: int = 0
    mapping: dict[int, int] = field(default_factory=dict)
    unlabelled_blocks: tuple[str, ...] = ()

    @property
    def rate(self) -> float:
        return self.misattributed / self.scored if self.scored else 0.0

    @property
    def coverage(self) -> float:
        return self.scored / self.reference_words if self.reference_words else 0.0

    def as_dict(self) -> dict:
        return {
            "speaker_error_rate": round(self.rate, 4),
            "misattributed_words": self.misattributed,
            "aligned_words": self.scored,
            "reference_words": self.reference_words,
            "coverage": round(self.coverage, 4),
            "mapping": {str(key): value for key, value in sorted(self.mapping.items())},
            "unlabelled_blocks": list(self.unlabelled_blocks),
        }


@dataclass
class SpeakerScore:
    misattributed_seconds: float = 0.0
    scored_seconds: float = 0.0
    mapping: dict[int, int] = field(default_factory=dict)

    @property
    def rate(self) -> float:
        return (
            self.misattributed_seconds / self.scored_seconds
            if self.scored_seconds
            else 0.0
        )

    def as_dict(self) -> dict:
        return {
            "speaker_error_rate": round(self.rate, 4),
            "misattributed_seconds": round(self.misattributed_seconds, 2),
            "scored_seconds": round(self.scored_seconds, 2),
            "mapping": {str(key): value for key, value in sorted(self.mapping.items())},
        }


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------


def parse_timecode(hours: str | None, minutes: str, seconds: str) -> float:
    return (
        (int(hours) if hours else 0) * 3600
        + int(minutes) * 60
        + float(str(seconds).replace(",", "."))
    )


def parse_reference(text: str) -> Reference:
    """Reads a reference of the form ``[01:23] Спикер 2: текст``.

    Lines without a timecode are appended to the previous turn: that makes
    annotating by hand easier, without fear of a stray line break. The last
    line is a lone timecode: it says where the annotated stretch ends."""

    turns: list[ReferenceTurn] = []
    closing: float | None = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = REFERENCE_LINE.match(line)
        if match:
            if closing is not None:
                raise ValueError(
                    "The reference has more turns after the closing timecode."
                )
            turns.append(
                ReferenceTurn(
                    start=parse_timecode(match.group(1), match.group(2), match.group(3)),
                    speaker=int(match.group(4)),
                    text=match.group(5).strip(),
                )
            )
            continue
        ending = CLOSING_LINE.match(line)
        if ending and turns:
            closing = parse_timecode(ending.group(1), ending.group(2), ending.group(3))
            continue
        plain = PLAIN_SPEAKER_LINE.match(line)
        if plain:
            raise ValueError(
                "The reference has a turn without a timecode: "
                f"«{line[:60]}». Expected format: [01:23] Спикер 2: текст."
            )
        if turns:
            turns[-1] = ReferenceTurn(
                turns[-1].start,
                turns[-1].speaker,
                f"{turns[-1].text} {line}".strip(),
            )
    if not turns:
        raise ValueError("No timecoded turn found in the reference.")
    if closing is None:
        raise ValueError(
            "The reference needs a closing timecode on a line of its own at the end, "
            "e.g. «[10:00]». Without it the last turn has no end."
        )
    if closing <= turns[-1].start:
        raise ValueError(
            "The closing timecode must come after the start of the last turn."
        )
    return Reference(tuple(turns), closing)


def load_reference(path: str | Path) -> Reference:
    return parse_reference(Path(path).read_text(encoding="utf-8"))


def load_hypothesis(path: str | Path) -> dict:
    """Reads the program's transcript: result.json or finished text.

    Text without timestamps is only good for comparing voices: there is
    nothing to compute the time-based metrics from."""

    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        payload = json.loads(text)
        if not isinstance(payload, dict) or "turns" not in payload:
            raise ValueError("Expected a transcription result file with a turns field.")
        return payload

    blocks, _ = parse_speaker_reference(text)
    return {
        "timed": False,
        "turns": [
            {"speaker": block.speaker, "text": block.text, "start": 0.0, "end": 0.0}
            for block in blocks
        ],
    }


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------


def normalize_words(text: str, *, keep_fillers: bool = True) -> list[str]:
    """Words in comparable form: no case, no ё, no punctuation, no hyphen seams."""

    words = []
    for match in WORD.finditer(str(text or "")):
        word = match.group(0).lower().replace("ё", "е")
        word = word.replace("’", "'")
        if not keep_fillers and FILLER.fullmatch(word.replace("-", "")):
            continue
        words.append(word)
    return words


def align_words(
    reference: Sequence[str],
    hypothesis: Sequence[str],
) -> ErrorCounts:
    """Word-level Levenshtein distance, counting each kind of error separately."""

    rows = len(reference)
    columns = len(hypothesis)
    # Each cell holds (cost, substitutions, deletions, insertions).
    previous: list[tuple[int, int, int, int]] = [
        (index, 0, 0, index) for index in range(columns + 1)
    ]
    for row in range(1, rows + 1):
        current = [(row, 0, row, 0)]
        for column in range(1, columns + 1):
            if reference[row - 1] == hypothesis[column - 1]:
                cost, subs, dels, ins = previous[column - 1]
                current.append((cost, subs, dels, ins))
                continue
            substitute = previous[column - 1]
            delete = previous[column]
            insert = current[column - 1]
            best = min(
                (substitute[0] + 1, substitute[1] + 1, substitute[2], substitute[3]),
                (delete[0] + 1, delete[1], delete[2] + 1, delete[3]),
                (insert[0] + 1, insert[1], insert[2], insert[3] + 1),
            )
            current.append(best)
        previous = current
    _, substitutions, deletions, insertions = previous[columns]
    return ErrorCounts(substitutions, deletions, insertions, rows)


def word_error_rate(
    reference: Reference | Iterable[ReferenceTurn] | str,
    hypothesis: Iterable[dict] | str,
    *,
    keep_fillers: bool = True,
) -> ErrorCounts:
    """WER over the whole annotated part, without relying on turn boundaries."""

    if isinstance(reference, str):
        reference_text = reference
    else:
        reference_text = " ".join(turn.text for turn in reference)
    if isinstance(hypothesis, str):
        hypothesis_text = hypothesis
    else:
        hypothesis_text = " ".join(
            str(turn.get("text") or "") for turn in hypothesis
        )
    return align_words(
        normalize_words(reference_text, keep_fillers=keep_fillers),
        normalize_words(hypothesis_text, keep_fillers=keep_fillers),
    )


# ---------------------------------------------------------------------------
# Speakers
# ---------------------------------------------------------------------------


def reference_intervals(
    turns: Sequence[ReferenceTurn],
    *,
    until: float,
) -> list[tuple[float, float, int]]:
    """The reference only gives turn starts: the end is the start of the next."""

    intervals = []
    for index, turn in enumerate(turns):
        end = turns[index + 1].start if index + 1 < len(turns) else until
        if end > turn.start:
            intervals.append((turn.start, end, turn.speaker))
    return intervals


def hypothesis_intervals(turns: Iterable[dict]) -> list[tuple[float, float, int]]:
    intervals = []
    for turn in turns:
        try:
            start = float(turn.get("start"))
            end = float(turn.get("end"))
            speaker = int(turn.get("speaker"))
        except (TypeError, ValueError):
            continue
        if end > start:
            intervals.append((start, end, speaker))
    return sorted(intervals)


def _overlap_matrix(
    reference: Sequence[tuple[float, float, int]],
    hypothesis: Sequence[tuple[float, float, int]],
) -> dict[tuple[int, int], float]:
    matrix: dict[tuple[int, int], float] = {}
    for left_start, left_end, left_speaker in reference:
        for right_start, right_end, right_speaker in hypothesis:
            shared = min(left_end, right_end) - max(left_start, right_start)
            if shared > 0:
                key = (right_speaker, left_speaker)
                matrix[key] = matrix.get(key, 0.0) + shared
    return matrix


def best_mapping(
    weights: dict[tuple[int, int], float],
    found: Sequence[int],
    expected: Sequence[int],
) -> dict[int, int]:
    """Matches up voice numbers: the program numbers them its own way.

    ``weights`` is the weight of a (program voice, reference speaker) pair:
    seconds or words, it doesn't matter. The matching with the largest
    total weight is sought."""

    found = sorted(set(found))
    expected = sorted(set(expected))
    if not found or not expected:
        return {}

    if len(found) <= 7 and len(expected) <= 7:
        best_score, best_arrangement = -1.0, {}
        if len(found) <= len(expected):
            candidates = (
                dict(zip(found, arrangement))
                for arrangement in permutations(expected, len(found))
            )
        else:
            candidates = (
                dict(zip(arrangement, expected))
                for arrangement in permutations(found, len(expected))
            )
        for mapping in candidates:
            score = sum(weights.get(pair, 0.0) for pair in mapping.items())
            if score > best_score:
                best_score, best_arrangement = score, mapping
        return best_arrangement

    mapping: dict[int, int] = {}
    taken: set[int] = set()
    for (candidate, target), _ in sorted(
        weights.items(), key=lambda item: item[1], reverse=True
    ):
        if candidate in mapping or target in taken:
            continue
        mapping[candidate] = target
        taken.add(target)
    return mapping


def best_speaker_mapping(
    reference: Sequence[tuple[float, float, int]],
    hypothesis: Sequence[tuple[float, float, int]],
) -> dict[int, int]:
    return best_mapping(
        _overlap_matrix(reference, hypothesis),
        [speaker for _, _, speaker in hypothesis],
        [speaker for _, _, speaker in reference],
    )


def speaker_error_rate(
    reference: Reference | Sequence[ReferenceTurn],
    hypothesis: Iterable[dict],
    *,
    until: float | None = None,
    resolution: float = 0.05,
) -> SpeakerScore:
    """The share of annotated speech attributed to the wrong voice.

    Measured by time, not by number of turns: one lost forty-second
    monologue turn and one mixed-up «угу» ("uh-huh") are very different
    breakages, and the measure must tell them apart."""

    turns = list(reference)
    window_end = until
    if window_end is None:
        window_end = getattr(reference, "end", None)
    if window_end is None:
        raise ValueError("The end of the annotated window is not set.")

    intervals = reference_intervals(turns, until=window_end)
    found = [
        span for span in hypothesis_intervals(hypothesis) if span[0] < window_end
    ]
    if not intervals:
        return SpeakerScore()

    mapping = best_speaker_mapping(intervals, found)
    starts = [span[0] for span in found]
    step = max(0.01, float(resolution))
    scored = 0.0
    wrong = 0.0
    for start, end, expected in intervals:
        position = start
        while position < end:
            middle = position + step / 2
            actual = None
            # The turns are ordered, so it's enough to look backwards from
            # the first one that starts after the current point.
            index = bisect.bisect_right(starts, middle) - 1
            while index >= 0:
                span_start, span_end, speaker = found[index]
                if span_start <= middle < span_end:
                    actual = mapping.get(speaker)
                    break
                if middle - span_start > 600:
                    break
                index -= 1
            scored += step
            if actual != expected:
                wrong += step
            position += step
    return SpeakerScore(wrong, scored, mapping)


# ---------------------------------------------------------------------------
# Speakers without timecodes
#
# Putting a timecode on every turn by hand is a job for a machine, not a
# person. But if the reference was made by editing a machine transcript,
# the words in it are the same, and that's enough: the two texts can be
# aligned word by word, and then compared on who each word is attributed
# to. The metric turns out even more accurate than the time-based one,
# because it doesn't depend on VAD boundaries.
#
# The price is that WER isn't computed against such a reference: its words
# haven't been checked by ear, they're the same words the model produced.
# ---------------------------------------------------------------------------


def parse_speaker_reference(text: str) -> tuple[list[SpeakerBlock], list[str]]:
    """Reads a reference of the form ``Спикер 2: текст`` without timecodes.

    A paragraph without a label is appended to the previous labelled speaker,
    the same way the program itself does it on output. Such paragraphs are
    returned as a separate list: usually they mean the label was forgotten."""

    blocks: list[SpeakerBlock] = []
    unlabelled: list[str] = []
    for raw in re.split(r"\n\s*\n", str(text or "")):
        piece = raw.strip()
        if not piece:
            continue
        if re.match(r"^\s*\[[^\]]*\]\s*$", piece):
            continue
        match = PLAIN_SPEAKER_LINE.match(piece.split("\n", 1)[0])
        if match:
            rest = piece.split("\n", 1)
            body = match.group(2).strip()
            if len(rest) > 1:
                body = f"{body} {rest[1].strip()}".strip()
            blocks.append(SpeakerBlock(int(match.group(1)), body))
            continue
        if not blocks:
            raise ValueError(
                "The reference starts with a paragraph without a speaker label: "
                f"«{piece[:60]}»."
            )
        unlabelled.append(piece[:80])
        blocks[-1] = SpeakerBlock(
            blocks[-1].speaker,
            f"{blocks[-1].text} {piece}".strip(),
        )
    if not blocks:
        raise ValueError("No turn found in the reference.")
    return blocks, unlabelled


def _tagged_words(
    pieces: Iterable[tuple[int, str]],
    *,
    keep_fillers: bool,
) -> tuple[list[str], list[int]]:
    words: list[str] = []
    owners: list[int] = []
    for speaker, text in pieces:
        for word in normalize_words(text, keep_fillers=keep_fillers):
            words.append(word)
            owners.append(speaker)
    return words, owners


def speaker_word_error_rate(
    reference: Sequence[SpeakerBlock],
    hypothesis: Iterable[dict],
    *,
    keep_fillers: bool = True,
    unlabelled: Sequence[str] = (),
) -> SpeakerWordScore:
    """The share of words attributed to the wrong voice.

    Alignment is done on the text: matched and substituted words count as
    one and the same position in the conversation; deletions and insertions
    don't count, as they belong to recognition quality, not to telling
    voices apart."""

    from difflib import SequenceMatcher

    reference_words, reference_owners = _tagged_words(
        ((block.speaker, block.text) for block in reference),
        keep_fillers=keep_fillers,
    )
    hypothesis_words, hypothesis_owners = _tagged_words(
        (
            (int(turn.get("speaker") or 1), str(turn.get("text") or ""))
            for turn in hypothesis
        ),
        keep_fillers=keep_fillers,
    )
    if not reference_words or not hypothesis_words:
        return SpeakerWordScore(
            reference_words=len(reference_words),
            unlabelled_blocks=tuple(unlabelled),
        )

    # Only exactly matched words are counted. Where recognition went wrong,
    # the alignment can shift by a word, and then a correct attribution
    # looks wrong. This measure is an underestimate, but it doesn't lie: it
    # answers exactly the question "who is the word we recognised
    # attributed to".
    matcher = SequenceMatcher(None, reference_words, hypothesis_words, autojunk=False)
    pairs: list[tuple[int, int]] = []
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag != "equal":
            continue
        pairs.extend(
            (
                reference_owners[left_start + offset],
                hypothesis_owners[right_start + offset],
            )
            for offset in range(left_end - left_start)
        )

    votes: dict[tuple[int, int], float] = {}
    for expected, actual in pairs:
        key = (actual, expected)
        votes[key] = votes.get(key, 0.0) + 1.0

    mapping = best_mapping(
        votes,
        [actual for _, actual in pairs],
        [expected for expected, _ in pairs],
    )

    misattributed = sum(
        1 for expected, actual in pairs if mapping.get(actual) != expected
    )
    return SpeakerWordScore(
        misattributed=misattributed,
        scored=len(pairs),
        reference_words=len(reference_words),
        mapping=mapping,
        unlabelled_blocks=tuple(unlabelled),
    )


# ---------------------------------------------------------------------------
# Anonymization
# ---------------------------------------------------------------------------


def anonymization_scores(
    gold: Sequence[Sequence[int]],
    found: Sequence[Sequence[int]],
) -> dict:
    """Precision and recall of the personal-data search.

    A find counts if it overlaps the reference one: demanding boundaries
    that match to the character makes no sense, since «Нюта Федермессер»
    and «Федермессер» do the job equally well."""

    gold_spans = [(int(item[0]), int(item[1])) for item in gold if len(item) >= 2]
    found_spans = [(int(item[0]), int(item[1])) for item in found if len(item) >= 2]
    matched_gold: set[int] = set()
    true_positive = 0
    for start, stop in found_spans:
        hit = next(
            (
                index
                for index, (gold_start, gold_stop) in enumerate(gold_spans)
                if index not in matched_gold and start < gold_stop and stop > gold_start
            ),
            None,
        )
        if hit is None:
            continue
        matched_gold.add(hit)
        true_positive += 1

    precision = true_positive / len(found_spans) if found_spans else 0.0
    recall = true_positive / len(gold_spans) if gold_spans else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "found": len(found_spans),
        "expected": len(gold_spans),
        "matched": true_positive,
        "missed": len(gold_spans) - true_positive,
        "false_alarms": len(found_spans) - true_positive,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def evaluate(
    reference: Reference,
    result: dict,
    *,
    keep_fillers: bool = True,
) -> dict:
    """Computes the metrics over the part of the recording annotated by hand.

    Both numbers are computed over one and the same window, from zero to
    the closing timecode. A program turn falls into the window if it began
    in it: that way a short tail past the boundary doesn't turn into a
    bunch of deletions."""

    window_end = reference.end
    turns = [turn for turn in (result.get("turns") or []) if isinstance(turn, dict)]
    inside = [
        turn for turn in turns if float(turn.get("start") or 0.0) < window_end
    ]
    recognition = word_error_rate(reference, inside, keep_fillers=keep_fillers)
    speakers = speaker_error_rate(reference, turns, until=window_end)
    return {
        "window_seconds": round(window_end, 2),
        "reference_turns": len(reference),
        "hypothesis_turns": len(inside),
        "recognition": recognition.as_dict(),
        "speakers": speakers.as_dict(),
    }


def evaluate_speakers(
    blocks: Sequence[SpeakerBlock],
    result: dict,
    *,
    keep_fillers: bool = True,
    unlabelled: Sequence[str] = (),
) -> dict:
    """A metric against a reference without timecodes: voice separation only.

    WER is deliberately not computed against such a reference. If the
    reference was made by editing a machine transcript, its words are the
    very ones the model produced, and zero errors would only mean the text
    had been compared with itself."""

    turns = [turn for turn in (result.get("turns") or []) if isinstance(turn, dict)]
    score = speaker_word_error_rate(
        blocks,
        turns,
        keep_fillers=keep_fillers,
        unlabelled=unlabelled,
    )
    return {
        "kind": "speakers",
        "reference_turns": len(blocks),
        "hypothesis_turns": len(turns),
        "speakers": score.as_dict(),
    }


def format_speaker_report(report: dict) -> str:
    speakers = report["speakers"]
    mapping = ", ".join(
        f"voice {key} → speaker {value}"
        for key, value in speakers["mapping"].items()
    )
    lines = [
        f"Reference without timecodes: {report['reference_turns']} turns, "
        f"{speakers['reference_words']} words "
        f"(the program has {report['hypothesis_turns']} turns)",
        "",
        f"Speech on the wrong voice  "
        f"{speakers['speaker_error_rate'] * 100:6.2f} %"
        f"   ({speakers['misattributed_words']} words of "
        f"{speakers['aligned_words']})",
        f"  aligned with reference   {speakers['coverage'] * 100:6.1f} %",
        f"  voice mapping            {mapping or '—'}",
    ]
    if speakers["unlabelled_blocks"]:
        lines.extend(
            (
                "",
                "Paragraphs without a speaker label, appended to the previous one:",
                *(f"  «{item}…»" for item in speakers["unlabelled_blocks"]),
            )
        )
    return "\n".join(lines)


def looks_timecoded(text: str) -> bool:
    return any(
        REFERENCE_LINE.match(line.strip())
        for line in str(text or "").splitlines()
    )


def format_report(report: dict) -> str:
    recognition = report["recognition"]
    speakers = report["speakers"]
    mapping = ", ".join(
        f"voice {key} → speaker {value}"
        for key, value in speakers["mapping"].items()
    )
    return "\n".join(
        (
            f"Annotated window: {report['window_seconds']:.0f} s "
            f"({report['reference_turns']} turns in the reference, "
            f"{report['hypothesis_turns']} from the program)",
            "",
            f"WER                        {recognition['wer'] * 100:6.2f} %"
            f"   ({recognition['errors']} errors in "
            f"{recognition['reference_words']} words)",
            f"  substitutions            {recognition['substitutions']:6d}",
            f"  deletions                {recognition['deletions']:6d}",
            f"  insertions               {recognition['insertions']:6d}",
            "",
            f"Speech on the wrong voice  {speakers['speaker_error_rate'] * 100:6.2f} %"
            f"   ({speakers['misattributed_seconds']:.1f} s of "
            f"{speakers['scored_seconds']:.1f} s)",
            f"  voice mapping            {mapping or '—'}",
        )
    )
