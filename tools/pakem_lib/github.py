"""What GitHub says about a project's repository, read with the owner's gh login.

A self-hosted runner serves private repositories only: a public repository's pull
requests come from anyone's fork, and the runner would run their code on the owner's
server (ADR 0010).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .common import CommandRefused, PakemError, run_git

GITHUB_REMOTE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.+?)(?:\.git)?/?$")
# A job on the Pakem runner: `runs-on: [self-hosted, ..., pakem]`, or a reusable template's
# `runner: pakem` input.
SELF_HOSTED_LINE = re.compile(r"^\s*(?:runs-on|runner)\s*:.*\b(?:self-hosted|pakem)\b")


def origin_repository(repo: Path) -> str | None:
    """owner/name when origin is on GitHub, else None."""
    remote = run_git(repo, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    match = GITHUB_REMOTE.match(remote)
    return f"{match[1]}/{match[2]}" if match else None


def repository_visibility(repository: str) -> str:
    """private or public, as GitHub reports it; never guessed."""
    completed = subprocess.run(["gh", "api", f"repos/{repository}", "--jq", ".private"], capture_output=True, text=True)
    answer = completed.stdout.strip()
    if completed.returncode != 0 or answer not in ("true", "false"):
        detail = completed.stderr.strip().splitlines()
        raise PakemError(f"gh could not read the visibility of {repository}: {detail[-1] if detail else 'no answer'}")
    return "private" if answer == "true" else "public"


def public_runner_refusal(repository: str) -> CommandRefused:
    return CommandRefused(
        f"{repository} is public: a self-hosted runner would run any fork's pull request on your server. "
        "Its CI must use GitHub-hosted runners"
    )


def refuse_public_runner(repository: str) -> None:
    if repository_visibility(repository) == "public":
        raise public_runner_refusal(repository)


def self_hosted_workflows(repo: Path) -> list[str]:
    """Workflows with a job on a self-hosted runner, as repository paths."""
    directory = repo / ".github" / "workflows"
    if not directory.is_dir():
        return []
    found = []
    for path in sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if any(SELF_HOSTED_LINE.match(line) for line in lines):
            found.append(path.relative_to(repo).as_posix())
    return found
