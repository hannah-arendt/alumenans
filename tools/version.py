#!/usr/bin/env python3
"""Read, set and pick the app version.

The version lives in three places that must agree (tests/test_packaging.py
checks it): `#define AppVersion` in packaging/windows/ALumenans.iss (the one
both builders read), `version` in pyproject.toml and `__version__` in
src/alumenans/__init__.py.

    python tools/version.py current           # print the version in the code
    python tools/version.py set 0.4.0         # write it to all three places
    python tools/version.py next v0.3.0 ...   # the version the next release gets
    gh release list ... | python tools/version.py next   # tags may come on stdin

The release workflow (.github/workflows/release.yml) uses `next` and `set`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (file, pattern) — group 1 is the text before the version, group 2 the version.
LOCATIONS = (
    (Path("packaging/windows/ALumenans.iss"), re.compile(r'^(\ufeff?#define AppVersion ")([^"]*)"', re.M)),
    (Path("pyproject.toml"), re.compile(r'^(version = ")([^"]*)"', re.M)),
    (Path("src/alumenans/__init__.py"), re.compile(r'^(__version__ = ")([^"]*)"', re.M)),
)

VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
TAG = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def parse(version: str) -> tuple[int, int, int]:
    match = VERSION.match(version)
    if not match:
        raise ValueError(f"Not a MAJOR.MINOR.PATCH version: {version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _read(path: Path) -> str:
    # newline="" keeps the line endings; utf-8 keeps the .iss BOM as U+FEFF.
    with path.open(encoding="utf-8", newline="") as file:
        return file.read()


def current(root: Path = REPO) -> str:
    path, pattern = LOCATIONS[0]
    match = re.search(r'^﻿?#define AppVersion "([^"]+)"', _read(root / path), re.M)
    if not match:
        raise SystemExit(f"No AppVersion found in {path}.")
    return match.group(1)


def set_version(version: str, root: Path = REPO) -> list[Path]:
    """Write `version` to every location; return the files that changed."""
    parse(version)
    updated = {}
    for path, pattern in LOCATIONS:
        text = _read(root / path)
        new, count = pattern.subn(rf'\g<1>{version}"', text)
        if count != 1:
            raise SystemExit(f"Expected one version in {path}, found {count}.")
        updated[path] = (text, new)
    # Only write once every file has matched, so a failure changes nothing.
    changed = []
    for path, (text, new) in updated.items():
        if new != text:
            with (root / path).open("w", encoding="utf-8", newline="") as file:
                file.write(new)
            changed.append(path)
    return changed


def next_version(code: str, published: list[str]) -> str:
    """The version the next release gets.

    The code's version if it is newer than every published one (someone set
    it on purpose, e.g. 1.0.0); otherwise the newest published version with
    the minor number raised. Tags that aren't `vX.Y.Z` are ignored.
    """
    code_parts = parse(code)
    versions = [parse(m.group(1)) for tag in published if (m := TAG.match(tag.strip()))]
    if not versions or code_parts > max(versions):
        return code
    major, minor, _ = max(versions)
    return f"{major}.{minor + 1}.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("current", help="print the version in the code")
    set_parser = commands.add_parser("set", help="write a version to all three places")
    set_parser.add_argument("version")
    next_parser = commands.add_parser("next", help="print the version for the next release")
    next_parser.add_argument("tags", nargs="*", help="published tags (also read from stdin if not a tty)")
    args = parser.parse_args(argv)

    if args.command == "current":
        print(current())
    elif args.command == "set":
        try:
            changed = set_version(args.version)
        except ValueError as error:
            raise SystemExit(str(error))
        for path in changed:
            print(f"{path}: {args.version}")
        if not changed:
            print(f"Already {args.version}.")
    else:
        tags = list(args.tags)
        if not sys.stdin.isatty():
            tags += sys.stdin.read().split()
        print(next_version(current(), tags))
    return 0


if __name__ == "__main__":
    sys.exit(main())
