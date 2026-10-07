"""`af hooks install`: install the framework git hooks into a repository."""

from __future__ import annotations

import os
from pathlib import Path

from .common import AfError, Framework, is_git_work_tree, read_text, run_git, write_text_atomically

HOOK_NAMES = ("commit-msg", "pre-push")
HOOKS_DIR_NAME = ".githooks"
HOOK_MODE = 0o755


def run_hooks_install(args) -> int:
    framework = Framework(Path(args.root))
    repo = Path(args.repo or ".").resolve()
    if not is_git_work_tree(repo):
        raise AfError(f"not a git work tree: {repo}")
    top_level = Path(run_git(repo, "rev-parse", "--show-toplevel").stdout.strip())

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
    blocked = False
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
            blocked = True
        print(f"  {action:22}  {HOOKS_DIR_NAME}/{name}")
        planned.append((destination, source_text if action in ("create", "replace") else ""))
    if configured == HOOKS_DIR_NAME:
        print(f"  {'unchanged':22}  git config core.hooksPath {HOOKS_DIR_NAME}")
    else:
        print(f"  {'set':22}  git config core.hooksPath {HOOKS_DIR_NAME}")

    if blocked:
        print(
            f"ERROR: an existing hook in {HOOKS_DIR_NAME}/ differs from the framework hook; use --force to replace it"
        )
        return 1
    if not args.apply:
        print("Dry run: nothing written. Re-run with --apply to install.")
        return 0

    for destination, text in planned:
        if text:
            write_text_atomically(destination, text, mode=HOOK_MODE)
        elif destination.exists():
            os.chmod(destination, HOOK_MODE)
    if configured != HOOKS_DIR_NAME:
        run_git(top_level, "config", "core.hooksPath", HOOKS_DIR_NAME)
    print("Installed.")
    return 0
