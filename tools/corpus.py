#!/usr/bin/env python3
"""End-to-end test corpus: real recordings, run through the pipeline stage by stage.

A *case* is a folder with one recording and whatever a person has prepared
for it (see tests/corpus/README.md). This module finds cases, runs the
pipeline stages on them, and compares the output with what was accepted
before. tests/test_corpus.py runs the same checks as unit tests.

    python tools/corpus.py list
    python tools/corpus.py new my-interview
    python tools/corpus.py transcribe [CASE ...]
    python tools/corpus.py draft [CASE ...]
    python tools/corpus.py run [CASE ...]
    python tools/corpus.py accept [CASE ...]
    python tools/corpus.py metrics [CASE ...]

Two kinds of check, on purpose:

* snapshots detect change: the full output of a stage is compared with the
  last accepted one. A difference is something to look at and then accept,
  not necessarily a regression;
* metrics judge quality: error rates against a reference made by a person,
  compared with the recorded baseline.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

CORPUS = REPO / "tests" / "corpus"
MANIFEST = "case.toml"
# Metrics where a smaller number is better; for all the others bigger is better.
LOWER_IS_BETTER = frozenset({"wer", "speaker_error_rate"})
DEFAULT_MARGIN = 0.01
# Personal data in the reference: {{person:Мария}}. The mark says what the
# fragment is, not whether to mask it: that is the researcher's decision,
# made case by case, and the reference does not record it.
MARKUP = re.compile(r"\{\{([a-z_]+):(.*?)\}\}")

MANIFEST_TEMPLATE = """\
# One recording of the test corpus. See tests/corpus/README.md.

# The recording, relative to this folder.
audio = "{audio}"

# Language of the recording.
language = "ru"

# Number of people speaking.
speakers = 2

# Optional: the interview guide and the researcher's notes, as text files.
# guide = "guide.txt"
# notes = "notes.txt"
"""


class CorpusError(Exception):
    """Something about a case is missing or inconsistent; the message says what."""


class StageUnavailable(CorpusError):
    """The stage can't run here (no models, no guide): skip, don't fail."""


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    folder: Path
    manifest: dict

    @property
    def name(self) -> str:
        return f"{self.folder.parent.name}/{self.folder.name}"

    @property
    def audio(self) -> Path | None:
        value = str(self.manifest.get("audio") or "").strip()
        return self.folder / value if value else None

    @property
    def speakers(self) -> int:
        return int(self.manifest.get("speakers") or 2)

    @property
    def language(self) -> str:
        return str(self.manifest.get("language") or "ru")

    @property
    def margin(self) -> float:
        return float(self.manifest.get("margin", DEFAULT_MARGIN))

    def _text(self, key: str) -> str:
        value = str(self.manifest.get(key) or "").strip()
        if not value:
            return ""
        path = self.folder / value
        if not path.is_file():
            raise CorpusError(f"{self.name}: {key} file «{value}» is not in the case folder.")
        return path.read_text(encoding="utf-8")

    @property
    def guide(self) -> str:
        return self._text("guide")

    @property
    def notes(self) -> str:
        return self._text("notes")

    @property
    def transcript_path(self) -> Path:
        return self.folder / "cache" / "transcript.json"

    @property
    def transcript_meta_path(self) -> Path:
        return self.folder / "cache" / "transcript.meta.json"

    @property
    def reference_path(self) -> Path:
        return self.folder / "reference.txt"

    @property
    def baseline_path(self) -> Path:
        return self.folder / "expected" / "baseline.json"

    def snapshot_path(self, stage: str) -> Path:
        return self.folder / "expected" / f"{stage}.json"

    def output_path(self, stage: str) -> Path:
        return self.folder / "out" / f"{stage}.json"


def corpus_roots() -> list[Path]:
    """Where cases live: the private and public folders of the repository,
    plus any folders named in ALUMENANS_CORPUS."""

    roots = [CORPUS / "private", CORPUS / "public"]
    for extra in os.environ.get("ALUMENANS_CORPUS", "").split(os.pathsep):
        if extra.strip():
            roots.append(Path(extra.strip()))
    return roots


def load_case(folder: Path) -> Case:
    manifest_path = Path(folder) / MANIFEST
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise CorpusError(f"Can't read {manifest_path}: {error}") from error
    return Case(Path(folder), manifest)


def discover(roots: list[Path] | None = None) -> list[Case]:
    cases = []
    for root in roots if roots is not None else corpus_roots():
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir()):
            if (folder / MANIFEST).is_file():
                cases.append(load_case(folder))
    return cases


def select(names: list[str]) -> list[Case]:
    cases = discover()
    if not names:
        return cases
    chosen = [
        case for case in cases if case.name in names or case.folder.name in names
    ]
    missing = set(names) - {c.name for c in chosen} - {c.folder.name for c in chosen}
    if missing:
        raise CorpusError("No such case: " + ", ".join(sorted(missing)))
    return chosen


# ---------------------------------------------------------------------------
# Transcription: slow, so it runs once and is cached in the case folder
# ---------------------------------------------------------------------------


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_status(case: Case) -> str:
    """«ok», «missing», or «stale»: the recording or the number of speakers
    has changed since the transcript was made. When the recording itself is
    absent (only its transcript was shared), the cache is taken on trust."""

    if not case.transcript_path.is_file():
        return "missing"
    try:
        meta = json.loads(case.transcript_meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "stale"
    if meta.get("speakers") != case.speakers:
        return "stale"
    audio = case.audio
    if audio and audio.is_file() and meta.get("audio_sha256") != file_digest(audio):
        return "stale"
    return "ok"


def find_models() -> Path | None:
    """The speech models are large; reuse the ones an installed copy of the
    app has already downloaded when this checkout has none of its own."""

    from alumenans.transcription.worker import DEFAULT_MODELS

    candidates = [
        os.environ.get("ALUMENANS_MODELS"),
        DEFAULT_MODELS,
        Path.home() / "Library" / "Application Support" / "ALumenans" / "models" / "transcriber",
        Path.home() / ".alumenans" / "models" / "transcriber",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


def transcribe(case: Case, *, models: Path | None = None, force: bool = False) -> bool:
    """Runs the transcription worker on the recording. Returns False when
    the cached transcript was still good and nothing was done."""

    from alumenans import __version__
    from alumenans.paths import child_environment

    if not force and cache_status(case) == "ok":
        return False
    audio = case.audio
    if not audio or not audio.is_file():
        raise CorpusError(f"{case.name}: the recording «{case.manifest.get('audio')}» is not in the case folder.")
    models = models or find_models()
    case.transcript_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "alumenans.transcription.worker"]
    if models:
        command += ["--models", str(models)]
    command += [
        "transcribe", str(audio),
        "--output", str(case.transcript_path),
        "--speakers", str(case.speakers),
    ]
    guide = str(case.manifest.get("guide") or "").strip()
    if guide:
        command += ["--guide-file", str(case.folder / guide)]
    # The worker's stdout is its progress protocol; it is of no use here. A
    # pipe, not the null device: the worker reconfigures its stdout, and
    # that fails on /dev/null.
    finished = subprocess.run(
        command, env=child_environment(), stdout=subprocess.PIPE, check=False
    )
    if finished.returncode != 0 or not case.transcript_path.is_file():
        raise CorpusError(
            f"{case.name}: transcription failed (exit code {finished.returncode}). "
            "If the models are missing, run the app once or pass --models."
        )
    meta = {
        "audio_sha256": file_digest(audio),
        "speakers": case.speakers,
        "app_version": __version__,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    case.transcript_meta_path.write_text(
        json.dumps(meta, indent=1) + "\n", encoding="utf-8"
    )
    return True


def load_transcript(case: Case) -> dict:
    status = cache_status(case)
    if status == "missing":
        raise StageUnavailable(
            f"{case.name}: no cached transcript. Run: python tools/corpus.py transcribe {case.folder.name}"
        )
    if status == "stale":
        raise CorpusError(
            f"{case.name}: the cached transcript doesn't match the recording or "
            f"case.toml. Run: python tools/corpus.py transcribe {case.folder.name}"
        )
    payload = json.loads(case.transcript_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("turns"), list):
        raise CorpusError(f"{case.name}: the cached transcript has no turns.")
    return payload


# ---------------------------------------------------------------------------
# The text of a transcript, as the anonymization screen receives it
# ---------------------------------------------------------------------------


def _clock(seconds: float) -> str:
    value = max(0, int(float(seconds or 0)))
    hours, minutes, rest = value // 3600, (value % 3600) // 60, value % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{rest:02d}"
    return f"{minutes:02d}:{rest:02d}"


def transcript_text(turns: list[dict]) -> tuple[str, list[tuple[int, int]]]:
    """Joins turns into one text and says where each turn landed in it.

    This repeats ``proofTranscriptWithMap`` from the page, which is what
    builds the text sent to /analyze today. Once that logic lives in Python
    this copy goes away and the stage calls the real thing.

    Returns the text and, for every input turn, its (start, end) in the
    text, or (-1, -1) for a turn with no text."""

    blocks: list[str] = []
    ranges: list[tuple[int, int]] = [(-1, -1)] * len(turns)
    length = 0
    speaker = None
    parts: list[str] = []
    pending: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal length, speaker, parts, pending
        if speaker is None or not parts:
            pending = []
            return
        prefix = f"Спикер {speaker}: "
        block = prefix + " ".join(parts)
        inside = length + len(prefix)
        for index, text in pending:
            ranges[index] = (inside, inside + len(text))
            inside += len(text) + 1
        length += len(block) + 2
        blocks.append(block)
        speaker, parts, pending = None, [], []

    for index, turn in enumerate(turns):
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        if turn.get("topic_start"):
            flush()
            mark = f"[{_clock(turn.get('start') or 0)}]"
            blocks.append(mark)
            length += len(mark) + 2
        if speaker != turn.get("speaker"):
            flush()
            speaker = turn.get("speaker")
        parts.append(text)
        pending.append((index, text))
    flush()
    return "\n\n".join(blocks), ranges


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _reset_shared_state(case: Case) -> None:
    """The analysis code keeps the open study and recording in module
    globals. A stage must not see what the previous case left there."""

    from alumenans import proofreading
    from alumenans.current_audio import AUDIO_STATE
    from alumenans.detection.subject import set_subject_terms

    set_subject_terms(())
    AUDIO_STATE["paths"] = [case.audio] if case.audio else []
    proofreading.STUDY_WORDS.clear()


def _detection_models() -> dict:
    from alumenans.detection.models import MODEL_STATE, warmup

    if MODEL_STATE.get("setup") != "done":
        warmup()
    state = {key: MODEL_STATE.get(key) for key in ("natasha", "gliner")}
    not_ready = [name for name, value in state.items() if value != "ready"]
    if not_ready:
        raise StageUnavailable(
            "Anonymization models are not installed here: " + ", ".join(not_ready)
        )
    return state


def detect(case: Case, turns: list[dict]) -> dict:
    """Personal-data search over the whole transcript: the Python half of
    anonymization. What the page adds on top of it is not covered."""

    from alumenans.current_audio import current_pseudonym
    from alumenans.detection.spans import entity_groups, find_spans
    from alumenans.introduction import respondent_speaker

    models = _detection_models()
    _reset_shared_state(case)
    text, _ = transcript_text(turns)
    spans = find_spans(text)
    return {
        "models": models,
        "text": text,
        "spans": [dict(span, text=text[span["start"]:span["stop"]]) for span in spans],
        "entities": entity_groups(text, spans),
        "pseudonym": current_pseudonym(),
        "respondent": respondent_speaker(text),
    }


def stage_transcript(case: Case, transcript: dict) -> dict:
    """What the speech stage produced, without the per-word timing."""

    turns = [
        {
            "speaker": turn.get("speaker"),
            "start": turn.get("start"),
            "end": turn.get("end"),
            "text": turn.get("text"),
            "topic_start": bool(turn.get("topic_start")),
            "words": len(turn.get("words") or []),
        }
        for turn in transcript["turns"]
    ]
    rest = {key: value for key, value in transcript.items() if key != "turns"}
    return {"turns": turns, "other": rest}


def stage_proofreading(case: Case, transcript: dict) -> dict:
    from alumenans.proofreading import analyze_proofreading, normalize_transcript_text

    _reset_shared_state(case)
    # The analysis expects text that has been through cleanup, as the app
    # gives it.
    turns = [
        dict(turn, text=normalize_transcript_text(turn.get("text") or "")[0])
        for turn in transcript["turns"]
    ]
    return analyze_proofreading(turns, case.notes, case.guide)


def stage_detection(case: Case, transcript: dict) -> dict:
    return detect(case, transcript["turns"])


def stage_guide(case: Case, transcript: dict) -> dict:
    from alumenans.guide.report import build_report

    guide = case.guide.strip()
    if not guide:
        raise StageUnavailable(f"{case.name}: the case has no guide.")
    return build_report(guide, transcript["turns"])


STAGES = {
    "transcript": stage_transcript,
    "proofreading": stage_proofreading,
    "detection": stage_detection,
    "guide": stage_guide,
}


def run_stage(case: Case, stage: str, transcript: dict | None = None) -> str:
    """Runs one stage and returns its output as canonical JSON text."""

    from alumenans import i18n

    if transcript is None:
        transcript = load_transcript(case)
    # Messages in the output follow the interface language, which on a
    # developer's machine is whatever they last chose in the app.
    i18n.set_language("en", persist=False)
    return canonical(STAGES[stage](case, transcript))


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _rounded(value):
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {str(key): _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_rounded(item) for item in value)
    if isinstance(value, Path):
        return value.name
    return value


def canonical(value) -> str:
    """One stable text form, so that a text diff of two outputs is the diff
    of their content: sorted keys, rounded floats, one item per line."""

    return json.dumps(
        _rounded(value), ensure_ascii=False, indent=1, sort_keys=True, default=str
    ) + "\n"


def snapshot_diff(expected: str, actual: str, *, limit: int = 80) -> str:
    """A unified diff, cut short: the whole of it is in the out/ folder."""

    lines = list(
        difflib.unified_diff(
            expected.splitlines(), actual.splitlines(),
            "accepted", "current", lineterm="", n=2,
        )
    )
    if len(lines) > limit:
        lines = lines[:limit] + [f"... and {len(lines) - limit} more lines"]
    return "\n".join(lines)


def compare_snapshot(case: Case, stage: str, actual: str) -> str | None:
    """None when the output matches the accepted snapshot, otherwise the
    diff. The current output is kept in out/ either way."""

    output = case.output_path(stage)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(actual, encoding="utf-8")
    path = case.snapshot_path(stage)
    if not path.is_file():
        raise StageUnavailable(
            f"{case.name}: no accepted snapshot of «{stage}» yet. "
            f"Look at {output} and run: python tools/corpus.py accept {case.folder.name}"
        )
    expected = path.read_text(encoding="utf-8")
    return None if expected == actual else snapshot_diff(expected, actual)


def accept_snapshot(case: Case, stage: str, actual: str) -> bool:
    path = case.snapshot_path(stage)
    if path.is_file() and path.read_text(encoding="utf-8") == actual:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(actual, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# The reference a person makes, and the metrics against it
# ---------------------------------------------------------------------------


def strip_markup(text: str) -> tuple[str, list[dict]]:
    """Removes {{category:fragment}} marks; returns the clean text and where
    each marked fragment sits in it."""

    clean: list[str] = []
    marks: list[dict] = []
    position = 0
    last = 0
    for match in MARKUP.finditer(text):
        before = text[last:match.start()]
        clean.append(before)
        position += len(before)
        fragment = match.group(2)
        marks.append(
            {
                "start": position,
                "stop": position + len(fragment),
                "cat": match.group(1),
                "text": fragment,
            }
        )
        clean.append(fragment)
        position += len(fragment)
        last = match.end()
    clean.append(text[last:])
    return "".join(clean), marks


def parse_marked_reference(text: str):
    """Reads reference.txt: a timecoded transcript corrected by a person,
    with the personal data marked in place.

    Returns the reference for the recognition metrics and, per turn, the
    marked fragments."""

    from alumenans.evaluation import Reference, ReferenceTurn, parse_reference

    marked = parse_reference(text)
    turns = []
    marks = []
    for turn in marked.turns:
        clean, found = strip_markup(turn.text)
        turns.append(ReferenceTurn(turn.start, turn.speaker, clean))
        marks.append(found)
    return Reference(tuple(turns), marked.end), marks


def draft_reference(case: Case, *, mark: bool = True) -> str:
    """A starting point for reference.txt: the machine transcript in the
    reference format, with the detector's findings already marked. A person
    then corrects the words, the speakers and the marks by ear and by eye."""

    turns = [
        turn for turn in load_transcript(case)["turns"]
        if str(turn.get("text") or "").strip()
    ]
    spans: list[dict] = []
    ranges: list[tuple[int, int]] = []
    if mark:
        try:
            spans = detect(case, turns)["spans"]
            _, ranges = transcript_text(turns)
        except StageUnavailable:
            spans = []
    lines = []
    for index, turn in enumerate(turns):
        text = str(turn["text"]).strip()
        if spans:
            start, stop = ranges[index]
            inside = sorted(
                (span for span in spans if start <= span["start"] and span["stop"] <= stop),
                key=lambda span: span["start"],
                reverse=True,
            )
            edge = len(text)
            for span in inside:
                low, high = span["start"] - start, span["stop"] - start
                if high > edge:
                    continue  # overlaps the one already marked
                category = span.get("cat") or "other"
                text = f"{text[:low]}{{{{{category}:{text[low:high]}}}}}{text[high:]}"
                edge = low
        lines.append(f"[{_precise_clock(turn.get('start') or 0)}] Спикер {turn.get('speaker') or 1}: {text}")
    end = max((float(turn.get("end") or 0) for turn in turns), default=0.0)
    lines.append(f"[{_precise_clock(end + 0.1)}]")
    return "\n".join(lines) + "\n"


def _precise_clock(seconds: float) -> str:
    value = max(0.0, float(seconds))
    hours, rest = divmod(value, 3600)
    minutes, rest = divmod(rest, 60)
    stamp = f"{int(minutes):02d}:{rest:04.1f}"
    return f"{int(hours):02d}:{stamp}" if hours else stamp


def _overlaps(first: dict, second: dict) -> bool:
    return first["start"] < second["stop"] and first["stop"] > second["start"]


def metrics(case: Case) -> dict:
    """The numbers for one case, flat: name -> value. Detection numbers are
    left out when the anonymization models are not installed."""

    from alumenans.evaluation import anonymization_scores, evaluate

    if not case.reference_path.is_file():
        raise StageUnavailable(f"{case.name}: no reference.txt yet.")
    reference, marks = parse_marked_reference(
        case.reference_path.read_text(encoding="utf-8")
    )
    report = evaluate(reference, load_transcript(case))
    result = {
        "wer": report["recognition"]["wer"],
        "speaker_error_rate": report["speakers"]["speaker_error_rate"],
    }
    if not any(marks):
        return result

    # Detection is judged on the corrected text: in the app anonymization
    # comes after proofreading, not straight after recognition.
    turns = [
        {"speaker": turn.speaker, "start": turn.start, "text": turn.text}
        for turn in reference.turns
    ]
    try:
        # Every finding counts, whether the program suggests masking it or
        # leaving it: finding it is the program's job, deciding is not.
        found = detect(case, turns)["spans"]
    except StageUnavailable:
        return result
    _, ranges = transcript_text(turns)
    gold = [
        dict(mark, start=ranges[index][0] + mark["start"], stop=ranges[index][0] + mark["stop"])
        for index, turn_marks in enumerate(marks)
        for mark in turn_marks
    ]
    scores = anonymization_scores(
        [(mark["start"], mark["stop"]) for mark in gold],
        [(span["start"], span["stop"]) for span in found],
    )
    # Of the fragments that were found, how many got the right category.
    hits = [
        (mark, [span for span in found if _overlaps(span, mark)]) for mark in gold
    ]
    hits = [(mark, spans) for mark, spans in hits if spans]
    right = sum(
        1 for mark, spans in hits if any(span.get("cat") == mark["cat"] for span in spans)
    )
    result.update(
        {
            "detection_recall": scores["recall"],
            "detection_precision": scores["precision"],
            "category_accuracy": round(right / len(hits), 4) if hits else 0.0,
        }
    )
    return result


def load_baseline(case: Case) -> dict:
    if not case.baseline_path.is_file():
        raise StageUnavailable(
            f"{case.name}: no baseline yet. Run: python tools/corpus.py accept {case.folder.name}"
        )
    return json.loads(case.baseline_path.read_text(encoding="utf-8"))


def regressions(case: Case, current: dict, baseline: dict) -> list[str]:
    """The metrics that got worse than the baseline by more than the margin."""

    worse = []
    for name, before in sorted(baseline.items()):
        if name not in current:
            continue
        now = current[name]
        slipped = (
            now > before + case.margin
            if name in LOWER_IS_BETTER
            else now < before - case.margin
        )
        if slipped:
            worse.append(f"{name}: {before} -> {now}")
    return worse


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _available_stages(case: Case, wanted: list[str]):
    """Yields (stage, output) for every stage that can run here."""

    transcript = load_transcript(case)
    for stage in wanted:
        try:
            yield stage, run_stage(case, stage, transcript)
        except StageUnavailable as reason:
            print(f"  {stage}: skipped. {reason}")


def command_list(arguments) -> int:
    cases = discover()
    if not cases:
        print("No cases. Create one with: python tools/corpus.py new NAME")
        return 0
    for case in cases:
        audio = case.audio
        parts = [
            "recording " + ("present" if audio and audio.is_file() else "ABSENT"),
            "transcript " + cache_status(case),
            "reference " + ("present" if case.reference_path.is_file() else "none"),
            "expected " + (", ".join(
                stage for stage in STAGES if case.snapshot_path(stage).is_file()
            ) or "none"),
        ]
        print(f"{case.name}: " + "; ".join(parts))
    return 0


def command_new(arguments) -> int:
    folder = CORPUS / ("public" if arguments.public else "private") / arguments.name
    if folder.exists():
        raise CorpusError(f"{folder} already exists.")
    folder.mkdir(parents=True)
    (folder / MANIFEST).write_text(
        MANIFEST_TEMPLATE.format(audio="recording.m4a"), encoding="utf-8"
    )
    print(f"Created {folder}")
    print("Put the recording there and set its file name in case.toml.")
    return 0


def command_transcribe(arguments) -> int:
    for case in select(arguments.cases):
        print(f"{case.name}: transcribing, this takes a while...", flush=True)
        done = transcribe(case, models=arguments.models, force=arguments.force)
        print(f"{case.name}: " + ("done" if done else "cached transcript is up to date"))
    return 0


def command_draft(arguments) -> int:
    for case in select(arguments.cases):
        if case.reference_path.exists():
            print(f"{case.name}: reference.txt already exists, left as it is")
            continue
        case.reference_path.write_text(
            draft_reference(case, mark=not arguments.no_marks), encoding="utf-8"
        )
        print(f"{case.name}: wrote {case.reference_path}")
    return 0


def command_run(arguments) -> int:
    changed = 0
    for case in select(arguments.cases):
        print(case.name)
        for stage, actual in _available_stages(case, arguments.stage or list(STAGES)):
            try:
                diff = compare_snapshot(case, stage, actual)
            except StageUnavailable as reason:
                print(f"  {stage}: {reason}")
                continue
            if diff is None:
                print(f"  {stage}: unchanged")
            else:
                changed += 1
                print(f"  {stage}: CHANGED (full output: {case.output_path(stage)})")
                print("\n".join("    " + line for line in diff.splitlines()))
    return 1 if changed else 0


def command_accept(arguments) -> int:
    for case in select(arguments.cases):
        print(case.name)
        for stage, actual in _available_stages(case, arguments.stage or list(STAGES)):
            updated = accept_snapshot(case, stage, actual)
            print(f"  {stage}: " + ("accepted" if updated else "unchanged"))
        if arguments.stage:
            continue
        try:
            current = metrics(case)
        except StageUnavailable as reason:
            print(f"  metrics: skipped. {reason}")
            continue
        case.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        case.baseline_path.write_text(
            json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("  metrics: baseline recorded")
    return 0


def command_metrics(arguments) -> int:
    for case in select(arguments.cases):
        print(case.name)
        try:
            current = metrics(case)
        except StageUnavailable as reason:
            print(f"  skipped. {reason}")
            continue
        baseline = (
            json.loads(case.baseline_path.read_text(encoding="utf-8"))
            if case.baseline_path.is_file()
            else {}
        )
        for name, value in sorted(current.items()):
            before = baseline.get(name)
            note = "" if before is None or before == value else f"  (baseline {before})"
            print(f"  {name}: {value}{note}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="End-to-end test corpus")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="show the cases and what each one has").set_defaults(
        run=command_list
    )

    new = commands.add_parser("new", help="create an empty case folder")
    new.add_argument("name")
    new.add_argument("--public", action="store_true", help="in the committed part of the corpus")
    new.set_defaults(run=command_new)

    transcribe_ = commands.add_parser("transcribe", help="run speech recognition and cache the result")
    transcribe_.add_argument("cases", nargs="*")
    transcribe_.add_argument("--force", action="store_true", help="even if the cache is up to date")
    transcribe_.add_argument("--models", type=Path, help="folder with the speech models")
    transcribe_.set_defaults(run=command_transcribe)

    draft = commands.add_parser(
        "draft", help="write a first reference.txt from the machine transcript, to correct by hand"
    )
    draft.add_argument("cases", nargs="*")
    draft.add_argument("--no-marks", action="store_true", help="don't pre-mark personal data")
    draft.set_defaults(run=command_draft)

    for name, function, text in (
        ("run", command_run, "run the stages and show what changed since the accepted snapshots"),
        ("accept", command_accept, "record the current output as the accepted one"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("cases", nargs="*")
        sub.add_argument("--stage", action="append", choices=list(STAGES))
        sub.set_defaults(run=function)

    metrics_ = commands.add_parser("metrics", help="quality numbers against the reference")
    metrics_.add_argument("cases", nargs="*")
    metrics_.set_defaults(run=command_metrics)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        return arguments.run(arguments)
    except CorpusError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
