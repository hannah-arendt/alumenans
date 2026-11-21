#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The study: what twenty interviews have in common.

The guide, the study programme, the list of what doesn't need to be
anonymized, and the codebook belong not to an individual recording but to
the whole series. Without this concept, every interview drags along its
own copy of the shared documents, and the program doesn't know that twenty
recordings are one piece of work.

The design is deliberately simple: the study file sits in the study folder
and knows about the interviews only where they are and how they differ
from one another. The transcripts themselves stay in their own project
files, so they can be moved or handed to a colleague without dragging
everything else along.

A separate word about the "don't anonymize" list. It is the only place in
the program where a decision reads "this data doesn't need to be hidden",
so it is built as an explicit list you can see with your own eyes. The
program may suggest things for it from the guide and the study programme,
but never adds anything silently: an opaque decision not to hide personal
data is a leak that will be discovered late.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

from alumenans.i18n import t
from alumenans.storage import cipher
from alumenans.storage.cipher import StudySecret
# The SQLCipher driver under the stdlib name: its exceptions, Row and Binary
# are its own, and `except sqlite3.Error` must catch them.
from sqlcipher3 import dbapi2 as sqlite3


STUDY_FILE_NAME = ".study.alumenans"
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    guide      TEXT NOT NULL DEFAULT '',
    programme  TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- The study vocabulary: words missing from an ordinary Russian dictionary
-- that people in this field say all the time, such as «задонатить»,
-- «отписка», «краудфандинг». It serves recognition and proofreading, not
-- names: it has nothing to do with anonymization.
CREATE TABLE IF NOT EXISTS study_words (
    study_id   TEXT NOT NULL,
    word       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    added_at   REAL NOT NULL,
    PRIMARY KEY (study_id, word)
);

-- The lead puts the study file together and hands it out to the team. The
-- version is kept so that it shows in the window and travels with the
-- export: otherwise nobody would know an interview followed an outdated
-- guide.
CREATE TABLE IF NOT EXISTS study_version (
    study_id   TEXT PRIMARY KEY,
    version    TEXT NOT NULL DEFAULT '',
    issued_at  TEXT NOT NULL DEFAULT '',
    issued_by  TEXT NOT NULL DEFAULT '',
    inbox      TEXT NOT NULL DEFAULT '',
    imported_at REAL NOT NULL DEFAULT 0
);

-- Study documents: the desk research, «Поле» ("the field"), meeting
-- notes, notes on a single conversation. The guide and the programme are
-- separate columns of the study itself, since the program needs them all
-- the time; everything else goes here, as it has no predefined place and
-- shouldn't have one.
CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    study_id     TEXT NOT NULL,
    interview_id TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'document',
    title        TEXT NOT NULL DEFAULT '',
    filename     TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL DEFAULT '',
    added_at     REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS documents_by_file
    ON documents (study_id, interview_id, filename);

-- The interview register. The transcript lives in its own project file;
-- here is only what sets an interview apart from the others in the series.
CREATE TABLE IF NOT EXISTS interviews (
    id           TEXT PRIMARY KEY,
    study_id     TEXT NOT NULL,
    source_name  TEXT NOT NULL DEFAULT '',
    source_path  TEXT NOT NULL DEFAULT '',
    project_path TEXT NOT NULL DEFAULT '',
    label        TEXT NOT NULL DEFAULT '',
    branch       TEXT NOT NULL DEFAULT '',
    branch_title TEXT NOT NULL DEFAULT '',
    duration     REAL NOT NULL DEFAULT 0,
    recorded_at  REAL,
    added_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS interviews_by_study ON interviews (study_id, added_at);

-- The respondent's card: country, age, occupation and whatever else the
-- guide asks outright. It is the axis for comparing cases and, at the same
-- time, the list of traits whose combination can identify a person.
CREATE TABLE IF NOT EXISTS case_attributes (
    interview_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    value        TEXT NOT NULL DEFAULT '',
    turn_id      TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (interview_id, name)
);

-- The subject of the study: what doesn't need to be hidden.
CREATE TABLE IF NOT EXISTS subject_terms (
    study_id   TEXT NOT NULL,
    term       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    confirmed  INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    PRIMARY KEY (study_id, term)
);

-- The codebook is shared by the series: codes are created once per study.
CREATE TABLE IF NOT EXISTS codes (
    id         TEXT PRIMARY KEY,
    study_id   TEXT NOT NULL,
    parent_id  TEXT,
    name       TEXT NOT NULL DEFAULT '',
    definition TEXT NOT NULL DEFAULT '',
    origin     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class StudyError(RuntimeError):
    """A study file error that can be shown to the person."""


def study_path_for(folder: str | Path) -> Path:
    return Path(folder).expanduser() / STUDY_FILE_NAME


def normalize_term(value: str) -> str:
    """A term's key: no case, no ё, no extra spaces."""

    return re.sub(r"\s+", " ", str(value or "").strip()).lower().replace("ё", "е")


TERM_STEM = 4
_TERM_WORD = re.compile(r"[0-9a-zа-я-]+")


def matches_subject_term(fragment: str, term: str) -> bool:
    """Whether an anonymizer find matches a term from the subject of the study.

    Compared by word beginnings, to survive declension: «Таких дел» is
    «Такие дела» (the same name, inflected). But no further than that: the
    list says "this data doesn't need to be hidden", and a spurious match
    costs more here than a missed one. So the number of words must match
    too, not just their beginnings.
    """

    left = _TERM_WORD.findall(normalize_term(fragment))
    right = _TERM_WORD.findall(normalize_term(term))
    if not left or len(left) != len(right):
        return False
    for spoken, expected in zip(left, right):
        size = min(TERM_STEM, len(spoken), len(expected))
        if size < 3 and spoken != expected:
            return False
        if spoken[:size] != expected[:size]:
            return False
    return True


class StudyStore:
    """The study file. Opened on demand, like the project file."""

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
            raise StudyError(str(error)) from error
        except (OSError, sqlite3.Error) as error:
            raise StudyError(
                t("study.open_failed", error=error)
            ) from error
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
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

    def __enter__(self) -> "StudyStore":
        self.connect()
        return self

    def __exit__(self, *_exception) -> None:
        self.close()

    # -- the study itself --------------------------------------------------

    def ensure_study(self, title: str = "") -> str:
        connection = self.connect()
        row = connection.execute(
            "SELECT id FROM study ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is not None:
            if title:
                self.save_study(str(row["id"]), title=title)
            return str(row["id"])
        identifier = uuid.uuid4().hex
        now = time.time()
        with connection:
            connection.execute(
                "INSERT INTO study (id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (identifier, str(title or ""), now, now),
            )
        return identifier

    def save_study(
        self,
        study_id: str,
        *,
        title: str | None = None,
        guide: str | None = None,
        programme: str | None = None,
        notes: str | None = None,
    ) -> None:
        assignments = ["updated_at = ?"]
        values: list[Any] = [time.time()]
        for column, value in (
            ("title", title),
            ("guide", guide),
            ("programme", programme),
            ("notes", notes),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(str(value))
        values.append(study_id)
        connection = self.connect()
        with connection:
            connection.execute(
                f"UPDATE study SET {', '.join(assignments)} WHERE id = ?", values
            )

    # -- study vocabulary --------------------------------------------------

    def add_word(self, study_id: str, word: str, source: str = "вычитка") -> str:
        """A field word: «задонатить» ("to donate"), «отписка»
        ("unsubscribing"). Not a name or a title."""

        clean = re.sub(r"\s+", " ", str(word or "")).strip()
        if not clean or len(clean) > 80:
            raise StudyError(t("study.bad_word"))
        connection = self.connect()
        with connection:
            connection.execute(
                "INSERT OR IGNORE INTO study_words (study_id, word, source, added_at)"
                " VALUES (?, ?, ?, ?)",
                (study_id, clean, str(source or ""), time.time()),
            )
        return clean

    def words(self, study_id: str) -> list[str]:
        return [
            line["word"]
            for line in self.connect().execute(
                "SELECT word FROM study_words WHERE study_id = ? ORDER BY word",
                (study_id,),
            )
        ]

    def remove_word(self, study_id: str, word: str) -> None:
        connection = self.connect()
        with connection:
            connection.execute(
                "DELETE FROM study_words WHERE study_id = ? AND word = ?",
                (study_id, str(word or "")),
            )

    # -- study version -----------------------------------------------------

    def save_version(self, study_id: str, **fields) -> None:
        connection = self.connect()
        with connection:
            connection.execute(
                "INSERT INTO study_version"
                " (study_id, version, issued_at, issued_by, inbox, imported_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(study_id) DO UPDATE SET version = excluded.version,"
                " issued_at = excluded.issued_at, issued_by = excluded.issued_by,"
                " inbox = excluded.inbox, imported_at = excluded.imported_at",
                (
                    study_id,
                    str(fields.get("version") or ""),
                    str(fields.get("issued_at") or ""),
                    str(fields.get("issued_by") or ""),
                    str(fields.get("inbox") or ""),
                    time.time(),
                ),
            )

    def version(self, study_id: str) -> dict:
        line = self.connect().execute(
            "SELECT version, issued_at, issued_by, inbox, imported_at"
            " FROM study_version WHERE study_id = ?",
            (study_id,),
        ).fetchone()
        return dict(line) if line else {
            "version": "", "issued_at": "", "issued_by": "", "inbox": "",
            "imported_at": 0,
        }

    # -- documents ---------------------------------------------------------

    def save_document(
        self,
        study_id: str,
        *,
        filename: str,
        text: str,
        kind: str = "document",
        title: str = "",
        interview_id: str = "",
    ) -> str:
        """Puts a document into the study. Same file, same record."""

        name = str(filename or "").strip()
        if not name:
            raise StudyError(t("study.document_needs_name"))
        identifier = uuid.uuid4().hex
        connection = self.connect()
        with connection:
            connection.execute(
                "INSERT INTO documents"
                " (id, study_id, interview_id, kind, title, filename, text, added_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(study_id, interview_id, filename) DO UPDATE SET"
                " kind = excluded.kind, title = excluded.title,"
                " text = excluded.text, added_at = excluded.added_at",
                (
                    identifier,
                    study_id,
                    str(interview_id or ""),
                    str(kind or "document"),
                    str(title or Path(name).stem),
                    name,
                    str(text or ""),
                    time.time(),
                ),
            )
            row = connection.execute(
                "SELECT id FROM documents"
                " WHERE study_id = ? AND interview_id = ? AND filename = ?",
                (study_id, str(interview_id or ""), name),
            ).fetchone()
        return str(row["id"]) if row else identifier

    def documents(self, study_id: str, interview_id: str | None = None) -> list[dict]:
        """The series' documents; with an interview given, only its notes."""

        query = (
            "SELECT id, interview_id, kind, title, filename, text, added_at"
            " FROM documents WHERE study_id = ?"
        )
        values: list[Any] = [study_id]
        if interview_id is not None:
            query += " AND interview_id = ?"
            values.append(str(interview_id))
        query += " ORDER BY kind, title"
        return [dict(row) for row in self.connect().execute(query, values)]

    def remove_document(self, document_id: str) -> None:
        connection = self.connect()
        with connection:
            connection.execute(
                "DELETE FROM documents WHERE id = ?", (str(document_id),)
            )

    def load_study(self) -> dict | None:
        connection = self.connect()
        row = connection.execute(
            "SELECT * FROM study ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        identifier = str(row["id"])
        return {
            "id": identifier,
            "title": str(row["title"]),
            "guide": str(row["guide"]),
            "programme": str(row["programme"]),
            "notes": str(row["notes"]),
            "updated_at": float(row["updated_at"]),
            "interviews": self.interviews(identifier),
            "subject_terms": self.subject_terms(identifier),
        }

    # -- interviews in the series ------------------------------------------

    def register_interview(
        self,
        study_id: str,
        *,
        interview_id: str,
        source_name: str = "",
        source_path: str | Path = "",
        project_path: str | Path = "",
        label: str = "",
        duration: float = 0.0,
    ) -> str:
        connection = self.connect()
        with connection:
            connection.execute(
                """
                INSERT INTO interviews (
                    id, study_id, source_name, source_path, project_path,
                    label, duration, added_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    study_id = excluded.study_id,
                    source_name = excluded.source_name,
                    source_path = excluded.source_path,
                    project_path = excluded.project_path,
                    label = CASE WHEN excluded.label = '' THEN interviews.label
                                 ELSE excluded.label END,
                    duration = excluded.duration
                """,
                (
                    interview_id,
                    study_id,
                    str(source_name or ""),
                    str(source_path or ""),
                    str(project_path or ""),
                    str(label or ""),
                    float(duration or 0.0),
                    time.time(),
                ),
            )
        return interview_id

    def save_branch(
        self,
        interview_id: str,
        branch: str,
        branch_title: str = "",
    ) -> None:
        """The guide branch the interview went down is a feature of the case."""

        connection = self.connect()
        with connection:
            connection.execute(
                "UPDATE interviews SET branch = ?, branch_title = ? WHERE id = ?",
                (str(branch or ""), str(branch_title or ""), interview_id),
            )

    def save_case_attributes(
        self,
        interview_id: str,
        attributes: Iterable[dict],
    ) -> None:
        connection = self.connect()
        with connection:
            connection.executemany(
                "INSERT INTO case_attributes "
                "(interview_id, name, value, turn_id, source) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (interview_id, name) DO UPDATE SET "
                "value = excluded.value, turn_id = excluded.turn_id, "
                "source = excluded.source",
                [
                    (
                        interview_id,
                        str(item.get("name") or ""),
                        str(item.get("value") or ""),
                        str(item.get("turn_id") or ""),
                        str(item.get("source") or ""),
                    )
                    for item in attributes
                    if isinstance(item, dict) and str(item.get("name") or "")
                ],
            )

    # -- codebook ----------------------------------------------------------
    #
    # A code belongs to the study: that's exactly what makes it useful,
    # that «отписка из-за денег» ("cancelled because of money") in the
    # fifth interview and in the twelfth is one and the same code.

    def save_code(
        self,
        study_id: str,
        name: str,
        *,
        definition: str = "",
        origin: str = "",
        code_id: str = "",
    ) -> str:
        """Creates a code or edits one that already exists.

        The name is brought to a common form, and the match is sought
        case-insensitively: «Отписка» and «отписка» are one code, otherwise
        the codebook sprouts duplicates all by itself."""

        from alumenans.codebook import code_key, new_code_id, normalize_name, now

        clean = normalize_name(name)
        if not clean:
            raise StudyError(t("study.code_needs_name"))
        connection = self.connect()
        if not code_id:
            existing = next(
                (
                    line["id"]
                    for line in connection.execute(
                        "SELECT id, name FROM codes WHERE study_id = ?", (study_id,)
                    )
                    if code_key(line["name"]) == code_key(clean)
                ),
                None,
            )
            code_id = existing or new_code_id()
        connection.execute(
            "INSERT INTO codes (id, study_id, parent_id, name, definition, origin,"
            " created_at) VALUES (?, ?, NULL, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name = excluded.name,"
            " definition = excluded.definition, origin = excluded.origin",
            (code_id, study_id, clean, str(definition or ""), str(origin or ""), now()),
        )
        connection.commit()
        return code_id

    def codes(self, study_id: str) -> list:
        """The study's codebook."""

        from alumenans.codebook import Code, sort_codes

        lines = self.connect().execute(
            "SELECT id, name, definition, origin, created_at FROM codes"
            " WHERE study_id = ?",
            (study_id,),
        ).fetchall()
        return sort_codes(
            Code(
                id=line["id"],
                name=line["name"],
                definition=line["definition"],
                origin=line["origin"],
                created_at=line["created_at"],
            )
            for line in lines
        )

    def rename_code(self, study_id: str, code_id: str, name: str) -> list:
        """Renames a code together with everything that sits under it."""

        from alumenans.codebook import normalize_name, rename_in_tree

        new_name = normalize_name(name)
        if not new_name:
            raise StudyError(t("study.code_needs_name"))
        before = next((code for code in self.codes(study_id) if code.id == code_id), None)
        if before is None:
            raise StudyError(t("study.no_such_code"))
        connection = self.connect()
        for code in self.codes(study_id):
            after = rename_in_tree(code.name, before.name, new_name)
            if after != code.name:
                connection.execute(
                    "UPDATE codes SET name = ? WHERE id = ?", (after, code.id)
                )
        connection.commit()
        return self.codes(study_id)

    def merge_codes(self, study_id: str, source_id: str, target_id: str) -> str:
        """Merges one code into another.

        Two researchers will inevitably call the same thing by different
        names: «страх» ("fear") and «опасения» ("apprehension"). As long as
        they can't be merged, the codebook fills up with junk, and no
        analysis can be built on it. The markup itself isn't lost in the
        process: the interview carries it over to itself."""

        codes = {code.id: code for code in self.codes(study_id)}
        if source_id not in codes or target_id not in codes:
            raise StudyError(t("study.no_such_code"))
        if source_id == target_id:
            return target_id
        connection = self.connect()
        connection.execute("DELETE FROM codes WHERE id = ?", (source_id,))
        connection.commit()
        return target_id

    def remove_code(self, study_id: str, code_id: str) -> None:
        connection = self.connect()
        connection.execute(
            "DELETE FROM codes WHERE study_id = ? AND id = ?", (study_id, code_id)
        )
        connection.commit()

    def interviews(self, study_id: str) -> list[dict]:
        connection = self.connect()
        attributes: dict[str, dict[str, str]] = {}
        for row in connection.execute(
            "SELECT c.* FROM case_attributes c JOIN interviews i "
            "ON i.id = c.interview_id WHERE i.study_id = ?",
            (study_id,),
        ):
            attributes.setdefault(str(row["interview_id"]), {})[
                str(row["name"])
            ] = str(row["value"])

        return [
            {
                "id": str(row["id"]),
                "source_name": str(row["source_name"]),
                "source_path": str(row["source_path"]),
                "project_path": str(row["project_path"]),
                "label": str(row["label"]),
                "branch": str(row["branch"]),
                "branch_title": str(row["branch_title"]),
                "duration": float(row["duration"]),
                "added_at": float(row["added_at"]),
                "attributes": attributes.get(str(row["id"]), {}),
            }
            for row in connection.execute(
                "SELECT * FROM interviews WHERE study_id = ? ORDER BY added_at",
                (study_id,),
            )
        ]

    # -- subject of the study ----------------------------------------------

    def suggest_subject_terms(
        self,
        study_id: str,
        terms: Iterable[dict],
    ) -> int:
        """Stores suggestions without confirming them.

        A suggestion changes nothing in the anonymizer's behaviour until the
        person confirms it."""

        connection = self.connect()
        now = time.time()
        rows = []
        for item in terms:
            if not isinstance(item, dict):
                continue
            term = re.sub(r"\s+", " ", str(item.get("term") or "")).strip()
            if len(term) < 2:
                continue
            rows.append(
                (
                    study_id,
                    term,
                    str(item.get("kind") or ""),
                    str(item.get("source") or ""),
                    now,
                )
            )
        with connection:
            connection.executemany(
                "INSERT INTO subject_terms "
                "(study_id, term, kind, source, confirmed, created_at) "
                "VALUES (?, ?, ?, ?, 0, ?) "
                "ON CONFLICT (study_id, term) DO NOTHING",
                rows,
            )
        return len(rows)

    def set_subject_term(
        self,
        study_id: str,
        term: str,
        *,
        confirmed: bool,
        kind: str = "",
    ) -> None:
        connection = self.connect()
        cleaned = re.sub(r"\s+", " ", str(term or "")).strip()
        if not cleaned:
            return
        with connection:
            connection.execute(
                "INSERT INTO subject_terms "
                "(study_id, term, kind, source, confirmed, created_at) "
                "VALUES (?, ?, ?, 'вручную', ?, ?) "
                "ON CONFLICT (study_id, term) DO UPDATE SET "
                "confirmed = excluded.confirmed",
                (study_id, cleaned, str(kind or ""), 1 if confirmed else 0, time.time()),
            )

    def remove_subject_term(self, study_id: str, term: str) -> None:
        connection = self.connect()
        with connection:
            connection.execute(
                "DELETE FROM subject_terms WHERE study_id = ? AND term = ?",
                (study_id, re.sub(r"\s+", " ", str(term or "")).strip()),
            )

    def subject_terms(self, study_id: str, *, confirmed_only: bool = False) -> list[dict]:
        connection = self.connect()
        query = "SELECT * FROM subject_terms WHERE study_id = ?"
        if confirmed_only:
            query += " AND confirmed = 1"
        query += " ORDER BY confirmed DESC, term"
        return [
            {
                "term": str(row["term"]),
                "kind": str(row["kind"]),
                "source": str(row["source"]),
                "confirmed": bool(row["confirmed"]),
            }
            for row in connection.execute(query, (study_id,))
        ]

    def is_subject(self, study_id: str, value: str) -> bool:
        """A confirmed subject of the study is not to be anonymized."""

        if not normalize_term(value):
            return False
        return any(
            matches_subject_term(value, item["term"])
            for item in self.subject_terms(study_id, confirmed_only=True)
        )
