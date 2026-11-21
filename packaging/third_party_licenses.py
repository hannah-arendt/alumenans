#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collects the license texts of everything a build ships into one folder.

    python packaging/third_party_licenses.py OUTPUT SITE_PACKAGES [SITE_PACKAGES ...]

Both builds run this on the packages they actually put inside (the Windows
runtime's site-packages; the Mac packages and the Mac runtime's
site-packages), so the folder matches the build exactly, including
transitive dependencies and platform-only ones (pythonnet on Windows,
pyobjc on the Mac).

For each installed distribution the license files listed in its RECORD
are copied into OUTPUT/<name>/. A few distributions ship without any; their
upstream text lives in packaging/license-texts/ (see OVERRIDES). A
distribution that has neither stops the build: shipping it without its
license would break that license's terms.

Also added: the CPython license (the bundled runtime), and the FFmpeg
license next to the GPL for the ffmpeg program that imageio-ffmpeg bundles,
and the texts of the libraries a wheel links in statically (BUNDLED).
OUTPUT/INDEX.txt lists every distribution with its exact version and
declared license.

Standard library only: it runs under the Python that ships in the build.
"""

from __future__ import annotations

import importlib.metadata
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEXTS = Path(__file__).resolve().parent / "license-texts"
APP_LICENSE = REPO / "LICENSE"

# Normalized distribution name → upstream license text, for distributions
# whose wheels contain no license file.
OVERRIDES = {
    "dawg-python": "dawg-python.txt",
    "flatbuffers": "flatbuffers.txt",
    "proxy-tools": "proxy-tools.txt",
    "pymorphy2-dicts-ru": "pymorphy2-dicts.txt",
    "pymorphy3-dicts-ru": "pymorphy3-dicts.txt",
    "pyobjc-core": "pyobjc.txt",
    "pyobjc-framework-security": "pyobjc.txt",
    "pyobjc-framework-uniformtypeidentifiers": "pyobjc.txt",
    "sentencepiece": "sentencepiece.txt",
    "sherpa-onnx-bin": "sherpa-onnx.txt",
    "sherpa-onnx-core": "sherpa-onnx.txt",
    "tokenizers": "tokenizers.txt",
}

LICENSE_FILE = re.compile(
    r"(^|/)(LICEN[CS]E|COPYING|NOTICE|AUTHORS|ThirdPartyNotices)[^/]*$", re.IGNORECASE
)


# Libraries compiled into a distribution's own binary: its wheel carries
# only its own license, so theirs are added from packaging/license-texts/.
# Distribution → (folder, text, version note, license).
BUNDLED = {
    "sqlcipher3": (
        ("sqlcipher", "sqlcipher.txt", "4.20.0 (bundled by sqlcipher3)", "BSD-3-Clause"),
        ("openssl", "openssl.txt", "3.6.0 (bundled by sqlcipher3)", "Apache-2.0"),
    ),
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_license(distribution: importlib.metadata.Distribution) -> str:
    metadata = distribution.metadata
    expression = metadata.get("License-Expression")
    if expression:
        return expression
    text = (metadata.get("License") or "").strip()
    if text and text.upper() != "UNKNOWN" and "\n" not in text and len(text) < 80:
        return text
    classifiers = [
        value.split("::")[-1].strip()
        for value in metadata.get_all("Classifier") or []
        if value.startswith("License ::")
    ]
    return "; ".join(classifiers) or "see license files"


def license_files(distribution: importlib.metadata.Distribution) -> list[Path]:
    found = []
    for entry in distribution.files or []:
        if LICENSE_FILE.search(entry.as_posix()):
            path = Path(distribution.locate_file(entry))
            if path.is_file():
                found.append(path)
    return found


def collect(output: Path, site_packages: list[Path]) -> list[tuple[str, str, str]]:
    """Fills OUTPUT; returns (name, version, license) per distribution."""

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    rows: dict[str, tuple[str, str, str]] = {}
    missing = []
    for distribution in importlib.metadata.distributions(path=[str(p) for p in site_packages]):
        name = normalize(distribution.metadata["Name"])
        if name in rows or name == "alumenans":
            continue
        rows[name] = (name, distribution.version, declared_license(distribution))
        target = output / name
        files = license_files(distribution)
        if files:
            target.mkdir()
            for path in files:
                destination = target / path.name
                # Several files with the same name (e.g. vendored
                # libraries' LICENSE files): keep them all.
                if destination.exists():
                    destination = target / f"{path.parent.name}-{path.name}"
                number = 2
                while destination.exists():
                    destination = target / f"{path.parent.name}-{number}-{path.name}"
                    number += 1
                shutil.copyfile(path, destination)
        elif name in OVERRIDES:
            target.mkdir()
            shutil.copyfile(TEXTS / OVERRIDES[name], target / "LICENSE.txt")
        else:
            missing.append(name)
    if missing:
        raise SystemExit(
            "No license text for: " + ", ".join(sorted(missing))
            + ". Add the upstream license to packaging/license-texts/ and to OVERRIDES"
            " in packaging/third_party_licenses.py."
        )

    (output / "python").mkdir()
    shutil.copyfile(TEXTS / "python.txt", output / "python" / "LICENSE.txt")
    rows["python"] = ("python", "3.13 (python-build-standalone)", "PSF-2.0")
    if "imageio-ffmpeg" in rows:
        (output / "ffmpeg").mkdir()
        shutil.copyfile(TEXTS / "ffmpeg.md", output / "ffmpeg" / "LICENSE.md")
        shutil.copyfile(APP_LICENSE, output / "ffmpeg" / "COPYING.GPLv3")
        rows["ffmpeg"] = ("ffmpeg", "bundled by imageio-ffmpeg", "GPL-3.0-or-later")
    for distribution, libraries in BUNDLED.items():
        if distribution not in rows:
            continue
        for folder, text, note, license_ in libraries:
            (output / folder).mkdir()
            shutil.copyfile(TEXTS / text, output / folder / "LICENSE.txt")
            rows[folder] = (folder, note, license_)

    lines = [f"{name}  {version}  {license_}" for name, version, license_ in sorted(rows.values())]
    (output / "INDEX.txt").write_text(
        "Third-party components shipped in this build. Full texts are in the folder"
        " of the same name. See also THIRD-PARTY-NOTICES.md.\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return sorted(rows.values())


def main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    rows = collect(Path(arguments[0]), [Path(path) for path in arguments[1:]])
    print(f"Licenses collected for {len(rows)} components into {arguments[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
