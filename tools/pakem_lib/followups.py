"""What a Pakem update asks of a project (ADR 0017).

Two kinds of follow-up, both read from the framework checkout's git history, so they work
offline and after a release consumes the change fragments:

- copies: files the project copied from Pakem (CI workflows, `deploy/af/*`). Each is
  compared with every version of its Pakem asset: the current one, an older one (an
  unmodified copy, which `pakem project sync --templates` re-copies), or none (a copy the
  project changed, which its agent merges by hand);
- notes: `- Projects: ...` bullets of the change fragments merged since the commit the
  project last aligned to, recorded in `.pakem/framework.toml` by
  `pakem project sync --aligned`.

A framework root that is not a git checkout has no follow-ups.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .common import Framework, PakemError, display_path, run_git, toml_value, write_text_atomically

ALIGN_DIR = ".pakem"
ALIGN_FILE = "framework.toml"
CI_ASSETS = "skills/release-delivery/assets/ci/github/workflows"
DEPLOY_ASSETS = "skills/release-delivery/assets/deploy"
CHANGES_DIR = "changes"
NOTE_PREFIX = "- Projects:"
# Copies may carry a provenance line near the top, after a shebang:
#   # Copied from Pakem 851a464 (skills/release-delivery/assets/ci/github/workflows/release.yml); do not edit here.
COPY_HEADER = re.compile(r"^#\s*Copied from (?:Pakem|agent-framework)\b[^(]*(?:\(([^)]+)\))?")
# Every reusable workflow template starts with this line; a project's own workflow that only
# shares a file name with one (a `dist` release.yml) does not.
TEMPLATE_MARK = "# Pakem CI template:"
HEADER_SCAN_LINES = 3
COMMIT = re.compile(r"^[0-9a-f]{40}$")

CURRENT = "current"
OUTDATED = "outdated"
MODIFIED = "modified"
MERGED = "merged"


@dataclass
class Copy:
    path: str
    asset: str
    state: str
    header: list[str] = field(default_factory=list)

    @property
    def pending(self) -> bool:
        return self.state in (OUTDATED, MODIFIED)


@dataclass
class Note:
    fragment: str
    text: str


@dataclass
class FollowUps:
    head: str | None = None
    aligned: str | None = None
    copies: list[Copy] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def pending(self) -> int:
        return sum(1 for copy in self.copies if copy.pending) + len(self.notes)

    def summary(self) -> str:
        parts = []
        for state in (OUTDATED, MODIFIED):
            count = sum(1 for copy in self.copies if copy.state == state)
            if count:
                parts.append(f"{count} {state} cop{'y' if count == 1 else 'ies'} of Pakem files")
        if self.notes:
            parts.append(f"{len(self.notes)} change note{'' if len(self.notes) == 1 else 's'} for projects")
        return ", ".join(parts)


def align_path(repo: Path) -> Path:
    return repo / ALIGN_DIR / ALIGN_FILE


def load_alignment(repo: Path) -> dict:
    path = align_path(repo)
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in {display_path(path)}: {exc}") from exc
    aligned = data.get("aligned")
    if aligned is not None and not (isinstance(aligned, str) and COMMIT.match(aligned)):
        raise PakemError(f"{display_path(path)}: aligned must be a 40-character commit")
    merged = data.get("merged", {})
    if not isinstance(merged, dict) or not all(isinstance(value, str) for value in merged.values()):
        raise PakemError(f"{display_path(path)}: [merged] maps copied paths to Pakem blob ids")
    return {"aligned": aligned, "merged": merged}


def alignment_text(head: str, merged: dict[str, str], today: str) -> str:
    lines = [
        "# Written by `pakem project sync --aligned` (ADR 0017): the Pakem commit this project last",
        "# aligned to, and the Pakem version each changed copy was merged with.",
        f"aligned = {toml_value(head)}",
        f"aligned_at = {toml_value(today)}",
    ]
    if merged:
        lines += ["", "[merged]"]
        lines += [f"{toml_value(path)} = {toml_value(blob)}" for path, blob in sorted(merged.items())]
    return "\n".join(lines) + "\n"


def _git(root: Path, *args: str) -> str | None:
    completed = run_git(root, *args, check=False)
    return completed.stdout if completed.returncode == 0 else None


def framework_head(framework: Framework) -> str | None:
    if _git(framework.root, "rev-parse", "--is-inside-work-tree") is None:
        return None
    head = _git(framework.root, "rev-parse", "HEAD")
    return head.strip() if head else None


def blob_id(data: bytes) -> str:
    """The id git gives this content, so a copy is compared with history without writing it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def split_header(text: str) -> tuple[list[str], str]:
    """(provenance lines, the text without them): only the first few lines are searched."""
    lines = text.splitlines(keepends=True)
    marked = {index for index, line in enumerate(lines[:HEADER_SCAN_LINES]) if COPY_HEADER.match(line)}
    header = [lines[index] for index in sorted(marked)]
    return header, "".join(line for index, line in enumerate(lines) if index not in marked)


def _asset_from_header(header: list[str], name: str) -> str | None:
    for line in header:
        match = COPY_HEADER.match(line)
        if match and match.group(1):
            source = match.group(1).strip()
            return source if source.endswith(name) else f"{source.rstrip('/')}/{name}"
    return None


def copy_candidates(framework: Framework, repo: Path) -> list[tuple[str, str]]:
    """(project path, Pakem asset) for each file the project holds that came from Pakem."""
    found: dict[str, str] = {}
    workflows = repo / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []:
        relative = path.relative_to(repo).as_posix()
        header, body = split_header(path.read_text(encoding="utf-8", errors="replace"))
        asset = _asset_from_header(header, path.name)
        if (asset is None and body.startswith(TEMPLATE_MARK)
                and (framework.root / CI_ASSETS / path.name).is_file()):
            asset = f"{CI_ASSETS}/{path.name}"
        if asset is not None:
            found[relative] = asset
    deploy = repo / "deploy" / "af"
    for path in sorted(deploy.iterdir()) if deploy.is_dir() else []:
        if path.is_file() and (framework.root / DEPLOY_ASSETS / path.name).is_file():
            found[path.relative_to(repo).as_posix()] = f"{DEPLOY_ASSETS}/{path.name}"
    return sorted(found.items())


def asset_history(framework: Framework, asset: str) -> set[str]:
    """Every blob id the asset has had, following renames."""
    output = _git(framework.root, "log", "--follow", "--format=", "--raw", "--no-abbrev", "--", asset) or ""
    blobs = set()
    for line in output.splitlines():
        if line.startswith(":"):
            fields = line.split()
            blobs.update(blob for blob in fields[2:4] if set(blob) != {"0"})
    return blobs


def classify(framework: Framework, repo: Path, merged: dict[str, str]) -> list[Copy]:
    copies = []
    for relative, asset in copy_candidates(framework, repo):
        current = _git(framework.root, "rev-parse", f"HEAD:{asset}")
        if current is None:
            continue
        current = current.strip()
        header, body = split_header((repo / relative).read_text(encoding="utf-8", errors="replace"))
        blob = blob_id(body.encode("utf-8"))
        if blob == current:
            state = CURRENT
        elif blob in asset_history(framework, asset):
            state = OUTDATED
        elif merged.get(relative) == current:
            state = MERGED
        else:
            state = MODIFIED
        copies.append(Copy(relative, asset, state, header))
    return copies


def _note_lines(text: str) -> list[str]:
    notes, current = [], None
    for line in text.splitlines():
        if line.startswith("- "):
            current = [line] if line.startswith(NOTE_PREFIX) else None
            if current is not None:
                notes.append(current)
        elif current is not None and line.startswith("  "):
            current.append(line.strip())
    return [" ".join(note)[len("- "):] for note in notes]


def _fragment_names(paths: list[str]) -> list[str]:
    return [path for path in paths if path.startswith(f"{CHANGES_DIR}/") and path.endswith(".md")
            and not path.endswith("/README.md")]


def notes_since(framework: Framework, aligned: str | None) -> tuple[list[Note], list[str]]:
    """Change notes for projects merged after `aligned`. A project without a record never aligned,
    so it gets every note in the history: a release deletes the fragments, never their notes."""
    problems: list[str] = []
    if aligned is not None and _git(framework.root, "cat-file", "-e", f"{aligned}^{{commit}}") is None:
        problems.append(f"the aligned Pakem commit {aligned[:12]} is not in {display_path(framework.root)}. "
                        "Update the framework checkout")
        aligned = None
    sources: dict[str, str] = {}
    revisions = f"{aligned}..HEAD" if aligned is not None else "HEAD"
    log = _git(framework.root, "log", "--reverse", "--diff-filter=AM", "--format=commit %H", "--name-only",
               revisions, "--", CHANGES_DIR) or ""
    commit = None
    for line in log.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1]
        elif line and commit is not None and line in _fragment_names([line]):
            text = _git(framework.root, "show", f"{commit}:{line}")
            if text is not None:
                sources[line] = text
    notes = [Note(Path(path).stem, text) for path, content in sorted(sources.items())
             for text in _note_lines(content)]
    return notes, problems


def collect(framework: Framework, repo: Path) -> FollowUps:
    result = FollowUps(head=framework_head(framework))
    if result.head is None:
        return result
    alignment = load_alignment(repo)
    result.aligned = alignment.get("aligned")
    result.copies = classify(framework, repo, alignment.get("merged", {}))
    result.notes, result.problems = notes_since(framework, result.aligned)
    return result


def describe(framework: Framework, result: FollowUps) -> list[str]:
    """One line per pending follow-up, for `pakem project check`."""
    lines = []
    for copy in result.copies:
        if copy.state == OUTDATED:
            lines.append(f"{copy.path} is an older copy of Pakem's {copy.asset}. "
                         "Re-copy it with `pakem project sync --templates --apply`")
        elif copy.state == MODIFIED:
            lines.append(f"{copy.path} differs from every version of Pakem's {copy.asset}. Merge Pakem's "
                         f"changes by hand (`git -C {display_path(framework.root)} log -p -- {copy.asset}`), "
                         "then record it with `pakem project sync --aligned --apply`")
    for note in result.notes:
        lines.append(f"Pakem change {note.fragment}: {note.text}")
    lines += result.problems
    return lines


def recopy(framework: Framework, repo: Path, copy: Copy) -> str:
    """The asset's current text, keeping a provenance line where the copy had one."""
    text = (framework.root / copy.asset).read_text(encoding="utf-8")
    if not copy.header:
        return text
    commit = framework_head(framework) or ""
    header = f"# Copied from Pakem {commit[:7]} ({copy.asset}); do not edit here.\n"
    lines = text.splitlines(keepends=True)
    position = 1 if lines and lines[0].startswith("#!") else 0
    return "".join(lines[:position] + [header] + lines[position:])


def sync(framework: Framework, repo: Path, templates: bool, aligned: bool, apply: bool) -> list[str]:
    """Re-copy outdated copies and/or record the alignment; returns what it did or would do."""
    result = collect(framework, repo)
    if result.head is None:
        raise PakemError(f"{display_path(framework.root)} is not a git checkout. Follow-ups need its history")
    done = []
    if templates:
        for copy in result.copies:
            if copy.state != OUTDATED:
                continue
            done.append(f"re-copy {copy.path} from {copy.asset}")
            if apply:
                target = repo / copy.path
                # Deploy scripts are often executable: the copy keeps the project's permissions.
                write_text_atomically(target, recopy(framework, repo, copy), mode=target.stat().st_mode & 0o777)
    if aligned:
        alignment = load_alignment(repo)
        merged = dict(alignment.get("merged", {}))
        for copy in classify(framework, repo, merged) if apply else result.copies:
            if copy.state in (MODIFIED, MERGED):
                current = _git(framework.root, "rev-parse", f"HEAD:{copy.asset}")
                if current:
                    merged[copy.path] = current.strip()
            else:
                merged.pop(copy.path, None)
        done.append(f"record alignment with Pakem {result.head[:12]} in {ALIGN_DIR}/{ALIGN_FILE}")
        if apply:
            write_text_atomically(align_path(repo), alignment_text(result.head, merged, date.today().isoformat()))
    return done


def known_projects() -> tuple[list[Path], list[str]]:
    """The device's projects: the weekly report's repositories and the hosts' project folders."""
    # Imported here: both modules import far more than follow-ups need.
    from .drift_report import DEFAULT_CONFIG, collect_repos, load_config
    from .host import load_hosts
    from .common import Report, expand_user_path

    repos: list[Path] = []
    problems: list[str] = []
    config_path = expand_user_path(DEFAULT_CONFIG)
    if config_path.is_file():
        try:
            repos += collect_repos(load_config(config_path), Report())
        except PakemError as exc:
            problems.append(f"report config unreadable: {exc}")
    try:
        for record in load_hosts().values():
            repos += [Path(directory) for directory in record.project_dirs]
    except PakemError as exc:
        problems.append(f"hosts unreadable: {exc}")
    unique = []
    for repo in repos:
        resolved = repo.expanduser().resolve()
        if resolved.is_dir() and resolved not in unique:
            unique.append(resolved)
    return unique, problems


def projects_to_align(framework: Framework) -> list[str]:
    """One line per known project with pending follow-ups, for the end of `pakem update`."""
    if framework_head(framework) is None:
        return []
    repos, lines = known_projects()
    for repo in repos:
        if not (repo / "AGENTS.md").is_file():
            continue
        try:
            result = collect(framework, repo)
        except PakemError as exc:
            lines.append(f"{repo.name}: follow-ups unreadable ({exc})")
            continue
        if result.pending:
            lines.append(f"{repo.name}: {result.summary()}")
    return lines
