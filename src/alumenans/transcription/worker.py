#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Separate process for local transcription of interviews.

The stdout protocol consists of JSON lines, the finished transcript
included: it goes to the app as one "result" message, never through a file,
so no plaintext copy of the interview is left on disk (a crash or a kill
would leave a temporary file behind). Everything else, including what
libraries print, goes to stderr; see open_protocol_channel.

For use from the command line, --output still writes the result to a file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from collections import Counter
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Sequence

from alumenans.paths import INSTALL_ROOT, models_home
from alumenans.transcription.core import (
    AudioChunk,
    MAX_SPEAKERS,
    MIN_SPEAKERS,
    TARGET_SAMPLE_RATE,
    DiarizedSpan,
    TimedWord,
    TranscriptResult,
    TranscriptTurn,
    TranscriptionCancelled,
    TranscriptionError,
    assign_turn_identifiers,
    merge_speaker_spans,
    merge_transcript_turns,
    plan_asr_chunks,
    probe_media_duration,
    renumber_speakers,
    speaker_for_timed_word,
    split_joined_utterance_words,
    split_timed_words_into_phrases,
    frames_to_spans,
    timed_words_from_tokens,
    validate_media_path,
    validate_speaker_count,
    viterbi_path,
    words_to_transcript_turns,
)
from alumenans.i18n import t


# The same folder the window uses: next to the program or in the home
# support folder.
DEFAULT_MODELS = models_home()
ASR_MODEL_NAME = "gigaam-v3-e2e-ctc"
ASR_FILE_NAME = "v3_e2e_ctc.int8.onnx"
ASR_TARGET_SECONDS = 20.0
ASR_MINIMUM_SECONDS = 16.0
ASR_MAXIMUM_SECONDS = 24.0
ASR_MINIMUM_SILENCE_SECONDS = 0.6
ASR_FORCED_OVERLAP_SECONDS = 1.2
ASR_BOUNDARY_PADDING_SECONDS = 0.3
DIARIZATION_MINIMUM_WINDOW_SECONDS = 0.8
DIARIZATION_MINIMUM_SILENCE_MS = 600
EMBEDDING_FILE_NAME = "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"speaker-recongition-models/{EMBEDDING_FILE_NAME}"
)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class ModelPaths:
    root: Path

    @property
    def asr(self) -> Path:
        return self.root / "asr"

    @property
    def vad(self) -> Path:
        return self.root / "vad"

    @property
    def embedding(self) -> Path:
        return self.root / "embedding"

    @property
    def embedding_model(self) -> Path:
        return self.embedding / EMBEDDING_FILE_NAME


def open_protocol_channel():
    """Keeps the real stdout for the protocol and sends file descriptor 1 to stderr.

    Libraries write to stdout too: onnxruntime and sherpa-onnx warnings,
    sometimes from their own threads and straight to the descriptor. A line
    of theirs landing inside the transcript's JSON would lose the whole
    result. So the protocol gets a private copy of the descriptor, and
    descriptor 1 (with Python's sys.stdout on it) becomes stderr. Call only
    in the worker process itself: it rewires the whole process."""

    sys.stdout.flush()
    try:
        private = os.dup(1)
        os.dup2(2, 1)
    except OSError:
        # No usable stderr (a detached run): keep the plain stdout.
        return sys.stdout
    return os.fdopen(private, "w", encoding="utf-8", errors="replace", buffering=1)


class JsonProgress:
    def __init__(self, stream=None) -> None:
        _configure_stdio()
        self._stream = stream
        # Stages run in parallel threads; one message must never cut into another.
        self._lock = threading.Lock()

    def send(self, event: dict) -> None:
        # Compact: the transcript is one long line, and no one reads it but the app.
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            stream = self._stream or sys.stdout
            stream.write(line + "\n")
            stream.flush()

    def emit(
        self,
        stage: str,
        message: str,
        fraction: float | None = None,
        **details,
    ) -> None:
        event = {"type": "progress", "stage": stage, "message": message}
        if fraction is not None:
            event["fraction"] = max(0.0, min(1.0, float(fraction)))
        event.update({key: value for key, value in details.items() if value is not None})
        self.send(event)

    def result(self, result: TranscriptResult, output: Path | None = None) -> None:
        """The finished transcript, in the message itself unless it went to a file."""

        event = {
            "type": "result",
            "duration": result.duration,
            "speakers": len({turn.speaker for turn in result.turns}),
            "turns": len(result.turns),
        }
        if output is None:
            event["transcript"] = result.to_dict()
        else:
            event["output"] = str(output)
        self.send(event)

    def error(self, message: str) -> None:
        self.send({"type": "error", "message": message})


CancelCallback = Callable[[], bool]


def model_paths(root: str | Path) -> ModelPaths:
    return ModelPaths(Path(root).expanduser().resolve())


def models_ready(paths: ModelPaths) -> bool:
    return all(
        (
            (paths.asr / ASR_FILE_NAME).is_file(),
            any(paths.vad.glob("*.onnx")),
            paths.embedding_model.is_file(),
        )
    )


def _download(
    url: str,
    destination: Path,
    *,
    progress: JsonProgress,
    stage: str,
    message: str,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    if destination.is_file() and destination.stat().st_size:
        return destination

    request = urllib.request.Request(url, headers={"User-Agent": "InterviewTranscriber/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
                downloaded += len(block)
                progress.emit(
                    stage,
                    message,
                    downloaded / total if total else None,
                    downloaded=downloaded,
                    total=total or None,
                )
        if not partial.stat().st_size:
            raise TranscriptionError(t("transcription.empty_model"))
        os.replace(partial, destination)
        return destination
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def prepare_models(paths: ModelPaths, progress: JsonProgress) -> None:
    """Downloads the models once. This function deliberately allows network
    access."""

    paths.root.mkdir(parents=True, exist_ok=True)
    progress.emit("models", t("transcription.loading_asr"))
    import onnx_asr

    asr = onnx_asr.load_model(
        ASR_MODEL_NAME,
        paths.asr,
        quantization="int8",
        providers=["CPUExecutionProvider"],
    )
    del asr

    progress.emit("models", t("transcription.loading_vad"))
    vad = onnx_asr.load_vad(
        "silero",
        paths.vad,
        providers=["CPUExecutionProvider"],
    )
    del vad

    _download(
        EMBEDDING_URL,
        paths.embedding_model,
        progress=progress,
        stage="models",
        message=t("transcription.loading_diarization"),
    )
    if not models_ready(paths):
        raise TranscriptionError(t("transcription.models_failed"))
    progress.emit("models", t("transcription.models_ready"), 1.0)


def get_ffmpeg_executable() -> Path:
    configured = os.environ.get("TRANSCRIBER_FFMPEG")
    candidates = [
        Path(configured) if configured else None,
        INSTALL_ROOT / "tools" / "ffmpeg.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()

    try:
        import imageio_ffmpeg

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if bundled.is_file():
            return bundled.resolve()
    except Exception:
        pass

    system = shutil.which("ffmpeg")
    if system:
        return Path(system).resolve()
    raise TranscriptionError(t("transcription.no_ffmpeg"))


def _cancelled(cancel: CancelCallback | None) -> bool:
    return bool(cancel and cancel())


# ffmpeg writes the decoded sound straight into this process, never to a file:
# a decoded interview lying in a temp folder is the conversation in the clear,
# and a crash or a kill would leave it behind.
_PCM_CHUNK = 1 << 20  # bytes per read from ffmpeg's stdout
# ffmpeg's -progress lines: "key=value" with a lowercase key.
_PROGRESS_LINE = re.compile(r"^[a-z0-9_]+=")


class PcmBuffer:
    """16 kHz mono float32 samples, filled straight from ffmpeg.

    Allocated once from the expected duration and converted chunk by chunk,
    so a two-hour interview costs one float32 copy (~460 MB) rather than
    raw bytes plus float32 side by side. Every part of a split recording
    goes into the same buffer, one after another."""

    def __init__(self, expected_seconds: float | None = None) -> None:
        import numpy as np

        capacity = int((expected_seconds or 0) * TARGET_SAMPLE_RATE * 1.01) + TARGET_SAMPLE_RATE
        self._samples = np.empty(capacity, dtype="float32")
        self.length = 0
        self._odd = b""

    def write(self, chunk: bytes) -> None:
        import numpy as np

        data = self._odd + chunk
        usable = len(data) - len(data) % 2
        self._odd = data[usable:]
        if not usable:
            return
        values = np.frombuffer(data[:usable], dtype="<i2")
        end = self.length + len(values)
        if end > len(self._samples):
            # The duration was a guess (or unknown): grow by half again.
            grown = np.empty(max(end, int(len(self._samples) * 1.5)), dtype="float32")
            grown[: self.length] = self._samples[: self.length]
            self._samples = grown
        np.multiply(values, 1 / 32768.0, out=self._samples[self.length : end], casting="unsafe")
        self.length = end

    def end_part(self) -> None:
        # A part never ends mid-sample; a stray byte is dropped, not carried over.
        self._odd = b""

    @property
    def seconds(self) -> float:
        return self.length / TARGET_SAMPLE_RATE

    def samples(self):
        return self._samples[: self.length]


def decode_media(
    source: Path,
    target: PcmBuffer,
    *,
    duration: float | None,
    progress: JsonProgress,
    cancel: CancelCallback | None = None,
) -> None:
    """Decodes one file into the buffer: 16 kHz, mono, 16-bit, via a pipe."""

    ffmpeg = get_ffmpeg_executable()
    command = [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "-1",
        "-ac",
        "1",
        "-ar",
        str(TARGET_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        # Progress and errors share stderr; the sound has stdout to itself.
        "-progress",
        "pipe:2",
        "-nostats",
        "pipe:1",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
    )
    errors: list[str] = []
    before = target.seconds

    def read_stderr() -> None:
        # Read all the time, beside the sound: a full stderr pipe would stall
        # ffmpeg, and with it the read of stdout below.
        last_reported = -1.0
        assert process.stderr is not None
        for raw in process.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if not _PROGRESS_LINE.match(line):
                if line:
                    errors.append(line)
                    del errors[:-20]
                continue
            key, _, value = line.partition("=")
            if key not in {"out_time_us", "out_time_ms"}:
                continue
            try:
                current = int(value) / 1_000_000
            except ValueError:
                continue
            if current - last_reported < 0.05:
                continue
            last_reported = current
            progress.emit(
                "audio",
                t("transcription.preparing_audio"),
                current / duration if duration else None,
                processed_seconds=current,
                total_seconds=duration,
            )

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    try:
        assert process.stdout is not None
        while True:
            if _cancelled(cancel):
                process.kill()
                raise TranscriptionCancelled(t("transcription.cancelled"))
            chunk = process.stdout.read1(_PCM_CHUNK)
            if not chunk:
                break
            target.write(chunk)
        return_code = process.wait()
        reader.join(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        target.end_part()

    if return_code:
        text = "\n".join(errors)
        if "matches no streams" in text or "does not contain any stream" in text:
            raise TranscriptionError(t("transcription.no_audio_track"))
        detail = errors[-1] if errors else t("transcription.unknown_error")
        raise TranscriptionError(t("transcription.audio_failed", detail=detail))
    if target.seconds <= before:
        raise TranscriptionError(t("transcription.audio_empty"))
    progress.emit("audio", t("transcription.audio_ready"), 1.0)


def decode_media_parts(
    sources: Sequence[Path],
    *,
    duration: float | None,
    progress: JsonProgress,
    cancel: CancelCallback | None = None,
):
    """Prepares the parts of one recording as a single continuous sound.

    Interviews get interrupted: the phone died, someone stepped out for a
    smoke, storage ran out. The conversation is still one, though, and it
    has to be transcribed as a whole; otherwise the second part gets its
    own «Спикер 1» and «Спикер 2» ("Speaker 1", "Speaker 2"), unrelated to
    the first part's, and turn numbering starts over.

    Returns the samples and the start time of each part in them: the start
    times find the right file when the person wants to listen to something
    again."""

    parts = [Path(item) for item in sources]
    if not parts:
        raise TranscriptionError(t("transcription.no_files"))
    target = PcmBuffer(duration)
    if len(parts) == 1:
        decode_media(parts[0], target, duration=duration, progress=progress, cancel=cancel)
        return target.samples(), [0.0]

    lengths = [probe_media_duration(part) or 0.0 for part in parts]
    total = sum(lengths) or None
    offsets: list[float] = []
    for index, part in enumerate(parts):
        offsets.append(target.seconds)
        decode_media(
            part,
            target,
            duration=lengths[index] or None,
            progress=_PartProgress(progress, sum(lengths[:index]), total),
            cancel=cancel,
        )
    progress.emit("audio", t("transcription.parts_joined"), 1.0)
    return target.samples(), offsets


class _PartProgress:
    """Reports a part's preparation as a share of the whole recording."""

    def __init__(self, progress: JsonProgress, done: float, total: float | None):
        self._progress = progress
        self._done = done
        self._total = total

    def emit(self, stage, message, fraction=None, **extra):
        if self._total and extra.get("processed_seconds") is not None:
            fraction = (self._done + extra["processed_seconds"]) / self._total
            extra["processed_seconds"] = self._done + extra["processed_seconds"]
            extra["total_seconds"] = self._total
        elif self._total and fraction is not None:
            fraction = None
        self._progress.emit(stage, message, fraction, **extra)

    def __getattr__(self, name):
        return getattr(self._progress, name)


def _load_diarization_vad(paths: ModelPaths):
    _offline_model_environment()
    import onnx_asr

    return onnx_asr.load_vad(
        "silero",
        paths.vad,
        providers=["CPUExecutionProvider"],
    )


def _diarization_speech_ranges(samples, vad) -> list[tuple[float, float]]:
    import numpy as np

    duration = len(samples) / TARGET_SAMPLE_RATE
    batches = vad.segment_batch(
        np.expand_dims(samples, axis=0),
        np.asarray([len(samples)], dtype=np.int64),
        TARGET_SAMPLE_RATE,
        threshold=0.4,
        neg_threshold=0.25,
        min_speech_duration_ms=100,
        max_speech_duration_s=max(30.0, duration + 1.0),
        min_silence_duration_ms=DIARIZATION_MINIMUM_SILENCE_MS,
        speech_pad_ms=0,
    )
    segments = next(batches, ())
    return [
        (start / TARGET_SAMPLE_RATE, end / TARGET_SAMPLE_RATE)
        for start, end in segments
        if end > start
    ]


# The voiceprint window and the step between windows. The step is smaller
# than the window on purpose: Viterbi resegmentation finds a boundary more
# precisely the denser the frames are.
RESEGMENT_WINDOW_SECONDS = 1.5
RESEGMENT_HOP_SECONDS = 0.5
# How many times a difference in similarity outweighs the voice-switch
# penalty. Cosine similarity varies within narrow limits, so it has to be
# stretched.
RESEGMENT_SCALE = 12.0
RESEGMENT_SWITCH_PENALTY = 1.6


def _resegmentation_frames(
    speech_ranges: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float]]:
    """Dense overlapping windows inside speech.

    Silence is skipped: a print of it is useless yet costs just as much time."""

    frames: list[tuple[float, float]] = []
    for start, end in speech_ranges:
        position = start
        while position < end - 0.3:
            frames.append(
                (
                    max(0.0, position),
                    min(duration, position + RESEGMENT_WINDOW_SECONDS),
                )
            )
            position += RESEGMENT_HOP_SECONDS
    return frames


def _embedding_for_range(extractor, samples, start: float, end: float):
    duration = len(samples) / TARGET_SAMPLE_RATE
    if end - start < DIARIZATION_MINIMUM_WINDOW_SECONDS:
        middle = (start + end) / 2
        half = DIARIZATION_MINIMUM_WINDOW_SECONDS / 2
        start = max(0.0, middle - half)
        end = min(duration, middle + half)
        if end - start < DIARIZATION_MINIMUM_WINDOW_SECONDS:
            if start <= 0:
                end = min(duration, DIARIZATION_MINIMUM_WINDOW_SECONDS)
            else:
                start = max(0.0, duration - DIARIZATION_MINIMUM_WINDOW_SECONDS)
    return _normalized_embedding(
        extractor,
        samples[
            max(0, int(start * TARGET_SAMPLE_RATE)) :
            min(len(samples), int(end * TARGET_SAMPLE_RATE))
        ],
    )


def _cluster_speaker_embeddings(
    vectors,
    *,
    speaker_count: int,
) -> list[int]:
    import numpy as np
    import sherpa_onnx

    if len(vectors) < speaker_count:
        raise TranscriptionError(
            t("transcription.too_little_speech")
        )
    features = np.stack(vectors).astype(np.float32)
    labels = [
        int(label)
        for label in sherpa_onnx.FastClustering(
            sherpa_onnx.FastClusteringConfig(
                num_clusters=speaker_count,
                threshold=0.5,
            )
        )(features)
    ]
    if len(set(labels)) != speaker_count:
        raise TranscriptionError(
            t("transcription.speakers_unreliable")
        )
    return labels


def diarize(
    samples,
    paths: ModelPaths,
    *,
    speaker_count: int,
    progress: JsonProgress,
    cancel: CancelCallback | None = None,
) -> list[DiarizedSpan]:
    speaker_count = validate_speaker_count(speaker_count)
    progress.emit("speakers", t("transcription.separating_speakers"), 0.0)

    vad = _load_diarization_vad(paths)
    speech_ranges = _diarization_speech_ranges(samples, vad)
    windows = _resegmentation_frames(
        speech_ranges, len(samples) / TARGET_SAMPLE_RATE
    )
    if not windows:
        raise TranscriptionError(t("transcription.no_speech"))

    if speaker_count == 1:
        # One voice: nothing to separate. The voiceprints would still be
        # computed, though, and that is the longest part of the work; on an
        # hour-long recording it takes more time than recognition itself.
        # All speech is given to a single speaker, and from there on
        # everything proceeds as usual.
        span_start = min(span_start for span_start, _ in windows)
        span_end = max(span_end for _, span_end in windows)
        progress.emit("speakers", t("transcription.single_voice"), 1.0)
        return [DiarizedSpan(start=span_start, end=span_end, speaker=1)]

    extractor = _speaker_embedding_extractor(paths)
    embedded_windows = []
    vectors = []
    for index, (start, end) in enumerate(windows):
        if _cancelled(cancel):
            raise TranscriptionCancelled(t("transcription.cancelled"))
        vector = _embedding_for_range(extractor, samples, start, end)
        if vector is not None:
            embedded_windows.append((start, end))
            vectors.append(vector)
        progress.emit(
            "speakers",
            t("transcription.separating_speakers"),
            0.05 + 0.85 * (index + 1) / len(windows),
            processed_chunks=index + 1,
            total_chunks=len(windows),
        )

    labels = _cluster_speaker_embeddings(
        vectors,
        speaker_count=speaker_count,
    )

    # Clustering answers the question "how many voices are here and what are
    # they", but places the boundaries between them roughly, at the edges of
    # its own windows. Viterbi refines the boundaries: it looks at the same
    # prints, but frame by frame.
    spans = _resegment(
        vectors,
        embedded_windows,
        labels,
        speaker_count=speaker_count,
    )
    spans = renumber_speakers(spans)
    spans = merge_speaker_spans(spans)
    progress.emit(
        "speakers",
        t("transcription.voices_separated"),
        1.0,
    )
    return spans


def _resegment(
    vectors,
    windows: list[tuple[float, float]],
    labels: list[int],
    *,
    speaker_count: int,
):
    """Refines voice boundaries with one principle instead of a set of rules."""

    import numpy as np

    centroids = []
    for speaker in range(speaker_count):
        chosen = [
            vector
            for vector, label in zip(vectors, labels)
            if label == speaker
        ]
        if not chosen:
            return [
                DiarizedSpan(start, end, label)
                for (start, end), label in zip(windows, labels, strict=True)
            ]
        centroid = np.mean(chosen, axis=0)
        length = float(np.linalg.norm(centroid))
        centroids.append(centroid / length if length else centroid)

    matrix = np.stack(vectors) @ np.stack(centroids).T
    path = viterbi_path(
        (matrix * RESEGMENT_SCALE).tolist(),
        switch_penalty=RESEGMENT_SWITCH_PENALTY,
    )
    return frames_to_spans(windows, path, hop=RESEGMENT_HOP_SECONDS)


def _offline_model_environment() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def _speech_ranges(samples, vad) -> list[tuple[float, float]]:
    import numpy as np

    duration = len(samples) / TARGET_SAMPLE_RATE
    batches = vad.segment_batch(
        np.expand_dims(samples, axis=0),
        np.asarray([len(samples)], dtype=np.int64),
        TARGET_SAMPLE_RATE,
        threshold=0.4,
        neg_threshold=0.25,
        min_speech_duration_ms=100,
        max_speech_duration_s=max(ASR_MAXIMUM_SECONDS + 1.0, duration + 1.0),
        min_silence_duration_ms=ASR_MINIMUM_SILENCE_SECONDS * 1000,
        speech_pad_ms=0,
    )
    segments = next(batches, ())
    return [
        (start / TARGET_SAMPLE_RATE, end / TARGET_SAMPLE_RATE)
        for start, end in segments
    ]


def _owned_words(
    words: list[TimedWord],
    chunks: list[AudioChunk],
    index: int,
) -> list[TimedWord]:
    chunk = chunks[index]
    left = chunk.start
    right = chunk.end
    if index and chunks[index - 1].end > chunk.start:
        left = (chunks[index - 1].end + chunk.start) / 2
    if index + 1 < len(chunks) and chunks[index + 1].start < chunk.end:
        right = (chunk.end + chunks[index + 1].start) / 2

    result = []
    for word in words:
        midpoint = (word.start + word.end) / 2
        if midpoint < left:
            continue
        if midpoint > right or (
            midpoint == right and index + 1 < len(chunks)
        ):
            continue
        near_overlap_edge = (
            (left > chunk.start and abs(midpoint - left) <= 0.65)
            or (right < chunk.end and abs(midpoint - right) <= 0.65)
        )
        result.append(
            replace(
                word,
                boundary_risk=word.boundary_risk or near_overlap_edge,
            )
        )
    return result


def _speaker_embedding_extractor(paths: ModelPaths):
    import sherpa_onnx

    threads = max(1, min(4, (os.cpu_count() or 2) - 1))
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(paths.embedding_model),
        num_threads=threads,
        provider="cpu",
    )
    if not config.validate():
        raise TranscriptionError(t("transcription.embedding_failed"))
    return sherpa_onnx.SpeakerEmbeddingExtractor(config)


def _normalized_embedding(extractor, samples):
    import numpy as np

    if len(samples) < int(0.7 * TARGET_SAMPLE_RATE):
        return None
    stream = extractor.create_stream()
    stream.accept_waveform(TARGET_SAMPLE_RATE, samples)
    stream.input_finished()
    if not extractor.is_ready(stream):
        return None
    vector = np.asarray(extractor.compute(stream), dtype=np.float32)
    length = float(np.linalg.norm(vector))
    return vector / length if length else None


def _speaker_reference_ranges(
    spans: list[DiarizedSpan],
    *,
    maximum_per_speaker: int = 3,
) -> dict[int, list[tuple[float, float]]]:
    grouped: dict[int, list[DiarizedSpan]] = {}
    for span in spans:
        if span.end - span.start >= 2.0:
            grouped.setdefault(span.speaker, []).append(span)

    references: dict[int, list[tuple[float, float]]] = {}
    for speaker, candidates in grouped.items():
        ranges = []
        for span in sorted(
            candidates,
            key=lambda item: item.end - item.start,
            reverse=True,
        ):
            duration = span.end - span.start
            if duration >= 4.5:
                middle = (span.start + span.end) / 2
                start = middle - 2.0
                end = middle + 2.0
            else:
                start = span.start + 0.15
                end = span.end - 0.15
            if end - start >= 1.7:
                ranges.append((start, end))
            if len(ranges) >= maximum_per_speaker:
                break
        if ranges:
            references[speaker] = ranges
    return references


def _normalized_match_text(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold()).strip()


def _guide_questions(guide: str) -> list[str]:
    questions = []
    for raw_line in str(guide or "").splitlines():
        line = re.sub(r"^\s*(?:[-–—•]|\d+[.)])\s*", "", raw_line).strip()
        normalized = _normalized_match_text(line)
        if len(normalized.split()) >= 4 and (
            "?" in line or re.match(r"^\s*\d+[.)]", raw_line)
        ):
            questions.append(normalized)
    return questions


def _phrase_guide_similarity(phrase: list[TimedWord], questions: list[str]) -> float:
    text = _normalized_match_text(" ".join(word.text for word in phrase))
    if not text or not questions:
        return 0.0
    return max(SequenceMatcher(None, text, question).ratio() for question in questions)


def refine_dialogue_speakers(
    samples,
    words: list[TimedWord],
    spans: list[DiarizedSpan],
    paths: ModelPaths,
    *,
    guide: str = "",
) -> list[int | None]:
    """Rechecks the voice labels of short turns against the voiceprint."""

    import numpy as np

    phrases = split_timed_words_into_phrases(words)
    overrides: list[int | None] = [None] * len(phrases)
    candidates = [
        (index, phrase)
        for index, phrase in enumerate(phrases)
        if phrase
        and 0.35 <= phrase[-1].end - phrase[0].start <= 12.0
        and len(
            {
                speaker_for_timed_word(word, spans)
                for word in phrase
            }
        ) == 1
    ]
    if not candidates:
        return overrides

    try:
        extractor = _speaker_embedding_extractor(paths)
        centroids = {}
        for speaker, ranges in _speaker_reference_ranges(spans).items():
            vectors = []
            for start, end in ranges:
                vector = _normalized_embedding(
                    extractor,
                    samples[
                        max(0, int(start * TARGET_SAMPLE_RATE)) :
                        min(len(samples), int(end * TARGET_SAMPLE_RATE))
                    ],
                )
                if vector is not None:
                    vectors.append(vector)
            if vectors:
                centroid = np.mean(vectors, axis=0)
                length = float(np.linalg.norm(centroid))
                if length:
                    centroids[speaker] = centroid / length
        if len(centroids) < 2:
            return overrides

        questions = _guide_questions(guide)
        guide_votes = Counter()
        guide_similarity = {}
        for index, phrase in candidates:
            similarity = _phrase_guide_similarity(phrase, questions)
            guide_similarity[index] = similarity
            if similarity >= 0.74:
                guide_votes[speaker_for_timed_word(phrase[-1], spans)] += 1
        guide_speaker = None
        if guide_votes:
            ranked_votes = guide_votes.most_common(2)
            if ranked_votes[0][1] >= 2 and (
                len(ranked_votes) == 1 or ranked_votes[0][1] > ranked_votes[1][1]
            ):
                guide_speaker = ranked_votes[0][0]

        for index, phrase in candidates:
            vector = _embedding_for_range(
                extractor,
                samples,
                phrase[0].start - 0.04,
                phrase[-1].end + 0.04,
            )
            if vector is None:
                continue
            scores = sorted(
                (
                    (float(vector @ centroid), speaker)
                    for speaker, centroid in centroids.items()
                ),
                reverse=True,
            )
            best_score, best_speaker = scores[0]
            second_score = scores[1][0]
            current_speaker = speaker_for_timed_word(phrase[-1], spans)
            current_score = next(
                (score for score, speaker in scores if speaker == current_speaker),
                -1.0,
            )
            guide_support = (
                guide_speaker is not None
                and best_speaker == guide_speaker
                and guide_similarity.get(index, 0.0) >= 0.68
            )
            minimum_margin = 0.10 if guide_support else 0.16
            if (
                best_speaker != current_speaker
                and best_score >= 0.30
                and best_score - second_score >= minimum_margin
                and best_score - current_score >= minimum_margin
            ):
                overrides[index] = best_speaker
    except Exception:
        return [None] * len(phrases)
    return overrides


def recognize_words(
    samples,
    paths: ModelPaths,
    *,
    spans: list[DiarizedSpan] | None = None,
    progress: JsonProgress,
    cancel: CancelCallback | None = None,
) -> list[TimedWord]:
    """Recognises speech. This work doesn't depend on speaker labelling.

    That is exactly why it can be computed at the same time as
    diarisation: speakers are assigned to finished words and take no part
    in recognition."""

    _offline_model_environment()
    import onnx_asr

    progress.emit("speech", t("transcription.loading_recognition"))
    model = onnx_asr.load_model(
        ASR_MODEL_NAME,
        paths.asr,
        quantization="int8",
        providers=["CPUExecutionProvider"],
    ).with_timestamps()
    vad = onnx_asr.load_vad(
        "silero",
        paths.vad,
        providers=["CPUExecutionProvider"],
    )
    progress.emit("speech", t("transcription.finding_pauses"))
    speech_ranges = _speech_ranges(samples, vad)
    if not speech_ranges and spans:
        speech_ranges = [(spans[0].start, spans[-1].end)]
    duration = len(samples) / TARGET_SAMPLE_RATE
    chunks = plan_asr_chunks(
        duration,
        speech_ranges,
        target_seconds=ASR_TARGET_SECONDS,
        minimum_seconds=ASR_MINIMUM_SECONDS,
        maximum_seconds=ASR_MAXIMUM_SECONDS,
        minimum_silence_seconds=ASR_MINIMUM_SILENCE_SECONDS,
        forced_overlap_seconds=ASR_FORCED_OVERLAP_SECONDS,
        boundary_padding_seconds=ASR_BOUNDARY_PADDING_SECONDS,
    )
    if not chunks:
        raise TranscriptionError(t("transcription.no_speech"))

    words: list[TimedWord] = []
    for index, chunk in enumerate(chunks):
        if _cancelled(cancel):
            raise TranscriptionCancelled(t("transcription.cancelled"))
        start_frame = max(0, int(chunk.start * TARGET_SAMPLE_RATE))
        end_frame = min(len(samples), int(chunk.end * TARGET_SAMPLE_RATE))
        fragment = samples[start_frame:end_frame]
        if len(fragment) >= int(0.18 * TARGET_SAMPLE_RATE):
            recognized = model.recognize(
                fragment,
                sample_rate=TARGET_SAMPLE_RATE,
            )
            tokens = recognized.tokens or []
            timestamps = recognized.timestamps or []
            logprobs = getattr(recognized, "logprobs", None) or None
            chunk_words = timed_words_from_tokens(
                tokens,
                timestamps,
                chunk_start=start_frame / TARGET_SAMPLE_RATE,
                chunk_end=end_frame / TARGET_SAMPLE_RATE,
                logprobs=logprobs,
            )
            words.extend(_owned_words(chunk_words, chunks, index))
        progress.emit(
            "speech",
            t("transcription.recognising"),
            (index + 1) / len(chunks),
            processed_seconds=chunk.end,
            total_seconds=duration,
        )

    progress.emit("speech", t("transcription.recognised"), 1.0)
    return words


def build_turns(
    samples,
    words: list[TimedWord],
    spans: list[DiarizedSpan],
    paths: ModelPaths,
    *,
    guide: str = "",
    progress: JsonProgress,
) -> list[TranscriptTurn]:
    """Assigns speakers to recognised words. The fast part of the work."""

    words = split_joined_utterance_words(words, spans)
    progress.emit("speech", t("transcription.short_turns"), 1.0)
    speaker_overrides = refine_dialogue_speakers(
        samples,
        words,
        spans,
        paths,
        guide=guide,
    )
    turns = words_to_transcript_turns(
        words,
        spans,
        speaker_overrides=speaker_overrides,
    )
    turns = merge_transcript_turns(turns)
    if not turns:
        raise TranscriptionError(t("transcription.no_text"))
    return turns


def _voice_checker(samples, turns, spans, paths):
    """How much the turn resembles the interviewer's voice rather than the
    one it is attributed to.

    Computed from the same voiceprints as the rest of diarisation, so it
    needs no separate models. If no print could be taken, it honestly
    returns ``None``: "nothing to check with"."""

    import numpy as np

    try:
        extractor = _speaker_embedding_extractor(paths)
    except Exception:
        return None

    centroids: dict[int, object] = {}
    for speaker, ranges in _speaker_reference_ranges(spans).items():
        vectors = []
        for start, end in ranges:
            vector = _normalized_embedding(
                extractor,
                samples[
                    max(0, int(start * TARGET_SAMPLE_RATE)) :
                    min(len(samples), int(end * TARGET_SAMPLE_RATE))
                ],
            )
            if vector is not None:
                vectors.append(vector)
        if vectors:
            centroid = np.mean(vectors, axis=0)
            length = float(np.linalg.norm(centroid))
            if length:
                centroids[speaker] = centroid / length
    if len(centroids) < 2:
        return None

    def check(turn_index, current, moderator):
        if moderator not in centroids or current not in centroids:
            return None
        turn = turns[turn_index]
        vector = _embedding_for_range(
            extractor, samples, float(turn.start) - 0.04, float(turn.end) + 0.04
        )
        if vector is None:
            return None
        return float(vector @ centroids[moderator]) - float(
            vector @ centroids[current]
        )

    return check


# Recognition costs roughly twice as much as diarisation: under the former
# progress ranges, voices took about a third of the total time.
DIARIZATION_SHARE = 0.35


class MergedProgress:
    """One progress value for two stages running at the same time.

    Without this the bar would jitter: two threads report in an interleaved
    way, and the label would jump between «различаю говорящих» ("telling
    speakers apart") and «распознаю речь» ("recognising speech")."""

    def __init__(self, progress: JsonProgress) -> None:
        self._progress = progress
        self._lock = threading.Lock()
        self._parts = {"speakers": 0.0, "speech": 0.0}
        self._done: set[str] = set()

    def emit(self, stage, message, fraction=None, **details):
        with self._lock:
            if fraction is not None and stage in self._parts:
                self._parts[stage] = max(self._parts[stage], float(fraction))
            combined = (
                self._parts["speakers"] * DIARIZATION_SHARE
                + self._parts["speech"] * (1.0 - DIARIZATION_SHARE)
            )
            waiting = [
                name
                for name in ("speakers", "speech")
                if name not in self._done
            ]
        title = {
            ("speakers", "speech"): t("transcription.separating_and_recognising"),
            ("speakers",): t("transcription.separating_speakers_short"),
            ("speech",): t("transcription.recognising"),
        }.get(tuple(waiting), message)
        self._progress.emit("listening", title, combined, **details)

    def finished(self, stage: str) -> None:
        with self._lock:
            self._done.add(stage)
            if stage in self._parts:
                self._parts[stage] = 1.0

    def __getattr__(self, name):
        return getattr(self._progress, name)


def _run_together(jobs: dict, cancel: CancelCallback | None):
    """Runs independent stages at the same time, preserving failure behaviour.

    If one stage fails or the person pressed «остановить» ("stop"), the
    other must finish on its own, and the first real error must surface."""

    results: dict = {}
    failures: dict = {}
    stopped = threading.Event()

    def guard() -> bool:
        return stopped.is_set() or _cancelled(cancel)

    def worker(name, action):
        try:
            results[name] = action(guard)
        except BaseException as error:  # noqa: BLE001 — re-raised further up
            failures[name] = error
            stopped.set()

    threads = [
        threading.Thread(target=worker, args=(name, action), name=f"stage-{name}")
        for name, action in jobs.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if failures:
        # A user stop matters more than any other error: it is not a breakage.
        for error in failures.values():
            if isinstance(error, TranscriptionCancelled):
                raise error
        raise next(iter(failures.values()))
    return results


def transcribe(
    source_path: str | Path | Sequence[str | Path],
    paths: ModelPaths,
    *,
    speaker_count: int,
    guide: str = "",
    progress: JsonProgress,
    cancel: CancelCallback | None = None,
) -> TranscriptResult:
    # An interrupted interview arrives as several files, but the
    # conversation in them is one, and there must be a single transcript.
    given = (
        [source_path]
        if isinstance(source_path, (str, Path))
        else list(source_path)
    )
    sources = [validate_media_path(item) for item in given]
    source = sources[0]
    speaker_count = validate_speaker_count(speaker_count)
    if not models_ready(paths):
        raise TranscriptionError(t("transcription.models_missing"))
    duration_hint = sum(probe_media_duration(item) or 0.0 for item in sources) or None

    samples, _offsets = decode_media_parts(
        sources,
        duration=duration_hint,
        progress=progress,
        cancel=cancel,
    )
    duration = len(samples) / TARGET_SAMPLE_RATE

    # Recognition and voice separation don't depend on each other:
    # speakers are assigned to finished words. So there is no reason to
    # wait for one to end before starting the other.
    merged = MergedProgress(progress)

    def run_diarization(guard):
        try:
            return diarize(
                samples,
                paths,
                speaker_count=speaker_count,
                progress=merged,
                cancel=guard,
            )
        finally:
            merged.finished("speakers")

    def run_recognition(guard):
        try:
            return recognize_words(
                samples,
                paths,
                progress=merged,
                cancel=guard,
            )
        finally:
            merged.finished("speech")

    done = _run_together(
        {"speakers": run_diarization, "speech": run_recognition},
        cancel,
    )
    spans = done["speakers"]
    turns = build_turns(
        samples,
        done["speech"],
        spans,
        paths,
        guide=guide,
        progress=progress,
    )

    # The guide knows the content of the conversation and so sees what
    # the sound doesn't: a turn that matches a question word for word was
    # said by the interviewer.
    moderator = None
    if guide.strip() and speaker_count > 1:
        progress.emit("speech", t("transcription.checking_guide"), 1.0)
        try:
            from alumenans.guide.speakers import correct_speakers

            turns, moderator, fixes = correct_speakers(
                guide,
                turns,
                voice_check=_voice_checker(samples, turns, spans, paths),
            )
            applied = sum(1 for fix in fixes if fix.applied)
            if applied:
                turns = merge_transcript_turns(turns)
                progress.emit(
                    "speech",
                    t("transcription.guide_fixed", count=applied),
                    1.0,
                )
        except Exception:
            # Checking against the guide is a refinement, not a mandatory
            # stage.
            moderator = None

    progress.emit("speech", t("transcription.assembled"), 1.0)
    return TranscriptResult(
        source_name=source.name,
        duration=duration,
        turns=tuple(assign_turn_identifiers(turns, source.name)),
        moderator_speaker=moderator,
    )


def write_result(path: Path, result: TranscriptResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Локальная транскрибация интервью")
    parser.add_argument(
        "--models",
        type=Path,
        default=DEFAULT_MODELS,
        help="папка локальных моделей",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="однократно скачать модели")
    commands.add_parser("check", help="проверить готовность моделей")
    run = commands.add_parser("transcribe", help="транскрибировать медиафайл")
    run.add_argument("source", type=Path, nargs="+", help="один или несколько файлов одной записи")
    run.add_argument(
        "--output",
        type=Path,
        help="записать результат в файл (без него результат уходит в stdout)",
    )
    run.add_argument(
        "--speakers",
        type=int,
        default=2,
        choices=range(MIN_SPEAKERS, MAX_SPEAKERS + 1),
        help="известное число участников записи",
    )
    run.add_argument(
        "--cancel-file",
        type=Path,
        help="остановить работу, когда появится этот файл",
    )
    run.add_argument(
        "--guide-file",
        type=Path,
        help="текстовый гайд интервью",
    )
    run.add_argument(
        "--guide-stdin",
        action="store_true",
        help="прочитать гайд интервью из stdin (UTF-8)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    arguments = build_parser().parse_args(argv)
    paths = model_paths(arguments.models)
    progress = JsonProgress(open_protocol_channel())
    try:
        if arguments.command == "prepare":
            prepare_models(paths, progress)
            return 0
        if arguments.command == "check":
            progress.send({"type": "check", "ready": models_ready(paths)})
            return 0 if models_ready(paths) else 1

        cancel_file = (
            arguments.cancel_file.expanduser().resolve()
            if arguments.cancel_file
            else None
        )
        cancel = (lambda: cancel_file.is_file()) if cancel_file else None
        if arguments.guide_stdin:
            # The app hands the guide over the pipe, so no copy of it waits
            # in a temp folder.
            guide = sys.stdin.buffer.read().decode("utf-8", "replace")
        elif arguments.guide_file and arguments.guide_file.is_file():
            guide = arguments.guide_file.read_text(encoding="utf-8")
        else:
            guide = ""
        result = transcribe(
            arguments.source,
            paths,
            speaker_count=arguments.speakers,
            guide=guide,
            progress=progress,
            cancel=cancel,
        )
        if arguments.output is None:
            progress.result(result)
        else:
            output = arguments.output.expanduser().resolve()
            write_result(output, result)
            progress.result(result, output)
        return 0
    except KeyboardInterrupt:
        progress.error(t("transcription.cancelled"))
        return 2
    except TranscriptionCancelled as error:
        progress.error(str(error))
        return 2
    except TranscriptionError as error:
        progress.error(str(error))
        return 1
    except Exception as error:
        progress.error(str(error)[:600] or type(error).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
