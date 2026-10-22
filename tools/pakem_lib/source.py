"""Provenance checks for what `pakem install` copies: the framework checkout and git profiles.

The framework is installed from merged `main` only: a `main` checkout, or a release tag
(`vX.Y.Z`, what the installer checks out) whose commit is part of `origin/main`. Two
independent waivers exist so a branch can still be tested deliberately:

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

import re
from dataclasses import dataclass
from pathlib import Path

from .common import is_git_work_tree, run_git

INSTALL_BRANCH = "main"
REMOTE = "origin"
REMOTE_BRANCH_REF = f"refs/remotes/{REMOTE}/{INSTALL_BRANCH}"

WAIVER_DIRTY = "--allow-dirty"
WAIVER_UNMERGED = "--allow-unmerged"
RELEASE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


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


def current_branch(root: Path) -> str | None:
    completed = run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def has_uncommitted_changes(root: Path) -> bool:
    return bool(run_git(root, "status", "--porcelain").stdout.strip())


def _remote_names(root: Path) -> set[str]:
    return set(run_git(root, "remote").stdout.split())


def resolve_commit(root: Path, ref: str) -> str | None:
    completed = run_git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _branch_findings(root: Path, label: str, waiver: str | None) -> tuple[list[SourceFinding], str | None]:
    branch = current_branch(root)
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
    remote_head = resolve_commit(root, REMOTE_BRANCH_REF)
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


def head_release_tag(root: Path) -> str | None:
    """The newest release tag pointing at HEAD, if any."""
    tags = run_git(root, "tag", "--points-at", "HEAD").stdout.split()
    releases = [tag for tag in tags if RELEASE_TAG.fullmatch(tag)]
    if not releases:
        return None
    return max(releases, key=lambda tag: tuple(int(part) for part in RELEASE_TAG.fullmatch(tag).groups()))


def _release_findings(root: Path, tag: str, head: str) -> tuple[list[SourceFinding], str]:
    """A detached release tag installs when its commit is part of origin/main."""
    remote_head = resolve_commit(root, REMOTE_BRANCH_REF)
    if remote_head is None:
        message = f"release {tag} cannot be checked against {REMOTE}/{INSTALL_BRANCH}; fetch to refresh"
        return [SourceFinding(message, WAIVER_UNMERGED)], f"{REMOTE}/{INSTALL_BRANCH} unknown"
    merged = run_git(root, "merge-base", "--is-ancestor", head, remote_head, check=False).returncode == 0
    if not merged:
        message = f"release {tag} is not part of {REMOTE}/{INSTALL_BRANCH}; install a published release"
        return [SourceFinding(message, WAIVER_UNMERGED)], f"not in {REMOTE}/{INSTALL_BRANCH}"
    return [], f"part of {REMOTE}/{INSTALL_BRANCH}"


def framework_source_status(root: Path) -> SourceStatus:
    if not is_git_work_tree(root):
        message = f"framework root {root} is not a git work tree; installed content cannot be tied to a commit"
        return SourceStatus([SourceFinding(message, WAIVER_DIRTY)], "not a git work tree")

    findings: list[SourceFinding] = []
    tag = head_release_tag(root) if current_branch(root) is None else None
    branch = None
    if tag is None:
        branch_findings, branch = _branch_findings(root, "framework checkout", WAIVER_UNMERGED)
        findings += branch_findings
    if has_uncommitted_changes(root):
        message = "framework git tree has uncommitted changes; installed content would not match a commit"
        findings.append(SourceFinding(message, WAIVER_DIRTY))

    head = resolve_commit(root, "HEAD")
    if head is None:
        findings.append(SourceFinding("framework checkout has no commits", WAIVER_DIRTY))
        return SourceStatus(findings, "no commits")
    if tag is not None:
        remote_findings, remote_summary = _release_findings(root, tag, head)
        findings += remote_findings
        return SourceStatus(findings, f"release {tag} @ {_short(head)}; {remote_summary}")
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
    if has_uncommitted_changes(root):
        findings.append(SourceFinding(f"{label} has uncommitted changes", None))
    return findings
