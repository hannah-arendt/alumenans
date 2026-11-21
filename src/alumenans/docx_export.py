#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The finished transcript in .docx format.

A .docx file is a zip with a few XML files inside, so it is built with
the standard library. A separate package is not needed for this: the
program already takes a long time to install on first launch, and every
new dependency is one more reason the launch might not happen.

The markup is deliberately sparse: the speaker's name in bold, the
utterance in regular type, timecodes on a line of their own. Nothing that
could shift when opened in a different editor.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from typing import Iterable, Sequence


# The speaker label is not necessarily «Спикер 2» ("Speaker 2"): by the time
# of export the roles have already been substituted, and what stands there is
# whatever the person wrote themselves — «Интервьюер» ("Interviewer"),
# «Респондент» ("Respondent"), a name. So the label is recognised by its
# shape, not from a list: short, capitalised, no sentence-ending
# punctuation, before a colon.
SPEAKER_LINE = re.compile(
    r"^\s*([А-ЯЁA-Z][А-Яа-яЁёA-Za-z0-9-]*(?:\s+[А-Яа-яЁёA-Za-z0-9-]+){0,2})\s*:\s+(.*)$",
    re.DOTALL,
)
TIMECODE_LINE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
MAXIMUM_LABEL = 40

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# One font and three styles: title, normal paragraph, timecode.
STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="Georgia" w:hAnsi="Georgia" w:cs="Georgia"/>
<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="ru-RU"/>
</w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal">
<w:name w:val="Normal"/><w:pPr><w:spacing w:after="160" w:line="288" w:lineRule="auto"/></w:pPr>
</w:style>
<w:style w:type="paragraph" w:styleId="Title">
<w:name w:val="Title"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:after="240"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="Timecode">
<w:name w:val="Timecode"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:before="240" w:after="80"/></w:pPr>
<w:rPr><w:b/><w:color w:val="5E645C"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>
</w:style>
</w:styles>"""


@dataclass(frozen=True)
class Block:
    """A paragraph of the document to be built."""

    kind: str  # title | timecode | turn | plain
    speaker: str = ""
    text: str = ""


def escape(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def parse_transcript(text: str) -> list[Block]:
    """Splits the finished transcript into paragraphs.

    The input is the same text the program saves to .txt — otherwise the
    document and the text file would drift apart in content."""

    blocks: list[Block] = []
    for piece in re.split(r"\n\s*\n", str(text or "")):
        chunk = piece.strip()
        if not chunk:
            continue
        timecode = TIMECODE_LINE.match(chunk)
        if timecode:
            blocks.append(Block("timecode", text=timecode.group(1).strip()))
            continue
        speaker = SPEAKER_LINE.match(chunk)
        label = re.sub(r"\s+", " ", speaker.group(1)).strip() if speaker else ""
        if speaker and len(label) <= MAXIMUM_LABEL:
            blocks.append(
                Block(
                    "turn",
                    speaker=label,
                    text=re.sub(r"\s+", " ", speaker.group(2)).strip(),
                )
            )
            continue
        blocks.append(Block("plain", text=re.sub(r"\s+", " ", chunk)))
    return blocks


def _paragraph(block: Block) -> str:
    if block.kind == "title":
        return (
            '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{escape(block.text)}</w:t></w:r></w:p>'
        )
    if block.kind == "timecode":
        return (
            '<w:p><w:pPr><w:pStyle w:val="Timecode"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">[{escape(block.text)}]</w:t></w:r></w:p>'
        )
    if block.kind == "turn":
        return (
            "<w:p>"
            f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">'
            f"{escape(block.speaker)}: </w:t></w:r>"
            f'<w:r><w:t xml:space="preserve">{escape(block.text)}</w:t></w:r>'
            "</w:p>"
        )
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(block.text)}</w:t></w:r></w:p>'


def build_document(blocks: Sequence[Block]) -> bytes:
    body = "".join(_paragraph(block) for block in blocks)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1701"/>'
        "</w:sectPr></w:body></w:document>"
    )
    return document.encode("utf-8")


def write_docx(path, blocks: Sequence[Block]) -> None:
    """Builds the file. Ordinary compression: the document must open everywhere."""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS)
        archive.writestr("word/styles.xml", STYLES)
        archive.writestr("word/document.xml", build_document(blocks))


def transcript_blocks(text: str, title: str = "") -> list[Block]:
    blocks: list[Block] = []
    if str(title or "").strip():
        blocks.append(Block("title", text=str(title).strip()))
    blocks.extend(parse_transcript(text))
    return blocks


def save_transcript(path, text: str, title: str = "") -> None:
    write_docx(path, transcript_blocks(text, title))
