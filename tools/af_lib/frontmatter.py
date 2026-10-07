"""Parser for the restricted SKILL.md frontmatter format.

Accepted shape (anything else is an error, so every host's YAML parser agrees):

    ---
    name: skill-name
    description: One line, plain or "double-quoted".
    license: optional
    compatibility: optional
    metadata:
      key: "value"
    ---
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOP_LEVEL_KEYS = ("name", "description", "license", "compatibility", "metadata")
REQUIRED_KEYS = ("name", "description")
TOP_LEVEL_LINE = re.compile(r"^([a-z_]+):(?: (.*))?$")
METADATA_LINE = re.compile(r"^  ([A-Za-z0-9_.-]+):(?: (.*))?$")
QUOTED_VALUE = re.compile(r'^"((?:[^"\\]|\\["\\])*)"$')
PLAIN_FORBIDDEN_START = set("[]{}&*!|>'\"%@`#,")


@dataclass
class Frontmatter:
    fields: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    body: str = ""
    errors: list[str] = field(default_factory=list)


def _parse_value(raw: str | None, location: str, errors: list[str]) -> str | None:
    if raw is None or raw.strip() == "":
        errors.append(f"{location}: empty value")
        return None
    value = raw.rstrip()
    if value.startswith('"'):
        quoted = QUOTED_VALUE.match(value)
        if quoted is None:
            errors.append(
                f'{location}: malformed double-quoted value (only \\" and \\\\ escapes are allowed)'
            )
            return None
        return quoted.group(1).replace('\\"', '"').replace("\\\\", "\\")
    if value[0] in PLAIN_FORBIDDEN_START or value.startswith(("- ", "? ", ": ")):
        errors.append(f"{location}: plain value starts with a YAML indicator; double-quote it")
        return None
    if ": " in value or " #" in value or value.endswith(":"):
        errors.append(f"{location}: plain value contains ': ' or ' #'; double-quote it")
        return None
    return value


def parse_frontmatter(text: str) -> Frontmatter:
    result = Frontmatter()
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0] != "---":
        result.errors.append("first line must be '---'")
        return result

    closing_index = None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            closing_index = index
            break
    if closing_index is None:
        result.errors.append("frontmatter is not closed with '---'")
        return result

    in_metadata = False
    for index in range(1, closing_index):
        line = lines[index]
        location = f"frontmatter line {index + 1}"
        metadata_match = METADATA_LINE.match(line)
        if in_metadata and metadata_match is not None:
            key = metadata_match.group(1)
            if key in result.metadata:
                result.errors.append(f"{location}: duplicate metadata key '{key}'")
                continue
            value = _parse_value(metadata_match.group(2), location, result.errors)
            if value is not None:
                result.metadata[key] = value
            continue

        top_match = TOP_LEVEL_LINE.match(line)
        if top_match is None:
            result.errors.append(f"{location}: expected 'key: value' (no blank lines, lists, or nesting)")
            in_metadata = False
            continue
        key, raw_value = top_match.group(1), top_match.group(2)
        if key not in TOP_LEVEL_KEYS:
            result.errors.append(f"{location}: unsupported key '{key}'")
            in_metadata = False
            continue
        if key in result.fields:
            result.errors.append(f"{location}: duplicate key '{key}'")
            continue
        if key == "metadata":
            if raw_value is not None and raw_value.strip():
                result.errors.append(
                    f"{location}: 'metadata:' must be followed by indented lines, not a value"
                )
            in_metadata = True
            result.fields["metadata"] = ""
            continue
        in_metadata = False
        value = _parse_value(raw_value, location, result.errors)
        if value is not None:
            result.fields[key] = value

    for key in REQUIRED_KEYS:
        if key not in result.fields:
            result.errors.append(f"missing required key '{key}'")
    result.fields.pop("metadata", None)
    result.body = "\n".join(lines[closing_index + 1 :])
    return result
