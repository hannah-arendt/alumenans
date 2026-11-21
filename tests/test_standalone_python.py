#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Picking the bundled Python out of a python-build-standalone release.

Both builds depend on this: the Windows runtime (fetch-runtime.py) and the
macOS bundle. The release listing below mirrors the real asset naming."""

import sys
import unittest
from unittest.mock import patch

from alumenans.paths import PACKAGE_DIR

# The build helpers live outside the package, next to the build scripts.
sys.path.insert(0, str(PACKAGE_DIR.parents[1] / "packaging"))
import standalone_python  # noqa: E402

RELEASE = {
    "tag_name": "20261101",
    "assets": [
        {"name": n, "browser_download_url": "https://example.invalid/" + n}
        for n in (
            "cpython-3.13.9+20261101-aarch64-apple-darwin-install_only.tar.gz",
            "cpython-3.13.9+20261101-aarch64-apple-darwin-install_only_stripped.tar.gz",
            "cpython-3.13.9+20261101-aarch64-apple-darwin-freethreaded-install_only.tar.gz",
            "cpython-3.13.9+20261101-x86_64-pc-windows-msvc-install_only.tar.gz",
            "cpython-3.13.9+20261101-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
            "cpython-3.13.9+20261101-x86_64-pc-windows-msvc-pgo-full.tar.zst",
            "cpython-3.130.1+20261101-x86_64-pc-windows-msvc-install_only.tar.gz",
            "cpython-3.14.1+20261101-x86_64-pc-windows-msvc-install_only.tar.gz",
        )
    ],
}


class PickAssetTests(unittest.TestCase):
    def test_each_platform_gets_the_stripped_install_only_build(self):
        for triple in ("aarch64-apple-darwin", "x86_64-pc-windows-msvc"):
            with self.subTest(triple=triple):
                name, url = standalone_python.pick_asset(RELEASE, "3.13", triple)
                self.assertEqual(
                    name, f"cpython-3.13.9+20261101-{triple}-install_only_stripped.tar.gz"
                )
                self.assertTrue(url.endswith(name))

    def test_a_missing_build_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            standalone_python.pick_asset(RELEASE, "3.13", "x86_64-unknown-linux-gnu")

    def test_the_ci_token_is_sent_to_the_api_only(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": "secret"}):
            self.assertEqual(
                standalone_python._headers(api=True)["Authorization"], "Bearer secret"
            )
            # Asset downloads redirect to other hosts; the token stays home.
            self.assertNotIn("Authorization", standalone_python._headers())


if __name__ == "__main__":
    unittest.main()
