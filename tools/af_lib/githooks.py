"""`af hooks install`: install the framework git hooks into a repository."""

from __future__ import annotations

import os
from pathlib import Path

from .common import AfError, Framework, is_git_work_tree, read_text, run_git, write_text_atomically

HOOK_NAMES = ("commit-msg", "pre-push")
HOOKS_DIR_NAME = ".githooks"
HOOK_MODE = 0o755
PROTECTED_BRANCH_KEY = "agentframework.protectedBranch"
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
        raise AfError(f"not a git work tree: {repo}")
    top_level = Path(run_git(repo, "rev-parse", "--show-toplevel").stdout.strip())
    if args.protect_none and args.protect:
        raise AfError("--protect-none and --protect are mutually exclusive")
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
    print(f"af hooks install ({mode}) {top_level}")
    errors: list[str] = []
    planned: list[tuple[Path, str]] = []
    for name in HOOK_NAMES:
        source_text = read_text(framework.hooks_dir / name)
        destination = top_level / HOOKS_DIR_NAME / name
        if not destination.exists():
            action = "create"
        elif read_text(destination) == source_text:
            action = "unchanged"
        elif args.force:
            action = "replace"
        else:
            action = "BLOCKED-different-hook"
            errors.append(
                f"an existing hook in {HOOKS_DIR_NAME}/ differs from the framework hook; use --force to replace it"
            )
        print(f"  {action:22}  {HOOKS_DIR_NAME}/{name}")
        planned.append((destination, source_text if action in ("create", "replace") else ""))
    if configured == HOOKS_DIR_NAME:
        print(f"  {'unchanged':22}  git config core.hooksPath {HOOKS_DIR_NAME}")
    else:
        print(f"  {'set':22}  git config core.hooksPath {HOOKS_DIR_NAME}")

    protected_change = plan_protected_branches(top_level, requested_branches)

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
    if protected_change:
        write_protected_branches(top_level, requested_branches)
    print("Installed.")
    return 0


def validated_branches(top_level: Path, names: list[str]) -> list[str]:
    """Requested branch names in order, without duplicates; rejects invalid names."""
    unique: list[str] = []
    for name in names:
        if name.startswith("refs/") or name.startswith("-"):
            raise AfError(f"--protect takes a short branch name such as development, not {name!r}")
        check = run_git(top_level, "check-ref-format", f"refs/heads/{name}", check=False)
        if check.returncode != 0:
            raise AfError(f"--protect: not a valid branch name: {name!r}")
        if name not in unique:
            unique.append(name)
    return unique


def plan_protected_branches(top_level: Path, requested: list[str]) -> bool:
    """Print the protected-branch plan; return True when the git config must change."""
    if not requested:
        return False
    current = run_git(
        top_level, "config", "--local", "--get-all", PROTECTED_BRANCH_KEY, check=False
    ).stdout.splitlines()
    wanted = describe_protected(requested)
    if current == requested:
        print(f"  {'unchanged':22}  git config {PROTECTED_BRANCH_KEY}: {wanted}")
        return False
    if current:
        previous = describe_protected(current)
    else:
        previous = f"unset, so {', '.join(DEFAULT_PROTECTED_BRANCHES)}"
    print(f"  {'set':22}  git config {PROTECTED_BRANCH_KEY}: {wanted} (currently {previous})")
    return True


def describe_protected(branches: list[str]) -> str:
    if branches == [NO_PROTECTED_BRANCH]:
        return "none (direct pushes allowed)"
    return ", ".join(branches)


def write_protected_branches(top_level: Path, branches: list[str]) -> None:
    run_git(top_level, "config", "--local", "--replace-all", PROTECTED_BRANCH_KEY, branches[0])
    for branch in branches[1:]:
        run_git(top_level, "config", "--local", "--add", PROTECTED_BRANCH_KEY, branch)


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
