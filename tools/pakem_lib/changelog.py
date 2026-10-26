"""`pakem changelog`: release pending `changes/` fragments into CHANGELOG.md.

Each change is a fragment file `changes/<type>-<kebab-topic>.md` holding one or more
Markdown bullet entries, so concurrent branches never edit the same CHANGELOG.md lines.
Only a release moves fragments into a `## [X.Y.Z] - DATE` section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .common import (
    IGNORED_FILE_NAMES,
    PakemError,
    Framework,
    normalize_newlines,
    read_text,
    version_tuple,
    write_text_atomically,
)

# Keep a Changelog types, in the order their subsections appear in a release.
CHANGE_TYPES = ("added", "changed", "deprecated", "removed", "fixed", "security")
LEGACY_DEFAULT_TYPE = "changed"
FRAGMENTS_README = "README.md"
FRAGMENT_NAME = re.compile(r"^(?P<type>[a-z]+)-(?P<topic>[a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
FRAGMENT_BULLET = re.compile(r"^- \S")
FRAGMENT_CONTINUATION = re.compile(r"^  \s*\S")
CHANGELOG_BULLET = re.compile(r"^\s*[-*+]\s")
RELEASE_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UNRELEASED_HEADING = "## [Unreleased]"
SUBSECTION_PREFIX = "### "
SECTION_PREFIX = "## "


def heading_for(change_type: str) -> str:
    return change_type.capitalize()


@dataclass(frozen=True)
class Fragment:
    path: Path
    change_type: str
    entries: tuple[str, ...]


def fragment_paths(changes_dir: Path) -> list[Path]:
    """Every entry in changes/ that must be a fragment (README.md is the rule, not a change)."""
    if not changes_dir.is_dir():
        return []
    paths = []
    for path in sorted(changes_dir.iterdir()):
        if path.name == FRAGMENTS_README or path.name in IGNORED_FILE_NAMES:
            continue
        paths.append(path)
    return paths


def parse_fragment_text(text: str) -> tuple[list[str], list[str]]:
    """(entries, problems): each entry is a bullet line plus its continuation lines."""
    entries: list[list[str]] = []
    problems: list[str] = []
    current: list[str] | None = None
    for number, raw_line in enumerate(normalize_newlines(text).split("\n"), start=1):
        line = raw_line.rstrip()
        if not line:
            current = None
            continue
        if FRAGMENT_BULLET.match(line):
            current = [line]
            entries.append(current)
            continue
        if current is not None and FRAGMENT_CONTINUATION.match(line):
            current.append(line)
            continue
        problems.append(
            f"line {number}: expected a '- ' bullet or a continuation line indented two spaces "
            "directly below it"
        )
    return ["\n".join(entry) for entry in entries], problems


def check_fragment(path: Path) -> tuple[Fragment | None, list[str]]:
    """Parse one fragment file; return (fragment or None, problems)."""
    if not path.is_file():
        return None, ["is not a regular file. Fragments are changes/<type>-<kebab-topic>.md files"]
    problems: list[str] = []
    change_type = None
    match = FRAGMENT_NAME.match(path.name)
    if match is None:
        problems.append("bad name. Use changes/<type>-<kebab-topic>.md")
    elif match.group("type") not in CHANGE_TYPES:
        problems.append(f"unknown type '{match.group('type')}' (use one of: {', '.join(CHANGE_TYPES)})")
    else:
        change_type = match.group("type")

    try:
        text = read_text(path)
    except PakemError as exc:
        return None, problems + [str(exc)]
    entries, content_problems = parse_fragment_text(text)
    problems += content_problems
    if not entries:
        problems.append("has no bullet line ('- ...')")
    if problems:
        return None, problems
    return Fragment(path, change_type, tuple(entries)), []


def load_fragments(changes_dir: Path) -> tuple[list[Fragment], list[str]]:
    """(valid fragments, problems labelled changes/<name>) for every file in changes/."""
    fragments: list[Fragment] = []
    problems: list[str] = []
    for path in fragment_paths(changes_dir):
        fragment, fragment_problems = check_fragment(path)
        label = f"{changes_dir.name}/{path.name}"
        problems += [f"{label}: {problem}" for problem in fragment_problems]
        if fragment is not None:
            fragments.append(fragment)
    return fragments, problems


@dataclass
class UnreleasedSection:
    """Location of `## [Unreleased]` in CHANGELOG.md lines: heading index and end (exclusive)."""

    lines: list[str]
    heading: int
    end: int


def find_unreleased(text: str) -> UnreleasedSection:
    lines = normalize_newlines(text).splitlines()
    headings = [index for index, line in enumerate(lines) if line.rstrip() == UNRELEASED_HEADING]
    if not headings:
        raise PakemError(f"CHANGELOG.md has no '{UNRELEASED_HEADING}' section")
    if len(headings) > 1:
        raise PakemError(f"CHANGELOG.md has more than one '{UNRELEASED_HEADING}' section")
    heading = headings[0]
    end = len(lines)
    for index in range(heading + 1, len(lines)):
        if lines[index].startswith(SECTION_PREFIX):
            end = index
            break
    return UnreleasedSection(lines, heading, end)


def unreleased_bullet_lines(text: str) -> list[int]:
    """1-based line numbers of bullet lines under `## [Unreleased]`."""
    section = find_unreleased(text)
    numbers = []
    for index in range(section.heading + 1, section.end):
        if CHANGELOG_BULLET.match(section.lines[index]):
            numbers.append(index + 1)
    return numbers


def legacy_entries(section: UnreleasedSection) -> dict[str, list[str]]:
    """Bullet entries written directly under `## [Unreleased]`, grouped by their `###` subsection.

    Entries before any subsection go to Changed. Any other text is refused rather than dropped.
    """
    groups: dict[str, list[str]] = {change_type: [] for change_type in CHANGE_TYPES}
    group = LEGACY_DEFAULT_TYPE
    current: list[str] | None = None
    entries: list[tuple[str, list[str]]] = []
    for index in range(section.heading + 1, section.end):
        line = section.lines[index].rstrip()
        location = f"CHANGELOG.md line {index + 1}"
        if not line:
            current = None
            continue
        if line.startswith(SUBSECTION_PREFIX):
            heading = line[len(SUBSECTION_PREFIX) :].strip()
            if heading.lower() not in CHANGE_TYPES:
                raise PakemError(f"{location}: unknown subsection '{heading}' under {UNRELEASED_HEADING}")
            group = heading.lower()
            current = None
            continue
        if CHANGELOG_BULLET.match(line):
            current = [line]
            entries.append((group, current))
            continue
        if current is not None and line.startswith(" "):
            current.append(line)
            continue
        raise PakemError(
            f"{location}: text under {UNRELEASED_HEADING} that is neither a bullet entry nor a "
            "### subsection. Move it before releasing"
        )
    for change_type, entry_lines in entries:
        groups[change_type].append("\n".join(entry_lines))
    return groups


def merge_groups(fragments: list[Fragment], legacy: dict[str, list[str]]) -> dict[str, list[str]]:
    """Fragment entries first (newest, by file name), then legacy entries in file order."""
    groups: dict[str, list[str]] = {change_type: [] for change_type in CHANGE_TYPES}
    for fragment in sorted(fragments, key=lambda item: item.path.name):
        groups[fragment.change_type].extend(fragment.entries)
    for change_type in CHANGE_TYPES:
        groups[change_type].extend(legacy.get(change_type, []))
    return groups


def entry_count(groups: dict[str, list[str]]) -> int:
    return sum(len(entries) for entries in groups.values())


def render_groups(groups: dict[str, list[str]]) -> list[str]:
    lines: list[str] = []
    for change_type in CHANGE_TYPES:
        entries = groups.get(change_type, [])
        if not entries:
            continue
        if lines:
            lines.append("")
        lines += [SUBSECTION_PREFIX + heading_for(change_type), ""]
        for entry in entries:
            lines += entry.split("\n")
    return lines


def render_release_section(version: str, release_date: str, groups: dict[str, list[str]]) -> list[str]:
    return [f"## [{version}] - {release_date}", "", *render_groups(groups)]


def released_text(section: UnreleasedSection, release_lines: list[str]) -> str:
    """CHANGELOG.md text with an empty Unreleased section followed by the new release."""
    head = section.lines[: section.heading + 1]
    tail = section.lines[section.end :]
    lines = [*head, "", *release_lines]
    if tail:
        lines += ["", *tail]
    return "\n".join(lines) + "\n"


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_tuple = version_tuple(candidate)
    current_tuple = version_tuple(current)
    if candidate_tuple != current_tuple:
        return candidate_tuple > current_tuple
    # X.Y.Z releases a pre-release of the same numbers (0.2.0 > 0.2.0-rc1).
    return "-" in current.split("+", 1)[0]


def validate_release(version: str, release_date: str, current_version: str) -> None:
    if not RELEASE_VERSION.match(version):
        raise PakemError(f"--release must be X.Y.Z, got {version!r}")
    if not is_newer_version(version, current_version):
        raise PakemError(f"release {version} is not greater than VERSION {current_version}")
    if not RELEASE_DATE.match(release_date):
        raise PakemError(f"--date must be YYYY-MM-DD, got {release_date!r}")
    try:
        date.fromisoformat(release_date)
    except ValueError as exc:
        raise PakemError(f"--date {release_date} is not a valid date") from exc


def pending_changes(framework: Framework) -> tuple[list[Fragment], UnreleasedSection, dict[str, list[str]]]:
    """Valid fragments, the Unreleased section, and the merged groups; refuses invalid fragments."""
    fragments, problems = load_fragments(framework.changes_dir)
    if problems:
        raise PakemError("invalid change fragments:\n  " + "\n  ".join(problems))
    section = find_unreleased(read_text(framework.changelog_path))
    groups = merge_groups(fragments, legacy_entries(section))
    return fragments, section, groups


def relative_label(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def show_pending(framework: Framework) -> int:
    fragments, _, groups = pending_changes(framework)
    print(f"pakem changelog (pending) {framework.root}")
    count = entry_count(groups)
    if count == 0:
        print("No pending changes.")
        return 0
    legacy_count = count - sum(len(fragment.entries) for fragment in fragments)
    print(
        f"{count} pending entry(ies): {len(fragments)} fragment file(s), "
        f"{legacy_count} legacy entry(ies) under {UNRELEASED_HEADING}"
    )
    for fragment in sorted(fragments, key=lambda item: item.path.name):
        print(f"  {relative_label(fragment.path, framework.root)}")
    print()
    print("\n".join(render_groups(groups)))
    return 0


def release(framework: Framework, version: str, release_date: str, apply: bool) -> int:
    current_version = framework.version()
    validate_release(version, release_date, current_version)
    fragments, section, groups = pending_changes(framework)
    if entry_count(groups) == 0:
        raise PakemError("nothing to release: no fragments in changes/ and no entries under ## [Unreleased]")
    release_prefix = f"## [{version}]"
    if any(line.startswith(release_prefix) for line in section.lines):
        raise PakemError(f"CHANGELOG.md already has a {release_prefix} section")

    release_lines = render_release_section(version, release_date, groups)
    mode = "apply" if apply else "dry-run"
    print(f"pakem changelog --release {version} ({mode}) {framework.root}")
    print(f"Section under {UNRELEASED_HEADING} in CHANGELOG.md:")
    print()
    print("\n".join(release_lines))
    print()
    fragment_labels = [relative_label(fragment.path, framework.root) for fragment in fragments]
    print(f"Fragments consumed: {', '.join(fragment_labels) if fragment_labels else 'none'}")
    print(f"VERSION: {current_version} -> {version}")

    if not apply:
        print("Dry run: nothing written. Re-run with --apply to write.")
        return 0

    write_text_atomically(framework.changelog_path, released_text(section, release_lines))
    write_text_atomically(framework.version_path, version + "\n")
    for fragment in fragments:
        fragment.path.unlink()
    print(f"Released {version}: CHANGELOG.md and VERSION written, {len(fragments)} fragment(s) deleted.")
    return 0


def run_changelog(args) -> int:
    framework = Framework(Path(args.root))
    if args.release is None:
        if args.date is not None or args.apply:
            raise PakemError("--date and --apply require --release")
        return show_pending(framework)
    release_date = args.date if args.date is not None else date.today().isoformat()
    return release(framework, args.release, release_date, args.apply)
