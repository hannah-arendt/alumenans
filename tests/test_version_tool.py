"""tools/version.py: picking the release version and writing it everywhere."""

import importlib.util
import re
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from alumenans.paths import PACKAGE_DIR

REPO = PACKAGE_DIR.parents[1]

_spec = importlib.util.spec_from_file_location("version_tool", REPO / "tools" / "version.py")
version_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(version_tool)


class NextVersionTests(unittest.TestCase):
    def test_first_release_uses_the_code_version(self):
        self.assertEqual(version_tool.next_version("0.3.0", []), "0.3.0")

    def test_released_version_gets_the_next_minor(self):
        self.assertEqual(version_tool.next_version("0.3.0", ["v0.3.0"]), "0.4.0")

    def test_newest_release_wins_over_an_older_code_version(self):
        self.assertEqual(version_tool.next_version("0.3.0", ["v0.3.0", "v0.5.2", "v0.4.0"]), "0.6.0")

    def test_code_version_set_on_purpose_is_kept(self):
        self.assertEqual(version_tool.next_version("1.0.0", ["v0.4.0"]), "1.0.0")

    def test_versions_compare_as_numbers(self):
        self.assertEqual(version_tool.next_version("0.3.0", ["v0.9.0", "v0.10.0"]), "0.11.0")

    def test_other_tags_are_ignored(self):
        self.assertEqual(version_tool.next_version("0.3.0", ["nightly", "v1.0", "v2.0.0-rc1"]), "0.3.0")

    def test_bad_code_version_is_rejected(self):
        with self.assertRaises(ValueError):
            version_tool.next_version("0.3", [])


class SetVersionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        for path, _ in version_tool.LOCATIONS:
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / path, self.root / path)

    def test_writes_all_three_places_and_keeps_the_bom(self):
        iss = self.root / "packaging" / "windows" / "ALumenans.iss"
        self.assertTrue(iss.read_bytes().startswith(b"\xef\xbb\xbf"))
        changed = version_tool.set_version("9.8.7", root=self.root)
        self.assertEqual(len(changed), 3)
        self.assertEqual(version_tool.current(root=self.root), "9.8.7")
        self.assertTrue(iss.read_bytes().startswith(b"\xef\xbb\xbf"))
        with (self.root / "pyproject.toml").open("rb") as file:
            self.assertEqual(tomllib.load(file)["project"]["version"], "9.8.7")
        init = (self.root / "src" / "alumenans" / "__init__.py").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'__version__ = "([^"]+)"', init).group(1), "9.8.7")

    def test_same_version_changes_nothing(self):
        self.assertEqual(version_tool.set_version(version_tool.current(root=self.root), root=self.root), [])

    def test_bad_version_is_rejected(self):
        with self.assertRaises(ValueError):
            version_tool.set_version("1.0", root=self.root)


if __name__ == "__main__":
    unittest.main()
