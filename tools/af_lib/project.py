"""`af project sync` and `af project check` for a repository's AGENTS.md."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from .capabilities import ALWAYS, declarable_names, load_capabilities
from .common import (
    AfError,
    Framework,
    Report,
    is_git_work_tree,
    normalize_newlines,
    read_text,
    run_git,
    version_tuple,
    write_text_atomically,
)
from .pinned import (
    DIGEST_KIND,
    PROFILE_KIND,
    join_lines,
    normalize_body,
    parse_document,
    render_block,
)
from .profiles import Profile, load_profiles

AGENTS_FILE = "AGENTS.md"
AGENTS_WARN_CHARS = 10_000
AGENTS_ERROR_CHARS = 12_000
PROJECT_NAME_PLACEHOLDER = "{{project_name}}"
OVERRIDE_LABEL_PATTERN = re.compile(r"default:[a-z-]+")
OVERRIDE_BULLET_PATTERN = re.compile(r"^[-*+]\s+(.*)$")
OVERRIDE_PREFIX_PATTERN = re.compile(r"^`default:([a-z-]+)`")
HOME_PATH_PATTERN = re.compile(r"/Users/[^/\s]+/|(?<![\w.~])~/")
INSTRUCTION_DOC_NAMES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
CAPABILITIES_LINE_PATTERN = re.compile(r"^(?:[-*+]\s+)?Capabilities:(.*)$")
NO_CAPABILITIES = "none"
PROJECT_STATE_FILE = "PROJECT_STATE.md"
PROJECT_STATE_WARN_CHARS = 8_000
PROJECT_STATE_ERROR_CHARS = 16_000
RESEARCH_DIR = "research"


def _insert_with_spacing(lines: list[str], index: int, block_lines: list[str]) -> None:
    inserted = list(block_lines)
    if index > 0 and lines[index - 1].strip():
        inserted.insert(0, "")
    if index < len(lines) and lines[index].strip():
        inserted.append("")
    lines[index:index] = inserted


def _delete_range(lines: list[str], start: int, end: int) -> None:
    del lines[start : end + 1]
    if 0 < start < len(lines) and not lines[start - 1].strip() and not lines[start].strip():
        del lines[start]


def _top_insert_index(lines: list[str]) -> int:
    if lines and lines[0].startswith("# "):
        return 1
    return 0


def digest_block_lines(framework: Framework) -> list[str]:
    body = read_text(framework.digest_path)
    return render_block(DIGEST_KIND, [("version", framework.version())], body)


def profile_block_lines(profile: Profile) -> list[str]:
    attributes = [("name", profile.name), ("version", profile.version)]
    return render_block(PROFILE_KIND, attributes, profile.digest_text())


def _require_well_formed(text: str):
    document = parse_document(text)
    if document.problems:
        raise AfError("AGENTS.md has malformed pinned blocks: " + "; ".join(document.problems))
    return document


def synchronize_text(
    text: str,
    digest_lines: list[str],
    profile_blocks: list[tuple[str, list[str]]],
    replace_legacy: bool,
    report: Report,
) -> str:
    """Return text with the digest and profile blocks inserted or refreshed."""
    document = _require_well_formed(text)
    lines = list(document.lines)

    wanted: dict[tuple[str, str], list[str]] = {(DIGEST_KIND, ""): digest_lines}
    for name, block_lines in profile_blocks:
        wanted[(PROFILE_KIND, name)] = block_lines

    placed: set[tuple[str, str]] = set()
    for block in sorted(document.blocks, key=lambda item: item.start, reverse=True):
        replacement = wanted.get(block.identity)
        if replacement is None:
            report.warn(
                f"profile block '{block.attributes.get('name', '')}' left unchanged (profile not given)"
            )
            continue
        if block.identity in placed:
            raise AfError(
                f"AGENTS.md contains more than one {block.kind} block for {block.identity[1] or 'digest'}"
            )
        lines[block.start : block.end + 1] = replacement
        placed.add(block.identity)

    document = _require_well_formed(join_lines(lines))
    if document.legacy is not None:
        legacy_start, legacy_end = document.legacy
        if not replace_legacy:
            report.warn(
                f"legacy owner-baseline block at lines {legacy_start + 1}-{legacy_end + 1}; "
                "pass --replace-legacy to replace it with the digest block"
            )
        elif (DIGEST_KIND, "") in placed:
            _delete_range(lines, legacy_start, legacy_end)
            report.warn("removed legacy owner-baseline block (digest block already present)")
        else:
            lines[legacy_start : legacy_end + 1] = digest_lines
            placed.add((DIGEST_KIND, ""))
            report.warn("replaced legacy owner-baseline block with the digest block")

    if (DIGEST_KIND, "") not in placed:
        document = _require_well_formed(join_lines(lines))
        if document.blocks:
            index = min(block.start for block in document.blocks)
        else:
            index = _top_insert_index(lines)
        _insert_with_spacing(lines, index, digest_lines)

    for name, block_lines in profile_blocks:
        if (PROFILE_KIND, name) in placed:
            continue
        document = _require_well_formed(join_lines(lines))
        index = max(block.end for block in document.blocks) + 1
        _insert_with_spacing(lines, index, block_lines)

    return join_lines(lines)


def _initial_agents_text(framework: Framework, repo: Path) -> str:
    template = framework.project_template_path
    if template.is_file():
        return read_text(template).replace(PROJECT_NAME_PLACEHOLDER, repo.name)
    return f"# {repo.name}\n"


def _resolve_repo(raw: str | None) -> Path:
    repo = Path(raw or ".").resolve()
    if not repo.is_dir():
        raise AfError(f"repository directory not found: {repo}")
    return repo


def run_sync(args) -> int:
    framework = Framework(Path(args.root))
    repo = _resolve_repo(args.repo)
    agents_path = repo / AGENTS_FILE
    report = Report()

    if agents_path.exists():
        original = normalize_newlines(read_text(agents_path))
        creating = False
    else:
        original = ""
        creating = True
    starting_text = _initial_agents_text(framework, repo) if creating else original

    profiles = load_profiles(args.profile, framework.version())
    profile_blocks = [(profile.name, profile_block_lines(profile)) for profile in profiles]
    updated = synchronize_text(
        starting_text, digest_block_lines(framework), profile_blocks, args.replace_legacy, report
    )

    mode = "apply" if args.apply else "dry-run"
    print(f"af project sync ({mode}) {agents_path}")
    if updated == original and not creating:
        report.print()
        print("AGENTS.md is up to date.")
        return 0

    if not args.apply:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile="a/AGENTS.md" if not creating else "/dev/null",
            tofile="b/AGENTS.md",
        )
        print("".join(diff), end="")
        report.print()
        print("Dry run: nothing written. Re-run with --apply to write.")
        return 0

    write_text_atomically(agents_path, updated)
    report.print()
    print("Created AGENTS.md." if creating else "Updated AGENTS.md.")
    return 0


def override_labels(framework: Framework) -> set[str]:
    labels = OVERRIDE_LABEL_PATTERN.findall(read_text(framework.kernel_path))
    return {label.removeprefix("default:") for label in labels}


def _lines_outside_blocks(document) -> list[tuple[int, str]]:
    covered: set[int] = set()
    for block in document.blocks:
        covered.update(range(block.start, block.end + 1))
    return [(index, line) for index, line in enumerate(document.lines) if index not in covered]


def _check_overrides(document, labels: set[str], report: Report) -> None:
    in_section = False
    for index, line in _lines_outside_blocks(document):
        if line.startswith("# ") or line.startswith("## "):
            in_section = line.strip() == "## Overrides"
            continue
        if not in_section:
            continue
        bullet = OVERRIDE_BULLET_PATTERN.match(line)
        if bullet is None:
            continue
        prefix = OVERRIDE_PREFIX_PATTERN.match(bullet.group(1))
        if prefix is None:
            report.error(f"AGENTS.md line {index + 1}: override bullet must start with `default:<label>`")
        elif prefix.group(1) not in labels:
            report.error(
                f"AGENTS.md line {index + 1}: unknown override label 'default:{prefix.group(1)}' "
                "(not defined in kernel/KERNEL.md)"
            )


def _instruction_docs(repo: Path) -> list[Path]:
    if is_git_work_tree(repo):
        listed = run_git(repo, "ls-files", "--", *INSTRUCTION_DOC_NAMES, ".agents/rules").stdout.splitlines()
        documents = []
        for relative in listed:
            path = Path(relative)
            in_rules = path.parent.as_posix() == ".agents/rules" and path.suffix == ".md"
            if relative in INSTRUCTION_DOC_NAMES or in_rules:
                documents.append(repo / relative)
        return documents
    documents = [repo / name for name in INSTRUCTION_DOC_NAMES if (repo / name).is_file()]
    documents += sorted((repo / ".agents" / "rules").glob("*.md"))
    return documents


def _check_home_paths(repo: Path, report: Report) -> None:
    for path in _instruction_docs(repo):
        if not path.is_file():
            continue
        document = parse_document(read_text(path))
        for index, line in _lines_outside_blocks(document):
            if HOME_PATH_PATTERN.search(line):
                relative = path.relative_to(repo).as_posix()
                report.warn(f"{relative} line {index + 1}: machine-specific home path (/Users/... or ~/)")


def _check_blocks(framework: Framework, document, report: Report) -> None:
    framework_version = framework.version()
    digest_blocks = [block for block in document.blocks if block.kind == DIGEST_KIND]
    if not digest_blocks:
        report.warn("AGENTS.md has no agent-framework digest block (run af project sync)")
    if len(digest_blocks) > 1:
        report.error("AGENTS.md has more than one digest block")

    current_digest = normalize_body(read_text(framework.digest_path))
    for block in document.blocks:
        label = block.kind if block.kind == DIGEST_KIND else f"profile {block.attributes.get('name', '?')}"
        if not block.integrity_ok():
            report.error(
                f"{label} block (line {block.start + 1}) body does not match its sha256; it was edited by hand"
            )
        if block.kind != DIGEST_KIND:
            continue
        block_version = block.attributes.get("version", "")
        try:
            older = version_tuple(block_version) < version_tuple(framework_version)
        except AfError:
            report.error(f"digest block has an invalid version: {block_version!r}")
            continue
        if older:
            report.warn(
                f"digest block version {block_version} is older than framework {framework_version} (run af project sync)"
            )
        elif block.integrity_ok() and block.body != current_digest:
            report.warn("digest block content differs from the framework DIGEST.md (run af project sync)")


def declared_capabilities(document) -> list[tuple[int, list[str]]]:
    """(line index, values) for every `Capabilities:` line outside pinned blocks."""
    declarations = []
    for index, line in _lines_outside_blocks(document):
        match = CAPABILITIES_LINE_PATTERN.match(line.strip())
        if match is None:
            continue
        values = []
        for raw in match.group(1).split(","):
            value = raw.strip().strip("`").strip()
            if value:
                values.append(value)
        declarations.append((index, values))
    return declarations


def _check_capabilities(framework: Framework, document, report: Report) -> None:
    declarations = declared_capabilities(document)
    if not declarations:
        report.warn(
            "AGENTS.md has no 'Capabilities:' line; declare project capabilities "
            "(for example `Capabilities: deployed, database`, or `Capabilities: none`)"
        )
        return
    if len(declarations) > 1:
        lines = ", ".join(str(index + 1) for index, _ in declarations)
        report.error(f"AGENTS.md declares capabilities on more than one line ({lines}); keep one")

    try:
        known = declarable_names(load_capabilities(framework.capabilities_path))
    except AfError as exc:
        report.error(f"cannot check declared capabilities: {exc}")
        return

    for index, values in declarations:
        location = f"AGENTS.md line {index + 1}"
        if not values:
            report.error(
                f"{location}: 'Capabilities:' has no values (write `Capabilities: none` if none apply)"
            )
            continue
        if values == [NO_CAPABILITIES]:
            continue
        seen: set[str] = set()
        for value in values:
            if value in seen:
                report.warn(f"{location}: capability '{value}' is listed twice")
                continue
            seen.add(value)
            if value == ALWAYS:
                report.error(f"{location}: '{ALWAYS}' applies to every project and is not declared")
            elif value == NO_CAPABILITIES:
                report.error(f"{location}: '{NO_CAPABILITIES}' cannot be combined with other capabilities")
            elif value not in known:
                report.error(
                    f"{location}: unknown capability '{value}' (known: {', '.join(known)})"
                )


def _check_project_files(repo: Path, report: Report) -> None:
    state_path = repo / PROJECT_STATE_FILE
    if state_path.is_file():
        char_count = len(read_text(state_path))
        if char_count > PROJECT_STATE_ERROR_CHARS:
            report.error(
                f"{PROJECT_STATE_FILE} is {char_count} chars (limit {PROJECT_STATE_ERROR_CHARS}); "
                "move history to JOURNAL.md or verification.md"
            )
        elif char_count > PROJECT_STATE_WARN_CHARS:
            report.warn(
                f"{PROJECT_STATE_FILE} is {char_count} chars (warning above {PROJECT_STATE_WARN_CHARS})"
            )
    if not (repo / RESEARCH_DIR).is_dir():
        report.warn(f"{RESEARCH_DIR}/ folder is missing (notes, prototypes, and evidence live there)")


def check_repo(framework: Framework, repo: Path) -> Report:
    report = Report()
    agents_path = repo / AGENTS_FILE
    if not agents_path.is_file():
        report.error(f"{AGENTS_FILE} not found in {repo}")
        return report

    text = normalize_newlines(read_text(agents_path))
    document = parse_document(text)
    for problem in document.problems:
        report.error(f"AGENTS.md {problem}")
    if document.legacy is not None:
        report.warn(
            "AGENTS.md still contains a legacy owner-baseline block (af project sync --replace-legacy)"
        )

    _check_blocks(framework, document, report)

    char_count = len(text)
    if char_count > AGENTS_ERROR_CHARS:
        report.error(f"AGENTS.md is {char_count} chars (limit {AGENTS_ERROR_CHARS})")
    elif char_count > AGENTS_WARN_CHARS:
        report.warn(f"AGENTS.md is {char_count} chars (warning above {AGENTS_WARN_CHARS})")

    _check_home_paths(repo, report)
    _check_overrides(document, override_labels(framework), report)
    _check_capabilities(framework, document, report)
    _check_project_files(repo, report)
    return report


def run_check(args) -> int:
    framework = Framework(Path(args.root))
    repo = _resolve_repo(args.repo)
    report = check_repo(framework, repo)
    print(f"af project check {repo}")
    report.print()
    return report.exit_code
