"""The capability registry in standards/capabilities.toml (Working Standard Profile, section 2)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .common import PakemError

ALWAYS = "always"
CAPABILITY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Capability:
    name: str
    summary: str
    standards: tuple[str, ...]


def _load_table(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise PakemError(f"capability registry not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"capability registry is invalid TOML ({path}): {exc}") from exc
    table = data.get("capabilities")
    if not isinstance(table, dict) or not table:
        raise PakemError(f"capability registry has no [capabilities] tables: {path}")
    return table


def registry_problems(path: Path) -> list[str]:
    """Every structural problem in the registry; an empty list means it is valid."""
    try:
        table = _load_table(path)
    except PakemError as exc:
        return [str(exc)]

    problems: list[str] = []
    if ALWAYS not in table:
        problems.append(f"[capabilities.{ALWAYS}] is missing")
    for name, entry in table.items():
        label = f"[capabilities.{name}]"
        if not CAPABILITY_NAME_PATTERN.match(name):
            problems.append(f"{label}: name must match {CAPABILITY_NAME_PATTERN.pattern}")
        if not isinstance(entry, dict):
            problems.append(f"{label}: must be a table")
            continue
        summary = entry.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            problems.append(f"{label}: needs a non-empty 'summary' string")
        standards = entry.get("standards")
        if not isinstance(standards, list) or not standards:
            problems.append(f"{label}: needs a non-empty 'standards' list")
            continue
        for index, standard in enumerate(standards):
            if not isinstance(standard, str) or not standard.strip():
                problems.append(f"{label}: standards[{index}] must be a non-empty string")
    return problems


def load_capabilities(path: Path) -> dict[str, Capability]:
    """Load a registry that registry_problems() accepts; raises PakemError otherwise."""
    problems = registry_problems(path)
    if problems:
        raise PakemError("invalid capability registry: " + "; ".join(problems))
    capabilities: dict[str, Capability] = {}
    for name, entry in _load_table(path).items():
        capabilities[name] = Capability(name, entry["summary"], tuple(entry["standards"]))
    return capabilities


def declarable_names(capabilities: dict[str, Capability]) -> list[str]:
    """Capabilities a project may list; "always" applies implicitly and is never declared."""
    return sorted(name for name in capabilities if name != ALWAYS)
