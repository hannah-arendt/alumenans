#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guide coverage report.

This is no longer a technical metric but a working tool: what of the
plan was actually said, where exactly, what was missing, what came up
beyond the plan, and how much time the blocks really took compared with
what was budgeted.

A separate note on the report's tone. The program doesn't know the
interview; it knows word matches. So «не прозвучал» ("not asked") here
means "I didn't find it", and confident findings are kept apart from
tentative ones. A report that can't be trusted in the small things is
useless as a whole.
"""

from __future__ import annotations

from typing import Sequence

from alumenans.guide.parse import Guide, parse_guide
from alumenans.guide.align import Alignment, ItemMatch, align_guide


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    value = max(0, int(round(float(seconds))))
    return f"{value // 60:02d}:{value % 60:02d}"


def section_timing(
    guide: Guide,
    alignment: Alignment,
    turns: Sequence[dict],
) -> list[dict]:
    """Plan versus actual, by guide block.

    The planned time in the guide was set by a human in advance; the
    actual time we know from the recording. The comparison shows where the
    conversation dragged on and what ran out of time: feedback for the
    next interview.
    """

    order: list[str] = []
    starts: dict[str, float] = {}
    for match in alignment.matches:
        if not match.covered or match.time is None:
            continue
        title = match.item.section or "Без названия"
        if title not in starts:
            starts[title] = match.time
            order.append(title)

    finish = max(
        (float(turn.get("end") or turn.get("start") or 0.0) for turn in turns),
        default=0.0,
    )

    rows = []
    for position, title in enumerate(order):
        start = starts[title]
        end = starts[order[position + 1]] if position + 1 < len(order) else finish
        section = guide.section(title)
        planned = section.planned_seconds if section else None
        actual = max(0.0, end - start)
        verdict = ""
        if planned:
            if actual > planned[1] * 1.35:
                verdict = "дольше плана"
            elif actual < planned[0] * 0.6:
                verdict = "короче плана"
            else:
                verdict = "по плану"
        rows.append(
            {
                "section": title,
                "start": start,
                "actual_seconds": actual,
                "planned_from": planned[0] if planned else None,
                "planned_to": planned[1] if planned else None,
                "verdict": verdict,
            }
        )
    return rows


def build_report(
    guide: Guide | str,
    turns: Sequence[dict],
    *,
    moderator: int | None = None,
) -> dict:
    if isinstance(guide, str):
        guide = parse_guide(guide)
    alignment = align_guide(guide, turns, moderator=moderator)

    items = []
    for match in alignment.matches:
        items.append(
            {
                "key": match.key,
                "kind": match.item.kind,
                "number": match.item.number,
                "section": match.item.section,
                "branch": match.item.branch,
                "text": match.item.text,
                "state": match.state,
                "time": match.time,
                "turn_index": match.turn_index,
                "score": round(match.score, 3),
            }
        )

    questions = [item for item in items if item["kind"] == "question"]
    asked = [item for item in questions if item["state"] != "не прозвучал"]
    sure = [item for item in questions if item["state"] == "прозвучал"]

    return {
        "moderator": alignment.moderator,
        "moderator_confidence": round(alignment.confidence, 3),
        "branch": alignment.branch,
        "branch_title": alignment.branch_title,
        "branches_available": [
            {"key": branch.key, "title": branch.title} for branch in guide.branches
        ],
        "items": items,
        "missing": [item for item in questions if item["state"] == "не прозвучал"],
        "out_of_order": [item for item in questions if item["state"] == "не по порядку"],
        "unplanned": [dict(entry) for entry in alignment.unplanned],
        "sections": section_timing(guide, alignment, turns),
        "anchors": [dict(entry) for entry in alignment.anchors],
        "guide_problems": list(guide.problems),
        "stats": {
            "questions": len(questions),
            "asked": len(asked),
            "confident": len(sure),
            "missing": len(questions) - len(asked),
            "coverage": round(len(asked) / len(questions), 3) if questions else 0.0,
        },
    }


def format_report(report: dict) -> str:
    lines = []
    stats = report["stats"]
    lines.append(
        f"Интервьюер: Спикер {report['moderator']} "
        f"(уверенность {report['moderator_confidence']:.0%})"
    )
    if report["branch"]:
        others = ", ".join(
            f"{item['key']}. {item['title']}"
            for item in report["branches_available"]
            if item["key"] != report["branch"]
        )
        lines.append(
            f"Ветка: {report['branch']}. {report['branch_title']}"
            + (f"   (не разбирались: {others})" if others else "")
        )
    lines.append(
        f"Покрытие: {stats['asked']} из {stats['questions']} вопросов, "
        f"уверенно опознано {stats['confident']}"
    )
    lines.append("")

    lines.append("ЧТО ПРОЗВУЧАЛО")
    for item in report["items"]:
        if item["kind"] != "question":
            continue
        mark = {
            "прозвучал": "  ",
            "вероятно": " ?",
            "вместе с соседним": " +",
            "не по порядку": " ~",
            "не прозвучал": " —",
        }.get(item["state"], "  ")
        number = f"{item['number']}." if item["number"] else "·"
        lines.append(
            f"{mark} {number:>4} {_clock(item['time']):>6}  "
            f"{item['text'][:62]}"
        )
    lines.append("")
    lines.append("  ? — нашлось по месту в разговоре, совпадение слабое")
    lines.append("  + — задано внутри соседнего вопроса")
    lines.append("  ~ — прозвучало не на своём месте")
    lines.append("  — — не нашлось")

    if report["unplanned"]:
        lines.append("")
        lines.append("СВЕРХ ПЛАНА")
        for entry in report["unplanned"]:
            lines.append(f"  {_clock(entry['time']):>6}  {entry['text'][:66]}")

    if report["sections"]:
        lines.append("")
        lines.append("ВРЕМЯ БЛОКОВ: план и факт")
        for row in report["sections"]:
            planned = (
                f"{row['planned_from'] / 60:g}–{row['planned_to'] / 60:g} мин"
                if row["planned_from"]
                else "—"
            )
            lines.append(
                f"  {_clock(row['start']):>6}  {row['section'][:34]:<34} "
                f"факт {row['actual_seconds'] / 60:4.1f} мин   план {planned:<10} "
                f"{row['verdict']}"
            )

    if report["guide_problems"]:
        lines.append("")
        lines.append("ЗАМЕЧАНИЯ К ГАЙДУ")
        for problem in report["guide_problems"]:
            lines.append(f"  {problem}")
    return "\n".join(lines)
