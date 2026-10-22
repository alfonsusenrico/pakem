"""`pakem update`: install the framework from merged `main`, then list what changed.

Steps, each skipped when already done, so a re-run continues where one stopped:

1. fetch origin (always: an update decides on fresh refs);
2. move the framework checkout to its target (upstream.py): fast-forward a `main`
   checkout to origin/main, or check out the newest release on main;
3. run `pakem install --apply` with the options the last install recorded;
4. refresh the guardrails of every host `pakem enforce` was installed for;
5. list the installed kernel and skill files that changed, for agents to re-read.

Steps 3 and 4 run the checkout's own tool in a subprocess, so an update that changes the
installer installs with the new installer. The update is refused (exit 6, nothing
changed) when the checkout follows nothing, has uncommitted changes, or holds commits
origin/main lacks.
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from .common import (
    CommandBusy,
    CommandRefused,
    Framework,
    OwnerActionRequired,
    PakemError,
    confirm_steps,
    display_path,
    run_git,
    version_tuple,
)
from .manifest import load_manifest, state_dir
from .source import INSTALL_BRANCH, REMOTE, has_uncommitted_changes, head_release_tag, resolve_commit
from .upstream import (
    FOLLOW_RELEASES,
    Target,
    changed_paths,
    describe_checkout,
    follow_mode,
    refresh,
    summarize_changes,
    target_for,
)

LOCK_NAME = "update.lock"
PROFILE_SOURCE_PREFIX = "profile:"


@contextmanager
def update_lock():
    path = state_dir() / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CommandBusy(f"another pakem update is running (lock {display_path(path)})") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def install_arguments(manifest: dict) -> list[str]:
    """Repeat the last install's options; older manifests lack them unless no profile was used."""
    options = manifest.get("install_options")
    if isinstance(options, dict):
        arguments = []
        if options.get("hosts"):
            arguments += ["--hosts", options["hosts"]]
        for profile in options.get("profiles") or []:
            arguments += ["--profile", profile]
        return arguments
    profiles = sorted(
        {
            entry["source"][len(PROFILE_SOURCE_PREFIX) :].split("/", 1)[0]
            for entry in manifest.get("entries", [])
            if str(entry.get("source", "")).startswith(PROFILE_SOURCE_PREFIX)
        }
    )
    if profiles:
        raise OwnerActionRequired(
            "OWNER ACTION REQUIRED: this install predates recorded install options and used profiles "
            f"{', '.join(profiles)}; run `pakem install --apply --profile <dir>` once with each profile "
            "directory, and later updates repeat it"
        )
    hosts = sorted({host for entry in manifest.get("entries", []) for host in str(entry.get("host", "")).split(",") if host})
    if hosts:
        return ["--hosts", ",".join(hosts)]
    return []


def _move_step(root: Path, follows: str, target_commit: str, target_ref: str) -> str | None:
    """The checkout move this update needs, or None; refuses moves that are not fast-forwards."""
    head = resolve_commit(root, "HEAD")
    if follows == FOLLOW_RELEASES:
        current = head_release_tag(root)
        if head == target_commit or version_tuple(target_ref[1:]) <= version_tuple(current[1:]):
            return None
        return f"check out {target_ref} in {display_path(root)}"
    if head == target_commit:
        return None
    if run_git(root, "merge-base", "--is-ancestor", "HEAD", target_commit, check=False).returncode != 0:
        raise CommandRefused(
            f"the framework checkout {display_path(root)} has commits {REMOTE}/{INSTALL_BRANCH} lacks; "
            "merge them through a pull request first"
        )
    return f"fast-forward {display_path(root)} {INSTALL_BRANCH} to {target_commit[:12]}"


def _run_tool(framework: Framework, arguments: list[str], capture: bool) -> None:
    command = [sys.executable, str(framework.root / "tools" / "pakem"), "--root", str(framework.root), *arguments]
    completed = subprocess.run(command, capture_output=capture, text=True)
    if completed.returncode != 0:
        detail = ""
        if capture:
            lines = (completed.stdout + completed.stderr).strip().splitlines()
            detail = ": " + " | ".join(lines[-5:])
        raise PakemError(f"`pakem {' '.join(arguments)}` failed with exit {completed.returncode}{detail}")




def _reread(before: dict, after: dict) -> list[str]:
    old = {entry["path"]: entry for entry in before.get("entries", [])}
    files = []
    for entry in after.get("entries", []):
        previous = old.get(entry["path"])
        if previous is not None and previous.get("sha256") == entry.get("sha256"):
            continue
        files.append(f"{entry['path']}/SKILL.md" if entry.get("kind") == "skill" else entry["path"])
    return sorted(files)


def plan_update(framework: Framework, manifest: dict) -> tuple[list[tuple[str, list[str] | None]], Target]:
    """The steps as (description, pakem arguments, or None for the checkout move) and the target."""
    root = framework.root
    follows = follow_mode(root)
    if follows is None:
        raise CommandRefused(
            f"the framework checkout {display_path(root)} is {describe_checkout(root)}; "
            f"`pakem update` installs only from merged {INSTALL_BRANCH} (a {INSTALL_BRANCH} checkout or a release)"
        )
    if has_uncommitted_changes(root):
        raise CommandRefused(f"the framework checkout {display_path(root)} has uncommitted changes")
    fetch = refresh(root, force=True)
    if fetch.error:
        raise PakemError(f"could not fetch {REMOTE}: {fetch.error}")
    target = target_for(root, follows)
    if target is None:
        raise PakemError(f"no release on {REMOTE}/{INSTALL_BRANCH} found")
    steps: list[tuple[str, list[str] | None]] = []
    move = _move_step(root, follows, target.commit, target.ref)
    if move is not None:
        steps.append((move, None))
    current = manifest.get("framework_commit")
    if move is not None or current != target.commit or manifest.get("framework_clean") is False:
        arguments = ["install", "--apply", *install_arguments(manifest)]
        steps.append((f"pakem {' '.join(arguments)}", arguments))
        for host in sorted(key for key in manifest.get("enforcement", {}) if key != "guard"):
            arguments = ["enforce", "install", "--host", host, "--apply"]
            steps.append((f"pakem {' '.join(arguments)}", arguments))
    return steps, target


def _move_checkout(root: Path, target: Target) -> None:
    if target.follows == FOLLOW_RELEASES:
        run_git(root, "-c", "advice.detachedHead=false", "checkout", "--quiet", target.ref)
    else:
        run_git(root, "merge", "--ff-only", "--quiet", target.commit)


def run_update(args) -> int:
    framework = Framework(Path(args.root))
    with update_lock():
        before = load_manifest()
        steps, target = plan_update(framework, before)
        result = {"status": "up-to-date", "done": [], "owner_actions": [], "gaps": [], "checks": [], "reread": []}
        if not steps:
            return _finish(args, result, [f"Pakem is up to date with {target.ref} ({target.commit[:12]}); nothing to do."])
        if not args.json:
            print(f"pakem update: {before.get('framework_version')} -> {target.version} ({target.ref})")
            for description, _ in steps:
                print(f"  {description}")
        if args.plan:
            result["status"] = "planned"
            result["done"] = [description for description, _ in steps]
            return _finish(args, result, ["Plan only: nothing changed."])
        confirm_steps(args.yes, "pakem update")
        for description, arguments in steps:
            if arguments is None:
                _move_checkout(framework.root, target)
            else:
                _run_tool(framework, arguments, capture=args.json)
            result["done"].append(description)
        after = load_manifest()
        result["status"] = "updated"
        result["reread"] = _reread(before, after)
        lines = [f"Updated to {after.get('framework_version')} ({target.commit[:12]})."]
        if before.get("framework_commit"):
            groups = summarize_changes(changed_paths(framework.root, before["framework_commit"], target.commit))
            if groups:
                lines.append(f"Changed: {', '.join(groups)}")
        if result["reread"]:
            lines.append(f"Re-read: {', '.join(result['reread'])}")
        return _finish(args, result, lines)


def _finish(args, result: dict, lines: list[str]) -> int:
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for line in lines:
            print(line)
    return 0
