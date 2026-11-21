#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A password-protected file: an ordinary zip with AES-256 encryption.

The anonymization key restores personal data, so it must leave the
program locked. The most boring format was chosen: a zip with WinZip AES
(AE-2) encryption. It opens in 7-Zip, WinRAR, Total Commander and
anything that handles ordinary archives; there's no need to install a
program for the recipient, or to explain what to open it with.

It is all built with the standard library. The program already has far
too many new dependencies for a first run, and the cryptography needed
here is exactly one, long-documented scheme: PBKDF2-HMAC-SHA1 from
hashlib, AES in counter mode, and HMAC-SHA1 for the integrity check.

Important: this file is a copy. The key stays in the study's project
file, so a forgotten password does not destroy the work.
"""

import hashlib
import hmac
import os
import struct
import time
import zlib

from alumenans.i18n import t

__all__ = ["make_secret_zip", "read_secret_zip", "SecretZipError"]


class SecretZipError(Exception):
    """The archive is the wrong one, damaged, or the password doesn't fit."""


# -- AES-256 -------------------------------------------------------------
#
# The cipher is only needed in one direction: counter mode encrypts and
# decrypts with the very same transform, so there is no inverse here.


def _build_sbox():
    """The substitution table is built on the spot, so as not to store 256
    numbers."""

    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        value = q ^ _rotl(q, 1) ^ _rotl(q, 2) ^ _rotl(q, 3) ^ _rotl(q, 4)
        sbox[p] = (value ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


def _rotl(value, count):
    return ((value << count) | (value >> (8 - count))) & 0xFF


def _xtime(value):
    value <<= 1
    return (value ^ 0x1B) & 0xFF if value & 0x100 else value


SBOX = _build_sbox()
DOUBLE = [_xtime(value) for value in range(256)]
TRIPLE = [DOUBLE[value] ^ value for value in range(256)]


def _expand_key(key):
    """Round keys: for 256 bits there are fifteen of them, sixteen bytes
    each."""

    words = len(key) // 4
    rounds = words + 6
    schedule = [list(key[4 * i : 4 * i + 4]) for i in range(words)]
    rcon = 1
    for index in range(words, 4 * (rounds + 1)):
        tail = list(schedule[index - 1])
        if index % words == 0:
            tail = [SBOX[byte] for byte in tail[1:] + tail[:1]]
            tail[0] ^= rcon
            rcon = _xtime(rcon)
        elif words > 6 and index % words == 4:
            tail = [SBOX[byte] for byte in tail]
        schedule.append(
            [left ^ right for left, right in zip(schedule[index - words], tail)]
        )
    keys = [
        bytes(byte for word in schedule[4 * r : 4 * r + 4] for byte in word)
        for r in range(rounds + 1)
    ]
    return keys, rounds


def _encrypt_block(block, keys, rounds):
    state = [byte ^ key for byte, key in zip(block, keys[0])]
    for number in range(1, rounds + 1):
        # Substitution and row shift together: the byte at (row r, column c)
        # is taken from column (c + r) of the same row.
        moved = [SBOX[state[row + 4 * ((column + row) % 4)]]
                 for column in range(4) for row in range(4)]
        if number < rounds:
            state = []
            for column in range(4):
                a0, a1, a2, a3 = moved[4 * column : 4 * column + 4]
                state += [
                    DOUBLE[a0] ^ TRIPLE[a1] ^ a2 ^ a3,
                    a0 ^ DOUBLE[a1] ^ TRIPLE[a2] ^ a3,
                    a0 ^ a1 ^ DOUBLE[a2] ^ TRIPLE[a3],
                    TRIPLE[a0] ^ a1 ^ a2 ^ DOUBLE[a3],
                ]
        else:
            state = moved
        key = keys[number]
        state = [byte ^ key[i] for i, byte in enumerate(state)]
    return bytes(state)


def _counter_stream(data, key):
    """WinZip counter mode: the counter is little-endian, starting at one."""

    keys, rounds = _expand_key(key)
    output = bytearray()
    for number, start in enumerate(range(0, len(data), 16), start=1):
        gamma = _encrypt_block(struct.pack("<QQ", number, 0), keys, rounds)
        piece = data[start : start + 16]
        output += bytes(byte ^ gamma[i] for i, byte in enumerate(piece))
    return bytes(output)


# -- the archive itself --------------------------------------------------

SALT_LENGTH = 16          # 256 bits require sixteen bytes of salt
KEY_LENGTH = 32
MAC_LENGTH = 10           # WinZip stores only the start of the HMAC
ITERATIONS = 1000         # that's what the format specification says
STRENGTH = 3              # 3 means AES-256
AES_METHOD = 99
DEFLATED = 8
# The first bit is "file is encrypted"; without it the archiver won't ask
# for a password and will try to unpack the ciphertext as is. The
# eleventh is "name in UTF-8".
FLAGS = 0x0001 | 0x0800


def _derive(password, salt):
    material = hashlib.pbkdf2_hmac(
        "sha1",
        password.encode("utf-8"),
        salt,
        ITERATIONS,
        2 * KEY_LENGTH + 2,
    )
    return (
        material[:KEY_LENGTH],
        material[KEY_LENGTH : 2 * KEY_LENGTH],
        material[2 * KEY_LENGTH :],
    )


def _dos_time(moment=None):
    stamp = time.localtime(moment if moment is not None else time.time())
    date = ((stamp.tm_year - 1980) << 9) | (stamp.tm_mon << 5) | stamp.tm_mday
    clock = (stamp.tm_hour << 11) | (stamp.tm_min << 5) | (stamp.tm_sec // 2)
    return clock, date


def _aes_extra():
    # Field 0x9901: version AE-2, vendor tag, strength, the real compression
    # method. AE-2 stores no checksum; the HMAC takes its place.
    return struct.pack(
        "<HHHBBBH", 0x9901, 7, 2, ord("A"), ord("E"), STRENGTH, DEFLATED
    )


def make_secret_zip(name, text, password, *, moment=None, salt=None):
    """Builds a password-protected archive out of a single text file."""

    if not str(password or ""):
        raise SecretZipError(t("key.no_password"))
    raw = str(text).encode("utf-8")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = compressor.compress(raw) + compressor.flush()

    salt = salt if salt is not None else os.urandom(SALT_LENGTH)
    key, mac_key, check_value = _derive(password, salt)
    ciphertext = _counter_stream(body, key)
    label = hmac.new(mac_key, ciphertext, hashlib.sha1).digest()[:MAC_LENGTH]
    data = salt + check_value + ciphertext + label

    name_bytes = str(name).encode("utf-8")
    extra = _aes_extra()
    clock, date = _dos_time(moment)

    header = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50, 20, FLAGS, AES_METHOD, clock, date,
        0, len(data), len(raw), len(name_bytes), len(extra),
    )
    entry = header + name_bytes + extra + data

    directory_record = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50, 20, 20, FLAGS, AES_METHOD, clock, date,
        0, len(data), len(raw), len(name_bytes), len(extra),
        0, 0, 0, 0, 0,
    ) + name_bytes + extra
    tail = struct.pack(
        "<IHHHHIIH",
        0x06054B50, 0, 0, 1, 1, len(directory_record), len(entry), 0,
    )
    return entry + directory_record + tail


def read_secret_zip(blob, password):
    """Gets the text back out. Needed for checks and for recovery."""

    if len(blob) < 30 or blob[:4] != b"PK\x03\x04":
        raise SecretZipError(t("key.not_our_archive"))
    (method, _, compressed_size, _, name_length, extra_length) = struct.unpack(
        "<H4xIIIHH", blob[8:30]
    )
    if method != AES_METHOD:
        raise SecretZipError(t("key.no_encryption"))
    start = 30 + name_length + extra_length
    data = blob[start : start + compressed_size]
    salt = data[:SALT_LENGTH]
    check_value = data[SALT_LENGTH : SALT_LENGTH + 2]
    ciphertext = data[SALT_LENGTH + 2 : -MAC_LENGTH]
    label = data[-MAC_LENGTH:]

    key, mac_key, expected_check = _derive(password, salt)
    if check_value != expected_check:
        raise SecretZipError(t("key.wrong_password"))
    own_machine = hmac.new(mac_key, ciphertext, hashlib.sha1).digest()[:MAC_LENGTH]
    if not hmac.compare_digest(own_machine, label):
        raise SecretZipError(t("key.damaged"))
    return zlib.decompress(_counter_stream(ciphertext, key), -15).decode("utf-8")
