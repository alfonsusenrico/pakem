"""Provenance checks for what `af install` copies: the framework checkout and git profiles.

The framework is installed from merged `main` only. Two independent waivers exist so
a branch can still be tested deliberately:

- `--allow-dirty` waives uncommitted changes, and a framework root that is not a git
  work tree at all (in both cases the content cannot be tied to a commit);
- `--allow-unmerged` waives a branch other than `main`, a detached HEAD, and a HEAD
  that differs from (or cannot be compared with) `origin/main`.

The comparison with `origin/main` uses the local remote-tracking ref and never
fetches, so it is only as fresh as the last `git fetch`.

Profiles given with `--profile` that are git work trees are checked for branch `main`
and a clean tree. Those findings are warnings unless `--strict-source` is given, in
which case `--apply` refuses; the framework waivers do not apply to profiles.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .common import is_git_work_tree, run_git

INSTALL_BRANCH = "main"
REMOTE = "origin"
REMOTE_BRANCH_REF = f"refs/remotes/{REMOTE}/{INSTALL_BRANCH}"

WAIVER_DIRTY = "--allow-dirty"
WAIVER_UNMERGED = "--allow-unmerged"


@dataclass(frozen=True)
class SourceFinding:
    message: str
    waiver: str | None = None


@dataclass
class SourceStatus:
    findings: list[SourceFinding]
    summary: str


def _short(sha: str) -> str:
    return sha[:12]


def _current_branch(root: Path) -> str | None:
    completed = run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _has_uncommitted_changes(root: Path) -> bool:
    return bool(run_git(root, "status", "--porcelain").stdout.strip())


def _remote_names(root: Path) -> set[str]:
    return set(run_git(root, "remote").stdout.split())


def _resolve(root: Path, ref: str) -> str | None:
    completed = run_git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _branch_findings(root: Path, label: str, waiver: str | None) -> tuple[list[SourceFinding], str | None]:
    branch = _current_branch(root)
    if branch is None:
        return [SourceFinding(f"{label} is on a detached HEAD, not {INSTALL_BRANCH}", waiver)], None
    if branch != INSTALL_BRANCH:
        message = f"{label} is on branch {branch}, not {INSTALL_BRANCH}; install from merged {INSTALL_BRANCH}"
        return [SourceFinding(message, waiver)], branch
    return [], branch


def _remote_findings(root: Path, head: str) -> tuple[list[SourceFinding], str]:
    """Compare HEAD with the local origin/main ref; returns findings and a summary fragment."""
    if REMOTE not in _remote_names(root):
        return [], f"no {REMOTE} remote; merge state not checked"
    remote_head = _resolve(root, REMOTE_BRANCH_REF)
    if remote_head is None:
        message = f"{REMOTE}/{INSTALL_BRANCH} is not known locally, so merge state is unknown; fetch to refresh"
        return [SourceFinding(message, WAIVER_UNMERGED)], f"{REMOTE}/{INSTALL_BRANCH} unknown"
    if remote_head == head:
        return [], f"matches {REMOTE}/{INSTALL_BRANCH} as of the last fetch; fetch to refresh"
    counts = run_git(root, "rev-list", "--left-right", "--count", f"{REMOTE_BRANCH_REF}...HEAD").stdout.split()
    behind, ahead = int(counts[0]), int(counts[1])
    message = (
        f"framework HEAD {_short(head)} differs from {REMOTE}/{INSTALL_BRANCH} {_short(remote_head)} "
        f"({ahead} ahead, {behind} behind as of the last fetch; fetch to refresh, then merge or pull)"
    )
    return [SourceFinding(message, WAIVER_UNMERGED)], f"differs from {REMOTE}/{INSTALL_BRANCH}"


def framework_source_status(root: Path) -> SourceStatus:
    if not is_git_work_tree(root):
        message = f"framework root {root} is not a git work tree; installed content cannot be tied to a commit"
        return SourceStatus([SourceFinding(message, WAIVER_DIRTY)], "not a git work tree")

    findings: list[SourceFinding] = []
    branch_findings, branch = _branch_findings(root, "framework checkout", WAIVER_UNMERGED)
    findings += branch_findings
    if _has_uncommitted_changes(root):
        message = "framework git tree has uncommitted changes; installed content would not match a commit"
        findings.append(SourceFinding(message, WAIVER_DIRTY))

    head = _resolve(root, "HEAD")
    if head is None:
        findings.append(SourceFinding("framework checkout has no commits", WAIVER_DIRTY))
        return SourceStatus(findings, "no commits")
    remote_findings, remote_summary = _remote_findings(root, head)
    findings += remote_findings
    summary = f"{branch or 'detached HEAD'} @ {_short(head)}; {remote_summary}"
    return SourceStatus(findings, summary)


def profile_source_findings(name: str, root: Path) -> list[SourceFinding]:
    """Branch and cleanliness findings for a profile that is a git work tree; none otherwise."""
    if not is_git_work_tree(root):
        return []
    label = f"profile {name} checkout"
    findings, _ = _branch_findings(root, label, None)
    if _has_uncommitted_changes(root):
        findings.append(SourceFinding(f"{label} has uncommitted changes", None))
    return findings
