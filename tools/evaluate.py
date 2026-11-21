#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runs the metrics against the golden reference.

    python tools/evaluate.py --reference .golden/<file_name> \\
                             --result .golden/<file_name>

The result file is the very result.json that transcriber_worker writes.
To get one, it's enough to run the worker directly:

    python -m alumenans.transcription.worker transcribe recording.m4a \\
        --output result.json --speakers 2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The script lives outside the package; in a working copy the package is
# taken from src/, even if `pip install -e .` hasn't been run yet.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alumenans.evaluation import (  # noqa: E402
    anonymization_scores,
    evaluate,
    evaluate_speakers,
    format_report,
    format_speaker_report,
    load_hypothesis,
    looks_timecoded,
    parse_reference,
    parse_speaker_reference,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transcript quality metrics")
    parser.add_argument(
        "--reference",
        type=Path,
        required=True,
        help="reference: [01:23] Спикер 2: текст (timecode, speaker label, text)",
    )
    parser.add_argument(
        "--result",
        type=Path,
        required=True,
        help="transcription result (result.json)",
    )
    parser.add_argument(
        "--drop-fillers",
        action="store_true",
        help="don't count «э-э», «угу» and other fillers as errors",
    )
    parser.add_argument(
        "--anonymization",
        type=Path,
        help="JSON with fields gold and found: lists of [start, end] pairs",
    )
    parser.add_argument(
        "--json",
        type=Path,
        help="where to write the machine-readable report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        text = arguments.reference.read_text(encoding="utf-8")
        result = load_hypothesis(arguments.result)
        timecoded = looks_timecoded(text)
        if timecoded and result.get("timed") is False:
            raise ValueError(
                "A timecoded reference needs result.json from the transcription worker: "
                "a plain-text transcript has no turn times."
            )
        if timecoded:
            report = evaluate(
                parse_reference(text),
                result,
                keep_fillers=not arguments.drop_fillers,
            )
        else:
            blocks, unlabelled = parse_speaker_reference(text)
            report = evaluate_speakers(
                blocks,
                result,
                keep_fillers=not arguments.drop_fillers,
                unlabelled=unlabelled,
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Could not read the input files: {error}", file=sys.stderr)
        return 1

    print(format_report(report) if timecoded else format_speaker_report(report))
    if not timecoded:
        print("")
        print(
            "WER is not computed for this reference: it verifies voices, "
            "not words."
        )

    if arguments.anonymization:
        try:
            payload = json.loads(arguments.anonymization.read_text(encoding="utf-8"))
            report["anonymization"] = anonymization_scores(
                payload.get("gold") or [],
                payload.get("found") or [],
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"Could not read the anonymization annotations: {error}", file=sys.stderr)
            return 1
        scores = report["anonymization"]
        print("")
        print(
            f"Anonymization: precision {scores['precision'] * 100:.1f} %, "
            f"recall {scores['recall'] * 100:.1f} %, F1 {scores['f1'] * 100:.1f} %"
        )
        print(
            f"  missed {scores['missed']}, false alarms {scores['false_alarms']}"
        )

    if arguments.json:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nReport written: {arguments.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
