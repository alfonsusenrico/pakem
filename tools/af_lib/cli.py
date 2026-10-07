"""Command-line interface for `af`."""

from __future__ import annotations

import argparse
from pathlib import Path

from .common import AfError, eprint
from .doctor import run_doctor
from .enforce import run_enforce
from .githooks import run_hooks_install
from .installer import run_install, run_uninstall
from .lint import run_lint
from .project import run_check, run_sync

DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent
ENFORCE_HOSTS = ("claude", "codex", "antigravity")


def _add_apply(parser: argparse.ArgumentParser, verb: str = "write") -> None:
    parser.add_argument("--apply", action="store_true", help=f"{verb} changes (default is a dry run)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="af", description="Agent engineering framework tooling.")
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="framework repository root (default: the checkout containing this tool)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install", help="install the kernel and skills into host locations")
    install.add_argument("--hosts", help="comma-separated host ids from hosts/targets.toml (default: all)")
    install.add_argument("--profile", action="append", metavar="DIR", help="profile directory (repeatable)")
    install.add_argument(
        "--force", action="store_true", help="back up and replace locally edited managed files"
    )
    install.add_argument(
        "--allow-dirty", action="store_true", help="install from an uncommitted framework tree"
    )
    _add_apply(install)

    uninstall = commands.add_parser("uninstall", help="remove af-managed files and restore backups")
    uninstall.add_argument(
        "--force", action="store_true", help="back up and remove locally edited managed files"
    )
    _add_apply(uninstall, "remove")

    doctor = commands.add_parser("doctor", help="compare installed state with the framework")
    doctor.add_argument("--repos", action="append", metavar="DIR", help="check git repos in DIR (repeatable)")
    doctor.add_argument(
        "--profile",
        action="append",
        metavar="DIR",
        help="check that this profile's skills are installed (repeatable)",
    )

    lint = commands.add_parser("lint", help="framework self-checks")
    lint.add_argument(
        "--profile", action="append", metavar="DIR", help="also lint this profile directory (repeatable)"
    )

    project = commands.add_parser("project", help="manage a project's AGENTS.md")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    sync = project_commands.add_parser("sync", help="insert or refresh pinned framework blocks")
    sync.add_argument("repo", nargs="?", help="repository directory (default: current directory)")
    sync.add_argument("--profile", action="append", metavar="DIR", help="profile directory (repeatable)")
    sync.add_argument("--replace-legacy", action="store_true", help="replace a legacy owner-baseline block")
    _add_apply(sync)
    check = project_commands.add_parser(
        "check", help="verify pinned blocks, size, overrides, capabilities, and project files"
    )
    check.add_argument("repo", nargs="?", help="repository directory (default: current directory)")

    hooks = commands.add_parser("hooks", help="manage repository git hooks")
    hooks_commands = hooks.add_subparsers(dest="hooks_command", required=True)
    hooks_install = hooks_commands.add_parser("install", help="install commit-msg and pre-push hooks")
    hooks_install.add_argument("repo", nargs="?", help="repository directory (default: current directory)")
    hooks_install.add_argument("--force", action="store_true", help="replace differing hooks in .githooks/")
    _add_apply(hooks_install)

    enforce = commands.add_parser("enforce", help="install host-level guardrails")
    enforce_commands = enforce.add_subparsers(dest="enforce_command", required=True)
    for name in ("install", "uninstall"):
        enforce_parser = enforce_commands.add_parser(name, help=f"{name} guardrails for one host")
        enforce_parser.add_argument("--host", required=True, choices=ENFORCE_HOSTS)
        _add_apply(enforce_parser)
    return parser


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "install":
        return run_install(args)
    if args.command == "uninstall":
        return run_uninstall(args)
    if args.command == "doctor":
        return run_doctor(args)
    if args.command == "lint":
        return run_lint(args)
    if args.command == "project" and args.project_command == "sync":
        return run_sync(args)
    if args.command == "project" and args.project_command == "check":
        return run_check(args)
    if args.command == "hooks":
        return run_hooks_install(args)
    if args.command == "enforce":
        return run_enforce(args)
    raise AfError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return dispatch(args)
    except AfError as exc:
        eprint(f"af: error: {exc}")
        return 2
