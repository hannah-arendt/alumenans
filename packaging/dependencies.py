#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prints the dependencies from pyproject.toml, one per line, like requirements.txt.

    python packaging/dependencies.py > requirements.txt

One list for everything: pyproject.toml. The Windows build installs it
into the installer's runtime with plain pip; markers like
`sys_platform == 'darwin'` pip evaluates itself, on the machine where it
installs.

Standard library only: it runs under the Python that ships in the
installer, before anything else has been put into it.
"""

import sys
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def dependencies() -> list[str]:
    with PYPROJECT.open("rb") as file:
        return list(tomllib.load(file)["project"]["dependencies"])


if __name__ == "__main__":
    sys.stdout.write("\n".join(dependencies()) + "\n")
