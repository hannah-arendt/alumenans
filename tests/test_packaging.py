#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The build must not lose files or dependencies.

The installer used to list modules one by one, and a new module was easy
to forget: everything worked in the developer's folder, but the installed
program crashed at startup. Now the installer and the Mac build take the
`alumenans` package whole, and what needs checking is different:

* that they really take the whole package, not individual files;
* that every third-party module the package imports is declared in
  pyproject.toml — otherwise it won't be in the build;
* that there is one version for all.
"""

import ast
import re
import sys
import tomllib
import unittest

from alumenans import __version__
from alumenans.dependencies import required_modules
from alumenans.paths import PACKAGE_DIR, RESOURCES

REPO = PACKAGE_DIR.parents[1]
INSTALLER = REPO / "packaging" / "windows" / "ALumenans.iss"
PYPROJECT = REPO / "pyproject.toml"

# Module name in import → package name in pyproject.toml, where they differ.
DISTRIBUTION = {
    "webview": "pywebview",
    "onnx_asr": "onnx-asr",
    "sherpa_onnx": "sherpa-onnx",
    "imageio_ffmpeg": "imageio-ffmpeg",
    "objc": "pyobjc-core",
    "WebKit": "pyobjc-framework-WebKit",
}


def third_party_imports() -> dict[str, set[str]]:
    """Top-level third-party modules → the package files that import them."""

    found: dict[str, set[str]] = {}
    for path in PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top == "alumenans" or top in sys.stdlib_module_names:
                    continue
                found.setdefault(top, set()).add(
                    path.relative_to(PACKAGE_DIR).as_posix()
                )
    return found


def declared_dependencies() -> set[str]:
    with PYPROJECT.open("rb") as file:
        project = tomllib.load(file)["project"]
    return {
        re.split(r"[\[=<>;! ]", requirement, maxsplit=1)[0].lower()
        for requirement in project["dependencies"]
    }


class DependencyTests(unittest.TestCase):
    def test_every_third_party_import_is_declared(self):
        declared = declared_dependencies()
        for module, where in sorted(third_party_imports().items()):
            export = DISTRIBUTION.get(module, module).lower()
            with self.subTest(module=module):
                self.assertIn(
                    export,
                    declared,
                    f"{module} is imported in {sorted(where)}, "
                    "but not declared in pyproject.toml",
                )

    def test_what_the_launch_checks_is_what_the_build_installs(self):
        # The check at startup and the install at build time are two ends of
        # one list. A module that is checked but not installed means
        # "the build is incomplete" for every single person.
        declared = declared_dependencies()
        for module in required_modules("darwin"):
            export = DISTRIBUTION.get(module, module).lower()
            with self.subTest(module=module):
                self.assertIn(export, declared)

    def test_the_windows_build_installs_and_checks_the_dependencies(self):
        build = (REPO / "packaging" / "windows" / "build-installer.ps1").read_text(
            encoding="utf-8"
        )
        install_at = build.find("-m pip install")
        check_value = build.find("missing_modules()")
        compiler_at = build.find("& $compiler $script")
        self.assertIn('packaging\\dependencies.py', build)
        self.assertGreater(install_at, 0)
        # Packages and the check first, then the installer — not the other way round.
        self.assertLess(install_at, check_value)
        self.assertLess(check_value, compiler_at)

    def test_the_dependency_list_is_read_from_pyproject(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "dependencies_helper", REPO / "packaging" / "dependencies.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with PYPROJECT.open("rb") as file:
            expected = tomllib.load(file)["project"]["dependencies"]
        self.assertEqual(module.dependencies(), expected)

    def test_every_dependency_is_pinned_exactly(self):
        # The program ships to people together with these packages; only
        # the exact combination has been tested.
        with PYPROJECT.open("rb") as file:
            requirements = tomllib.load(file)["project"]["dependencies"]
        for requirement in requirements:
            with self.subTest(requirement=requirement):
                self.assertRegex(requirement.split(";")[0], r"==\d")


class InstallerContentsTests(unittest.TestCase):
    def setUp(self):
        if not INSTALLER.is_file():
            self.skipTest("the installer script is not available")
        self.script = INSTALLER.read_text(encoding="utf-8-sig")

    def test_the_whole_package_is_shipped(self):
        line = re.search(
            r'^Source: "\.\.\\\.\.\\src\\alumenans\\\*";[^\n]*$',
            self.script,
            re.M,
        )
        self.assertIsNotNone(line, "the installer does not take src\\alumenans as a whole")
        self.assertIn('DestDir: "{app}\\alumenans"', line.group(0))
        self.assertIn("recursesubdirs", line.group(0))
        self.assertIn("__pycache__", line.group(0))

    def test_no_module_is_listed_one_by_one(self):
        # A per-file list is exactly what someone once forgot to update.
        self.assertNotRegex(self.script, r'Source: "[^"]*\.py"')

    def test_the_program_starts_as_a_module(self):
        for section in ("[Icons]", "[Run]"):
            with self.subTest(section=section):
                relative = self.script.split(section, 1)[1].split("\n[", 1)[0]
                self.assertIn('Parameters: "-m alumenans"', relative)
                self.assertIn('WorkingDir: "{app}"', relative)

    def test_the_installer_speaks_both_interface_languages(self):
        languages = self.script.split("[Languages]", 1)[1].split("\n[", 1)[0]
        for name, info in (("russian", "first-run.txt"), ("english", "first-run.en.txt")):
            with self.subTest(language=name):
                self.assertRegex(languages, rf'Name: "{name}";[^\n]*InfoBeforeFile: "{re.escape(info)}"')
                self.assertTrue((INSTALLER.parent / info).is_file(), info)
        # The installer's language seeds the program's, without overriding a later choice.
        self.assertIn("{app}\\.ui-language", self.script)
        self.assertIn("if not FileExists(LanguageFile)", self.script)

    def test_the_version_is_the_same_everywhere(self):
        version = re.search(r'#define AppVersion "([^"]+)"', self.script).group(1)
        with PYPROJECT.open("rb") as file:
            self.assertEqual(tomllib.load(file)["project"]["version"], version)
        self.assertEqual(__version__, version)


class ResourceTests(unittest.TestCase):
    def test_interface_and_dictionary_live_in_the_package(self):
        for name in (
            "app.html",
            "names-and-organizations.txt",
            "ALumenans_logo.ico",
            "ALumenans_logo.png",
        ):
            with self.subTest(name=name):
                self.assertTrue((RESOURCES / name).is_file())

    def test_there_is_only_one_dictionary(self):
        # Two lists of the same thing raise the question "which one do I
        # edit", which gets decided anew every time.
        self.assertEqual(list(PACKAGE_DIR.rglob("proofreading_lexicon.json")), [])


if __name__ == "__main__":
    unittest.main()
