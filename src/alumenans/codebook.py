#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The study's codebook.

A code belongs to the study, not to an individual interview: that is
exactly what makes it useful, that «отписка из-за денег» ("cancelled
because of money") in the fifth interview and in the twelfth is one and
the same code; otherwise there is nothing to compare.

The hierarchy is set by a dot in the name: «деньги.нехватка»
("money.shortage") sits under «деньги» ("money"). This is simplest: the
person types a name and immediately sees the tree, there's no need to
set up a separate tree, and renaming a branch carries the whole subtree
along.

Merging codes is a must, not an embellishment. Two researchers will
inevitably call the same thing by different names: «страх» ("fear") and
«опасения» ("apprehension"). As long as they can't be merged, the
codebook fills up with junk, and no analysis can be built on it.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Iterable, Sequence

SEPARATOR = "."
SPACES = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Brings a code name to a common form without changing its meaning."""

    parts = [
        SPACES.sub(" ", piece).strip()
        for piece in str(name or "").split(SEPARATOR)
    ]
    return SEPARATOR.join(piece for piece in parts if piece)


def code_key(name: str) -> str:
    """A key for comparing names: case and ё (vs е) don't matter."""

    return normalize_name(name).lower().replace("ё", "е")


def parent_name(name: str) -> str:
    """The parent code's name: «деньги.нехватка» → «деньги»."""

    clean = normalize_name(name)
    return clean.rpartition(SEPARATOR)[0]


def ancestors(name: str) -> list[str]:
    """All the branches above a code, from the root down to it."""

    parts = normalize_name(name).split(SEPARATOR)
    return [
        SEPARATOR.join(parts[: length])
        for length in range(1, len(parts))
    ]


def is_under(name: str, branch: str) -> bool:
    """Whether the code sits in this branch. The branch itself counts too.

    Needed for filtering: clicking «деньги» ("money"), the person wants to
    see what sits under it as well, not only what's tagged with exactly
    that word."""

    code, branch_key = code_key(name), code_key(branch)
    return code == branch_key or code.startswith(branch_key + SEPARATOR)


@dataclass(frozen=True)
class Code:
    """One code in the codebook."""

    id: str
    name: str
    definition: str = ""
    origin: str = ""
    created_at: float = 0.0

    @property
    def depth(self) -> int:
        return len(normalize_name(self.name).split(SEPARATOR)) - 1

    @property
    def leaf(self) -> str:
        """The last part of the name: what to show in the tree."""

        return normalize_name(self.name).rpartition(SEPARATOR)[2]

    def as_dict(self, uses: int = 0) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "leaf": self.leaf,
            "depth": self.depth,
            "definition": self.definition,
            "origin": self.origin,
            "uses": uses,
        }


def new_code_id() -> str:
    return "c-" + uuid.uuid4().hex[:12]


def sort_codes(codes: Iterable[Code]) -> list[Code]:
    """Tree order: a branch comes before its leaves."""

    return sorted(codes, key=lambda code: code_key(code.name))


def rename_in_tree(name: str, was: str, becomes: str) -> str:
    """The code's new name after a branch has been renamed.

    If «деньги» ("money") was renamed to «финансы» ("finance"),
    «деньги.нехватка» must become «финансы.нехватка», otherwise the
    subtree is orphaned."""

    if not is_under(name, was):
        return name
    tail = normalize_name(name)[len(normalize_name(was)) :]
    return normalize_name(becomes) + tail


def build_tree(codes: Sequence[Code], uses: dict[str, int] | None = None) -> list[dict]:
    """A flat list for display: with indentation and a usage count.

    The tree deliberately stays a flat list. The person needs to see all
    the codes at once and click on them, not expand branches: a codebook
    in qualitative research is rarely longer than a hundred lines."""

    count = uses or {}
    known = {code_key(code.name) for code in codes}
    lines: list[dict] = []
    for code in sort_codes(codes):
        # A branch that was never created explicitly must still be visible:
        # «деньги» exists if «деньги.нехватка» exists.
        for ancestor in ancestors(code.name):
            if code_key(ancestor) in known:
                continue
            known.add(code_key(ancestor))
            lines.append(
                {
                    "id": "",
                    "name": ancestor,
                    "leaf": ancestor.rpartition(SEPARATOR)[2],
                    "depth": len(ancestor.split(SEPARATOR)) - 1,
                    "definition": "",
                    "origin": "ветка",
                    "uses": 0,
                }
            )
        lines.append(code.as_dict(count.get(code.id, 0)))
    lines.sort(key=lambda line: code_key(line["name"]))
    # A branch's uses are its own plus everything under it.
    for line in lines:
        line["uses_with_children"] = sum(
            other_row["uses"] for other_row in lines if is_under(other_row["name"], line["name"])
        )
    return lines


def now() -> float:
    return time.time()
