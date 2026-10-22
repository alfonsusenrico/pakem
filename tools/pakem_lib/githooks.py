"""`pakem hooks install`: install the framework git hooks into a repository."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .common import PakemError, Framework, is_git_work_tree, read_text, run_git, write_text_atomically

HOOK_NAMES = ("commit-msg", "pre-push")
HOOKS_DIR_NAME = ".githooks"
HOOK_MODE = 0o755
# SHA-256 of every earlier released version of each hook. `hooks install` replaces these
# without --force, because they are Pakem's own and were never edited in the repository.
# Add the outgoing version here whenever a hook changes (tests/test_githooks.py checks it).
PREVIOUS_HOOK_SHA256 = {
    "commit-msg": frozenset({
        "7f6c0591b4a05cfd1d6cbbdb0c3a9fd2fd8b29ae488c586fa3c6888e7dd918c8",
    }),
    "pre-push": frozenset({
        "85dc3f0b251ca35538d99c0c558c245b8c6f8fe5cab19efeb7e660c9f1fe9ee8",
        "cb4e5be45985114e36528fcecf6a9c6516a080986205c732fc7207581b333dc3",
        "fe19b8b42273b109961f256870a9188ccdad85768ead47173be3713a3132c97e",
        "df05f48c534cdeceffadb010400acc636f513d28409a3917e971bbdb835a59aa",
    }),
}
PROTECTED_BRANCH_KEY = "pakem.protectedBranch"
# Repositories configured before the product was renamed; install copies it to the key above.
LEGACY_PROTECTED_BRANCH_KEY = "agentframework.protectedBranch"
# Git rejects "-" as a branch name, so this value matches no branch: nothing is protected.
NO_PROTECTED_BRANCH = "-"
DEFAULT_PROTECTED_BRANCHES = ("main", "master")
EXCLUDE_ENTRY = f"{HOOKS_DIR_NAME}/"
# Any of these lines in info/exclude already keeps the hooks directory out of commits.
EQUIVALENT_EXCLUDE_ENTRIES = {HOOKS_DIR_NAME, f"{HOOKS_DIR_NAME}/", f"/{HOOKS_DIR_NAME}", f"/{HOOKS_DIR_NAME}/"}


def run_hooks_install(args) -> int:
    framework = Framework(Path(args.root))
    repo = Path(args.repo or ".").resolve()
    if not is_git_work_tree(repo):
        raise PakemError(f"not a git work tree: {repo}")
    top_level = Path(run_git(repo, "rev-parse", "--show-toplevel").stdout.strip())
    if args.protect_none and args.protect:
        raise PakemError("--protect-none and --protect are mutually exclusive")
    if args.protect_none:
        requested_branches = [NO_PROTECTED_BRANCH]
    else:
        requested_branches = validated_branches(top_level, args.protect or [])

    configured = run_git(top_level, "config", "--get", "core.hooksPath", check=False).stdout.strip()
    if configured and configured != HOOKS_DIR_NAME:
        origin = run_git(
            top_level, "config", "--show-origin", "--get", "core.hooksPath", check=False
        ).stdout.strip()
        print(f"core.hooksPath is already set: {origin or configured}")
        print(
            "Refusing to change it: that would silently disable the hooks it points to. "
            f"Move those hooks into {HOOKS_DIR_NAME}/ or unset core.hooksPath, then re-run."
        )
        return 1

    mode = "apply" if args.apply else "dry-run"
    print(f"pakem hooks install ({mode}) {top_level}")
    errors: list[str] = []
    planned: list[tuple[Path, str]] = []
    for name in HOOK_NAMES:
        source_text = read_text(framework.hooks_dir / name)
        destination = top_level / HOOKS_DIR_NAME / name
        if not destination.exists():
            action = "create"
        elif read_text(destination) == source_text:
            action = "unchanged"
        elif _sha256(destination) in PREVIOUS_HOOK_SHA256.get(name, frozenset()):
            action = "update"
        elif args.force:
            action = "replace"
        else:
            action = "BLOCKED-different-hook"
            errors.append(
                f"an existing hook in {HOOKS_DIR_NAME}/ differs from the framework hook; use --force to replace it"
            )
        print(f"  {action:22}  {HOOKS_DIR_NAME}/{name}")
        planned.append((destination, source_text if action in ("create", "update", "replace") else ""))
    if configured == HOOKS_DIR_NAME:
        print(f"  {'unchanged':22}  git config core.hooksPath {HOOKS_DIR_NAME}")
    else:
        print(f"  {'set':22}  git config core.hooksPath {HOOKS_DIR_NAME}")

    protected_write, legacy_write = plan_protected_branches(top_level, requested_branches)

    exclude_file: Path | None = None
    exclude_text = ""
    if args.local_only:
        tracked = run_git(top_level, "ls-files", "--", HOOKS_DIR_NAME).stdout.strip()
        if tracked:
            print(f"  {'BLOCKED-tracked-hooks':22}  {HOOKS_DIR_NAME}/ is already committed")
            errors.append(
                f"{HOOKS_DIR_NAME}/ is tracked in this repository, so --local-only cannot keep it "
                "out of commits; remove it from the index or install without --local-only"
            )
        else:
            exclude_file, exclude_text = plan_exclude(top_level)

    if errors:
        for message in dict.fromkeys(errors):
            print(f"ERROR: {message}")
        return 1
    if not args.apply:
        print("Dry run: nothing written. Re-run with --apply to install.")
        return 0

    if exclude_file is not None and exclude_text:
        write_text_atomically(exclude_file, exclude_text)
    for destination, text in planned:
        if text:
            write_text_atomically(destination, text, mode=HOOK_MODE)
        elif destination.exists():
            os.chmod(destination, HOOK_MODE)
    if configured != HOOKS_DIR_NAME:
        run_git(top_level, "config", "core.hooksPath", HOOKS_DIR_NAME)
    if protected_write:
        write_protected_branches(top_level, protected_write)
    if legacy_write:
        write_protected_branches(top_level, legacy_write, LEGACY_PROTECTED_BRANCH_KEY)
    print("Installed.")
    return 0


def validated_branches(top_level: Path, names: list[str]) -> list[str]:
    """Requested branch names in order, without duplicates; rejects invalid names."""
    unique: list[str] = []
    for name in names:
        if name.startswith("refs/") or name.startswith("-"):
            raise PakemError(f"--protect takes a short branch name such as development, not {name!r}")
        check = run_git(top_level, "check-ref-format", f"refs/heads/{name}", check=False)
        if check.returncode != 0:
            raise PakemError(f"--protect: not a valid branch name: {name!r}")
        if name not in unique:
            unique.append(name)
    return unique


def _configured_branches(top_level: Path, key: str) -> list[str]:
    return run_git(top_level, "config", "--local", "--get-all", key, check=False).stdout.splitlines()


def plan_protected_branches(top_level: Path, requested: list[str]) -> tuple[list[str], list[str]]:
    """Print the protected-branch plan; return the branches to write under (current key, former key).

    A setting under the former key is copied to the current one. The former key is kept,
    and kept equal, because hooks copied before the rename (other worktrees, older
    branches) read only that key; an empty list means no change.
    """
    current = _configured_branches(top_level, PROTECTED_BRANCH_KEY)
    legacy = _configured_branches(top_level, LEGACY_PROTECTED_BRANCH_KEY)
    write: list[str] = []
    write_legacy: list[str] = []
    if requested:
        wanted = describe_protected(requested)
        if current == requested:
            print(f"  {'unchanged':22}  git config {PROTECTED_BRANCH_KEY}: {wanted}")
        else:
            if current:
                previous = describe_protected(current)
            elif legacy:
                previous = f"{describe_protected(legacy)} under {LEGACY_PROTECTED_BRANCH_KEY}"
            else:
                previous = f"unset, so {', '.join(DEFAULT_PROTECTED_BRANCHES)}"
            print(f"  {'set':22}  git config {PROTECTED_BRANCH_KEY}: {wanted} (currently {previous})")
            write = requested
        if legacy and legacy != requested:
            print(f"  {'set':22}  git config {LEGACY_PROTECTED_BRANCH_KEY}: {wanted} (former name, for older hooks)")
            write_legacy = requested
    elif legacy and not current:
        print(
            f"  {'copy':22}  git config {LEGACY_PROTECTED_BRANCH_KEY} -> {PROTECTED_BRANCH_KEY}: "
            f"{describe_protected(legacy)}"
        )
        write = legacy
    return write, write_legacy


def describe_protected(branches: list[str]) -> str:
    if branches == [NO_PROTECTED_BRANCH]:
        return "none (direct pushes allowed)"
    return ", ".join(branches)


def write_protected_branches(top_level: Path, branches: list[str], key: str = PROTECTED_BRANCH_KEY) -> None:
    run_git(top_level, "config", "--local", "--replace-all", key, branches[0])
    for branch in branches[1:]:
        run_git(top_level, "config", "--local", "--add", key, branch)


def plan_exclude(top_level: Path) -> tuple[Path, str]:
    """Print the info/exclude plan; return the file and its new text ('' when unchanged)."""
    # --git-path resolves into the common git directory, so a linked worktree shares
    # the main checkout's info/exclude.
    reported = run_git(top_level, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    exclude_file = Path(reported)
    if not exclude_file.is_absolute():
        exclude_file = top_level / exclude_file
    existing = ""
    if exclude_file.exists():
        existing = read_text(exclude_file)
    if any(line.strip() in EQUIVALENT_EXCLUDE_ENTRIES for line in existing.splitlines()):
        print(f"  {'unchanged':22}  {reported} excludes {EXCLUDE_ENTRY}")
        return exclude_file, ""
    print(f"  {'add':22}  {EXCLUDE_ENTRY} to {reported}")
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return exclude_file, existing + EXCLUDE_ENTRY + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
