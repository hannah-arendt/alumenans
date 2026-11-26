#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The interview project file: the one place where all the work lives.

State used to live in the window's memory: a two-hour interview, an hour
of proofreading, the program closed by accident, and that was it. Here it
sits on disk in the study folder and survives both the window closing and
the audio file being deleted.

SQLite is part of Python's standard library, so no new dependencies
appear, and writes stay atomic even through a sudden power-off.

The main thing in this file is not the tables but the stable identifiers.
A word belongs to a turn and a turn to an interview. As long as edits are
textual, any anchoring falls apart at the very first edit; here it
survives the edit, because the turn's identifier is derived from the
sound, not from the text.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Sequence

from alumenans.interview_name import parse_interview_name
from alumenans.i18n import t
from alumenans.storage import cipher
from alumenans.storage.cipher import StudySecret
# The SQLCipher driver under the stdlib name: its exceptions, Row and Binary
# are its own, and `except sqlite3.Error` must catch them.
from sqlcipher3 import dbapi2 as sqlite3


# Two-part on purpose: the study file (".study.alumenans") shares the folder,
# and no recording name can turn into it.
PROJECT_SUFFIX = ".interview.alumenans"
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interview (
    id                TEXT PRIMARY KEY,
    source_name       TEXT NOT NULL DEFAULT '',
    source_path       TEXT NOT NULL DEFAULT '',
    duration          REAL NOT NULL DEFAULT 0,
    speaker_count     INTEGER,
    moderator_speaker INTEGER,
    guide             TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    anonymized_at     REAL,
    anonymization_left INTEGER NOT NULL DEFAULT -1,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS speakers (
    interview_id TEXT NOT NULL,
    speaker      INTEGER NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (interview_id, speaker),
    FOREIGN KEY (interview_id) REFERENCES interview (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS turns (
    id              TEXT PRIMARY KEY,
    interview_id    TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    start           REAL NOT NULL DEFAULT 0,
    end             REAL NOT NULL DEFAULT 0,
    speaker         INTEGER NOT NULL DEFAULT 1,
    text            TEXT NOT NULL DEFAULT '',
    topic_start     INTEGER NOT NULL DEFAULT 0,
    topic_reason    TEXT,
    topic_title     TEXT NOT NULL DEFAULT '',
    manually_edited INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (interview_id) REFERENCES interview (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS turns_by_interview
    ON turns (interview_id, ordinal);

CREATE TABLE IF NOT EXISTS words (
    turn_id       TEXT NOT NULL,
    ordinal       INTEGER NOT NULL,
    start         REAL NOT NULL DEFAULT 0,
    end           REAL NOT NULL DEFAULT 0,
    text          TEXT NOT NULL DEFAULT '',
    confidence    REAL,
    boundary_risk INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (turn_id, ordinal),
    FOREIGN KEY (turn_id) REFERENCES turns (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id TEXT NOT NULL,
    turn_id      TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT '',
    fragment     TEXT NOT NULL DEFAULT '',
    action       TEXT NOT NULL DEFAULT '',
    replacement  TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS decisions_by_interview
    ON decisions (interview_id, created_at);

-- Ключ анонимизации лежит здесь только зашифрованным.
--
-- Проектные файлы и файлы исследования передают друг другу: коллеге,
-- в архив, на другой компьютер. Открытый ключ в таком файле — это тот
-- самый ущерб, ради предотвращения которого затевалась анонимизация.
-- Поэтому хранится готовый архив под паролем, а не пары «псевдоним —
-- настоящее имя». Пароль нигде не хранится; забытый пароль означает,
-- что ключ не открыть, и это обычное свойство запароленных данных.
CREATE TABLE IF NOT EXISTS anonymization_key_archive (
    interview_id TEXT PRIMARY KEY,
    archive      BLOB NOT NULL,
    entries      INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);
"""

_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ProjectError(RuntimeError):
    """A project file error that can be shown to the person."""


def interview_stem(media_path: str | Path) -> str:
    """The project file's name: one per interview, even if there are two
    recordings.

    An interrupted interview sits on disk as the files «Татьяна1» and
    «Татьяна2» ("Tatiana1", "Tatiana2"), but there was one conversation,
    and there must be one piece of work on it."""

    media = Path(media_path).expanduser()
    parsed = parse_interview_name(media)
    if parsed.recognised:
        stem = "-".join(
            piece
            for piece in (
                parsed.study,
                parsed.interviewer,
                parsed.number,
                parsed.respondent,
            )
            if piece
        )
    else:
        stem = media.stem
    return _UNSAFE_NAME.sub("_", stem).strip(" .") or "интервью"


def project_path_for(media_path: str | Path, folder: str | Path) -> Path:
    """The project file is named after the recording and lives in the study
    folder.

    The audio file is a consumable: it is deleted once the interview has
    been processed, and the team has no plans to keep a corpus of
    recordings. The transcript, the proofreading decisions and the
    anonymization key must survive the audio file's deletion, so they never
    sit next to it."""

    media = Path(media_path).expanduser()
    return Path(folder).expanduser() / f"{interview_stem(media)}{PROJECT_SUFFIX}"


# The same word splitting as in the window: if the two diverge, the word
# highlighting during playback will start to drift.
WORD_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё'’·-]*")


def _split_timed_words(words) -> list[tuple]:
    """One entry may hold several words, so the time is split as well."""

    laid_out = []
    for text, start_offset, end_offset, confidence in words:
        parts = WORD_TOKEN_RE.findall(text)
        if not parts:
            continue
        right_text = end_offset if end_offset > start_offset else start_offset
        step = (right_text - start_offset) / len(parts)
        for number, relative in enumerate(parts):
            laid_out.append(
                (relative, start_offset + step * number, start_offset + step * (number + 1), confidence)
            )
    return laid_out


def _spread(turn_id, words, place, start_offset, end_offset, confidence) -> list[tuple]:
    """Splits a time span between the words that took a replacement's place."""

    right_text = end_offset if end_offset > start_offset else start_offset
    step = (right_text - start_offset) / max(1, len(words))
    return [
        (turn_id, place + number, start_offset + step * number, start_offset + step * (number + 1),
         word, confidence)
        for number, word in enumerate(words)
    ]


def summarize_project(path: str | Path, secret: StudySecret) -> dict[str, Any] | None:
    """What is known about the interview, without opening it fully.

    Returns None for a file that isn't a project. A file that can't be
    decrypted with this study's secret raises cipher.CipherError."""

    target = Path(path).expanduser()
    if not target.is_file():
        return None
    try:
        with closing(cipher.connect(target, secret, read_only=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT id, source_name, source_path, duration, speaker_count,"
                " updated_at, anonymized_at, anonymization_left"
                " FROM interview ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            turns = db.execute(
                "SELECT COUNT(*) FROM turns WHERE interview_id = ?", (row["id"],)
            ).fetchone()[0]
            edited = db.execute(
                "SELECT COUNT(*) FROM turns"
                " WHERE interview_id = ? AND manually_edited = 1",
                (row["id"],),
            ).fetchone()[0]
            # The file is open read-only, so a table can't be added to it.
            # Projects from earlier versions won't have it at all; that's no
            # reason to drop the interview from the series list.
            try:
                keys = db.execute(
                    "SELECT COALESCE(SUM(entries), 0) FROM anonymization_key_archive"
                    " WHERE interview_id = ?",
                    (row["id"],),
                ).fetchone()[0]
            except sqlite3.Error:
                keys = 0
    except sqlite3.Error:
        return None
    return {
        "path": str(target),
        "interview_id": row["id"],
        "source_name": row["source_name"] or "",
        "source_path": row["source_path"] or "",
        "duration": _number(row["duration"]),
        "speaker_count": row["speaker_count"],
        "updated_at": _number(row["updated_at"]),
        "turns": int(turns),
        "edited": int(edited),
        "anonymized": int(keys),
        "anonymization_done": bool(
            row["anonymized_at"] is not None and int(row["anonymization_left"]) == 0
        ),
    }


def project_summaries(
    folder: str | Path, secret: StudySecret
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """The interviews the study folder remembers, and the files it can't read.

    The work list is built from the project files, not from the recordings
    in the folder: recordings come and go, the work stays. A file that
    doesn't open with the study's password is reported, not silently left
    out: it may be someone's whole interview."""

    home = Path(folder).expanduser()
    if not home.is_dir():
        return [], []
    found = []
    unreadable = []
    for item in sorted(home.iterdir()):
        if not item.name.endswith(PROJECT_SUFFIX):
            continue
        try:
            summary = summarize_project(item, secret)
        except cipher.CipherError as error:
            unreadable.append({"name": item.name, "error": str(error)})
            continue
        if summary is not None:
            found.append(summary)
    return found, unreadable


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if result == result and abs(result) != float("inf") else fallback


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _integer(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


class ProjectStore:
    """One interview, one file. Opened and closed on demand."""

    def __init__(self, path: str | Path, secret: StudySecret) -> None:
        self.path = Path(path).expanduser()
        self._secret = secret
        self._connection: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = cipher.connect(self.path, self._secret)
        except cipher.CipherError as error:
            raise ProjectError(str(error)) from error
        except (OSError, sqlite3.Error) as error:
            raise ProjectError(
                t("project.open_failed", error=error)
            ) from error
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        # Project files from earlier versions knew nothing about
        # anonymization.
        columns = {
            line["name"]
            for line in connection.execute("PRAGMA table_info(interview)")
        }
        if "anonymized_at" not in columns:
            connection.execute("ALTER TABLE interview ADD COLUMN anonymized_at REAL")
        if "anonymization_left" not in columns:
            connection.execute(
                "ALTER TABLE interview ADD COLUMN anonymization_left"
                " INTEGER NOT NULL DEFAULT -1"
            )
        # It's the anonymized text that gets coded, not the original: a quote
        # goes off into reports and tables, and a real name in it can never
        # be caught up with again.
        turn_columns = {
            line["name"] for line in connection.execute("PRAGMA table_info(turns)")
        }
        # The person writes the topic title themselves, next to the timecode.
        if "topic_title" not in turn_columns:
            connection.execute(
                "ALTER TABLE turns ADD COLUMN topic_title TEXT NOT NULL DEFAULT ''"
            )
        if "anonymized_text" not in {
            line["name"] for line in connection.execute("PRAGMA table_info(turns)")
        }:
            connection.execute(
                "ALTER TABLE turns ADD COLUMN anonymized_text TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
        self._connection = connection
        return connection

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.commit()
            finally:
                self._connection.close()
            self._connection = None

    def __enter__(self) -> "ProjectStore":
        self.connect()
        return self

    def __exit__(self, *_exception) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def save_transcript(
        self,
        result: dict,
        *,
        source_path: str | Path = "",
        guide: str = "",
        notes: str = "",
        speaker_count: int | None = None,
    ) -> str:
        """Puts a fresh transcript into the file, replacing the previous
        version.

        Returns the interview identifier."""

        connection = self.connect()
        now = time.time()
        source_name = str(result.get("source_name") or "")
        interview_id = self._interview_id_for(source_name) or uuid.uuid4().hex

        turns = [turn for turn in (result.get("turns") or []) if isinstance(turn, dict)]
        with connection:
            connection.execute(
                """
                INSERT INTO interview (
                    id, source_name, source_path, duration, speaker_count,
                    moderator_speaker, guide, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    source_name = excluded.source_name,
                    source_path = excluded.source_path,
                    duration = excluded.duration,
                    speaker_count = excluded.speaker_count,
                    moderator_speaker = excluded.moderator_speaker,
                    guide = excluded.guide,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    interview_id,
                    source_name,
                    str(source_path or ""),
                    _number(result.get("duration")),
                    speaker_count,
                    _optional_number(result.get("moderator_speaker")),
                    str(guide or ""),
                    str(notes or ""),
                    now,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM words WHERE turn_id IN "
                "(SELECT id FROM turns WHERE interview_id = ?)",
                (interview_id,),
            )
            connection.execute(
                "DELETE FROM turns WHERE interview_id = ?", (interview_id,)
            )
            self._write_turns(connection, interview_id, turns)
        return interview_id

    def save_turns(self, interview_id: str, turns: Iterable[dict]) -> int:
        """Rewrites the turns, keeping the words of turns already known.

        Called by the proofreading autosave. Words are wiped only for the
        turns the person edited by hand: for the rest, the per-word timing
        is needed by the player and the suggestions."""

        connection = self.connect()
        rows = [
            turn
            for turn in turns
            if isinstance(turn, dict) and str(turn.get("id") or "")
        ]
        incoming = [str(turn["id"]) for turn in rows]
        keep_words = {
            str(turn["id"])
            for turn in rows
            if not turn.get("words") and not turn.get("manually_edited")
        }
        with connection:
            if incoming:
                placeholders = ",".join("?" for _ in incoming)
                connection.execute(
                    "DELETE FROM turns WHERE interview_id = ? "
                    f"AND id NOT IN ({placeholders})",
                    (interview_id, *incoming),
                )
            else:
                connection.execute(
                    "DELETE FROM turns WHERE interview_id = ?", (interview_id,)
                )
            self._write_turns(
                connection,
                interview_id,
                rows,
                skip_words_for=keep_words,
            )
            connection.execute(
                "UPDATE interview SET updated_at = ? WHERE id = ?",
                (time.time(), interview_id),
            )
        return len(rows)

    def save_context(
        self,
        interview_id: str,
        *,
        guide: str | None = None,
        notes: str | None = None,
        moderator_speaker: int | None = None,
    ) -> None:
        connection = self.connect()
        assignments = ["updated_at = ?"]
        values: list[Any] = [time.time()]
        if guide is not None:
            assignments.append("guide = ?")
            values.append(str(guide))
        if notes is not None:
            assignments.append("notes = ?")
            values.append(str(notes))
        if moderator_speaker is not None:
            assignments.append("moderator_speaker = ?")
            values.append(_integer(moderator_speaker, 0) or None)
        values.append(interview_id)
        with connection:
            connection.execute(
                f"UPDATE interview SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def save_speaker_roles(
        self,
        interview_id: str,
        roles: Iterable[dict],
    ) -> None:
        connection = self.connect()
        with connection:
            connection.execute(
                "DELETE FROM speakers WHERE interview_id = ?", (interview_id,)
            )
            connection.executemany(
                "INSERT INTO speakers (interview_id, speaker, label, role) "
                "VALUES (?, ?, ?, ?)",
                [
                    (
                        interview_id,
                        _integer(row.get("speaker"), 1),
                        str(row.get("label") or ""),
                        str(row.get("role") or ""),
                    )
                    for row in roles
                    if isinstance(row, dict)
                ],
            )

    def record_decision(
        self,
        interview_id: str,
        *,
        turn_id: str = "",
        kind: str = "",
        fragment: str = "",
        action: str = "",
        replacement: str = "",
    ) -> None:
        """Records a proofreading decision: what was suggested and what the
        person chose."""

        connection = self.connect()
        with connection:
            connection.execute(
                "INSERT INTO decisions "
                "(interview_id, turn_id, kind, fragment, action, replacement, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    interview_id,
                    str(turn_id or ""),
                    str(kind or ""),
                    str(fragment or "")[:400],
                    str(action or ""),
                    str(replacement or "")[:400],
                    time.time(),
                ),
            )

    def mark_anonymized(
        self,
        interview_id: str,
        *,
        left: int = 0,
    ) -> None:
        """Marks that the anonymization has been carried through to the end.

        A non-anonymized transcript must not be exported: quotes from it spread
        into tables, reports and exchange files, and can never be pulled
        back from there. So the state lives in the interview itself, not in
        the person's memory. ``left`` is how many finds remain unresolved:
        while there are more than zero, the work isn't finished."""

        connection = self.connect()
        connection.execute(
            "UPDATE interview SET anonymized_at = ?, anonymization_left = ?,"
            " updated_at = ? WHERE id = ?",
            (time.time(), max(0, int(left)), time.time(), interview_id),
        )
        connection.commit()

    def anonymization_state(self, interview_id: str) -> dict:
        """Whether the anonymization is finished."""

        line = self.connect().execute(
            "SELECT anonymized_at, anonymization_left FROM interview WHERE id = ?",
            (interview_id,),
        ).fetchone()
        if line is None:
            return {"done": False, "left": -1, "at": None,
                    "reason": t("project.not_transcribed")}
        when = line["anonymized_at"]
        remaining = int(line["anonymization_left"])
        if when is None:
            return {"done": False, "left": remaining, "at": None,
                    "reason": t("project.not_anonymized")}
        if remaining > 0:
            return {
                "done": False, "left": remaining, "at": when,
                "reason": t("project.unresolved", count=remaining),
            }
        return {"done": True, "left": 0, "at": when, "reason": ""}

    # -- anonymized text ---------------------------------------------------

    def save_anonymized_turns(self, interview_id: str, turns: Iterable[dict]) -> int:
        """Remembers what the turns look like after anonymization.

        A separate column rather than replacing the text: the original is
        needed so the person can go back to proofreading and see what they
        edited. But only the anonymized one gets exported and quoted."""

        connection = self.connect()
        saved = 0
        with connection:
            for turn in turns or []:
                if not isinstance(turn, dict):
                    continue
                identifier = str(turn.get("id") or "")
                if not identifier:
                    continue
                cursor = connection.execute(
                    "UPDATE turns SET anonymized_text = ?"
                    " WHERE id = ? AND interview_id = ?",
                    (str(turn.get("text") or ""), identifier, interview_id),
                )
                saved += cursor.rowcount
        return saved

    def anonymized_turns(self, interview_id: str) -> list[dict]:
        """The anonymized text. Empty means there's nothing to export yet."""

        return [
            {
                "id": line["id"],
                "ordinal": int(line["ordinal"]),
                "speaker": int(line["speaker"]),
                "start": _number(line["start"]),
                "end": _number(line["end"]),
                "text": line["anonymized_text"],
            }
            for line in self.connect().execute(
                "SELECT id, ordinal, speaker, start, end, anonymized_text FROM turns"
                " WHERE interview_id = ? AND anonymized_text <> ''"
                " ORDER BY ordinal",
                (interview_id,),
            )
        ]

    def turns_without_anonymized_text(self, interview_id: str) -> int:
        """Turns for which no anonymized text was sent.

        The original text must not be removed while any such turns exist:
        for them it would remain the only text, and it is not anonymized."""

        return int(
            self.connect().execute(
                "SELECT COUNT(*) FROM turns WHERE interview_id = ?"
                " AND TRIM(text) <> '' AND anonymized_text = ''",
                (interview_id,),
            ).fetchone()[0]
        )

    def _retimed_words(self, interview_id: str) -> list[tuple]:
        """Carries the word timings over to the anonymized text.

        Matching words take their time as is; where a replacement was made,
        the original piece's time is split among the new words. The
        precision needed here is not stopwatch precision but human
        precision: landing on the right spot in the recording when you
        re-listen to a quote."""

        from difflib import SequenceMatcher

        lines = self.connect().execute(
            "SELECT t.id AS turn_id, t.start AS turn_start, t.end AS turn_end,"
            " t.anonymized_text AS текст"
            " FROM turns t WHERE t.interview_id = ? AND t.anonymized_text <> ''"
            " ORDER BY t.ordinal",
            (interview_id,),
        ).fetchall()

        ready: list[tuple] = []
        for line in lines:
            words = [
                (str(row["text"]), _number(row["start"]), _number(row["end"]),
                 row["confidence"])
                for row in self.connect().execute(
                    "SELECT text, start, end, confidence FROM words"
                    " WHERE turn_id = ? ORDER BY ordinal",
                    (line["turn_id"],),
                )
            ]
            old = _split_timed_words(words)
            new_words = WORD_TOKEN_RE.findall(line["текст"])
            if not new_words:
                continue
            if not old:
                ready.extend(
                    _spread(line["turn_id"], new_words, 0,
                            _number(line["turn_start"]), _number(line["turn_end"]),
                            None)
                )
                continue

            place = 0
            pieces = SequenceMatcher(
                None,
                [word[0].casefold() for word in old],
                [word.casefold() for word in new_words],
                autojunk=False,
            ).get_opcodes()
            for tag, i1, i2, j1, j2 in pieces:
                if tag == "equal":
                    for offset in range(j2 - j1):
                        _, start_offset, end_offset, confidence = old[i1 + offset]
                        ready.append(
                            (line["turn_id"], place, start_offset, end_offset,
                             new_words[j1 + offset], confidence)
                        )
                        place += 1
                    continue
                if j1 == j2:
                    continue
                slice_ = old[i1:i2] or old[max(0, i1 - 1):i1] or old[:1]
                start_offset = slice_[0][1]
                end_offset = max(slice_[-1][2], start_offset)
                ready.extend(
                    _spread(line["turn_id"], new_words[j1:j2], place, start_offset, end_offset,
                            slice_[0][3])
                )
                place += j2 - j1
        return ready

    def original_text_traces(self, interview_id: str) -> dict:
        """How much non-anonymized text still sits in the project.

        There are four places, and the turn text is only one of them. The
        real name also sits in the timed words, in the proofreading decisions
        and in the deleted pages of the file itself. Cleaning up one and
        reporting the cleanup done is the worst outcome: the file looks clean.

        Words are counted separately: they aren't deleted but transplanted
        onto the anonymized text, otherwise the link between the recording
        and the words would be lost."""

        db = self.connect()
        turn_count = db.execute(
            "SELECT COUNT(*) FROM turns WHERE interview_id = ?"
            " AND anonymized_text <> '' AND text <> anonymized_text",
            (interview_id,),
        ).fetchone()[0]
        word_count = db.execute(
            "SELECT COUNT(*) FROM words w JOIN turns t ON t.id = w.turn_id"
            " WHERE t.interview_id = ?",
            (interview_id,),
        ).fetchone()[0]
        decision_count = db.execute(
            "SELECT COUNT(*) FROM decisions WHERE interview_id = ?",
            (interview_id,),
        ).fetchone()[0]
        return {"turns": int(turn_count), "words": int(word_count), "decisions": int(decision_count)}

    def forget_original_text(self, interview_id: str) -> dict:
        """Removes the original text, keeping the anonymized one.

        The turns aren't emptied but rewritten with the anonymized text: an
        empty proofreading would look like lost work. Proofreading decisions
        are deleted entirely: by their very nature they contain real
        names.

        Timed words aren't deleted but transplanted onto the anonymized
        text. Otherwise the link between the recording and the words would
        be lost: a turn could only be played as a whole, and in a
        six-hundred-word answer that's as good as not at all.

        VACUUM at the end: without it the deleted rows stay in the file as
        pages of free space, and the name can be found in it with an
        ordinary byte search."""

        remaining = self.turns_without_anonymized_text(interview_id)
        if remaining:
            raise ProjectError(
                t("project.missing_anonymized", count=remaining)
            )

        before = self.original_text_traces(interview_id)
        new_words = self._retimed_words(interview_id)
        connection = self.connect()
        with connection:
            connection.execute(
                "UPDATE turns SET text = anonymized_text"
                " WHERE interview_id = ? AND anonymized_text <> ''",
                (interview_id,),
            )
            connection.execute(
                "DELETE FROM words WHERE turn_id IN"
                " (SELECT id FROM turns WHERE interview_id = ?)",
                (interview_id,),
            )
            connection.executemany(
                "INSERT INTO words (turn_id, ordinal, start, end, text, confidence,"
                " boundary_risk) VALUES (?, ?, ?, ?, ?, ?, 0)",
                new_words,
            )
            connection.execute(
                "DELETE FROM decisions WHERE interview_id = ?", (interview_id,)
            )
        before["retimed"] = len(new_words)
        previous = connection.isolation_level
        try:
            connection.isolation_level = None
            connection.execute("VACUUM")
        except sqlite3.Error:
            pass
        finally:
            connection.isolation_level = previous
        return before

    def save_anonymization_key(
        self,
        interview_id: str,
        archive: bytes,
        entries: int = 0,
    ) -> None:
        """Puts the already encrypted key into the project, as it went to disk.

        The program accepts only a finished archive: it no longer has any
        means of laying out "pseudonym to real name" pairs in columns, and
        that's deliberate; otherwise one day someone would put them there."""

        if not isinstance(archive, (bytes, bytearray)) or not archive:
            raise ProjectError(t("project.key_must_be_encrypted"))
        connection = self.connect()
        with connection:
            connection.execute(
                "INSERT INTO anonymization_key_archive"
                " (interview_id, archive, entries, created_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(interview_id) DO UPDATE SET"
                " archive = excluded.archive, entries = excluded.entries,"
                " created_at = excluded.created_at",
                (interview_id, sqlite3.Binary(bytes(archive)), int(entries), time.time()),
            )

    def anonymization_key_archive(self, interview_id: str) -> bytes | None:
        """The same archive back. Without the password it's useless, as
        intended."""

        row = self.connect().execute(
            "SELECT archive FROM anonymization_key_archive WHERE interview_id = ?",
            (interview_id,),
        ).fetchone()
        return bytes(row["archive"]) if row is not None else None

    # -- reading -----------------------------------------------------------

    def load(self, interview_id: str | None = None) -> dict | None:
        """Returns the interview state in the same shape the window expects."""

        connection = self.connect()
        if interview_id:
            row = connection.execute(
                "SELECT * FROM interview WHERE id = ?", (interview_id,)
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM interview ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None

        identifier = str(row["id"])
        words: dict[str, list[dict]] = {}
        for word in connection.execute(
            "SELECT w.* FROM words w JOIN turns t ON t.id = w.turn_id "
            "WHERE t.interview_id = ? ORDER BY w.turn_id, w.ordinal",
            (identifier,),
        ):
            words.setdefault(str(word["turn_id"]), []).append(
                {
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "text": str(word["text"]),
                    "confidence": (
                        None
                        if word["confidence"] is None
                        else float(word["confidence"])
                    ),
                    "boundary_risk": bool(word["boundary_risk"]),
                }
            )

        turns = [
            {
                "id": str(turn["id"]),
                "start": float(turn["start"]),
                "end": float(turn["end"]),
                "speaker": int(turn["speaker"]),
                "text": str(turn["text"]),
                "topic_start": bool(turn["topic_start"]),
                "topic_reason": turn["topic_reason"],
                "topic_title": turn["topic_title"] if "topic_title" in turn.keys() else "",
                "manually_edited": bool(turn["manually_edited"]),
                "words": words.get(str(turn["id"]), []),
            }
            for turn in connection.execute(
                "SELECT * FROM turns WHERE interview_id = ? ORDER BY ordinal",
                (identifier,),
            )
        ]

        return {
            "interview_id": identifier,
            "source_name": str(row["source_name"]),
            "source_path": str(row["source_path"]),
            "duration": float(row["duration"]),
            "speaker_count": row["speaker_count"],
            "moderator_speaker": row["moderator_speaker"],
            "guide": str(row["guide"]),
            "notes": str(row["notes"]),
            "updated_at": float(row["updated_at"]),
            "turns": turns,
            "speaker_roles": [
                {
                    "speaker": int(item["speaker"]),
                    "label": str(item["label"]),
                    "role": str(item["role"]),
                }
                for item in connection.execute(
                    "SELECT * FROM speakers WHERE interview_id = ? ORDER BY speaker",
                    (identifier,),
                )
            ],
            "decisions": [
                {
                    "turn_id": str(item["turn_id"]),
                    "kind": str(item["kind"]),
                    "fragment": str(item["fragment"]),
                    "action": str(item["action"]),
                    "replacement": str(item["replacement"]),
                    "created_at": float(item["created_at"]),
                }
                for item in connection.execute(
                    "SELECT * FROM decisions WHERE interview_id = ? "
                    "ORDER BY created_at",
                    (identifier,),
                )
            ],
        }

    # -- internals ---------------------------------------------------------

    def _interview_id_for(self, source_name: str) -> str | None:
        connection = self.connect()
        row = connection.execute(
            "SELECT id FROM interview WHERE source_name = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (str(source_name or ""),),
        ).fetchone()
        return str(row["id"]) if row else None

    @staticmethod
    def _write_turns(
        connection: sqlite3.Connection,
        interview_id: str,
        turns: Sequence[dict],
        *,
        skip_words_for: set[str] | None = None,
    ) -> None:
        skip = skip_words_for or set()
        for ordinal, turn in enumerate(turns):
            identifier = str(turn.get("id") or "")
            if not identifier:
                continue
            connection.execute(
                """
                INSERT INTO turns (
                    id, interview_id, ordinal, start, end, speaker, text,
                    topic_start, topic_reason, topic_title, manually_edited
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    ordinal = excluded.ordinal,
                    start = excluded.start,
                    end = excluded.end,
                    speaker = excluded.speaker,
                    text = excluded.text,
                    topic_start = excluded.topic_start,
                    topic_reason = excluded.topic_reason,
                    topic_title = excluded.topic_title,
                    manually_edited = excluded.manually_edited
                """,
                (
                    identifier,
                    interview_id,
                    ordinal,
                    _number(turn.get("start")),
                    _number(turn.get("end")),
                    max(1, _integer(turn.get("speaker"), 1)),
                    str(turn.get("text") or ""),
                    1 if turn.get("topic_start") else 0,
                    turn.get("topic_reason"),
                    str(turn.get("topic_title") or ""),
                    1 if turn.get("manually_edited") else 0,
                ),
            )
            if identifier in skip:
                continue
            connection.execute("DELETE FROM words WHERE turn_id = ?", (identifier,))
            connection.executemany(
                "INSERT INTO words "
                "(turn_id, ordinal, start, end, text, confidence, boundary_risk) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        identifier,
                        index,
                        _number(word.get("start")),
                        _number(word.get("end")),
                        str(word.get("text") or ""),
                        _optional_number(word.get("confidence")),
                        1 if word.get("boundary_risk") else 0,
                    )
                    for index, word in enumerate(turn.get("words") or [])
                    if isinstance(word, dict)
                ],
            )

