"""Pinned framework blocks inside a project's AGENTS.md.

Block format (one line per marker):

    <!-- agent-framework:digest version=0.1.0 sha256=<hex> -->
    ...body...
    <!-- /agent-framework:digest -->

The sha256 covers the body exactly as written: every line between the opening
and closing marker lines, each terminated by a single LF. The writer always
stores the body with exactly one trailing newline. tools/check_pinned_blocks.sh
implements the same definition and must stay in agreement with this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .common import normalize_newlines, sha256_text

DIGEST_KIND = "digest"
PROFILE_KIND = "profile"
OPEN_MARKER = re.compile(r"^<!-- agent-framework:(digest|profile)((?: [a-z0-9_]+=\S*)*) -->$")
CLOSE_MARKER = re.compile(r"^<!-- /agent-framework:(digest|profile) -->$")
ATTRIBUTE = re.compile(r"([a-z0-9_]+)=(\S*)")
LEGACY_OPEN = "<!-- owner-baseline:start -->"
LEGACY_CLOSE = "<!-- owner-baseline:end -->"


@dataclass
class PinnedBlock:
    kind: str
    attributes: dict[str, str]
    start: int
    end: int
    body: str

    @property
    def identity(self) -> tuple[str, str]:
        return (self.kind, self.attributes.get("name", ""))

    def integrity_ok(self) -> bool:
        return self.attributes.get("sha256") == sha256_text(self.body)


@dataclass
class ParsedDocument:
    lines: list[str]
    blocks: list[PinnedBlock] = field(default_factory=list)
    legacy: tuple[int, int] | None = None
    problems: list[str] = field(default_factory=list)


def split_lines(text: str) -> list[str]:
    normalized = normalize_newlines(text)
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
    if normalized == "":
        return []
    return normalized.split("\n")


def join_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def parse_document(text: str) -> ParsedDocument:
    document = ParsedDocument(lines=split_lines(text))
    open_block: tuple[str, dict[str, str], int] | None = None
    legacy_start: int | None = None

    for index, line in enumerate(document.lines):
        stripped = line.rstrip()
        if stripped == LEGACY_OPEN:
            legacy_start = index
            continue
        if stripped == LEGACY_CLOSE and legacy_start is not None:
            document.legacy = (legacy_start, index)
            legacy_start = None
            continue

        opening = OPEN_MARKER.match(stripped)
        if opening is not None:
            if open_block is not None:
                document.problems.append(
                    f"line {index + 1}: block opened before line {open_block[2] + 1} was closed"
                )
                continue
            attributes = dict(ATTRIBUTE.findall(opening.group(2)))
            open_block = (opening.group(1), attributes, index)
            continue

        closing = CLOSE_MARKER.match(stripped)
        if closing is not None:
            if open_block is None:
                document.problems.append(f"line {index + 1}: closing marker without an opening marker")
                continue
            kind, attributes, start = open_block
            if closing.group(1) != kind:
                document.problems.append(
                    f"line {index + 1}: closing {closing.group(1)} marker for an open {kind} block"
                )
            body = join_lines(document.lines[start + 1 : index])
            document.blocks.append(PinnedBlock(kind, attributes, start, index, body))
            open_block = None

    if open_block is not None:
        document.problems.append(f"line {open_block[2] + 1}: {open_block[0]} block is never closed")
    if legacy_start is not None:
        document.problems.append(f"line {legacy_start + 1}: legacy owner-baseline block is never closed")
    return document


def normalize_body(text: str) -> str:
    stripped = normalize_newlines(text).rstrip("\n")
    if stripped == "":
        return ""
    return stripped + "\n"


def render_block(kind: str, attributes: list[tuple[str, str]], body_text: str) -> list[str]:
    body = normalize_body(body_text)
    rendered_attributes = " ".join(f"{key}={value}" for key, value in attributes)
    header = f"<!-- agent-framework:{kind} {rendered_attributes} sha256={sha256_text(body)} -->"
    return [header, *split_lines(body), f"<!-- /agent-framework:{kind} -->"]
