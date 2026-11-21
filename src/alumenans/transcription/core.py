#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure transcriber logic that doesn't depend on the heavy models."""

from __future__ import annotations

import hashlib
import math
import wave
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

from alumenans.i18n import t


SUPPORTED_MEDIA_EXTENSIONS = frozenset(
    {".mp3", ".wav", ".wma", ".mp4", ".m4a", ".ogg"}
)
TARGET_SAMPLE_RATE = 16_000
# Words a sentence cannot end on. A turn cut off at «в» ("in") or «что»
# ("that") is interrupted speech, and the honest mark for it is an
# ellipsis, not a full stop.
UNFINISHED_TAIL = {
    "а", "б", "бы", "без", "будто", "в", "во", "для", "до", "если",
    "же", "за", "и", "из", "из-за", "или", "к", "как", "когда", "ко",
    "которая", "которые", "который", "ли", "между", "на", "над", "но",
    "о", "об", "около", "от", "перед", "по", "под", "после", "потому",
    "при", "про", "с", "со", "то", "тоже", "у", "хотя", "чем", "через",
    "что", "чтобы", "чуть",
}

MIN_SPEAKERS = 1
MAX_SPEAKERS = 10


class TranscriptionError(RuntimeError):
    """An error that can safely be shown to the user."""


class TranscriptionCancelled(TranscriptionError):
    """The user stopped the processing."""


def validate_speaker_count(value: int | str) -> int:
    """Validates the known number of interview participants."""

    if isinstance(value, bool):
        raise TranscriptionError(t("transcription.speaker_count"))
    try:
        count = int(value)
    except (TypeError, ValueError) as error:
        raise TranscriptionError(t("transcription.speaker_count")) from error
    if count < MIN_SPEAKERS or count > MAX_SPEAKERS:
        raise TranscriptionError(t("transcription.speaker_count"))
    return count


def existing_directory(path: str | Path) -> str | None:
    """Working directory for the child process, but only if it still exists.

    On a Mac, an app launched from Downloads may be run by the system from
    its own copy, which it makes and removes by itself. The program's
    folder then disappears while it is running, and starting the child
    process fails with "[Errno 2] No such file or directory", with no hint
    of what happened.

    The child process doesn't need a working directory: all paths are
    passed to it in full, and it finds its own folder from its location."""

    try:
        return str(path) if Path(path).is_dir() else None
    except OSError:
        return None


def probe_media_duration(path: str | Path) -> float | None:
    """Returns the media duration without decoding the whole file."""

    try:
        import mutagen

        media = mutagen.File(Path(path))
        duration = float(media.info.length)
        if math.isfinite(duration) and duration > 0:
            return duration
    except Exception:
        pass

    # mutagen doesn't always read WAV, but the duration of the recording's
    # parts is mandatory: the player uses it to know which file to seek in.
    if Path(path).suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as source:
                rate = source.getframerate()
                if rate:
                    duration = source.getnframes() / rate
                    return duration if duration > 0 else None
        except Exception:
            return None
    return None


@dataclass(frozen=True)
class DiarizedSpan:
    start: float
    end: float
    speaker: int


@dataclass(frozen=True)
class AudioChunk:
    start: float
    end: float


@dataclass(frozen=True)
class TimedWord:
    start: float
    end: float
    text: str
    confidence: float | None = None
    boundary_risk: bool = False
    speaker_hint: int | None = None


@dataclass(frozen=True)
class TranscriptTurn:
    start: float
    end: float
    speaker: int
    text: str
    topic_start: bool = False
    words: tuple[TimedWord, ...] = ()
    id: str = ""


@dataclass(frozen=True)
class TranscriptResult:
    source_name: str
    duration: float
    turns: tuple[TranscriptTurn, ...]
    moderator_speaker: int | None

    @property
    def text(self) -> str:
        return render_transcript(self.turns)

    def to_dict(self) -> dict:
        return {
            "schema": 2,
            "source_name": self.source_name,
            "duration": self.duration,
            "moderator_speaker": self.moderator_speaker,
            "turns": [asdict(turn) for turn in self.turns],
            "text": self.text,
        }


def turn_identifier(source_name: str, turn: TranscriptTurn, ordinal: int) -> str:
    """A turn identifier that survives text edits.

    Tied to the sound, not the text: the turn's start and end times and the
    voice stay the same however the person rewrites the words during
    proofreading. The sequence number is part of the key only to tell apart
    two turns whose boundaries happen to coincide."""

    raw = "|".join(
        (
            str(source_name or ""),
            f"{float(turn.start):.3f}",
            f"{float(turn.end):.3f}",
            str(int(turn.speaker)),
            str(int(ordinal)),
        )
    ).encode("utf-8")
    return "t-" + hashlib.sha1(raw).hexdigest()[:16]


def assign_turn_identifiers(
    turns: Sequence[TranscriptTurn],
    source_name: str = "",
) -> list[TranscriptTurn]:
    """Assigns stable identifiers without touching those already issued."""

    result = []
    used: set[str] = set()
    for ordinal, turn in enumerate(turns):
        identifier = turn.id or turn_identifier(source_name, turn, ordinal)
        suffix = 1
        while identifier in used:
            suffix += 1
            identifier = f"{turn_identifier(source_name, turn, ordinal)}-{suffix}"
        used.add(identifier)
        result.append(replace(turn, id=identifier))
    return result


def validate_media_path(path: str | Path) -> Path:
    media = Path(path).expanduser()
    if not media.is_file():
        raise TranscriptionError(t("transcription.file_not_found"))
    if media.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
        allowed = ", ".join(sorted(ext[1:].upper() for ext in SUPPORTED_MEDIA_EXTENSIONS))
        raise TranscriptionError(t("transcription.unsupported_file", allowed=allowed))
    return media.resolve()


def _valid_spans(spans: Iterable[DiarizedSpan]) -> list[DiarizedSpan]:
    cleaned = []
    for span in spans:
        start = max(0.0, float(span.start))
        end = max(start, float(span.end))
        if end - start < 0.08:
            continue
        cleaned.append(DiarizedSpan(start, end, int(span.speaker)))
    return sorted(cleaned, key=lambda item: (item.start, item.end, item.speaker))


def plan_asr_chunks(
    duration: float,
    speech_ranges: Iterable[tuple[float, float]],
    *,
    target_seconds: float = 20.0,
    minimum_seconds: float = 16.0,
    maximum_seconds: float = 24.0,
    minimum_silence_seconds: float = 0.6,
    forced_overlap_seconds: float = 1.2,
    boundary_padding_seconds: float = 0.3,
) -> list[AudioChunk]:
    """Plans large windows, using pauses only as boundaries."""

    duration = max(0.0, float(duration))
    ranges: list[tuple[float, float]] = []
    for raw_start, raw_end in sorted(speech_ranges):
        start = min(duration, max(0.0, float(raw_start)))
        end = min(duration, max(start, float(raw_end)))
        if end - start < 0.08:
            continue
        if ranges and start - ranges[-1][1] < minimum_silence_seconds:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
    if not ranges:
        return []

    content_start = max(0.0, ranges[0][0] - boundary_padding_seconds)
    content_end = min(duration, ranges[-1][1] + boundary_padding_seconds)
    gaps = [
        (left[1], right[0])
        for left, right in zip(ranges, ranges[1:])
        if right[0] - left[1] >= minimum_silence_seconds
    ]

    chunks: list[AudioChunk] = []
    cursor = content_start
    while content_end - cursor > maximum_seconds:
        remaining = content_end - cursor
        desired_length = min(
            target_seconds,
            max(minimum_seconds, remaining - minimum_seconds),
        )
        desired_cut = cursor + desired_length
        candidates = []
        for gap_start, gap_end in gaps:
            midpoint = (gap_start + gap_end) / 2
            chunk_end = min(midpoint, gap_start + boundary_padding_seconds)
            next_start = max(midpoint, gap_end - boundary_padding_seconds)
            if chunk_end - cursor < minimum_seconds / 2:
                continue
            if not cursor + minimum_seconds <= midpoint <= cursor + maximum_seconds:
                continue
            if content_end - next_start < minimum_seconds / 2:
                continue
            candidates.append(
                (abs(midpoint - desired_cut), midpoint, chunk_end, next_start)
            )

        if candidates:
            _, _, chunk_end, next_start = min(candidates)
            chunks.append(AudioChunk(cursor, chunk_end))
            cursor = next_start
            continue

        chunk_end = min(content_end, cursor + desired_length)
        chunks.append(AudioChunk(cursor, chunk_end))
        cursor = max(cursor, chunk_end - forced_overlap_seconds)

    if content_end - cursor >= 0.08:
        chunks.append(AudioChunk(cursor, content_end))
    return chunks


def timed_words_from_tokens(
    tokens: Sequence[str],
    timestamps: Sequence[float],
    *,
    chunk_start: float,
    chunk_end: float,
    logprobs: Sequence[float] | None = None,
) -> list[TimedWord]:
    """Assembles model tokens into words with absolute timestamps."""

    if len(tokens) != len(timestamps):
        raise ValueError("Число токенов и временных меток не совпадает.")
    if logprobs is not None and len(tokens) != len(logprobs):
        raise ValueError("Число токенов и акустических оценок не совпадает.")

    words: list[TimedWord] = []
    parts: list[str] = []
    part_logprobs: list[float] = []
    word_start: float | None = None
    last_time = chunk_start

    def flush(end: float) -> None:
        nonlocal parts, part_logprobs, word_start
        text = clean_recognized_text("".join(parts))
        if text and word_start is not None:
            confidence = None
            if part_logprobs:
                # One poorly recognised fragment inside a word matters more than
                # the average: that is exactly how invented names and broken
                # terms show up.
                confidence = math.exp(min(0.0, min(part_logprobs)))
            words.append(
                TimedWord(
                    word_start,
                    max(word_start + 0.04, min(chunk_end, end)),
                    text,
                    confidence,
                )
            )
        parts = []
        part_logprobs = []
        word_start = None

    score_values = logprobs if logprobs is not None else [None] * len(tokens)
    for raw_token, raw_timestamp, raw_logprob in zip(
        tokens,
        timestamps,
        score_values,
        strict=True,
    ):
        token = str(raw_token)
        timestamp = min(
            chunk_end,
            max(chunk_start, chunk_start + float(raw_timestamp)),
        )
        starts_word = bool(token[:1].isspace())
        if starts_word and parts:
            flush(timestamp)
        piece = token.lstrip() if starts_word else token
        if not piece:
            flush(timestamp)
            last_time = timestamp
            continue
        if word_start is None:
            word_start = timestamp
        parts.append(piece)
        if raw_logprob is not None and piece.strip():
            try:
                score = float(raw_logprob)
            except (TypeError, ValueError):
                score = math.nan
            if math.isfinite(score):
                part_logprobs.append(score)
        last_time = timestamp

    flush(min(chunk_end, last_time + 0.12))
    return words


def split_timed_words_into_phrases(
    words: Iterable[TimedWord],
    *,
    maximum_gap: float = 1.5,
) -> list[list[TimedWord]]:
    """Splits words into phrases without losing the time boundaries."""

    phrases: list[list[TimedWord]] = []
    phrase: list[TimedWord] = []
    for word in sorted(words, key=lambda item: (item.start, item.end)):
        text = clean_recognized_text(word.text)
        if not text:
            continue
        if phrase and word.start - phrase[-1].end > maximum_gap:
            phrases.append(phrase)
            phrase = []
        phrase.append(replace(word, text=text))
        if re.search(r"[.!?…](?:[»\"')\]]*)$", text):
            phrases.append(phrase)
            phrase = []
    if phrase:
        phrases.append(phrase)
    return phrases


def speaker_for_timed_word(
    word: TimedWord,
    spans: Sequence[DiarizedSpan],
    *,
    previous_speaker: int | None = None,
) -> int:
    """Chooses the voice for a word from the nearest time labels."""

    if word.speaker_hint is not None:
        return int(word.speaker_hint)

    midpoint = (word.start + word.end) / 2

    def score(span: DiarizedSpan) -> tuple[float, float, int]:
        overlap = max(
            0.0,
            min(word.end, span.end) - max(word.start, span.start),
        )
        distance = (
            0.0
            if span.start <= midpoint <= span.end
            else min(abs(midpoint - span.start), abs(midpoint - span.end))
        )
        return (
            overlap,
            -distance,
            int(span.speaker == previous_speaker),
        )

    return max(spans, key=score).speaker


_JOINED_UTTERANCE_BOUNDARY = re.compile(
    r"(?<=[!?…])(?=[A-Za-zА-Яа-яЁё])|(?<=\.)(?=[A-ZА-ЯЁ])"
)


def split_joined_utterance_words(
    words: Iterable[TimedWord],
    spans: Iterable[DiarizedSpan],
    *,
    boundary_tolerance: float = 0.35,
) -> list[TimedWord]:
    """Splits glued tokens like ``вопрос?Ответ`` ("question?Answer") at a
    voice change."""

    ordered_spans = _valid_spans(spans)
    transitions = []
    for previous, current in zip(ordered_spans, ordered_spans[1:]):
        if previous.speaker == current.speaker:
            continue
        transitions.append(
            (
                (previous.end + current.start) / 2,
                previous.speaker,
                current.speaker,
            )
        )

    result: list[TimedWord] = []
    for word in sorted(words, key=lambda item: (item.start, item.end)):
        text_boundaries = [
            match.start()
            for match in _JOINED_UTTERANCE_BOUNDARY.finditer(word.text)
        ]
        nearby_transitions = [
            transition
            for transition in transitions
            if word.start - boundary_tolerance <= transition[0] < word.end
        ]
        if not text_boundaries or not nearby_transitions:
            result.append(word)
            continue

        available = list(text_boundaries)
        chosen = []
        duration = max(0.01, word.end - word.start)
        for point, previous_speaker, current_speaker in nearby_transitions:
            if not available:
                break
            expected_index = len(word.text) * (point - word.start) / duration
            index = min(available, key=lambda value: abs(value - expected_index))
            available.remove(index)
            chosen.append(
                (index, point, previous_speaker, current_speaker)
            )
        chosen.sort()
        if not chosen:
            result.append(word)
            continue

        segment_start = word.start
        text_start = 0
        speaker_hint = chosen[0][2]
        for text_end, boundary, _previous_speaker, current_speaker in chosen:
            split_time = min(
                word.end - 0.01,
                max(segment_start + 0.01, boundary),
            )
            part = word.text[text_start:text_end]
            if part:
                result.append(
                    replace(
                        word,
                        start=segment_start,
                        end=split_time,
                        text=part,
                        boundary_risk=True,
                        speaker_hint=speaker_hint,
                    )
                )
            segment_start = split_time
            text_start = text_end
            speaker_hint = current_speaker
        part = word.text[text_start:]
        if part:
            result.append(
                replace(
                    word,
                    start=segment_start,
                    text=part,
                    boundary_risk=True,
                    speaker_hint=speaker_hint,
                )
            )
    return result


def words_to_transcript_turns(
    words: Iterable[TimedWord],
    spans: Iterable[DiarizedSpan],
    *,
    maximum_gap: float = 1.5,
    speaker_overrides: Sequence[int | None] | None = None,
) -> list[TranscriptTurn]:
    """Assigns speakers to already recognised words along the timeline."""

    ordered_spans = _valid_spans(spans)
    if not ordered_spans:
        return []

    separated_words = split_joined_utterance_words(words, ordered_spans)
    phrases = split_timed_words_into_phrases(
        separated_words,
        maximum_gap=maximum_gap,
    )

    turns: list[TranscriptTurn] = []
    previous_speaker: int | None = None
    for index, phrase in enumerate(phrases):
        override = (
            speaker_overrides[index]
            if speaker_overrides is not None and index < len(speaker_overrides)
            else None
        )
        groups: list[tuple[int, list[TimedWord]]] = []
        phrase_speaker = previous_speaker
        for word in phrase:
            speaker = override or speaker_for_timed_word(
                word,
                ordered_spans,
                previous_speaker=phrase_speaker,
            )
            if groups and groups[-1][0] == speaker:
                groups[-1][1].append(word)
            else:
                groups.append((speaker, [word]))
            phrase_speaker = speaker

        for speaker, group in groups:
            text = clean_recognized_text(" ".join(item.text for item in group))
            text = re.sub(r"^—\s*", "", text)
            if not text:
                continue
            if (
                turns
                and turns[-1].speaker == speaker
                and group[0].start - turns[-1].end <= maximum_gap
            ):
                turns[-1] = replace(
                    turns[-1],
                    end=max(turns[-1].end, group[-1].end),
                    text=clean_recognized_text(f"{turns[-1].text} {text}"),
                    words=turns[-1].words + tuple(group),
                )
            else:
                turns.append(
                    TranscriptTurn(
                        start=group[0].start,
                        end=group[-1].end,
                        speaker=speaker,
                        text=text,
                        words=tuple(group),
                    )
                )
            previous_speaker = speaker
    return turns


# ---------------------------------------------------------------------------
# Viterbi resegmentation.
#
# Voice labelling used to be assembled from rules, each added for a
# specific breakage: 2.2-second windows, a separate recheck of short
# turns, a ban on merging across a voice change. Code like that grows with
# every new recording and has no overall sense to it.
#
# Here, instead of a pile of rules, there is one principle. The voice is
# treated as a state that changes rarely; each short frame carries
# evidence of how much it resembles each of the voices. We look for the
# most likely sequence of states, with a penalty for every switch.
#
# This resolves exactly the dilemma the rules were piled up for. A long
# monologue isn't torn in the middle: a single smeared frame doesn't pay
# for the penalty. But a short «угу» ("uh-huh") in another voice does break
# through: evidence over several frames in a row outweighs the penalty.
# ---------------------------------------------------------------------------


def viterbi_path(
    scores: Sequence[Sequence[float]],
    *,
    switch_penalty: float = 1.6,
) -> list[int]:
    """The most likely sequence of voices.

    ``scores[i][k]`` is how much frame ``i`` resembles voice ``k``. The
    values are arbitrary: only their order and the differences between
    them matter. The ``switch_penalty`` is paid once for each voice change.
    """

    if not scores:
        return []
    voices = len(scores[0])
    if voices <= 1:
        return [0] * len(scores)

    best = [float(value) for value in scores[0]]
    came_from: list[list[int]] = []
    for frame in scores[1:]:
        previous_best = max(best)
        previous_index = best.index(previous_best)
        current = []
        choice = []
        for voice in range(voices):
            stay = best[voice]
            switch = previous_best - switch_penalty
            # A voice change makes sense only if the gain on this frame
            # exceeds the cost of the break itself.
            if switch > stay:
                current.append(switch + float(frame[voice]))
                choice.append(previous_index)
            else:
                current.append(stay + float(frame[voice]))
                choice.append(voice)
        best = current
        came_from.append(choice)

    path = [best.index(max(best))]
    for choice in reversed(came_from):
        path.append(choice[path[-1]])
    path.reverse()
    return path


def frames_to_spans(
    frames: Sequence[tuple[float, float]],
    labels: Sequence[int],
    *,
    hop: float = 0.5,
) -> list[DiarizedSpan]:
    """Gathers neighbouring frames of one voice into segments.

    The boundary is taken as the midpoint between frames: the available
    data can't say anything more precise, and a systematic shift to one
    side is worse than an honest midpoint."""

    if not frames or len(frames) != len(labels):
        return []

    spans: list[DiarizedSpan] = []
    start = float(frames[0][0])
    current = int(labels[0])
    for index in range(1, len(frames)):
        if int(labels[index]) == current and (
            frames[index][0] - frames[index - 1][0] <= hop * 1.5
        ):
            continue
        edge = (float(frames[index - 1][1]) + float(frames[index][0])) / 2
        edge = max(start + 0.02, edge)
        spans.append(DiarizedSpan(start, edge, current))
        start = edge
        current = int(labels[index])
    spans.append(DiarizedSpan(start, max(start + 0.02, float(frames[-1][1])), current))
    return _valid_spans(spans)


def resolve_overlaps(spans: Iterable[DiarizedSpan]) -> list[DiarizedSpan]:
    """Turns overlapping labels into a single chronological track.

    A mono recording doesn't allow two simultaneous voices to be recognised
    honestly. In a disputed interval the voice already speaking is kept;
    this prevents the same phrase from being duplicated in two turns.
    """

    source = _valid_spans(spans)
    if not source:
        return []

    boundaries = sorted({point for span in source for point in (span.start, span.end)})
    pieces: list[DiarizedSpan] = []
    previous_speaker: int | None = None

    for start, end in zip(boundaries, boundaries[1:]):
        if end - start < 0.02:
            continue
        middle = (start + end) / 2
        active = [span for span in source if span.start <= middle < span.end]
        if not active:
            previous_speaker = None
            continue

        continuing = [span for span in active if span.speaker == previous_speaker]
        chosen = continuing[0] if continuing else max(
            active, key=lambda span: (span.end - span.start, -span.start)
        )
        piece = DiarizedSpan(start, end, chosen.speaker)
        if pieces and pieces[-1].speaker == piece.speaker and math.isclose(
            pieces[-1].end, piece.start, abs_tol=0.02
        ):
            pieces[-1] = DiarizedSpan(pieces[-1].start, piece.end, piece.speaker)
        else:
            pieces.append(piece)
        previous_speaker = chosen.speaker

    return pieces


def renumber_speakers(spans: Iterable[DiarizedSpan]) -> list[DiarizedSpan]:
    """Numbers the voices from one in order of first appearance."""

    mapping: dict[int, int] = {}
    result = []
    for span in sorted(spans, key=lambda item: (item.start, item.end)):
        if span.speaker not in mapping:
            mapping[span.speaker] = len(mapping) + 1
        result.append(replace(span, speaker=mapping[span.speaker]))
    return result


def merge_speaker_spans(
    spans: Iterable[DiarizedSpan], *, maximum_gap: float = 1.5
) -> list[DiarizedSpan]:
    """Gathers neighbouring pieces of one voice into turns."""

    merged: list[DiarizedSpan] = []
    for span in _valid_spans(spans):
        if (
            merged
            and merged[-1].speaker == span.speaker
            and span.start - merged[-1].end <= maximum_gap
        ):
            merged[-1] = DiarizedSpan(
                merged[-1].start, max(merged[-1].end, span.end), span.speaker
            )
        else:
            merged.append(span)
    return merged


_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?])")
_MANY_SPACES = re.compile(r"[ \t]+")


def clean_recognized_text(text: str) -> str:
    cleaned = _MANY_SPACES.sub(" ", str(text).replace("\n", " ")).strip()
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", cleaned)


def unfinished_phrase(text: str) -> bool:
    """Whether the turn was cut off mid-phrase.

    A phrase doesn't end on a preposition, a conjunction or a comma: the
    person paused in the middle of a thought rather than finishing. This is
    more reliable than pause length: people think for a long time in
    interviews, and three seconds of silence mid-phrase is commonplace."""

    source = str(text or "").rstrip()
    if not source:
        return False
    if re.search(r"[,;:—–-]$", source):
        return True
    words = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]*", source)
    return bool(words) and words[-1].lower().replace("ё", "е") in UNFINISHED_TAIL


def merge_transcript_turns(
    turns: Iterable[TranscriptTurn],
    *,
    maximum_gap: float = 1.5,
    unfinished_gap: float = 6.0,
) -> list[TranscriptTurn]:
    """Merges neighbouring turns of one voice into one.

    The usual pause after which speech counts as a new turn is one and a
    half seconds. But if the previous turn was cut off mid-phrase, it is
    worth waiting longer: a break in the middle of a phrase reads as
    someone else's interjection, and the person has to stitch it back
    together by hand during proofreading."""

    merged: list[TranscriptTurn] = []
    for raw in sorted(turns, key=lambda item: (item.start, item.end)):
        text = clean_recognized_text(raw.text)
        if not text:
            continue
        turn = replace(raw, text=text)
        limit = (
            unfinished_gap
            if merged and unfinished_phrase(merged[-1].text)
            else maximum_gap
        )
        if (
            merged
            and merged[-1].speaker == turn.speaker
            and turn.start - merged[-1].end <= limit
        ):
            merged[-1] = replace(
                merged[-1],
                end=max(merged[-1].end, turn.end),
                text=clean_recognized_text(f"{merged[-1].text} {turn.text}"),
                topic_start=merged[-1].topic_start or turn.topic_start,
                words=merged[-1].words + turn.words,
            )
        else:
            merged.append(turn)
    return merged


_TOPIC_TRANSITION = re.compile(
    r"(?:"
    r"\b(?:а\s+)?(?:теперь|сейчас)\b[^.!?\n]{0,80}"
    r"(?:\b(?:поговорим|обсудим|перейд[её]м)\b|"
    r"\b(?:с\s+)?друг(?:ой|ую)\s+сторон(?:ы|у)\b)|"
    r"\b(?:давай|давайте)\b[^.!?\n]{0,60}\b"
    r"(?:поговорим|обсудим|перейд[её]м)\b|"
    r"\bперейд[её]м\b[^.!?\n]{0,40}\bк\b|"
    r"\bследующ(?:ая|ий|ее|ую)\s+(?:тема|вопрос|блок|часть)\b|"
    r"\b(?:ещ[её]\s+один|другой)\s+вопрос\b|"
    r"\bхочу\s+(?:теперь\s+)?(?:спросить|поговорить)\s+(?:о|об|про)\b|"
    r"\bрасскажите\s+(?:теперь\s+)?(?:о|об|про)\b|"
    r"\bчто\s+касается\b"
    r")",
    re.IGNORECASE,
)


def mark_topic_starts(
    turns: Sequence[TranscriptTurn],
) -> list[TranscriptTurn]:
    """Places markers of explicit topic transitions regardless of speaker
    role."""

    result = [replace(turn, topic_start=False) for turn in turns]
    for index, turn in enumerate(result):
        if turn.start < 60 or not _TOPIC_TRANSITION.search(turn.text):
            continue
        minute = int(turn.start // 60)
        if minute < 1:
            continue
        result[index] = replace(turn, topic_start=True)
    return result


def render_transcript(turns: Sequence[TranscriptTurn]) -> str:
    blocks: list[str] = []
    speaker: int | None = None
    paragraphs: list[str] = []

    def flush_speaker() -> None:
        nonlocal speaker, paragraphs
        if speaker is None or not paragraphs:
            return
        block = f"Спикер {speaker}: {paragraphs[0]}"
        if len(paragraphs) > 1:
            block += "\n\n" + "\n\n".join(paragraphs[1:])
        blocks.append(block)
        speaker = None
        paragraphs = []

    for turn in turns:
        text = clean_recognized_text(turn.text)
        if not text:
            continue
        if turn.topic_start:
            flush_speaker()
            blocks.append(f"[{int(turn.start // 60)} мин]")
        if speaker != turn.speaker:
            flush_speaker()
            speaker = turn.speaker
        paragraphs.append(text)
    flush_speaker()
    return "\n\n".join(blocks)
