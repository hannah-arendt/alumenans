#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find and download a portable CPython from python-build-standalone.

Both builds ship their own Python: the Windows installer in
`packaging/windows/python-runtime/`, the macOS app inside the bundle. Both
take it from the same place, astral-sh/python-build-standalone: official
CPython builds that run from any folder without being installed.

Asset names change from release to release, so the right file is found
by its properties rather than a hard-coded URL. Standard library only:
this runs before anything else is installed.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

RELEASES = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"


def _headers(api: bool = False) -> dict[str, str]:
    headers = {"User-Agent": "ALumenans-build"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        # Unauthenticated API calls are limited to 60 an hour per IP, which
        # shared CI runners hit easily. GitHub Actions provides a token.
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def latest_release() -> dict:
    request = urllib.request.Request(RELEASES, headers=_headers(api=True))
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def pick_asset(release: dict, version: str, triple: str) -> tuple[str, str]:
    """(name, download URL) of the install_only build for `version` and `triple`.

    Raises LookupError if the release has none."""

    suitable = [
        asset
        for asset in release.get("assets", [])
        if f"cpython-{version}." in asset["name"]
        and triple in asset["name"]
        and "install_only" in asset["name"]
        # The free-threaded build is tagged cp313t instead of cp313, and
        # regular wheels don't fit it: torch 2.13 has no cp313t build at
        # all, so the install breaks on someone else's machine.
        and "freethreaded" not in asset["name"]
        and asset["name"].endswith(".tar.gz")
    ]
    if not suitable:
        raise LookupError(
            f"release {release.get('tag_name')} has no CPython {version} for {triple}"
        )
    # install_only_stripped is smaller; the regular one will do if it's missing.
    suitable.sort(key=lambda asset: ("stripped" not in asset["name"], asset["name"]))
    return suitable[0]["name"], suitable[0]["browser_download_url"]


def find_asset(version: str, triple: str) -> tuple[str, str]:
    return pick_asset(latest_release(), version, triple)


def download(url: str, destination: Path) -> Path:
    request = urllib.request.Request(url, headers=_headers())
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    with urllib.request.urlopen(request, timeout=600) as stream, partial.open("wb") as out:
        while chunk := stream.read(1 << 20):
            out.write(chunk)
    partial.replace(destination)
    return destination
