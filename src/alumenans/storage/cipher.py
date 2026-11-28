#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Encryption of the study's SQLite files (SQLCipher, AES-256).

The study file and every interview file hold real names and the original,
un-anonymized speech, and they get passed around: to a colleague, into an
archive, onto another computer. So every page of them, the WAL included, is
encrypted, and one study password opens them all.

The password is stretched once per unlock (PBKDF2-HMAC-SHA512) into a raw
256-bit key. Every file of the study is opened with that raw key plus the
study's salt, which SQLCipher stores as the file's first 16 bytes in the
clear. Raw keys skip SQLCipher's own per-file key derivation, so listing a
study with dozens of interviews costs one derivation, not one per file. And
since every file carries the salt, an interview file sent on its own still
opens with the password alone.

Python strings and bytes can't be wiped from memory, so the key lives in the
process until it exits; it is never written to disk or logged.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlite3

from alumenans.i18n import t

__all__ = [
    "CipherError",
    "NotAStudyFile",
    "NotEncrypted",
    "OtherStudy",
    "StudySecret",
    "WrongPassword",
    "connect",
    "read_salt",
]

KDF_ITERATIONS = 256_000
KEY_SIZE = 32
SALT_SIZE = 16
PLAIN_SQLITE_HEADER = b"SQLite format 3\x00"
MIN_PASSWORD_LENGTH = 8


class CipherError(RuntimeError):
    """A study file can't be opened with this secret; the text is for people."""


class WrongPassword(CipherError):
    def __init__(self) -> None:
        super().__init__(t("study.wrong_password"))


class NotEncrypted(CipherError):
    def __init__(self) -> None:
        super().__init__(t("study.not_encrypted"))


class OtherStudy(CipherError):
    def __init__(self) -> None:
        super().__init__(t("study.other_study"))


class NotAStudyFile(CipherError):
    def __init__(self) -> None:
        super().__init__(t("study.not_a_study_file"))


@dataclass(frozen=True, repr=False)
class StudySecret:
    """The study's salt and the key derived from its password."""

    salt: bytes
    key: bytes

    def __post_init__(self) -> None:
        if len(self.salt) != SALT_SIZE or len(self.key) != KEY_SIZE:
            raise ValueError("a study secret needs a 16-byte salt and a 32-byte key")

    def __repr__(self) -> str:
        return "StudySecret(<hidden>)"

    @staticmethod
    def derive(password: str, salt: bytes) -> "StudySecret":
        # NFC: the same password typed on another system may arrive with
        # «й» composed or decomposed, and must give the same key.
        secret = unicodedata.normalize("NFC", str(password)).encode("utf-8")
        key = hashlib.pbkdf2_hmac("sha512", secret, salt, KDF_ITERATIONS, KEY_SIZE)
        return StudySecret(salt=salt, key=key)

    @classmethod
    def create(cls, password: str) -> "StudySecret":
        return cls.derive(password, os.urandom(SALT_SIZE))

    @classmethod
    def for_file(cls, path: str | Path, password: str) -> "StudySecret":
        return cls.derive(password, read_salt(path))

    def pragma_literal(self) -> str:
        # PRAGMA key takes no bound parameters, so the literal is built here,
        # from fixed-length bytes only: the password itself never reaches SQL.
        return f"\"x'{self.key.hex()}{self.salt.hex()}'\""


def read_salt(path: str | Path) -> bytes:
    """The salt an encrypted file starts with."""

    try:
        with open(Path(path).expanduser(), "rb") as file:
            header = file.read(SALT_SIZE)
    except OSError as error:
        raise NotAStudyFile() from error
    if header == PLAIN_SQLITE_HEADER:
        raise NotEncrypted()
    if len(header) != SALT_SIZE:
        raise NotAStudyFile()
    return header


def connect(
    path: str | Path,
    secret: StudySecret,
    *,
    read_only: bool = False,
    timeout: float = 10,
) -> sqlite3.Connection:
    """Opens (or creates) an encrypted database of the study.

    A file that already exists is checked first: a plaintext SQLite file, or
    one made with another study's salt, gets its own message instead of the
    generic "wrong password"."""

    target = Path(path).expanduser()
    exists = target.is_file() and target.stat().st_size > 0
    if exists and read_salt(target) != secret.salt:
        raise OtherStudy()
    # The window calls the application on a new thread each time, so a store
    # is opened on one thread and used on others. The stores' owner lets one
    # call in at a time.
    connection = sqlite3.connect(str(target), timeout=timeout, check_same_thread=False)
    try:
        # A wrong key is an ordinary event here; SQLCipher would otherwise
        # write its own error lines to stderr.
        connection.execute("PRAGMA cipher_log_level = NONE")
        connection.execute(f"PRAGMA key = {secret.pragma_literal()}")
        # Pinned so that a future SQLCipher can't change the file format.
        connection.execute("PRAGMA cipher_compatibility = 4")
        try:
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as error:
            raise WrongPassword() from error
        # Temporary tables and VACUUM's work copy stay in memory.
        connection.execute("PRAGMA temp_store = MEMORY")
        if read_only:
            # Not "?mode=ro": a read-only connection to a WAL database leaves
            # empty "-wal" and "-shm" files behind that it may not delete.
            connection.execute("PRAGMA query_only = ON")
    except BaseException:
        connection.close()
        raise
    return connection


def cipher_details(connection: sqlite3.Connection) -> dict[str, str]:
    """What the build encrypts with; the self-test reports it."""

    def one(pragma: str) -> str:
        row = connection.execute(f"PRAGMA {pragma}").fetchone()
        return str(row[0]) if row else ""

    return {
        "version": one("cipher_version"),
        "provider": one("cipher_provider"),
        "provider_version": one("cipher_provider_version"),
    }
