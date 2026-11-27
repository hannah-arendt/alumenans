"""The interview file: where the transcript of one interview is kept.

One encrypted file per interview, in the study folder. It holds the
transcript as transcription returned it, written once and never changed,
and the transcript as it stands now. An edit rewrites the rows of the turns
it touched, in one transaction.

A file made by another version of the program is refused: there is no
conversion between versions.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

# The SQLCipher driver under the stdlib name: its exceptions are its own.
from sqlcipher3 import dbapi2 as sqlite3

from alumenans.document.transcript import Author, Transcript, Turn, Word, changed_turns
from alumenans.i18n import t
from alumenans.storage import cipher
from alumenans.storage.cipher import StudySecret

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- The transcript as transcription returned it, compressed. One row.
CREATE TABLE machine_transcript (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    content BLOB NOT NULL
);

-- What the transcript value carries besides its turns. One row.
CREATE TABLE document (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    revision     INTEGER NOT NULL,
    next_word_id INTEGER NOT NULL,
    next_turn_id INTEGER NOT NULL
);

CREATE TABLE turns (
    id      INTEGER PRIMARY KEY,
    ordinal INTEGER NOT NULL,
    speaker INTEGER NOT NULL
);

CREATE TABLE words (
    id         INTEGER PRIMARY KEY,
    turn_id    INTEGER NOT NULL REFERENCES turns (id) ON DELETE CASCADE,
    ordinal    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    start      REAL NOT NULL,
    end        REAL NOT NULL,
    confidence REAL,
    author     TEXT NOT NULL,
    origin     TEXT NOT NULL  -- machine word identifiers, as a JSON list
);

CREATE INDEX words_by_turn ON words (turn_id, ordinal);
"""


class InterviewFileError(RuntimeError):
    """The interview file can't be used; the text is for the person."""


class InterviewStore:
    def __init__(self, path: str | Path, secret: StudySecret) -> None:
        self.path = Path(path).expanduser()
        self._secret = secret
        self._connection: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = cipher.connect(self.path, self._secret)
        except cipher.CipherError as error:
            raise InterviewFileError(str(error)) from error
        except (OSError, sqlite3.Error) as error:
            raise InterviewFileError(t("project.open_failed", error=error)) from error
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            if not connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                with connection:
                    connection.executescript(SCHEMA)
                    connection.execute(
                        "INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),)
                    )
            elif _schema_version(connection) != str(SCHEMA_VERSION):
                raise InterviewFileError(t("project.other_version"))
        except BaseException:
            connection.close()
            raise
        self._connection = connection
        return connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def create(self, machine: Transcript) -> None:
        """Starts the interview from a fresh transcription, replacing
        whatever the file held."""

        connection = self.connect()
        with connection:
            connection.execute("DELETE FROM turns")
            connection.execute("DELETE FROM document")
            connection.execute(
                "INSERT OR REPLACE INTO machine_transcript VALUES (1, ?)",
                (zlib.compress(json.dumps(_as_json(machine)).encode("utf-8")),),
            )
            self._write(connection, machine, machine.turns)

    def machine(self) -> Transcript | None:
        """The transcript as transcription returned it."""

        row = self.connect().execute("SELECT content FROM machine_transcript").fetchone()
        return None if row is None else _from_json(json.loads(zlib.decompress(row[0])))

    def load(self) -> Transcript | None:
        """The transcript as it stands now; None before the first transcription."""

        connection = self.connect()
        document = connection.execute(
            "SELECT revision, next_word_id, next_turn_id FROM document"
        ).fetchone()
        if document is None:
            return None
        words: dict[int, list[Word]] = {}
        for turn_id, word_id, text, start, end, confidence, author, origin in connection.execute(
            "SELECT turn_id, id, text, start, end, confidence, author, origin"
            " FROM words ORDER BY turn_id, ordinal"
        ):
            words.setdefault(turn_id, []).append(
                Word(word_id, text, start, end, confidence, Author(author), tuple(json.loads(origin)))
            )
        turns = tuple(
            Turn(turn_id, speaker, tuple(words[turn_id]))
            for turn_id, speaker in connection.execute(
                "SELECT id, speaker FROM turns ORDER BY ordinal"
            )
        )
        return Transcript(turns, *document)

    def save(self, transcript: Transcript, previous: Transcript) -> None:
        """Brings the file from `previous`, which it holds, to `transcript`.

        Only the turns that differ are rewritten. Going back to an earlier
        version is the same operation."""

        changed, removed = changed_turns(previous, transcript)
        connection = self.connect()
        with connection:
            connection.executemany(
                "DELETE FROM turns WHERE id = ?", [(turn_id,) for turn_id in changed + removed]
            )
            self._write(connection, transcript, [transcript.turn(turn_id) for turn_id in changed])

    @staticmethod
    def _write(connection: sqlite3.Connection, transcript: Transcript, turns) -> None:
        """Inserts `turns`, which the file doesn't hold, and makes the order
        of turns and the counters those of `transcript`."""

        connection.executemany(
            "INSERT INTO turns VALUES (?, 0, ?)", [(turn.id, turn.speaker) for turn in turns]
        )
        connection.executemany(
            "INSERT INTO words VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    word.id,
                    turn.id,
                    ordinal,
                    word.text,
                    word.start,
                    word.end,
                    word.confidence,
                    word.author.value,
                    json.dumps(word.origin),
                )
                for turn in turns
                for ordinal, word in enumerate(turn.words)
            ],
        )
        connection.executemany(
            "UPDATE turns SET ordinal = ? WHERE id = ?",
            [(ordinal, turn.id) for ordinal, turn in enumerate(transcript.turns)],
        )
        connection.execute(
            "INSERT OR REPLACE INTO document VALUES (1, ?, ?, ?)",
            (transcript.revision, transcript.next_word_id, transcript.next_turn_id),
        )


def _schema_version(connection: sqlite3.Connection) -> str | None:
    try:
        row = connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    except sqlite3.Error:
        return None  # no such table: not a file of this program's making
    return None if row is None else str(row[0])


def _as_json(transcript: Transcript) -> dict:
    return {
        "revision": transcript.revision,
        "next_word_id": transcript.next_word_id,
        "next_turn_id": transcript.next_turn_id,
        "turns": [
            {
                "id": turn.id,
                "speaker": turn.speaker,
                "words": [
                    [word.id, word.text, word.start, word.end, word.confidence,
                     word.author.value, list(word.origin)]
                    for word in turn.words
                ],
            }
            for turn in transcript.turns
        ],
    }


def _from_json(data: dict) -> Transcript:
    return Transcript(
        tuple(
            Turn(
                turn["id"],
                turn["speaker"],
                tuple(
                    Word(word_id, text, start, end, confidence, Author(author), tuple(origin))
                    for word_id, text, start, end, confidence, author, origin in turn["words"]
                ),
            )
            for turn in data["turns"]
        ),
        data["revision"],
        data["next_word_id"],
        data["next_turn_id"],
    )
