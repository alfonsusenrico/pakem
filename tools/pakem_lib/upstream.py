"""Where the installed framework stands against merged `main`.

A framework checkout follows one of two lines, decided by how it is checked out:

- on branch `main` (a clone someone develops in): it follows `origin/main`;
- detached at a release tag `vX.Y.Z` (what install.sh makes): it follows the newest
  release tag that is part of `origin/main`.

Any other checkout (another branch, a detached commit, no git) follows nothing, so
`pakem update` refuses it, matching `pakem install`'s provenance rules (source.py).

Comparisons use local refs only. `refresh` fetches from `origin` at most every
REFRESH_SECONDS (or when forced) and records when it last did, so session hooks stay
fast and work offline.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .common import (
    PakemError,
    is_git_work_tree,
    run_git,
    utc_now_iso,
    version_tuple,
    write_text_atomically,
)
from .manifest import state_dir
from .source import (
    INSTALL_BRANCH,
    RELEASE_TAG,
    REMOTE,
    REMOTE_BRANCH_REF,
    current_branch,
    head_release_tag,
    resolve_commit,
)

FOLLOW_MAIN = "main"
FOLLOW_RELEASES = "releases"
# The owner asked for a network check at most every few hours (ADR 0011, decision 7).
REFRESH_SECONDS = 4 * 3600
FETCH_TIMEOUT_SECONDS = 20
CACHE_NAME = "upstream.json"

UP_TO_DATE = "up-to-date"
UPDATE_AVAILABLE = "update-available"
NOT_INSTALLED = "not-installed"
UNTRACKED_SOURCE = "untracked-source"
UNKNOWN = "unknown"

# Paths whose changes alter what agents load or how pakem behaves; reported per update.
WATCHED_PATHS = ("kernel", "skills", "enforcement", "hooks", "tools")


@dataclass(frozen=True)
class Target:
    follows: str
    ref: str
    commit: str
    version: str


@dataclass
class FetchState:
    checked_at: str | None
    error: str | None
    fetched_now: bool = False


@dataclass
class FrameworkStatus:
    state: str
    root: Path
    follows: str | None
    installed_version: str | None
    installed_commit: str | None
    target: Target | None
    fetch: FetchState
    checkout_commit: str | None = None
    changed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def follow_mode(root: Path) -> str | None:
    if not is_git_work_tree(root):
        return None
    branch = current_branch(root)
    if branch == INSTALL_BRANCH:
        return FOLLOW_MAIN
    if branch is None and head_release_tag(root) is not None:
        return FOLLOW_RELEASES
    return None


def describe_checkout(root: Path) -> str:
    if not is_git_work_tree(root):
        return "not a git work tree"
    branch = current_branch(root)
    if branch is not None:
        return f"on branch {branch}"
    tag = head_release_tag(root)
    if tag is not None:
        return f"at release {tag}"
    return "on a detached commit"


def newest_release(root: Path) -> str | None:
    """The newest `vX.Y.Z` tag that is part of origin/main, as of the last fetch."""
    if resolve_commit(root, REMOTE_BRANCH_REF) is None:
        return None
    tags = run_git(root, "tag", "--merged", REMOTE_BRANCH_REF, "--list", "v*").stdout.split()
    releases = [tag for tag in tags if RELEASE_TAG.fullmatch(tag)]
    if not releases:
        return None
    return max(releases, key=lambda tag: version_tuple(tag[1:]))


def target_for(root: Path, follows: str) -> Target | None:
    if follows == FOLLOW_RELEASES:
        tag = newest_release(root)
        if tag is None:
            return None
        return Target(follows, tag, resolve_commit(root, tag), tag[1:])
    commit = resolve_commit(root, REMOTE_BRANCH_REF)
    if commit is None:
        return None
    shown = run_git(root, "show", f"{REMOTE_BRANCH_REF}:VERSION", check=False)
    version = shown.stdout.strip() if shown.returncode == 0 else "unknown"
    return Target(follows, f"{REMOTE}/{INSTALL_BRANCH}", commit, version)


def _cache_path() -> Path:
    return state_dir() / CACHE_NAME


def _load_cache() -> dict:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Derived data only: concurrent session hooks can leave a torn write, and the
        # next fetch rewrites it, so a damaged cache counts as never checked.
        return {}
    return data if isinstance(data, dict) else {}


def seconds_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds()


def _fetch(root: Path) -> str | None:
    """Fetch origin without prompting; returns an error summary, or None on success."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    # An SSH remote must fail fast instead of asking for a passphrase inside a hook.
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=10")
    # --prune drops tracking refs of branches already deleted on origin (a forge deletes a
    # merged branch itself), so branch pruning never plans one that is gone. Tags fetched
    # with --tags are never pruned by it.
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "fetch", "--quiet", "--prune", "--tags", REMOTE],
            capture_output=True,
            text=True,
            env=env,
            timeout=FETCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"git fetch timed out after {FETCH_TIMEOUT_SECONDS}s"
    if completed.returncode != 0:
        lines = completed.stderr.strip().splitlines()
        return lines[-1] if lines else f"git fetch exited {completed.returncode}"
    return None


def refresh(root: Path, force: bool = False, allow_network: bool = True) -> FetchState:
    """Fetch origin when the last check is older than REFRESH_SECONDS, or when forced."""
    cache = _load_cache()
    record = cache.get(str(root), {}) if isinstance(cache.get(str(root)), dict) else {}
    state = FetchState(record.get("checked_at"), record.get("error"))
    if not allow_network or not is_git_work_tree(root):
        return state
    if REMOTE not in run_git(root, "remote").stdout.split():
        return FetchState(None, f"no {REMOTE} remote")
    age = seconds_since(state.checked_at)
    if not force and age is not None and age < REFRESH_SECONDS:
        return state
    error = _fetch(root)
    state = FetchState(utc_now_iso(), error, fetched_now=True)
    cache[str(root)] = {"checked_at": state.checked_at, "error": error}
    write_text_atomically(_cache_path(), json.dumps(cache, indent=2) + "\n")
    return state


def changed_paths(root: Path, old: str, new: str) -> list[str]:
    """Watched files that differ between two commits; empty when either is unknown locally."""
    completed = run_git(root, "diff", "--name-only", old, new, "--", *WATCHED_PATHS, check=False)
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line]


def summarize_changes(paths: list[str]) -> list[str]:
    """Group changed paths into what an agent must re-read: kernel, each skill, tool, guardrails."""
    groups: list[str] = []
    skills = sorted({path.split("/")[1] for path in paths if path.startswith("skills/") and path.count("/") >= 2})
    if any(path.startswith("kernel/") for path in paths):
        groups.append("kernel")
    groups += [f"skill {name}" for name in skills]
    if any(path.startswith(("enforcement/", "hooks/")) for path in paths):
        groups.append("guardrails and hooks")
    if any(path.startswith("tools/") for path in paths):
        groups.append("pakem tool")
    return groups


def framework_status(root: Path, manifest: dict, fetch: FetchState) -> FrameworkStatus:
    installed_version = manifest.get("framework_version")
    installed_commit = manifest.get("framework_commit")
    follows = follow_mode(root)
    status = FrameworkStatus(UNKNOWN, root, follows, installed_version, installed_commit, None, fetch)
    if installed_version is None:
        status.state = NOT_INSTALLED
        return status
    if follows is None:
        status.state = UNTRACKED_SOURCE
        status.notes.append(
            f"the framework checkout is {describe_checkout(root)}; updates come only from merged {INSTALL_BRANCH}"
        )
        _note_unmerged(root, installed_commit, status)
        return status
    status.checkout_commit = resolve_commit(root, "HEAD")
    status.target = target_for(root, follows)
    if status.target is None:
        status.notes.append(
            f"no {'release on ' if follows == FOLLOW_RELEASES else ''}{REMOTE}/{INSTALL_BRANCH} known yet. "
            "The update check needs one successful fetch"
        )
        return status
    if manifest.get("framework_clean") is False:
        status.notes.append("the install was made from uncommitted changes")
    if installed_commit is None:
        status.notes.append("this install predates commit tracking, and `pakem update` records it")
    _note_unmerged(root, installed_commit, status)
    status.state = _compare(status, manifest)
    if status.state == UPDATE_AVAILABLE and installed_commit is not None:
        status.changed = summarize_changes(changed_paths(root, installed_commit, status.target.commit))
    if status.checkout_commit not in (None, installed_commit) and status.checkout_commit == status.target.commit:
        status.notes.append("the checkout already has the update, and `pakem update` installs it")
    return status


def _note_unmerged(root: Path, installed_commit: str | None, status: FrameworkStatus) -> None:
    """An install made from a branch that never reached main runs rules nobody merged."""
    if installed_commit is None or resolve_commit(root, REMOTE_BRANCH_REF) is None:
        return
    merged = run_git(root, "merge-base", "--is-ancestor", installed_commit, REMOTE_BRANCH_REF, check=False)
    if merged.returncode == 1:
        status.notes.append(
            f"the installed commit {installed_commit[:7]} is not on {REMOTE}/{INSTALL_BRANCH} (an unmerged branch). "
            f"`pakem update` from {INSTALL_BRANCH} replaces it"
        )


def _compare(status: FrameworkStatus, manifest: dict) -> str:
    target = status.target
    if status.installed_commit == target.commit:
        if manifest.get("framework_clean") is False:
            return UPDATE_AVAILABLE
        return UP_TO_DATE
    if status.follows == FOLLOW_MAIN and status.installed_commit is not None:
        return UPDATE_AVAILABLE
    # Releases are compared by version, and so is an install that predates commit tracking.
    try:
        newer = version_tuple(target.version) > version_tuple(status.installed_version)
    except PakemError:
        return UNKNOWN
    if newer:
        return UPDATE_AVAILABLE
    return UP_TO_DATE
