"""Command-line interface for `pakem`."""

from __future__ import annotations

import argparse
from pathlib import Path

from .changelog import run_changelog
from .common import Framework, PakemError, eprint
from .device_config import SETTINGS, run_config
from .doctor import run_doctor
from .drift_report import DEFAULT_CONFIG, DEFAULT_KEEP, run_report
from .enforce import run_enforce
from .evals import HOSTS as EVAL_HOSTS
from .evals import MAX_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS, run_eval
from .githooks import run_hooks_install
from .installer import run_install, run_uninstall
from .legacy_install import migrate
from .lint import run_lint
from .project import run_check, run_sync
from .schedule import DEFAULT_HOUR, DEFAULT_MINUTE, DEFAULT_WEEKDAY, default_context, run_schedule
from .setup_device import run_setup_check, run_setup_device, run_setup_key
from .status import HOOK_PROMPT, HOOK_SESSION_START, run_status
from .update import run_update

DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent
ENFORCE_HOSTS = ("claude", "codex", "antigravity")
# Commands that write installed state migrate it from the former name first;
# install does so itself, after its source checks.
MIGRATING_COMMANDS = ("uninstall", "enforce", "schedule")
# Maturity per area (ADR 0011, decision 10); the kernel rule "use pakem" applies to stable ones.
AREAS = (
    ("status and updates", "experimental", "status, update, config"),
    ("device", "experimental", "setup device, setup key, setup check"),
    ("host", "draft", "setup host, host"),
    ("project", "draft", "project init, project adopt, decide"),
    ("secrets", "draft", "secret"),
    ("release", "draft", "release"),
    (
        "framework and agents",
        "stable",
        "install, uninstall, doctor, lint, changelog, project sync|check, report, schedule, hooks, enforce, eval",
    ),
)


def _add_apply(parser: argparse.ArgumentParser, verb: str = "write") -> None:
    parser.add_argument("--apply", action="store_true", help=f"{verb} changes (default is a dry run)")


def _bounded_int(low: int, high: int):
    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"expected a whole number, got {raw!r}") from exc
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}, got {value}")
        return value

    return parse


def areas_text() -> str:
    width = max(len(name) for name, _, _ in AREAS)
    lines = ["areas (stage: commands; see docs/pakem-command-map.md):"]
    lines += [f"  {name.ljust(width)}  {stage}: {commands}" for name, stage, commands in AREAS]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pakem",
        description="Pakem: kernel, skills, and project checks for coding agents.",
        epilog=areas_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="framework repository root (default: the checkout containing this tool)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("help", help="show commands and each area's maturity stage")

    status = commands.add_parser(
        "status", help="compare the installed framework with merged main, and this project's pins"
    )
    status.add_argument("--brief", action="store_true", help="only the summary and problems")
    status.add_argument("--json", action="store_true", help="machine-readable result")
    network = status.add_mutually_exclusive_group()
    network.add_argument(
        "--refresh", action="store_true", help="fetch origin now (default: at most every 4 hours)"
    )
    network.add_argument("--offline", action="store_true", help="never fetch; use the last check")
    status.add_argument("--hook", choices=(HOOK_SESSION_START, HOOK_PROMPT), help=argparse.SUPPRESS)
    status.add_argument("--host", default="claude", help=argparse.SUPPRESS)

    update = commands.add_parser(
        "update", help="install the framework from merged main, then list the files that changed"
    )
    update.add_argument("--plan", action="store_true", help="print the steps without doing them")
    update.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    update.add_argument("--json", action="store_true", help="machine-readable result")

    setup = commands.add_parser("setup", help="prepare this device (installs only after one confirmation)")
    setup_commands = setup.add_subparsers(dest="setup_command", required=True)
    setup_device = setup_commands.add_parser(
        "device", help="prepare everything a device can need in one go (commands also prepare their own needs)"
    )
    setup_device.add_argument("--plan", action="store_true", help="print the steps without doing them")
    setup_device.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    setup_device.add_argument("--json", action="store_true", help="machine-readable result")
    setup_key = setup_commands.add_parser(
        "key", help="create the device key on your terminal (asks for its passphrase)"
    )
    setup_key.add_argument(
        "--no-passphrase",
        action="store_true",
        help="no passphrase, only for a device nobody can unlock (recorded in device.toml)",
    )
    setup_check = setup_commands.add_parser("check", help="list tools, versions, the key, and forge logins")
    setup_check.add_argument("--json", action="store_true", help="machine-readable result")

    config = commands.add_parser("config", help="device settings in ~/.config/pakem/config.toml")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_get = config_commands.add_parser("get", help="print one setting")
    config_get.add_argument("key", choices=sorted(SETTINGS))
    config_set = config_commands.add_parser("set", help="change one setting")
    config_set.add_argument("key", choices=sorted(SETTINGS))
    config_set.add_argument("value")
    config_commands.add_parser("list", help="print every setting with its allowed values")

    install = commands.add_parser("install", help="install the kernel and skills into host locations")
    install.add_argument("--hosts", help="comma-separated host ids from hosts/targets.toml (default: all)")
    install.add_argument("--profile", action="append", metavar="DIR", help="profile directory (repeatable)")
    install.add_argument(
        "--force", action="store_true", help="back up and replace locally edited managed files"
    )
    install.add_argument(
        "--allow-dirty",
        action="store_true",
        help="install from a framework tree with uncommitted changes, or one that is not a git work tree",
    )
    install.add_argument(
        "--allow-unmerged",
        action="store_true",
        help=(
            "install from a framework checkout that is not on main or differs from origin/main; "
            "independent of --allow-dirty (testing a dirty branch needs both)"
        ),
    )
    install.add_argument(
        "--strict-source",
        action="store_true",
        help="refuse --apply when a --profile git checkout is not on a clean main (default: warn)",
    )
    _add_apply(install)

    uninstall = commands.add_parser("uninstall", help="remove pakem-managed files and restore backups")
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

    changelog = commands.add_parser(
        "changelog", help="show pending changes/ fragments or release them into CHANGELOG.md"
    )
    changelog.add_argument(
        "--release", metavar="X.Y.Z", help="build the release section for this version (dry run by default)"
    )
    changelog.add_argument(
        "--date", metavar="YYYY-MM-DD", help="release date (default: today; requires --release)"
    )
    _add_apply(changelog)

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

    report = commands.add_parser("report", help="write a read-only Markdown drift report")
    report.add_argument("--config", metavar="FILE", help=f"report config (default: {DEFAULT_CONFIG})")
    report.add_argument(
        "--output-dir",
        metavar="DIR",
        help="where reports are written (default: ~/.local/state/pakem/reports)",
    )
    report.add_argument(
        "--keep",
        type=_bounded_int(1, 10_000),
        metavar="N",
        help=f"dated reports to keep (default: the config's keep, else {DEFAULT_KEEP})",
    )

    schedule = commands.add_parser("schedule", help="run pakem report weekly through launchd (macOS)")
    schedule_commands = schedule.add_subparsers(dest="schedule_command", required=True)
    schedule_install = schedule_commands.add_parser("install", help="install the weekly launchd agent")
    schedule_install.add_argument(
        "--weekday",
        type=_bounded_int(0, 7),
        default=DEFAULT_WEEKDAY,
        metavar="N",
        help="launchd weekday, 0 and 7 are Sunday (default: 1, Monday)",
    )
    schedule_install.add_argument(
        "--hour", type=_bounded_int(0, 23), default=DEFAULT_HOUR, metavar="H", help="hour (default: 8)"
    )
    schedule_install.add_argument(
        "--minute", type=_bounded_int(0, 59), default=DEFAULT_MINUTE, metavar="M", help="minute (default: 0)"
    )
    _add_apply(schedule_install)
    schedule_uninstall = schedule_commands.add_parser("uninstall", help="unload and remove the launchd agent")
    _add_apply(schedule_uninstall, "remove")
    schedule_commands.add_parser("status", help="show the plist and launchd state (read-only)")

    hooks = commands.add_parser("hooks", help="manage repository git hooks")
    hooks_commands = hooks.add_subparsers(dest="hooks_command", required=True)
    hooks_install = hooks_commands.add_parser("install", help="install commit-msg and pre-push hooks")
    hooks_install.add_argument("repo", nargs="?", help="repository directory (default: current directory)")
    hooks_install.add_argument("--force", action="store_true", help="replace differing hooks in .githooks/")
    hooks_install.add_argument(
        "--protect",
        action="append",
        metavar="BRANCH",
        help=(
            "protect this branch (repeatable); replaces the repository's protected set, "
            "so list main too if it stays protected (default when never set: main, master)"
        ),
    )
    hooks_install.add_argument(
        "--protect-none",
        action="store_true",
        help=(
            "protect no branch, so code may be pushed directly to main; only for a project whose "
            "AGENTS.md records default:branching in ## Overrides"
        ),
    )
    hooks_install.add_argument(
        "--local-only",
        action="store_true",
        help="add .githooks/ to .git/info/exclude so the hooks are never committed (client repositories)",
    )
    _add_apply(hooks_install)

    enforce = commands.add_parser("enforce", help="install host-level guardrails")
    enforce_commands = enforce.add_subparsers(dest="enforce_command", required=True)
    for name in ("install", "uninstall"):
        enforce_parser = enforce_commands.add_parser(name, help=f"{name} guardrails for one host")
        enforce_parser.add_argument("--host", required=True, choices=ENFORCE_HOSTS)
        _add_apply(enforce_parser)

    evals = commands.add_parser("eval", help="measure kernel compliance per agent host")
    eval_commands = evals.add_subparsers(dest="eval_command", required=True)
    eval_run = eval_commands.add_parser(
        "run", help="run compliance scenarios against one host CLI (dry run by default)"
    )
    eval_run.add_argument("--host", required=True, choices=sorted(EVAL_HOSTS))
    eval_run.add_argument(
        "--scenario", action="append", metavar="ID", help="run only this scenario (repeatable; default: all)"
    )
    eval_run.add_argument(
        "--repeat", type=_bounded_int(1, 20), default=3, metavar="K", help="repeats per scenario (default: 3)"
    )
    eval_run.add_argument(
        "--timeout",
        type=_bounded_int(MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS),
        metavar="SECONDS",
        help="per-run timeout for every scenario (default: each scenario's timeout_seconds)",
    )
    eval_run.add_argument(
        "--keep-fixtures",
        action="store_true",
        help="keep each fixture and a redacted transcript in the system temp directory",
    )
    eval_run.add_argument(
        "--results-dir", metavar="DIR", help="where results are appended (default: evals/results in the framework)"
    )
    eval_run.add_argument(
        "--apply",
        action="store_true",
        help="run live, billed agent sessions and record results (default is a dry run)",
    )
    return parser


def migrate_before(args: argparse.Namespace) -> None:
    if args.command not in MIGRATING_COMMANDS or not hasattr(args, "apply"):
        return
    migration = migrate(Framework(Path(args.root)), args.apply, default_context())
    migration.print()
    for warning in migration.warnings:
        print(f"WARNING: {warning}")


def dispatch(args: argparse.Namespace) -> int:
    migrate_before(args)
    if args.command == "help":
        build_parser().print_help()
        return 0
    if args.command == "status":
        return run_status(args)
    if args.command == "update":
        return run_update(args)
    if args.command == "config":
        return run_config(args)
    if args.command == "setup" and args.setup_command == "device":
        return run_setup_device(args)
    if args.command == "setup" and args.setup_command == "key":
        return run_setup_key(args)
    if args.command == "setup" and args.setup_command == "check":
        return run_setup_check(args)
    if args.command == "install":
        return run_install(args)
    if args.command == "uninstall":
        return run_uninstall(args)
    if args.command == "doctor":
        return run_doctor(args)
    if args.command == "lint":
        return run_lint(args)
    if args.command == "changelog":
        return run_changelog(args)
    if args.command == "project" and args.project_command == "sync":
        return run_sync(args)
    if args.command == "project" and args.project_command == "check":
        return run_check(args)
    if args.command == "report":
        return run_report(args)
    if args.command == "schedule":
        return run_schedule(args)
    if args.command == "hooks":
        return run_hooks_install(args)
    if args.command == "enforce":
        return run_enforce(args)
    if args.command == "eval" and args.eval_command == "run":
        return run_eval(args)
    raise PakemError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return dispatch(args)
    except PakemError as exc:
        eprint(f"pakem: error: {exc}")
        return exc.exit_code
