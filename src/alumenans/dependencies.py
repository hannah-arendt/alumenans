#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Third-party modules the program cannot work without.

The program does not install them itself. The packages are put in at
build time — into the installer for Windows and inside the app bundle for
the Mac — at exactly the versions from pyproject.toml. Here there is only
the list of what must end up inside, and a check that it is there.

What is checked is presence, not version: the build is responsible for
the version, and there is no point peeking into package metadata on every
launch.

The module deliberately imports nothing beyond the standard library: the
build scripts read it too, and they have no reason to pull in the whole
program.
"""

from __future__ import annotations

import importlib.util
import sys

REQUIRED_MODULES = (
    "natasha",
    "pymorphy3",
    "gliner",
    "webview",
    "torch",
    "transformers",
    "onnxruntime",
    "numpy",
    "onnx_asr",
    "sherpa_onnx",
    "imageio_ffmpeg",
    "mutagen",
    "sqlcipher3",
)

# On the Mac the window is a WKWebView, and pywebview reaches it through pyobjc.
MAC_ONLY_MODULES = ("objc", "WebKit")


def required_modules(platform: str = sys.platform) -> tuple[str, ...]:
    """What must be in the build for this system."""

    return REQUIRED_MODULES + (MAC_ONLY_MODULES if platform == "darwin" else ())


def missing_modules(platform: str = sys.platform) -> list[str]:
    """Modules that are missing from this build."""

    return [
        module
        for module in required_modules(platform)
        if importlib.util.find_spec(module) is None
    ]
