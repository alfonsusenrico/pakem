"""Merged-branch pruning (`default:prune-merged`).

A branch whose tip is already part of the default branch on origin carries no work of its
own, so it is deleted locally and on origin. A repository opts out with a
`default:prune-merged` bullet in its `AGENTS.md` `## Overrides`, or keeps single branches
with a `Keep branches:` line (names or glob patterns, comma-separated). The default branch,
the current branch, and branches checked out in a worktree are never touched. A
squash-merged branch is kept: its commits never appear in the default branch, so ancestry
cannot prove it carries nothing.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from .common import read_text, run_git

REMOTE = "origin"
KEEP_LINE = re.compile(r"^(?:[-*+]\s+)?Keep branches:(.*)$")
OPT_OUT_BULLET = re.compile(r"^[-*+]\s+`default:prune-merged`")


@dataclass
class PrunePlan:
    local: list[str] = field(default_factory=list)
    remote: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.local or self.remote)

    def describe(self) -> str:
        parts = []
        if self.local:
            parts.append(f"{len(self.local)} local ({', '.join(self.local)})")
        if self.remote:
            parts.append(f"{len(self.remote)} on {REMOTE} ({', '.join(self.remote)})")
        return "prune merged branches: " + ", ".join(parts)


def repository_rules(repo: Path) -> tuple[bool, list[str]]:
    """Whether AGENTS.md opts out of pruning, and the branch patterns it keeps."""
    path = repo / "AGENTS.md"
    if not path.is_file():
        return False, []
    opted_out = False
    keep: list[str] = []
    in_overrides = False
    for line in read_text(path).splitlines():
        if line.startswith("## "):
            in_overrides = line.strip() == "## Overrides"
            continue
        if in_overrides and OPT_OUT_BULLET.match(line):
            opted_out = True
        match = KEEP_LINE.match(line)
        if match:
            keep += [name.strip().strip("`") for name in match.group(1).split(",") if name.strip().strip("`")]
    return opted_out, keep


def default_branch(repo: Path) -> str:
    completed = run_git(repo, "symbolic-ref", "--short", f"refs/remotes/{REMOTE}/HEAD", check=False)
    name = completed.stdout.strip()
    return name.removeprefix(f"{REMOTE}/") if completed.returncode == 0 and name else "main"


def _refs(repo: Path, prefix: str) -> list[str]:
    return run_git(repo, "for-each-ref", "--format=%(refname)", prefix).stdout.split()


def _checked_out(repo: Path) -> set[str]:
    listing = run_git(repo, "worktree", "list", "--porcelain").stdout.splitlines()
    return {line.removeprefix("branch refs/heads/") for line in listing if line.startswith("branch refs/heads/")}


def _merged(repo: Path, ref: str, target: str) -> bool:
    return run_git(repo, "merge-base", "--is-ancestor", ref, target, check=False).returncode == 0


def plan_prune(repo: Path) -> PrunePlan:
    """Branches to delete, from refs as last fetched."""
    plan = PrunePlan()
    opted_out, keep = repository_rules(repo)
    if opted_out:
        return plan
    default = default_branch(repo)
    target = f"refs/remotes/{REMOTE}/{default}"
    if run_git(repo, "rev-parse", "--verify", "--quiet", target, check=False).returncode != 0:
        return plan

    def kept(name: str) -> bool:
        return name == default or any(fnmatch.fnmatchcase(name, pattern) for pattern in keep)

    busy = _checked_out(repo)
    for ref in _refs(repo, "refs/heads"):
        name = ref.removeprefix("refs/heads/")
        if name not in busy and not kept(name) and _merged(repo, ref, target):
            plan.local.append(name)
    for ref in _refs(repo, f"refs/remotes/{REMOTE}"):
        name = ref.removeprefix(f"refs/remotes/{REMOTE}/")
        if name != "HEAD" and not kept(name) and _merged(repo, ref, target):
            plan.remote.append(name)
    return plan


def apply_prune(repo: Path, plan: PrunePlan) -> tuple[PrunePlan, list[str]]:
    """Delete the planned branches; returns what is gone and the warnings. A remote refusal
    (no push rights, a protected branch) is a warning: pruning tidies up and never fails the
    command that runs it."""
    done = PrunePlan(local=list(plan.local))
    warnings = []
    if plan.local:
        # -D, not -d: -d compares with HEAD, which on a release checkout lags the default
        # branch. plan_prune already proved each tip is part of origin's default branch.
        run_git(repo, "branch", "-D", *plan.local)
    if plan.remote:
        completed = run_git(repo, "push", "--quiet", REMOTE, "--delete", *plan.remote, check=False)
        run_git(repo, "fetch", "--quiet", "--prune", REMOTE, check=False)
        # Report what origin no longer has, not what the push meant to do.
        remaining = [name for name in plan.remote
                     if run_git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{REMOTE}/{name}",
                                check=False).returncode == 0]
        done.remote = [name for name in plan.remote if name not in remaining]
        if remaining:
            detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:] or ["no output"]
            warnings.append(f"could not delete {', '.join(remaining)} on {REMOTE}: {detail[0]}")
    return done, warnings
