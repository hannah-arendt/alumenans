#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Licensing: the program is GPLv3, and every build carries the licenses
of what it ships.

The builds distribute GPL software (mutagen in-process, the ffmpeg program
from imageio-ffmpeg), so leaving license texts out is not a cosmetic gap.
"""

import importlib.util
import re
import tempfile
import tomllib
import unittest
from pathlib import Path

from alumenans.paths import PACKAGE_DIR

REPO = PACKAGE_DIR.parents[1]
PACKAGING = REPO / "packaging"


def load_collector():
    # Loaded by path: the folder is named "packaging", like the PyPI package.
    spec = importlib.util.spec_from_file_location(
        "third_party_licenses", PACKAGING / "third_party_licenses.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_distribution(site: Path, name: str, license_file: bool, extra: str = "") -> None:
    info = site / f"{name}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\n"
        f"License-Expression: MIT\n{extra}",
        encoding="utf-8",
    )
    record = [f"{info.name}/METADATA,,"]
    if license_file:
        (info / "LICENSE").write_text(f"license of {name}\n", encoding="utf-8")
        record.append(f"{info.name}/LICENSE,,")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")


class ProjectLicenseTests(unittest.TestCase):
    def test_the_program_is_gplv3(self):
        text = (REPO / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3, 29 June 2007", text)
        with (REPO / "pyproject.toml").open("rb") as file:
            project = tomllib.load(file)["project"]
        self.assertEqual(project["license"], "GPL-3.0-or-later")
        self.assertIn("LICENSE", project["license-files"])
        self.assertIn("THIRD-PARTY-NOTICES.md", project["license-files"])

    def test_every_dependency_is_in_the_notices(self):
        notices = (REPO / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8").lower()
        with (REPO / "pyproject.toml").open("rb") as file:
            dependencies = tomllib.load(file)["project"]["dependencies"]
        for requirement in dependencies:
            name = re.split(r"[\[=<>;! ]", requirement, maxsplit=1)[0].lower()
            with self.subTest(dependency=name):
                # The pyobjc packages are listed together.
                self.assertIn("pyobjc" if name.startswith("pyobjc") else name, notices)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.collector = load_collector()

    def test_every_override_text_exists_and_is_used(self):
        texts = {path.name for path in (PACKAGING / "license-texts").iterdir()}
        bundled = {
            text
            for libraries in self.collector.BUNDLED.values()
            for _folder, text, _note, _license in libraries
        }
        used = set(self.collector.OVERRIDES.values()) | bundled | {"python.txt", "ffmpeg.md"}
        self.assertEqual(texts, used)

    def test_license_files_overrides_python_and_ffmpeg_are_collected(self):
        with tempfile.TemporaryDirectory() as folder:
            site, output = Path(folder) / "site", Path(folder) / "out"
            fake_distribution(site, "own-license", license_file=True)
            fake_distribution(site, "tokenizers", license_file=False)
            fake_distribution(site, "imageio_ffmpeg", license_file=True)
            rows = self.collector.collect(output, [site])

            names = [row[0] for row in rows]
            self.assertEqual(
                names, ["ffmpeg", "imageio-ffmpeg", "own-license", "python", "tokenizers"]
            )
            self.assertEqual(
                (output / "own-license" / "LICENSE").read_text(encoding="utf-8"),
                "license of own-license\n",
            )
            self.assertIn("Apache License", (output / "tokenizers" / "LICENSE.txt").read_text())
            self.assertIn("PYTHON SOFTWARE FOUNDATION", (output / "python" / "LICENSE.txt").read_text())
            self.assertIn("GNU GENERAL PUBLIC LICENSE", (output / "ffmpeg" / "COPYING.GPLv3").read_text())
            self.assertTrue((output / "ffmpeg" / "LICENSE.md").is_file())
            self.assertIn("own-license  1.0  MIT", (output / "INDEX.txt").read_text())

    def test_statically_linked_libraries_get_their_own_texts(self):
        with tempfile.TemporaryDirectory() as folder:
            site, output = Path(folder) / "site", Path(folder) / "out"
            fake_distribution(site, "sqlcipher3", license_file=True)
            rows = self.collector.collect(output, [site])

            names = [row[0] for row in rows]
            self.assertIn("sqlcipher", names)
            self.assertIn("openssl", names)
            self.assertIn("ZETETIC", (output / "sqlcipher" / "LICENSE.txt").read_text())
            self.assertIn("Apache License", (output / "openssl" / "LICENSE.txt").read_text())

    def test_a_package_without_any_license_stops_the_build(self):
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder) / "site"
            fake_distribution(site, "nameless", license_file=False)
            with self.assertRaises(SystemExit) as raised:
                self.collector.collect(Path(folder) / "out", [site])
        self.assertIn("nameless", str(raised.exception))


class BuildTests(unittest.TestCase):
    def test_the_windows_installer_ships_the_licenses(self):
        build = (PACKAGING / "windows" / "build-installer.ps1").read_text(encoding="utf-8")
        collect_at = build.find("packaging\\third_party_licenses.py")
        self.assertGreater(collect_at, 0)
        self.assertLess(collect_at, build.find("& $compiler $script"))
        self.assertIn('Join-Path $runtime "Lib\\site-packages"', build)

        iss = (PACKAGING / "windows" / "ALumenans.iss").read_text(encoding="utf-8-sig")
        self.assertIn('Source: "..\\..\\LICENSE"', iss)
        self.assertIn('Source: "..\\..\\THIRD-PARTY-NOTICES.md"', iss)
        self.assertIn('Source: "output\\licenses\\*"; DestDir: "{app}\\licenses"', iss)
        # Torch's dist-info licenses are nested too deep for Windows' path
        # limit; they ship flattened in {app}\licenses instead.
        runtime = next(line for line in iss.splitlines() if line.startswith('Source: "python-runtime\\*"'))
        self.assertIn("*.dist-info\\licenses\\*", runtime)

    def test_the_mac_app_ships_the_licenses(self):
        build = (PACKAGING / "macos" / "build-mac.py").read_text(encoding="utf-8")
        self.assertIn("third_party_licenses.collect(licenses, [packages, runtime.joinpath(*site)])", build)
        self.assertIn("Contents/Resources/LICENSE.txt", build)
        self.assertIn("Contents/Resources/THIRD-PARTY-NOTICES.txt", build)
        self.assertIn("Contents/Resources/licenses/", build)


if __name__ == "__main__":
    unittest.main()
