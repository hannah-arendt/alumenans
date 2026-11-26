#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exchange with the shared side: the study file comes in, interviews go out.

The work is split in two. On the sociologist's device: the audio
recording, transcription, proofreading, anonymization; nothing leaves it.
In the shared folder: anonymized transcripts, coding, «Поле» ("Field").

Both edges of that boundary are described here, and both are files, not
the network. The program reads a file a person has put into the folder,
and writes a file a person will take from there. There is not a single
network call in it, and none must ever appear: that is the one property
that is simple to check.

About the version. The lead assembles the study file and hands it out to
the team; the trouble with shared folders is that somebody is working
from last year's guide and nobody knows it. So the version is stored,
shown in the window and travels along with the export: on the other
side you can see who worked with what.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from alumenans.i18n import t

__all__ = [
    "STUDY_FILE_NAME",
    "EXPORT_SUFFIX",
    "export_name",
    "read_study_file",
    "build_export",
]

STUDY_FILE_NAME = "исследование.json"
# By this ending, the script in the folder recognises the file to be sent.
EXPORT_SUFFIX = "-для-коллег.json"

FORMAT = 1


class ExchangeError(RuntimeError):
    """The exchange file is the wrong one or is corrupted."""


def export_name(stem: str) -> str:
    return f"{str(stem or 'интервью').strip()}{EXPORT_SUFFIX}"


def read_study_file(path) -> dict:
    """Parses the study file assembled by the lead.

    Everything except the title is optional: a study may start with just
    a guide and pick up the rest as it goes along."""

    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except OSError as error:
        raise ExchangeError(t("exchange.read_failed", error=error)) from error
    except ValueError as error:
        raise ExchangeError(t("exchange.study_corrupt", error=error)) from error
    if not isinstance(data, dict):
        raise ExchangeError(t("exchange.study_not_object"))

    title = str(data.get("title") or "").strip()
    if not title:
        raise ExchangeError(t("exchange.study_no_title"))

    return {
        "title": title,
        "guide": str(data.get("guide") or ""),
        "programme": str(data.get("programme") or ""),
        "version": str(data.get("version") or ""),
        "issued_at": str(data.get("issued_at") or ""),
        "issued_by": str(data.get("issued_by") or ""),
        # The address where the script in the folder sends finished
        # interviews. The program only remembers it and passes it on.
        "inbox": str(data.get("inbox") or ""),
        "terms": [
            str(word).strip()
            for word in data.get("terms") or []
            if str(word).strip()
        ],
        "words": [
            str(word).strip()
            for word in data.get("words") or []
            if str(word).strip()
        ],
        "documents": [
            {
                "name": str(document.get("name") or ""),
                "kind": str(document.get("kind") or "document"),
                "text": str(document.get("text") or ""),
            }
            for document in data.get("documents") or []
            if isinstance(document, dict)
        ],
    }


def build_export(*, study, interview, turns, notes="", coder="") -> dict:
    """Assembles what will go off to the shared folder.

    Only the anonymized text and the facts about the interview. There is
    no markup and no codebook here: coding is joint work, it lives in the
    shared tool, not on the device.

    No original text, no words with timings, no proofreading decisions,
    no key: all of that stays on the device and cannot get in here,
    because only what is listed below gets in here."""

    clean_turns = [
        {
            "id": str(turn.get("id") or ""),
            "ordinal": int(turn.get("ordinal") or 0),
            "speaker": int(turn.get("speaker") or 0),
            "start": float(turn.get("start") or 0.0),
            "end": float(turn.get("end") or 0.0),
            "text": str(turn.get("text") or ""),
        }
        for turn in turns or []
        if str(turn.get("text") or "").strip()
    ]
    if not clean_turns:
        raise ExchangeError(t("exchange.nothing_to_export"))

    return {
        "format": FORMAT,
        "made_at": time.strftime("%Y-%m-%d %H:%M"),
        "study": {
            "title": str(study.get("title") or ""),
            "version": str(study.get("version") or ""),
            "issued_at": str(study.get("issued_at") or ""),
        },
        "interview": {
            "key": str(interview.get("key") or ""),
            "study": str(interview.get("study") or ""),
            "interviewer": str(interview.get("interviewer") or ""),
            "number": str(interview.get("number") or ""),
            "respondent": str(interview.get("respondent") or ""),
            "duration": float(interview.get("duration") or 0.0),
            "moderator": interview.get("moderator"),
            "anonymized_at": interview.get("anonymized_at"),
        },
        "coder": str(coder or ""),
        "turns": clean_turns,
        "notes": str(notes or ""),
    }
